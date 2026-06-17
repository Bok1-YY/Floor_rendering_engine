# -*- coding: utf-8 -*-
"""生成失败原因知识库（Failure Knowledge Base）。

—— 来源：对 app_local_save.log（2517 行 / 174 ERROR / 403 WARNING）的大汇总分析，
   + 对照 api.py 实际产生 job.error 的代码路径。把"真实出现过的失败"固化成一张
   有序分类表，失败卡片实时查表 → 给出【预写原因 + 处理建议】，省去每次翻日志。

以后新增/调整失败类型，**只改本文件**（webui 只消费 classify_failure 的结果）。

日志归类（量级仅供参考，按当时日志统计）：

  网络/线路类（绝大多数，~300+ 次 WARNING/ERROR）—— 都被 api.py 包成 "网络错误: …"：
    · ('Connection aborted.', RemoteDisconnected(...))         远端无响应直接断开（最常见）
    · SSLError(SSLEOFError ... UNEXPECTED_EOF_WHILE_READING)   TLS 被中途切断（透明代理特征）
    · ('Connection aborted.', ConnectionResetError(10054,'远程主机强迫关闭了一个现有的连接。'))
    · ProxyError('Unable to connect to proxy', ...)            代理连不上
    · 请求超时 / TimeoutError('The write operation timed out')
    · 连接假死被看门狗中断 / 看门狗触发 / 超过总时限 / 总时限 Ns 到
    · Response ended prematurely / Max retries exceeded
    根因：公司软路由（透明代理）对 4K 大图的长连接偶发重置——已知现象，非程序 bug。

  服务端过载（可重试 5xx/429 重试耗尽）：
    · HTTP 503: This model is currently experiencing high demand...
    · HTTP 429: 限流

  账号额度：
    · HTTP {code}: Your project has exceeded its monthly spending cap...

  输入图问题（不可重试）：
    · HTTP 400: Unable to process input image...

  配置/输入：
    · 未配置 Fal API Key(请在 API 设置里填写 Fal Key)
    · 素材图不存在: <path>
    · 未上传地板图

  其它：
    · 安全拦截: <类别>
    · API 未返回图片 / 解码失败: ...
    · 已取消 / 程序重启，任务已中断
    · 直连失败(...)；Fal 备用也失败(...)
"""

import re

# 有序规则表：**特例在前、网络兜底在后**。
# 注意顺序：spend_cap / bad_input_image / fal_key_missing / no_floor_image 必须排在
# overload / network 之前——否则会被 "HTTP …" / "网络错误: …" 抢先匹配。
# 每条：
#   key     稳定标识
#   title   超短标题（卡片内联显示，含 emoji）
#   cause   这种失败为什么会发生（预写）
#   action  该怎么办（预写）
#   subs    子串列表，命中任一即算匹配（大小写不敏感）
#   regex   可选正则（与 subs 任一命中即匹配）
FAILURE_RULES = [
    {
        'key': 'cancelled',
        'title': '↩ 已取消 / 已中断',
        'cause': '任务被手动停止，或程序重启时该任务尚未跑完被中断。',
        'action': '重新发起一次生成即可（已中断的任务也可点「🔁 重试」）。',
        'subs': ['已取消', '程序重启', '任务已中断'],
    },
    {
        'key': 'no_floor_image',
        'title': '📁 缺地板素材图',
        'cause': '没有选/上传地板素材图，或之前选用的图文件已不在磁盘上。',
        'action': '在左侧「地板素材图」重新上传，或从「📁 历史小样」里选一张已有的地板图。',
        'subs': ['未上传地板图', '素材图不存在'],
    },
    {
        'key': 'fal_key_missing',
        'title': '🔑 未配 Fal Key',
        'cause': '生图线路选了「Fal 路由」，但 API 设置里没有填 Fal API Key。',
        'action': '到「🔑 API 设置」填上 Fal Key；或把生图线路切回「Google 直连」。',
        'subs': ['未配置 Fal API Key', '未配置 Fal Key', 'Fal API Key'],
    },
    {
        'key': 'spend_cap',
        'title': '💳 API 额度用尽',
        'cause': '这个 Gemini API Key 所属项目的月度消费额度已用尽（账号/计费问题，与网络无关）。',
        'action': '去 AI Studio（ai.studio/spend）调高或解除消费上限；或在 API 设置换一个额度充足的 Key。',
        'subs': ['spending cap', 'spend cap', 'monthly spend', 'exceeded its monthly'],
    },
    {
        'key': 'bad_input_image',
        'title': '🖼 输入图无法处理',
        'cause': '上传的地板/房间/参照图 Google 无法处理——通常是图片过大、损坏，或格式异常。',
        'action': '换一张正常的图重试；确认图片能正常打开、不是超大异形图（可先压一下尺寸）。',
        'subs': ['Unable to process input image'],
    },
    {
        'key': 'safety_block',
        'title': '🛡 被安全策略拦截',
        'cause': '请求被内容安全策略拦截（较少见，可能是提示词或图片触发了安全分类）。',
        'action': '微调提示词或更换素材/参照图后重试。',
        'subs': ['安全拦截'],
    },
    {
        'key': 'overload',
        'title': '🔥 服务端过载·稍后重试',
        'cause': 'Google 模型侧临时繁忙或限流（503 高需求 / 429 限流），与你的网络无关，是服务端过载。',
        'action': '等十几秒到几分钟再点「🔁 重试」；高峰期可错峰，或在 API 设置开「自动转 Fal」走备用线路。',
        'subs': ['high demand', 'experiencing high demand', 'RESOURCE_EXHAUSTED'],
        'regex': r'HTTP (429|500|502|503|504)',
    },
    {
        'key': 'both_failed',
        'title': '⚠ 双线路都失败',
        'cause': 'Google 直连与 Fal 备用线路都失败了——通常是整个时段网络都不通。',
        'action': '检查网络/代理后再重试；若一直不行，稍后再试或切换网络环境。',
        'subs': ['Fal 备用也失败', '直连失败('],
    },
    {
        'key': 'no_image_returned',
        'title': '❔ 没拿到图·重试',
        'cause': '服务端返回了响应但没有有效图片，或图片解码失败（偶发）。',
        'action': '直接点「🔁 重试」通常就好；多次复现可换提示词/素材再试。',
        'subs': ['API 未返回图片', '解码失败'],
    },
    {
        # 网络/传输类兜底——放在最后（"网络错误:" 包了一大堆底层异常）
        'key': 'network',
        'title': '🌐 网络波动·重试即可',
        'cause': '公司软路由（透明代理）对 4K 大图的长连接偶发重置 TLS——这是已知现象，不是程序 bug。'
                 '常见表现：远端断开 / SSL EOF / 连接被强制关闭 / 代理连不上 / 请求超时 / 连接假死。',
        'action': '直接点「🔁 重试」通常即可成功；千万别为此降分辨率（4K 是硬需求）。'
                  '若一段时间频繁失败：API 设置切「🛡 韧性」策略，或勾「直连失败自动转 Fal」用备用线路应急。',
        'subs': [
            '网络错误', '请求超时', '连接假死', '看门狗', '超过总时限', '总时限',
            'Connection aborted', 'RemoteDisconnected', 'ConnectionReset',
            '远程主机强迫关闭', 'SSLEOFError', 'UNEXPECTED_EOF', 'ProxyError',
            'Response ended prematurely', 'Max retries exceeded', 'timed out',
        ],
    },
]

_UNKNOWN = {
    'key': 'unknown',
    'title': '❔ 未知错误',
    'cause': '这条报错暂未归入已知类别。',
    'action': '看下方「完整原始报错」；多为临时问题，可先点「🔁 重试」。若反复出现，把原始报错反馈给维护者补充知识库。',
}


def classify_failure(err) -> dict:
    """把一条 job.error 文本归类到 FAILURE_RULES 中最匹配的一条。

    顺序匹配、首条命中即返回（故特例规则必须排在网络兜底之前）。
    返回 dict：{key, title, cause, action}。无命中 → _UNKNOWN。
    """
    e = str(err or '')
    if not e.strip():
        return dict(_UNKNOWN)
    low = e.lower()
    for rule in FAILURE_RULES:
        for s in rule.get('subs', ()):
            if s.lower() in low:
                return {'key': rule['key'], 'title': rule['title'],
                        'cause': rule['cause'], 'action': rule['action']}
        rx = rule.get('regex')
        if rx and re.search(rx, e):
            return {'key': rule['key'], 'title': rule['title'],
                    'cause': rule['cause'], 'action': rule['action']}
    return dict(_UNKNOWN)

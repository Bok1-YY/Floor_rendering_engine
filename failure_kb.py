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

  线路落地地区（HTTP 400，非网络重置）：
    · HTTP 400: User location is not supported for the API use.
      根因：出口 IP 落在 Gemini 不支持的地区（大陆被排除）→ Google 直接回绝。
      请求已到达 Google，故不是透明代理重置；换海外节点或转 Fal 可解。

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
        'key': 'geo_block',
        'title': '🌍 线路落地地区不支持',
        'cause': '出口 IP 落在了 Gemini API 不支持的地区（大陆即在被排除之列），被 Google 直接回绝'
                 '（HTTP 400: User location is not supported）。请求其实已到达 Google，不是网络重置、'
                 '也不是 Key 问题——是这条线路的境外落地节点掉了/换了。',
        'action': '恢复软路由上把 Google 流量落地到支持地区的海外出口后点「🔁 重试」即可；'
                  '应急可把生图线路切「Fal 路由」或开「直连失败自动转 Fal」绕过地区封锁（走你自己的 Fal 额度）。',
        'subs': ['User location is not supported', 'location is not supported',
                 'not supported for the API use'],
    },
    {
        'key': 'tls_cert',
        'title': '🔐 证书校验失败',
        'cause': '开了 HTTPS 证书校验（tls_verify=true），但当前网络（软路由/代理）在拦截 HTTPS、'
                 '证书链验证不过——常见于换了网络环境，或代理改了 TLS 解密策略。重试无用。',
        'action': '在 engine_config.json 设 tls_verify=false 关掉校验即可恢复；'
                  '或把代理根证书路径填到 tls_ca_bundle。改完点「🔑 连通性自检」看「证书校验」那行确认。',
        'subs': ['CERTIFICATE_VERIFY_FAILED', 'certificate verify failed', 'SSLCertVerificationError',
                 'unable to get local issuer', 'self signed certificate', 'self-signed certificate'],
    },
    {
        'key': 'safety_block',
        'title': '🛡 被安全策略拦截',
        'cause': '请求被内容安全策略拦截（较少见，可能是提示词或图片触发了安全分类）。',
        'action': '微调提示词或更换素材/参照图后重试。',
        'subs': ['安全拦截'],
    },
    # ── 生成式修补（inpaint）专属：ComfyUI 是内网自备实例，错误话术与云线路完全不同，
    #    必须排在 overload/network 之前，否则「ComfyUI 连接失败」会被网络兜底吃掉 ──
    {
        'key': 'comfyui_unreachable',
        'title': '🔌 ComfyUI 连不上',
        'cause': '修补引擎选了 ComfyUI，但配置的地址无法访问：实例没启动、地址/端口写错、或跨机器时防火墙未放行。',
        'action': '先确认 ComfyUI 已启动并能在浏览器打开；到「⚙ 设置 → 生成式修补引擎」点「测试连接」自查；'
                  '或把引擎切回「Fal FLUX Fill」。',
        'subs': ['ComfyUI 连接失败', '未配置 ComfyUI 地址', 'ComfyUI 提交失败', 'ComfyUI 状态轮询网络错误'],
    },
    {
        'key': 'comfyui_workflow',
        'title': '🧩 ComfyUI workflow 问题',
        'cause': '多数是内置模板里的 checkpoint 文件名在你的 ComfyUI 里不存在；或自定义 workflow JSON 不合法/缺占位符。',
        'action': '改内置模板的 ckpt_name 为你已有的模型文件；或在设置里指向自己的 inpaint workflow'
                  '（记得写 __INPAINT_IMAGE__/__INPAINT_MASK__/__INPAINT_PROMPT__ 占位符）。',
        'subs': ['ComfyUI workflow 校验失败', 'workflow 模板不存在', 'workflow 模板解析失败',
                 'ComfyUI 执行失败', '未产出图片'],
    },
    {
        'key': 'eraser_endpoint',
        'title': '🧹 修补模型暂不可用',
        'cause': '生成式修补所选的云端模型端点返回 403/404：模型下线、改名或账号无权限。',
        'action': '到「⚙ 设置 → 生成式修补引擎」换一个移除模型（如切回 FLUX Fill）应急，并反馈开发者更新端点。',
        'regex': r'修补失败.*HTTP (403|404)',
        'subs': [],
    },
    {
        'key': 'inpaint_mask',
        'title': '🖌️ 遮罩无效',
        'cause': '提交的涂抹遮罩为空、解码失败、或与原图宽高比不一致（一般是前端图未加载完就提交）。',
        'action': '重新打开修补弹窗，等图完全显示后再涂抹提交；「生成式添加」记得填要添加内容的描述。',
        'subs': ['遮罩为空', '遮罩解码失败', '遮罩与原图宽高比不一致'],
    },
    # ── Omakase 文本模型专属：必须排在 overload/network 之前，
    #    否则文本线路的 429/5xx 会被误导向生图 Fal 线路 ──
    {
        'key': 'omakase_both_failed',
        'title': '⚠ Omakase 主备线路都失败',
        'cause': 'Gemini 场景主线路失败后，DeepSeek 备用线路也未能返回可用候选。',
        'action': '检查网络、Gemini Key 和 DeepSeek Key；也可直接在场景定稿框中手写后继续生图。',
        'subs': ['Omakase Gemini 主线路失败'],
    },
    {
        'key': 'omakase_model_unavailable',
        'title': '🔄 Gemini 场景模型已停用',
        'cause': '当前配置的 Omakase Gemini 文本模型已下线、改名，或不再向该项目开放；Key 和网络本身可能完全正常。',
        'action': '更新 Floor Engine 或把 Omakase 文本模型切换到当前稳定版本；无需重新申请 Gemini Key。',
        'subs': [
            'no longer available to new users',
            'Omakase Gemini HTTP 404',
            '模型不可用 (HTTP 404)',
        ],
    },
    {
        'key': 'omakase_gemini_error',
        'title': '❌ Gemini 场景生成失败',
        'cause': 'Omakase 的 Gemini 文本请求失败或未返回可用的结构化候选。',
        'action': '检查 Gemini Key 和网络；配置 DeepSeek Key 后可自动走备用线路，也可直接手写场景定稿。',
        'subs': ['Omakase Gemini', '未配置 Gemini API Key'],
    },
    {
        'key': 'deepseek_auth',
        'title': '🔑 DeepSeek 认证/余额问题',
        'cause': 'Omakase 用的 DeepSeek API Key 无效或过期(401)，或账户余额不足(402)——与生图的 Gemini/Fal Key 无关。',
        'action': '到「设置」检查 DeepSeek API Key 是否正确；若是余额不足(402)，去 DeepSeek 平台充值后再试。',
        'subs': ['DeepSeek HTTP 401', 'DeepSeek HTTP 402', '未配置 DeepSeek', 'insufficient balance',
                 'invalid_api_key', 'authentication fails'],
    },
    {
        'key': 'deepseek_limit',
        'title': '🚦 DeepSeek 限流·稍后重试',
        'cause': 'DeepSeek 文本服务临时限流(429)或并发过高。',
        'action': '等一会儿再点「✨ Omakase 生成」；若频繁出现可降低使用频率或升级 DeepSeek 订阅。',
        'subs': ['DeepSeek HTTP 429'],
    },
    {
        'key': 'deepseek_error',
        'title': '❌ Omakase 场景生成失败',
        'cause': 'DeepSeek 处理你的场景诉求时出错（服务端异常、网络波动或返回格式异常）。这只影响 Omakase 场景生成，不影响手动生图。',
        'action': '换个说法或简化诉求再试；也可直接在场景框里手写描述、跳过 Omakase 照常生图。',
        'subs': ['DeepSeek', 'Omakase', '场景诉求为空', '未返回可用场景', '返回解析失败'],
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

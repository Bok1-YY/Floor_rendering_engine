"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ConfigPatch, ConfigView, FailureKB } from "@/lib/types";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

// 移除/添加模式可选模型（provider=fal 时生效）；与后端 config.INPAINT_*_MODELS 对齐
const REMOVE_MODELS = [
  { value: "bria-eraser", label: "BRIA Eraser（专职移除 · 推荐）" },
  { value: "gemini-mark", label: "Gemini 标记法（需有效 Gemini Key · 较慢）" },
  { value: "finegrain-eraser", label: "Finegrain Eraser（连阴影一起除 · 会降分辨率）" },
  { value: "flux-fill", label: "FLUX Fill（移除易脑补物体，不推荐）" },
  { value: "lama", label: "LaMa（廉价快速 · 复杂纹理弱）" },
];
const ADD_MODELS = [
  { value: "flux-fill", label: "FLUX Fill（真 inpainting 填充）" },
  { value: "qwen-inpaint", label: "Qwen Inpaint（便宜 · 指令式）" },
  { value: "gemini-mark", label: "Gemini 标记法（质量最佳 · 较慢）" },
];

const fieldInput =
  "h-10 w-full rounded-[10px] border-border bg-panel text-[13px] text-foreground";
const fieldLabel = "text-[12px] font-semibold text-muted-foreground";

export default function SettingsPage() {
  const [cfg, setCfg] = useState<ConfigView | null>(null);
  const [geminiKey, setGeminiKey] = useState("");
  const [falKey, setFalKey] = useState("");
  const [proxy, setProxy] = useState("");
  const [falQueueProxy, setFalQueueProxy] = useState("");
  const [provider, setProvider] = useState("google");
  const [speed, setSpeed] = useState("fast");
  const [failover, setFailover] = useState(false);
  const [autoColorMatchEnabled, setAutoColorMatchEnabled] = useState(true);
  const [tlsVerify, setTlsVerify] = useState(true);
  const [conc, setConc] = useState(1);
  const [testResult, setTestResult] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [rules, setRules] = useState<FailureKB[]>([]);
  const [showGemini, setShowGemini] = useState(false);
  const [deepseekKey, setDeepseekKey] = useState("");
  const [omakaseEnabled, setOmakaseEnabled] = useState(false);
  const [sdEnabled, setSdEnabled] = useState(false);
  // 成本单价（元/张成功图）：字符串态便于清空；保存时空串=删除该项
  const [priceB2, setPriceB2] = useState("");
  const [pricePro, setPricePro] = useState("");
  const [priceVR360, setPriceVR360] = useState("");
  const [priceLite, setPriceLite] = useState("");
  const [priceSD35, setPriceSD35] = useState("");
  const [priceAuraSR, setPriceAuraSR] = useState("");
  const [priceFluxFill, setPriceFluxFill] = useState("");
  // 生成式修补引擎（inpaint）：fal=云 API（remove/add 分模型）；comfyui=自备 ComfyUI 实例
  const [inpaintProvider, setInpaintProvider] = useState("fal");
  const [removeModel, setRemoveModel] = useState("bria-eraser");
  const [addModel, setAddModel] = useState("flux-fill");
  const [priceBriaEraser, setPriceBriaEraser] = useState("");
  const [priceFinegrain, setPriceFinegrain] = useState("");
  const [priceQwen, setPriceQwen] = useState("");
  const [priceGeminiMark, setPriceGeminiMark] = useState("");
  const [comfyuiUrl, setComfyuiUrl] = useState("");
  const [comfyuiWorkflow, setComfyuiWorkflow] = useState("");
  const [comfyuiTimeout, setComfyuiTimeout] = useState(600);
  const [removePrompt, setRemovePrompt] = useState("");
  const [comfyTesting, setComfyTesting] = useState(false);
  const [comfyResult, setComfyResult] = useState("");
  // PPTX 品牌导出
  const [pptxCompany, setPptxCompany] = useState("");
  const [pptxContact, setPptxContact] = useState("");
  const [logoUploading, setLogoUploading] = useState(false);

  function load() {
    api
      .getConfig()
      .then((c) => {
        setCfg(c);
        setProxy(c.proxy || "");
        setFalQueueProxy(c.fal_queue_proxy || "");
        setProvider(c.image_provider);
        setSpeed(c.speed_profile);
        setFailover(c.auto_failover);
        setAutoColorMatchEnabled(c.auto_color_match_enabled !== false);
        setTlsVerify(c.tls_verify);
        setConc(c.max_concurrent_per_model);
        setOmakaseEnabled(!!c.omakase_enabled);
        setSdEnabled(!!c.sd_enabled);
        setPriceB2(c.usage_prices?.B2 != null ? String(c.usage_prices.B2) : "");
        setPricePro(c.usage_prices?.Pro != null ? String(c.usage_prices.Pro) : "");
        setPriceVR360(c.usage_prices?.VR360 != null ? String(c.usage_prices.VR360) : "");
        setPriceLite(c.usage_prices?.Lite != null ? String(c.usage_prices.Lite) : "");
        setPriceSD35(c.usage_prices?.SD35 != null ? String(c.usage_prices.SD35) : "");
        setPriceAuraSR(c.usage_prices?.AuraSR != null ? String(c.usage_prices.AuraSR) : "");
        setPriceFluxFill(c.usage_prices?.FluxFill != null ? String(c.usage_prices.FluxFill) : "");
        setInpaintProvider(c.inpaint_provider || "fal");
        setRemoveModel(c.inpaint_remove_model || "bria-eraser");
        setAddModel(c.inpaint_add_model || "flux-fill");
        setPriceBriaEraser(c.usage_prices?.BriaEraser != null ? String(c.usage_prices.BriaEraser) : "");
        setPriceFinegrain(
          c.usage_prices?.FinegrainEraser != null ? String(c.usage_prices.FinegrainEraser) : "",
        );
        setPriceQwen(c.usage_prices?.QwenInpaint != null ? String(c.usage_prices.QwenInpaint) : "");
        setPriceGeminiMark(c.usage_prices?.GeminiMark != null ? String(c.usage_prices.GeminiMark) : "");
        setComfyuiUrl(c.comfyui_base_url || "");
        setComfyuiWorkflow(c.comfyui_workflow_path || "");
        setComfyuiTimeout(c.comfyui_timeout || 600);
        setRemovePrompt(c.inpaint_remove_prompt || "");
        setPptxCompany(c.pptx_company || "");
        setPptxContact(c.pptx_contact || "");
      })
      .catch((e) => toast.error((e as Error).message));
  }
  useEffect(() => {
    load();
  }, []);

  async function save() {
    setSaving(true);
    try {
      const patch: ConfigPatch = {
        image_provider: provider,
        speed_profile: speed,
        auto_failover: failover,
        auto_color_match_enabled: autoColorMatchEnabled,
        proxy,
        fal_queue_proxy: falQueueProxy,
        tls_verify: tlsVerify,
        max_concurrent_per_model: conc,
      };
      if (geminiKey.trim()) patch.gemini_api_key = geminiKey.trim();
      if (falKey.trim()) patch.fal_api_key = falKey.trim();
      if (deepseekKey.trim()) patch.deepseek_api_key = deepseekKey.trim();
      patch.omakase_enabled = omakaseEnabled;
      patch.sd_enabled = sdEnabled;
      patch.inpaint_provider = inpaintProvider;
      patch.inpaint_remove_model = removeModel;
      patch.inpaint_add_model = addModel;
      patch.comfyui_base_url = comfyuiUrl.trim();
      patch.comfyui_workflow_path = comfyuiWorkflow.trim();
      patch.comfyui_timeout = Math.max(60, Math.min(3600, comfyuiTimeout || 600));
      patch.inpaint_remove_prompt = removePrompt.trim();
      // 单价：以已存配置为底（保留 'B2:fal' 等细分 key），B2/Pro 按输入覆盖，空串=删除
      const prices: Record<string, number> = { ...(cfg?.usage_prices || {}) };
      for (const [key, raw] of [
        ["B2", priceB2],
        ["Pro", pricePro],
        ["VR360", priceVR360],
        ["Lite", priceLite],
        ["SD35", priceSD35],
        ["AuraSR", priceAuraSR],
        ["FluxFill", priceFluxFill],
        ["BriaEraser", priceBriaEraser],
        ["FinegrainEraser", priceFinegrain],
        ["QwenInpaint", priceQwen],
        ["GeminiMark", priceGeminiMark],
      ] as const) {
        const t = raw.trim();
        if (!t) {
          delete prices[key];
          continue;
        }
        const n = Number(t);
        if (!Number.isFinite(n) || n < 0) {
          toast.error(`${key} 单价必须是非负数字`);
          setSaving(false);
          return;
        }
        prices[key] = n;
      }
      patch.usage_prices = prices;
      patch.pptx_company = pptxCompany.trim();
      patch.pptx_contact = pptxContact.trim();
      const c = await api.putConfig(patch);
      setCfg(c);
      setGeminiKey("");
      setFalKey("");
      setDeepseekKey("");
      toast.success("已保存");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setTesting(true);
    setTestResult("");
    try {
      const r = await api.connectionTest();
      setTestResult(r.result);
    } catch (e) {
      setTestResult("失败：" + (e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  function openRules() {
    setRulesOpen(true);
    if (rules.length === 0)
      api.getFailureRules().then(setRules).catch(() => {});
  }

  async function pickLogo(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setLogoUploading(true);
    try {
      await api.uploadLogo(f);
      toast.success("logo 已上传并保存");
      load(); // 重取配置里的 pptx_logo_url 刷新预览
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLogoUploading(false);
    }
  }

  async function clearLogo() {
    if (!cfg?.pptx_logo_url || !window.confirm("移除当前 PPTX 品牌 logo？")) return;
    setLogoUploading(true);
    try {
      await api.clearLogo();
      toast.success("logo 已移除");
      load();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setLogoUploading(false);
    }
  }

  const cloudModels = [removeModel, addModel];
  const needsFalForInpaint = cloudModels.some((m) => m !== "gemini-mark");
  const needsGeminiForInpaint = cloudModels.includes("gemini-mark");
  const missingInpaintKeys = [
    needsFalForInpaint && !(cfg?.has_fal_key || falKey.trim()) ? "Fal Key" : "",
    needsGeminiForInpaint && !(cfg?.has_gemini_key || geminiKey.trim()) ? "Gemini Key" : "",
  ].filter(Boolean);

  const provBtn = (active: boolean) =>
    cn(
      "h-[38px] flex-1 rounded-[9px] border text-[13px] font-semibold transition-colors",
      active
        ? "border-primary bg-primary-soft text-accent-foreground"
        : "border-border bg-card text-secondary-foreground hover:bg-accent",
    );

  return (
    <div className="h-full overflow-y-auto p-[26px]">
      <div className="mx-auto max-w-[680px]">
        <div className="mb-[18px] flex items-center justify-between">
          <div className="text-[17px] font-extrabold tracking-tight">设置</div>
          <button
            onClick={openRules}
            className="h-[34px] rounded-[9px] px-3 text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent"
          >
            ❓ 常见失败参考
          </button>
        </div>

        {cfg && (
          <div className="space-y-[14px]">
            {/* 密钥卡 */}
            <div className="rounded-[14px] border border-border bg-card p-[20px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-[14px] text-[11px] font-extrabold tracking-[0.1em] text-accent-foreground">
                密钥 / KEYS
              </div>

              <div className="mb-[15px] flex flex-col gap-[7px]">
                <span className={fieldLabel}>
                  Gemini API Key
                  {cfg.has_gemini_key && (
                    <span className="text-success">（已配置，留空不改）</span>
                  )}
                </span>
                <div className="relative">
                  <Input
                    type={showGemini ? "text" : "password"}
                    value={geminiKey}
                    onChange={(e) => setGeminiKey(e.target.value)}
                    placeholder={cfg.has_gemini_key ? "•••••••• 已配置，留空不改" : "未配置"}
                    className={cn(fieldInput, "pr-[58px]")}
                  />
                  <button
                    type="button"
                    onClick={() => setShowGemini((v) => !v)}
                    className="absolute right-1.5 top-1.5 h-7 rounded-md px-2.5 text-[11.5px] font-semibold text-muted-foreground hover:text-secondary-foreground"
                  >
                    {showGemini ? "隐藏" : "显示"}
                  </button>
                </div>
              </div>

              <div className="mb-[15px] flex flex-col gap-[7px]">
                <span className={fieldLabel}>
                  Fal API Key
                  {cfg.has_fal_key && (
                    <span className="text-success">（已配置，留空不改）</span>
                  )}
                </span>
                <Input
                  type="password"
                  value={falKey}
                  onChange={(e) => setFalKey(e.target.value)}
                  placeholder={cfg.has_fal_key ? "•••••••• 已配置，留空不改" : "未配置"}
                  className={fieldInput}
                />
              </div>

              <div className="mb-[15px] flex flex-col gap-[7px]">
                <span className={fieldLabel}>
                  DeepSeek API Key（Omakase 可选备用）
                  {cfg.has_deepseek_key && (
                    <span className="text-success">（已配置，留空不改）</span>
                  )}
                </span>
                <Input
                  type="password"
                  value={deepseekKey}
                  onChange={(e) => setDeepseekKey(e.target.value)}
                  placeholder={cfg.has_deepseek_key ? "•••••••• 已配置，留空不改" : "可选：Gemini 失败时自动使用"}
                  className={fieldInput}
                />
              </div>

              <div className="mb-[15px] flex items-center justify-between rounded-[10px] border border-border bg-panel p-[13px]">
                <div className="leading-snug">
                  <div className="text-[13px] font-bold text-foreground">启用 Omakase 模式</div>
                  <div className="mt-px text-[11px] text-muted-foreground">
                    开启 AI 场景生成：默认复用 Gemini Key，失败时自动使用已配置的 DeepSeek 备用线路；关闭后仍可手写场景定稿
                  </div>
                </div>
                <Switch checked={omakaseEnabled} onCheckedChange={setOmakaseEnabled} />
              </div>

              <div className="mb-[15px] flex items-center justify-between rounded-[10px] border border-border bg-panel p-[13px]">
                <div className="leading-snug">
                  <div className="text-[13px] font-bold text-foreground">启用 SD 3.5 实验线路</div>
                  <div className="mt-px text-[11px] text-muted-foreground">
                    仅纯效果图；使用 Fal SD 3.5 Large + IP-Adapter，2K/4K 追加 AuraSR 超分
                  </div>
                </div>
                <Switch checked={sdEnabled} onCheckedChange={setSdEnabled} />
              </div>

              <div className="mb-[15px] flex items-center justify-between rounded-[10px] border border-border bg-panel p-[13px]">
                <div className="leading-snug">
                  <div className="text-[13px] font-bold text-foreground">生图后自动校色</div>
                  <div className="mt-px text-[11px] text-muted-foreground">
                    B2 / Pro 地板工作流出图后自动匹配地板小样色彩；本地处理、不增加 API 费用，并保留 API 原图
                  </div>
                </div>
                <Switch checked={autoColorMatchEnabled} onCheckedChange={setAutoColorMatchEnabled} />
              </div>

              <div className="flex flex-col gap-[7px]">
                <span className={fieldLabel}>代理（留空走系统/软路由）</span>
                <Input
                  value={proxy}
                  onChange={(e) => setProxy(e.target.value)}
                  placeholder="http://127.0.0.1:7890 或留空"
                  className={fieldInput}
                />
              </div>
              <div className="mt-[15px] flex flex-col gap-[7px]">
                <span className={fieldLabel}>FAL 队列专用代理（默认留空直连）</span>
                <Input
                  value={falQueueProxy}
                  onChange={(e) => setFalQueueProxy(e.target.value)}
                  placeholder="留空直连；仅 FAL 无法直连时单独填写"
                  className={fieldInput}
                />
                <span className="text-[10.5px] text-muted-foreground">
                  Google 代理可能截断 SD 大请求；此项与上面的 Gemini/通用代理分离。
                </span>
              </div>
            </div>

            {/* 线路与网络卡 */}
            <div className="rounded-[14px] border border-border bg-card p-[20px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-[14px] text-[11px] font-extrabold tracking-[0.1em] text-accent-foreground">
                线路与网络 / NETWORK
              </div>

              <div className="grid grid-cols-3 gap-[14px]">
                <div>
                  <span className={cn(fieldLabel, "mb-[7px] block")}>生图线路</span>
                  <div className="flex gap-2">
                    <button onClick={() => setProvider("google")} className={provBtn(provider === "google")}>
                      Google 直连
                    </button>
                    <button onClick={() => setProvider("fal")} className={provBtn(provider === "fal")}>
                      Fal 路由
                    </button>
                  </div>
                </div>
                <div>
                  <span className={cn(fieldLabel, "mb-[7px] block")}>网络策略</span>
                  <div className="flex gap-2">
                    <button onClick={() => setSpeed("fast")} className={provBtn(speed === "fast")}>
                      极速 fast
                    </button>
                    <button onClick={() => setSpeed("resilient")} className={provBtn(speed === "resilient")}>
                      韧性 resilient
                    </button>
                  </div>
                </div>
              </div>

              <div className="mt-[15px] grid grid-cols-2 gap-[14px]">
                <div>
                  <span className={cn(fieldLabel, "mb-[7px] block")}>每模型并发数</span>
                  <Input
                    type="number"
                    min={1}
                    max={8}
                    value={conc}
                    onChange={(e) => setConc(Math.max(1, Math.min(8, Number(e.target.value) || 1)))}
                    className={fieldInput}
                  />
                </div>
                <div className="mt-[26px] flex items-center justify-between rounded-[10px] border border-border bg-panel px-[13px]">
                  <div className="leading-tight">
                    <div className="text-[12.5px] font-bold text-foreground">HTTPS 证书校验</div>
                    <div className="text-[10.5px] text-muted-foreground">坏网络可临时关</div>
                  </div>
                  <Switch checked={tlsVerify} onCheckedChange={setTlsVerify} />
                </div>
              </div>

              <div className="mt-[14px] flex items-center justify-between rounded-[10px] border border-border bg-panel p-[13px]">
                <div className="leading-snug">
                  <div className="text-[13px] font-bold text-foreground">直连失败自动转 Fal</div>
                  <div className="mt-px text-[11px] text-muted-foreground">
                    仅网络类失败触发 · Fal 走你自己的额度
                  </div>
                </div>
                <Switch checked={failover} onCheckedChange={setFailover} />
              </div>
            </div>

            {/* 生成式修补引擎卡 */}
            <div className="rounded-[14px] border border-border bg-card p-[20px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-[14px] text-[11px] font-extrabold tracking-[0.1em] text-accent-foreground">
                生成式修补引擎 / INPAINT
              </div>
              <div>
                <span className={cn(fieldLabel, "mb-[7px] block")}>引擎（画笔涂抹移除/添加所用的模型）</span>
                <div className="flex gap-2">
                  <button onClick={() => setInpaintProvider("fal")} className={provBtn(inpaintProvider === "fal")}>
                    云端模型
                  </button>
                  <button onClick={() => setInpaintProvider("comfyui")} className={provBtn(inpaintProvider === "comfyui")}>
                    ComfyUI（本地）
                  </button>
                </div>
                <div className="mt-[7px] text-[11px] leading-relaxed text-muted-foreground">
                  {inpaintProvider === "fal"
                    ? missingInpaintKeys.length
                      ? `⚠ 当前所选模型还需要：${missingInpaintKeys.join("、")}。请先在上方填写，否则修补会报错。`
                      : "按移除/添加各自选择的云模型调用并按张计费；移除模型负责延续背景，添加模型按描述生成新内容。"
                    : "走你自备的 ComfyUI 实例，本地算力零 API 费用；请填可信内网地址（ComfyUI 无鉴权）。本地引擎不区分移除/添加模型（由 workflow 模板决定）。"}
                </div>
              </div>

              {inpaintProvider === "fal" && (
                <div className="mt-[14px] grid grid-cols-2 gap-[14px] max-[700px]:grid-cols-1">
                  <div className="flex flex-col gap-[7px]">
                    <span className={fieldLabel}>移除模型（生成式移除用）</span>
                    <Select
                      value={removeModel}
                      onValueChange={(v) => setRemoveModel(v || "bria-eraser")}
                    >
                      <SelectTrigger className={fieldInput}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {REMOVE_MODELS.map((m) => (
                          <SelectItem key={m.value} value={m.value}>
                            {m.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex flex-col gap-[7px]">
                    <span className={fieldLabel}>添加模型（生成式添加用）</span>
                    <Select value={addModel} onValueChange={(v) => setAddModel(v || "flux-fill")}>
                      <SelectTrigger className={fieldInput}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ADD_MODELS.map((m) => (
                          <SelectItem key={m.value} value={m.value}>
                            {m.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )}

              {inpaintProvider === "comfyui" && (
                <div className="mt-[14px] space-y-[12px]">
                  <div className="flex flex-col gap-[7px]">
                    <span className={fieldLabel}>ComfyUI 地址</span>
                    <div className="flex gap-2">
                      <Input
                        value={comfyuiUrl}
                        onChange={(e) => setComfyuiUrl(e.target.value)}
                        placeholder="http://127.0.0.1:8188"
                        className={fieldInput}
                      />
                      <button
                        onClick={async () => {
                          setComfyTesting(true);
                          setComfyResult("");
                          try {
                            const r = await api.comfyuiPing(comfyuiUrl.trim());
                            setComfyResult(
                              r.ok
                                ? `✅ 连接成功${r.version ? ` · ComfyUI ${r.version}` : ""}${
                                    r.devices?.length ? ` · ${r.devices[0]}` : ""
                                  }`
                                : `❌ 连接失败：${r.error || "未知错误"}`,
                            );
                          } catch (e) {
                            setComfyResult("❌ " + (e as Error).message);
                          } finally {
                            setComfyTesting(false);
                          }
                        }}
                        disabled={comfyTesting || !comfyuiUrl.trim()}
                        className="h-10 flex-none rounded-[10px] border border-border bg-panel px-3 text-[12px] font-bold text-secondary-foreground hover:bg-accent disabled:opacity-50"
                      >
                        {comfyTesting ? "测试中…" : "测试连接"}
                      </button>
                    </div>
                    {comfyResult && (
                      <span className="text-[11px] text-muted-foreground">{comfyResult}</span>
                    )}
                  </div>
                  <div className="flex flex-col gap-[7px]">
                    <span className={fieldLabel}>自定义 workflow 路径（可选，API 格式 JSON）</span>
                    <Input
                      value={comfyuiWorkflow}
                      onChange={(e) => setComfyuiWorkflow(e.target.value)}
                      placeholder="留空用内置默认模板（需自改模板里的 checkpoint 名）"
                      className={fieldInput}
                    />
                    <span className="text-[10.5px] leading-relaxed text-muted-foreground">
                      模板里写占位符 __INPAINT_IMAGE__ / __INPAINT_MASK__ / __INPAINT_PROMPT__ /
                      __INPAINT_NEGATIVE__ / __INPAINT_SEED__，引擎自动注入；mask 已由服务端二值化并按模式外扩，
                      自定义 workflow 不要再次 GrowMask。可用任意 inpaint workflow（含 Flux Fill GGUF）。
                    </span>
                  </div>
                  <div className="flex flex-col gap-[7px]">
                    <span className={fieldLabel}>超时（秒，60–3600）</span>
                    <Input
                      type="number"
                      min={60}
                      max={3600}
                      value={comfyuiTimeout}
                      onChange={(e) => setComfyuiTimeout(Number(e.target.value) || 600)}
                      className={fieldInput}
                    />
                  </div>
                </div>
              )}

              <div className="mt-[14px] flex flex-col gap-[7px]">
                <span className={fieldLabel}>指令式移除模型默认提示词（可选）</span>
                <Input
                  value={removePrompt}
                  onChange={(e) => setRemovePrompt(e.target.value)}
                  placeholder="供 FLUX / Gemini / ComfyUI 使用；专职 Eraser 不读取提示词"
                  className={fieldInput}
                />
              </div>
            </div>

            {/* 成本单价卡 */}
            <div className="rounded-[14px] border border-border bg-card p-[20px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-[14px] text-[11px] font-extrabold tracking-[0.1em] text-accent-foreground">
                成本单价 / PRICING
              </div>
              <div className="grid grid-cols-2 gap-[14px]">
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>B2 每张成功图（元）</span>
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    value={priceB2}
                    onChange={(e) => setPriceB2(e.target.value)}
                    placeholder="留空不估算"
                    className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>Pro 每张成功图（元）</span>
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    value={pricePro}
                    onChange={(e) => setPricePro(e.target.value)}
                    placeholder="留空不估算"
                    className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>360° VR 全景每次（元）</span>
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    value={priceVR360}
                    onChange={(e) => setPriceVR360(e.target.value)}
                    placeholder="留空则确认框不显示估算"
                    className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>Lite 预览每张（元）</span>
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    value={priceLite}
                    onChange={(e) => setPriceLite(e.target.value)}
                    placeholder="留空不估算"
                    className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>SD 3.5 基础图（元）</span>
                  <Input
                    type="number" min={0} step="0.01" value={priceSD35}
                    onChange={(e) => setPriceSD35(e.target.value)}
                    placeholder="留空不估算" className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>AuraSR 超分（元）</span>
                  <Input
                    type="number" min={0} step="0.01" value={priceAuraSR}
                    onChange={(e) => setPriceAuraSR(e.target.value)}
                    placeholder="留空不估算" className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>FLUX Fill 修补每张（元）</span>
                  <Input
                    type="number" min={0} step="0.01" value={priceFluxFill}
                    onChange={(e) => setPriceFluxFill(e.target.value)}
                    placeholder="留空不估算（ComfyUI 引擎恒 0 成本）" className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>BRIA Eraser 移除每张（元）</span>
                  <Input
                    type="number" min={0} step="0.01" value={priceBriaEraser}
                    onChange={(e) => setPriceBriaEraser(e.target.value)}
                    placeholder="参考 $0.04/次" className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>Finegrain 移除每张（元）</span>
                  <Input
                    type="number" min={0} step="0.01" value={priceFinegrain}
                    onChange={(e) => setPriceFinegrain(e.target.value)}
                    placeholder="参考 Express $0.04/次" className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>Qwen 修补每张（元）</span>
                  <Input
                    type="number" min={0} step="0.01" value={priceQwen}
                    onChange={(e) => setPriceQwen(e.target.value)}
                    placeholder="参考 $0.03/MP" className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>Gemini 标记修补每张（元）</span>
                  <Input
                    type="number" min={0} step="0.01" value={priceGeminiMark}
                    onChange={(e) => setPriceGeminiMark(e.target.value)}
                    placeholder="参考 2K 档 ~$0.13/次" className={fieldInput}
                  />
                </div>
              </div>
              <div className="mt-[10px] text-[11px] leading-relaxed text-muted-foreground">
                用于「用量」页的成本估算（成功张数 × 单价，失败不计）。留空则对应行显示 —。
              </div>
            </div>

            {/* 品牌导出卡 */}
            <div className="rounded-[14px] border border-border bg-card p-[20px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-[14px] text-[11px] font-extrabold tracking-[0.1em] text-accent-foreground">
                品牌导出 / BRANDING
              </div>
              <div className="grid grid-cols-2 gap-[14px]">
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>公司名（PPTX 封面副标题 + 每页页脚）</span>
                  <Input
                    value={pptxCompany}
                    onChange={(e) => setPptxCompany(e.target.value)}
                    maxLength={200}
                    placeholder="留空不显示"
                    className={fieldInput}
                  />
                </div>
                <div className="flex flex-col gap-[7px]">
                  <span className={fieldLabel}>联系方式（PPTX 封面底部）</span>
                  <Input
                    value={pptxContact}
                    onChange={(e) => setPptxContact(e.target.value)}
                    maxLength={200}
                    placeholder="电话 / 微信 / 邮箱，留空不显示"
                    className={fieldInput}
                  />
                </div>
              </div>
              <div className="mt-[14px] flex items-center gap-[14px]">
                <div className="flex h-[54px] w-[110px] flex-none items-center justify-center overflow-hidden rounded-[10px] border border-border bg-panel">
                  {cfg.pptx_logo_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={api.imgUrl(cfg.pptx_logo_url)}
                      alt="logo"
                      className="max-h-full max-w-full object-contain"
                    />
                  ) : (
                    <span className="text-[11px] text-muted-foreground">无 logo</span>
                  )}
                </div>
                <label
                  className={cn(
                    "flex h-9 cursor-pointer items-center rounded-[9px] border border-border bg-card px-[14px] text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent",
                    logoUploading && "pointer-events-none opacity-50",
                  )}
                >
                  {logoUploading ? "上传中…" : "上传 logo"}
                  <input type="file" accept="image/*" className="hidden" onChange={pickLogo} />
                </label>
                {cfg.pptx_logo_url && (
                  <button
                    type="button"
                    onClick={clearLogo}
                    disabled={logoUploading}
                    className="h-9 rounded-[9px] border border-border bg-card px-[14px] text-[12.5px] font-semibold text-destructive hover:bg-destructive-soft disabled:opacity-50"
                  >
                    移除 logo
                  </button>
                )}
                <span className="text-[11px] leading-relaxed text-muted-foreground">
                  上传即生效（无需点保存）；显示在 PPTX 封面顶部。公司名/联系方式需点下方「保存」。
                </span>
              </div>
            </div>

            <div className="flex items-center gap-2.5">
              <button
                onClick={save}
                disabled={saving}
                className="h-[42px] rounded-[11px] bg-primary px-[22px] text-[13.5px] font-bold text-primary-foreground shadow-[0_5px_14px_rgba(193,95,60,.28)] hover:bg-primary-hover disabled:opacity-50"
              >
                {saving ? "保存中…" : "保存"}
              </button>
              <button
                onClick={test}
                disabled={testing}
                className="h-[42px] rounded-[11px] border border-border bg-card px-[18px] text-[13.5px] font-bold text-secondary-foreground hover:bg-accent disabled:opacity-50"
              >
                {testing ? "检测中…" : "连通性自检"}
              </button>
              <button
                onClick={load}
                className="h-[42px] rounded-[11px] px-[14px] text-[13.5px] font-semibold text-muted-foreground hover:bg-accent"
              >
                重载
              </button>
            </div>

            {testResult && (
              <pre className="whitespace-pre-wrap rounded-[11px] bg-accent p-[14px] font-mono text-[12.5px] leading-relaxed text-foreground">
                {testResult}
              </pre>
            )}

            <div className="text-[11.5px] text-muted-foreground">
              每模型并发改动需重启后端生效（信号量在启动时建）。
            </div>
          </div>
        )}

        <Dialog open={rulesOpen} onOpenChange={setRulesOpen}>
          <DialogContent className="max-h-[80vh] overflow-auto">
            <div className="space-y-3">
              <div className="text-[15px] font-bold">常见失败 · 原因与处理</div>
              {rules.length === 0 && (
                <div className="text-xs text-muted-foreground">加载中…</div>
              )}
              {rules.map((r) => (
                <div key={r.key} className="rounded-[10px] border border-border p-[11px] text-[12.5px]">
                  <div className="font-bold text-foreground">{r.title}</div>
                  {r.cause && <div className="mt-1 text-muted-foreground">{r.cause}</div>}
                  {r.action && <div className="mt-1 text-accent-foreground">➡ {r.action}</div>}
                </div>
              ))}
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}

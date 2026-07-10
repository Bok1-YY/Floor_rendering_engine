"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ConfigPatch, ConfigView, FailureKB } from "@/lib/types";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

const fieldInput =
  "h-10 w-full rounded-[10px] border-border bg-[#faf7f0] text-[13px] text-[#2a241f]";
const fieldLabel = "text-[12px] font-semibold text-[#857c6e]";

export default function SettingsPage() {
  const [cfg, setCfg] = useState<ConfigView | null>(null);
  const [geminiKey, setGeminiKey] = useState("");
  const [falKey, setFalKey] = useState("");
  const [proxy, setProxy] = useState("");
  const [provider, setProvider] = useState("google");
  const [speed, setSpeed] = useState("fast");
  const [failover, setFailover] = useState(false);
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

  function load() {
    api
      .getConfig()
      .then((c) => {
        setCfg(c);
        setProxy(c.proxy || "");
        setProvider(c.image_provider);
        setSpeed(c.speed_profile);
        setFailover(c.auto_failover);
        setTlsVerify(c.tls_verify);
        setConc(c.max_concurrent_per_model);
        setOmakaseEnabled(!!c.omakase_enabled);
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
        proxy,
        tls_verify: tlsVerify,
        max_concurrent_per_model: conc,
      };
      if (geminiKey.trim()) patch.gemini_api_key = geminiKey.trim();
      if (falKey.trim()) patch.fal_api_key = falKey.trim();
      if (deepseekKey.trim()) patch.deepseek_api_key = deepseekKey.trim();
      patch.omakase_enabled = omakaseEnabled;
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

  const provBtn = (active: boolean) =>
    cn(
      "h-[38px] flex-1 rounded-[9px] border text-[13px] font-semibold transition-colors",
      active
        ? "border-primary bg-[#fbf3ee] text-[#a8472a]"
        : "border-border bg-card text-[#6b6356] hover:bg-[#f2e9e0]",
    );

  return (
    <div className="h-full overflow-y-auto p-[26px]">
      <div className="mx-auto max-w-[680px]">
        <div className="mb-[18px] flex items-center justify-between">
          <div className="text-[17px] font-extrabold tracking-tight">设置</div>
          <button
            onClick={openRules}
            className="h-[34px] rounded-[9px] px-3 text-[12.5px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
          >
            ❓ 常见失败参考
          </button>
        </div>

        {cfg && (
          <div className="space-y-[14px]">
            {/* 密钥卡 */}
            <div className="rounded-[14px] border border-border bg-card p-[20px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-[14px] text-[11px] font-extrabold tracking-[0.1em] text-[#a8472a]">
                密钥 / KEYS
              </div>

              <div className="mb-[15px] flex flex-col gap-[7px]">
                <span className={fieldLabel}>
                  Gemini API Key
                  {cfg.has_gemini_key && (
                    <span className="text-[#2e8c7e]">（已配置，留空不改）</span>
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
                    className="absolute right-1.5 top-1.5 h-7 rounded-md px-2.5 text-[11.5px] font-semibold text-[#9a9082] hover:text-[#6b6356]"
                  >
                    {showGemini ? "隐藏" : "显示"}
                  </button>
                </div>
              </div>

              <div className="mb-[15px] flex flex-col gap-[7px]">
                <span className={fieldLabel}>
                  Fal API Key
                  {cfg.has_fal_key && (
                    <span className="text-[#2e8c7e]">（已配置，留空不改）</span>
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
                    <span className="text-[#2e8c7e]">（已配置，留空不改）</span>
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

              <div className="mb-[15px] flex items-center justify-between rounded-[10px] border border-border bg-[#faf7f0] p-[13px]">
                <div className="leading-snug">
                  <div className="text-[13px] font-bold text-[#2a241f]">启用 Omakase 模式</div>
                  <div className="mt-px text-[11px] text-[#9a9082]">
                    开启 AI 场景生成：默认复用 Gemini Key，失败时自动使用已配置的 DeepSeek 备用线路；关闭后仍可手写场景定稿
                  </div>
                </div>
                <Switch checked={omakaseEnabled} onCheckedChange={setOmakaseEnabled} />
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
            </div>

            {/* 线路与网络卡 */}
            <div className="rounded-[14px] border border-border bg-card p-[20px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-[14px] text-[11px] font-extrabold tracking-[0.1em] text-[#a8472a]">
                线路与网络 / NETWORK
              </div>

              <div className="grid grid-cols-2 gap-[14px]">
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
                <div className="mt-[26px] flex items-center justify-between rounded-[10px] border border-border bg-[#faf7f0] px-[13px]">
                  <div className="leading-tight">
                    <div className="text-[12.5px] font-bold text-[#2a241f]">HTTPS 证书校验</div>
                    <div className="text-[10.5px] text-[#9a9082]">坏网络可临时关</div>
                  </div>
                  <Switch checked={tlsVerify} onCheckedChange={setTlsVerify} />
                </div>
              </div>

              <div className="mt-[14px] flex items-center justify-between rounded-[10px] border border-border bg-[#faf7f0] p-[13px]">
                <div className="leading-snug">
                  <div className="text-[13px] font-bold text-[#2a241f]">直连失败自动转 Fal</div>
                  <div className="mt-px text-[11px] text-[#9a9082]">
                    仅网络类失败触发 · Fal 走你自己的额度
                  </div>
                </div>
                <Switch checked={failover} onCheckedChange={setFailover} />
              </div>
            </div>

            <div className="flex items-center gap-2.5">
              <button
                onClick={save}
                disabled={saving}
                className="h-[42px] rounded-[11px] bg-primary px-[22px] text-[13.5px] font-bold text-primary-foreground shadow-[0_5px_14px_rgba(193,95,60,.28)] hover:bg-[#a8472a] disabled:opacity-50"
              >
                {saving ? "保存中…" : "保存"}
              </button>
              <button
                onClick={test}
                disabled={testing}
                className="h-[42px] rounded-[11px] border border-border bg-card px-[18px] text-[13.5px] font-bold text-[#6b6356] hover:bg-[#f2e9e0] disabled:opacity-50"
              >
                {testing ? "检测中…" : "连通性自检"}
              </button>
              <button
                onClick={load}
                className="h-[42px] rounded-[11px] px-[14px] text-[13.5px] font-semibold text-[#9a9082] hover:bg-[#f2e9e0]"
              >
                重载
              </button>
            </div>

            {testResult && (
              <pre className="whitespace-pre-wrap rounded-[11px] bg-[#f2e9e0] p-[14px] font-mono text-[12.5px] leading-relaxed text-[#2a241f]">
                {testResult}
              </pre>
            )}

            <div className="text-[11.5px] text-[#9a9082]">
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
                  <div className="font-bold text-[#2a241f]">{r.title}</div>
                  {r.cause && <div className="mt-1 text-[#857c6e]">{r.cause}</div>}
                  {r.action && <div className="mt-1 text-[#a8472a]">➡ {r.action}</div>}
                </div>
              ))}
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}

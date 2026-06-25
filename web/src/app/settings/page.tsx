"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ConfigPatch, ConfigView, FailureKB } from "@/lib/types";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

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
      const c = await api.putConfig(patch);
      setCfg(c);
      setGeminiKey("");
      setFalKey("");
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

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">设置</h1>
        <Button variant="ghost" size="sm" onClick={openRules}>
          ❓ 常见失败参考
        </Button>
      </div>

      {cfg && (
        <div className="space-y-4 rounded-xl border bg-background p-4">
          <div className="grid gap-1.5">
            <Label className="text-xs">
              Gemini API Key
              {cfg.has_gemini_key && (
                <span className="text-green-600">（已配置，留空不改）</span>
              )}
            </Label>
            <Input
              type="password"
              value={geminiKey}
              onChange={(e) => setGeminiKey(e.target.value)}
              placeholder={cfg.has_gemini_key ? "••••••（已配置）" : "未配置"}
            />
          </div>

          <div className="grid gap-1.5">
            <Label className="text-xs">
              Fal API Key
              {cfg.has_fal_key && (
                <span className="text-green-600">（已配置，留空不改）</span>
              )}
            </Label>
            <Input
              type="password"
              value={falKey}
              onChange={(e) => setFalKey(e.target.value)}
              placeholder={cfg.has_fal_key ? "••••••（已配置）" : "未配置"}
            />
          </div>

          <div className="grid gap-1.5">
            <Label className="text-xs">代理（留空走系统/软路由）</Label>
            <Input
              value={proxy}
              onChange={(e) => setProxy(e.target.value)}
              placeholder="http://127.0.0.1:7890 或留空"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label className="text-xs">生图线路</Label>
              <Select value={provider} onValueChange={(v) => setProvider(v ?? "google")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="google">Google 直连</SelectItem>
                  <SelectItem value="fal">Fal 路由</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs">网络策略</Label>
              <Select value={speed} onValueChange={(v) => setSpeed(v ?? "fast")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="fast">极速 fast</SelectItem>
                  <SelectItem value="resilient">韧性 resilient</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label className="text-xs">每模型并发数</Label>
              <Input
                type="number"
                min={1}
                max={8}
                value={conc}
                onChange={(e) => setConc(Math.max(1, Number(e.target.value) || 1))}
              />
            </div>
            <div className="flex items-end justify-between rounded-md border p-2">
              <div>
                <div className="text-sm font-medium">HTTPS 证书校验</div>
                <div className="text-xs text-muted-foreground">坏网络可临时关</div>
              </div>
              <Switch checked={tlsVerify} onCheckedChange={setTlsVerify} />
            </div>
          </div>

          <div className="flex items-center justify-between rounded-md border p-2">
            <div>
              <div className="text-sm font-medium">直连失败自动转 Fal</div>
              <div className="text-xs text-muted-foreground">
                仅网络类失败触发；Fal 走你自己的额度
              </div>
            </div>
            <Switch checked={failover} onCheckedChange={setFailover} />
          </div>

          <div className="flex gap-2">
            <Button onClick={save} disabled={saving}>
              {saving ? "保存中…" : "保存"}
            </Button>
            <Button variant="outline" onClick={test} disabled={testing}>
              {testing ? "检测中…" : "连通性自检"}
            </Button>
            <Button variant="ghost" onClick={load}>
              重载
            </Button>
          </div>

          {testResult && (
            <pre className="whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">
              {testResult}
            </pre>
          )}

          <div className="text-xs text-muted-foreground">
            每模型并发改动需重启后端生效（信号量在启动时建）。
          </div>
        </div>
      )}

      <Dialog open={rulesOpen} onOpenChange={setRulesOpen}>
        <DialogContent className="max-h-[80vh] overflow-auto">
          <div className="space-y-3">
            <div className="text-sm font-medium">常见失败 · 原因与处理</div>
            {rules.length === 0 && (
              <div className="text-xs text-muted-foreground">加载中…</div>
            )}
            {rules.map((r) => (
              <div key={r.key} className="rounded-md border p-2 text-xs">
                <div className="font-medium">{r.title}</div>
                {r.cause && (
                  <div className="mt-1 text-muted-foreground">{r.cause}</div>
                )}
                {r.action && <div className="mt-1">➡ {r.action}</div>}
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

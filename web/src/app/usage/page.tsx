"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { UsageSummary } from "@/lib/types";
import { toast } from "sonner";

export default function UsagePage() {
  const [data, setData] = useState<UsageSummary | null>(null);

  function load() {
    api
      .getUsage()
      .then(setData)
      .catch((e) => toast.error((e as Error).message));
  }
  useEffect(() => {
    load();
  }, []);

  const rate =
    data && data.totals.total > 0
      ? Math.round((data.totals.ok / data.totals.total) * 100)
      : 0;
  const hasCost = data?.totals.cost != null;
  const costComplete = data?.totals.cost_complete ?? true;
  const unpricedOk = data?.totals.unpriced_ok ?? 0;

  return (
    <div className="h-full overflow-y-auto p-[26px]">
      <div className="mx-auto max-w-[860px]">
        <div className="mb-[18px] flex items-center justify-between">
          <div className="text-[17px] font-extrabold tracking-tight">用量统计</div>
          <button
            onClick={load}
            className="h-[34px] rounded-[9px] border border-border bg-card px-[14px] text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent"
          >
            刷新
          </button>
        </div>

        {!data ? (
          <div className="text-sm text-muted-foreground">加载中…</div>
        ) : (
          <>
            <div
              className={
                hasCost ? "grid grid-cols-4 gap-[14px]" : "grid grid-cols-3 gap-[14px]"
              }
            >
              <Stat label="累计出图" value={data.totals.total} color="var(--foreground)" />
              <Stat label="成功" value={data.totals.ok} color="var(--success)" />
              <Stat label="失败" value={data.totals.fail} color="var(--destructive)" />
              {hasCost && (
                <Stat
                  label={costComplete ? "估算成本 (元)" : "已计价成本 (元)"}
                  value={`¥${data.totals.cost!.toFixed(2)}`}
                  color="var(--accent-foreground)"
                />
              )}
            </div>

            <div className="mt-[14px] rounded-[14px] border border-border bg-card p-[20px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-[11px] flex items-center justify-between">
                <span className="text-[13px] font-bold text-foreground">整体成功率</span>
                <span className="text-[15px] font-extrabold text-success">{rate}%</span>
              </div>
              <div className="h-3 w-full overflow-hidden rounded-lg bg-muted">
                <div
                  className="h-full rounded-lg"
                  style={{
                    width: `${rate}%`,
                    background: "linear-gradient(90deg,var(--success),var(--chart-3))",
                  }}
                />
              </div>
            </div>

            <div className="mt-[14px] overflow-hidden rounded-[14px] border border-border bg-card shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="grid grid-cols-[1.2fr_1fr_1fr_1fr_.6fr_.6fr_.8fr] bg-accent px-[18px] py-[11px] text-[11.5px] font-bold tracking-wide text-accent-foreground">
                <span>模式</span>
                <span>操作</span>
                <span>模型</span>
                <span>线路</span>
                <span className="text-right">成功</span>
                <span className="text-right">失败</span>
                <span className="text-right">估算成本</span>
              </div>
              {data.rows.length === 0 && (
                <div className="px-[18px] py-6 text-center text-[13px] text-muted-foreground">
                  暂无数据
                </div>
              )}
              {data.rows.map((r, i) => (
                <div
                  key={i}
                  className="grid grid-cols-[1.2fr_1fr_1fr_1fr_.6fr_.6fr_.8fr] items-center border-t border-muted px-[18px] py-3 text-[13px] text-foreground"
                >
                  <span>{r.mode}</span>
                  <span className="text-secondary-foreground">{r.operation}</span>
                  <span className="text-secondary-foreground">{r.model}</span>
                  <span className="text-secondary-foreground">{r.provider}</span>
                  <span className="text-right font-bold tabular-nums text-success">
                    {r.ok}
                  </span>
                  <span className="text-right font-bold tabular-nums text-destructive">
                    {r.fail}
                  </span>
                  <span className="text-right tabular-nums text-secondary-foreground">
                    {r.cost != null ? `¥${r.cost.toFixed(2)}` : "—"}
                  </span>
                </div>
              ))}
            </div>

            <div className="mt-[10px] px-1 text-[11.5px] leading-relaxed text-muted-foreground">
              估算口径：成本 = 各行成功张数 × 设置页配置的单价（失败不计费）；未配置单价的行显示
              —。单价在「设置 → 成本单价」中配置。
              {!costComplete && (
                <span className="ml-1 font-semibold text-warn">
                  当前还有 {unpricedOk} 张成功图未配置单价，顶部仅为已计价部分。
                </span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: number | string;
  color: string;
}) {
  return (
    <div className="rounded-[14px] border border-border bg-card p-[20px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
      <div
        className="text-[34px] font-extrabold leading-none tracking-tight"
        style={{ color }}
      >
        {value}
      </div>
      <div className="mt-[9px] text-[12.5px] font-semibold text-muted-foreground">{label}</div>
    </div>
  );
}

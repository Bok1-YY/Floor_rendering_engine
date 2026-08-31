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
                hasCost ? "grid grid-cols-5 gap-[14px]" : "grid grid-cols-4 gap-[14px]"
              }
            >
              <Stat label="累计出图" value={data.totals.total} color="var(--foreground)" />
              <Stat label="成功" value={data.totals.ok} color="var(--success)" />
              <Stat label="失败" value={data.totals.fail} color="var(--destructive)" />
              <Stat label="结果不确定" value={data.totals.uncertain} color="var(--warn)" />
              {hasCost && (
                <Stat
                  label={costComplete ? "成本区间 (元)" : "已计价成本区间"}
                  value={`¥${data.totals.cost_min!.toFixed(2)}–${data.totals.cost_max!.toFixed(2)}`}
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
              <div className="grid grid-cols-[1.2fr_1fr_1fr_1fr_.55fr_.55fr_.65fr_1fr] bg-accent px-[18px] py-[11px] text-[11.5px] font-bold tracking-wide text-accent-foreground">
                <span>模式</span>
                <span>操作</span>
                <span>模型</span>
                <span>线路</span>
                <span className="text-right">成功</span>
                <span className="text-right">失败</span>
                <span className="text-right">不确定</span>
                <span className="text-right">成本区间</span>
              </div>
              {data.rows.length === 0 && (
                <div className="px-[18px] py-6 text-center text-[13px] text-muted-foreground">
                  暂无数据
                </div>
              )}
              {data.rows.map((r, i) => (
                <div
                  key={i}
                  className="grid grid-cols-[1.2fr_1fr_1fr_1fr_.55fr_.55fr_.65fr_1fr] items-center border-t border-muted px-[18px] py-3 text-[13px] text-foreground"
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
                  <span className="text-right font-bold tabular-nums text-warn">
                    {r.uncertain}
                  </span>
                  <span className="text-right tabular-nums text-secondary-foreground">
                    {r.cost_min != null && r.cost_max != null
                      ? `¥${r.cost_min.toFixed(2)}–${r.cost_max.toFixed(2)}`
                      : "—"}
                  </span>
                </div>
              ))}
            </div>

            <div className="mt-[10px] px-1 text-[11.5px] leading-relaxed text-muted-foreground">
              估算口径：下限只计算明确成功；上限再加上“结果不确定、可能已计费”的调用。明确失败不计费；未配置单价的行显示
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

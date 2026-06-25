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

  return (
    <div className="h-full overflow-y-auto p-[26px]">
      <div className="mx-auto max-w-[860px]">
        <div className="mb-[18px] flex items-center justify-between">
          <div className="text-[17px] font-extrabold tracking-tight">用量统计</div>
          <button
            onClick={load}
            className="h-[34px] rounded-[9px] border border-border bg-card px-[14px] text-[12.5px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
          >
            刷新
          </button>
        </div>

        {!data ? (
          <div className="text-sm text-muted-foreground">加载中…</div>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-[14px]">
              <Stat label="累计出图" value={data.totals.total} color="#2a241f" />
              <Stat label="成功" value={data.totals.ok} color="#2e8c7e" />
              <Stat label="失败" value={data.totals.fail} color="#b5503a" />
            </div>

            <div className="mt-[14px] rounded-[14px] border border-border bg-card p-[20px] shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="mb-[11px] flex items-center justify-between">
                <span className="text-[13px] font-bold text-[#2a241f]">整体成功率</span>
                <span className="text-[15px] font-extrabold text-[#2e8c7e]">{rate}%</span>
              </div>
              <div className="h-3 w-full overflow-hidden rounded-lg bg-[#efe9dc]">
                <div
                  className="h-full rounded-lg"
                  style={{
                    width: `${rate}%`,
                    background: "linear-gradient(90deg,#2e8c7e,#5b9b6b)",
                  }}
                />
              </div>
            </div>

            <div className="mt-[14px] overflow-hidden rounded-[14px] border border-border bg-card shadow-[0_2px_8px_rgba(120,90,60,.05)]">
              <div className="grid grid-cols-[1.3fr_1fr_1.2fr_.7fr_.7fr] bg-[#f2e9e0] px-[18px] py-[11px] text-[11.5px] font-bold tracking-wide text-[#a8472a]">
                <span>模式</span>
                <span>模型</span>
                <span>线路</span>
                <span className="text-right">成功</span>
                <span className="text-right">失败</span>
              </div>
              {data.rows.length === 0 && (
                <div className="px-[18px] py-6 text-center text-[13px] text-[#9a9082]">
                  暂无数据
                </div>
              )}
              {data.rows.map((r, i) => (
                <div
                  key={i}
                  className="grid grid-cols-[1.3fr_1fr_1.2fr_.7fr_.7fr] items-center border-t border-[#efe9dc] px-[18px] py-3 text-[13px] text-[#2a241f]"
                >
                  <span>{r.mode}</span>
                  <span className="text-[#6b6356]">{r.model}</span>
                  <span className="text-[#6b6356]">{r.provider}</span>
                  <span className="text-right font-bold tabular-nums text-[#2e8c7e]">
                    {r.ok}
                  </span>
                  <span className="text-right font-bold tabular-nums text-[#b5503a]">
                    {r.fail}
                  </span>
                </div>
              ))}
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
  value: number;
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
      <div className="mt-[9px] text-[12.5px] font-semibold text-[#9a9082]">{label}</div>
    </div>
  );
}

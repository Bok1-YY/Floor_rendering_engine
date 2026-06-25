"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { UsageSummary } from "@/lib/types";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";

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

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">用量统计</h1>
        <Button variant="outline" size="sm" onClick={load}>
          刷新
        </Button>
      </div>

      {!data ? (
        <div className="text-sm text-muted-foreground">加载中…</div>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <Stat label="累计出图" value={data.totals.total} />
            <Stat label="成功" value={data.totals.ok} accent="text-green-600" />
            <Stat label="失败" value={data.totals.fail} accent="text-destructive" />
          </div>

          <div className="overflow-hidden rounded-xl border bg-background">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left">模式</th>
                  <th className="px-3 py-2 text-left">模型</th>
                  <th className="px-3 py-2 text-left">线路</th>
                  <th className="px-3 py-2 text-right">成功</th>
                  <th className="px-3 py-2 text-right">失败</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.length === 0 && (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-3 py-6 text-center text-muted-foreground"
                    >
                      暂无数据
                    </td>
                  </tr>
                )}
                {data.rows.map((r, i) => (
                  <tr key={i} className="border-t">
                    <td className="px-3 py-2">{r.mode}</td>
                    <td className="px-3 py-2">{r.model}</td>
                    <td className="px-3 py-2">{r.provider}</td>
                    <td className="px-3 py-2 text-right text-green-600">{r.ok}</td>
                    <td className="px-3 py-2 text-right text-destructive">
                      {r.fail}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: string;
}) {
  return (
    <div className="rounded-xl border bg-background p-4 text-center">
      <div className={`text-2xl font-semibold ${accent || ""}`}>{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

/* eslint-disable @next/next/no-img-element */
"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { RecordEntry, RecordFile } from "@/lib/types";
import { toast } from "sonner";

export default function RecordsPage() {
  const [files, setFiles] = useState<RecordFile[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [records, setRecords] = useState<RecordEntry[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api
      .listRecords()
      .then(setFiles)
      .catch((e) => toast.error((e as Error).message));
  }, []);

  async function open(jsonPath: string) {
    setActive(jsonPath);
    setLoading(true);
    try {
      setRecords(await api.loadRecord(jsonPath));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto grid max-w-7xl grid-cols-1 gap-4 p-4 lg:grid-cols-[280px_1fr]">
      <aside className="h-fit space-y-1 rounded-xl border bg-background p-2">
        <h2 className="px-2 py-1 text-sm font-semibold">记录文件</h2>
        {files.length === 0 && (
          <div className="px-2 text-xs text-muted-foreground">暂无记录</div>
        )}
        {files.map((f) => (
          <button
            key={f.json_path}
            onClick={() => open(f.json_path)}
            title={f.json_path}
            className={`block w-full truncate rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted ${
              active === f.json_path ? "bg-muted font-medium" : ""
            }`}
          >
            {f.json_path.split(/[\\/]/).pop()}{" "}
            <span className="text-muted-foreground">({f.labels.length})</span>
          </button>
        ))}
      </aside>

      <section className="space-y-3">
        {!active && (
          <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
            选择左侧记录文件查看。
          </div>
        )}
        {loading && (
          <div className="text-sm text-muted-foreground">加载中…</div>
        )}
        {!loading &&
          active &&
          records.map((r, i) => (
            <div key={r.id || i} className="rounded-xl border bg-background p-3">
              <div className="mb-2 text-sm font-medium">
                {r.id || `记录 ${i + 1}`}
                {r.room_type ? (
                  <span className="text-muted-foreground"> · {r.room_type}</span>
                ) : null}
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
                {(r.results || []).map((res, j) =>
                  res.result_thumb || res.result_url ? (
                    <a
                      key={j}
                      href={api.imgUrl(res.result_url || "")}
                      target="_blank"
                      rel="noreferrer"
                      className="block"
                    >
                      <img
                        src={api.imgUrl(res.result_thumb || res.result_url || "")}
                        alt={res.model || "result"}
                        className="aspect-[4/3] w-full rounded-lg border object-cover hover:opacity-90"
                      />
                      <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                        {res.model || ""}
                      </div>
                    </a>
                  ) : (
                    <div
                      key={j}
                      className="flex aspect-[4/3] items-center justify-center rounded-lg border bg-muted text-[11px] text-muted-foreground"
                    >
                      {res.has_inline ? "内联图(旧记录)" : "无图"}
                    </div>
                  ),
                )}
              </div>
            </div>
          ))}
      </section>
    </div>
  );
}

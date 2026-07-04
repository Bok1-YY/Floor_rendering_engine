/* eslint-disable @next/next/no-img-element */
"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { RecordEntry, RecordFile } from "@/lib/types";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { ImageZoom } from "@/components/ImageZoom";
import { cn } from "@/lib/utils";

const toolBtn =
  "h-8 rounded-lg border border-border bg-card px-[13px] text-[12.5px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]";

export default function RecordsPage() {
  const [files, setFiles] = useState<RecordFile[]>([]);
  const [search, setSearch] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const [records, setRecords] = useState<RecordEntry[]>([]);
  const [roomFilter, setRoomFilter] = useState("__all__");
  const [loading, setLoading] = useState(false);
  const [zoom, setZoom] = useState<string | null>(null);

  // 解密弹窗
  const [reveal, setReveal] = useState<{
    open: boolean;
    rid: string;
    pw: string;
    text: string;
  }>({ open: false, rid: "", pw: "", text: "" });

  // 记录内二改弹窗
  const [edit, setEdit] = useState<{
    open: boolean;
    rid: string;
    idx: number;
    instruction: string;
  }>({ open: false, rid: "", idx: 0, instruction: "" });

  useEffect(() => {
    api
      .listRecords()
      .then(setFiles)
      .catch((e) => toast.error((e as Error).message));
  }, []);

  // 加载乱序防护：快速连点两个记录文件时，先发的响应后到会覆盖后选文件的内容（左侧高亮与右侧内容错位）
  const openSeq = useRef(0);

  async function open(jsonPath: string) {
    const seq = ++openSeq.current;
    setActive(jsonPath);
    setRoomFilter("__all__");
    setLoading(true);
    try {
      const recs = await api.loadRecord(jsonPath);
      if (seq !== openSeq.current) return;
      setRecords(recs);
    } catch (e) {
      if (seq === openSeq.current) toast.error((e as Error).message);
    } finally {
      if (seq === openSeq.current) setLoading(false);
    }
  }

  async function reload() {
    if (!active) return;
    const seq = ++openSeq.current;
    try {
      const recs = await api.loadRecord(active);
      if (seq !== openSeq.current) return;
      setRecords(recs);
    } catch (e) {
      if (seq === openSeq.current) toast.error((e as Error).message);
    }
  }

  const visibleFiles = files.filter((f) =>
    search.trim()
      ? (f.json_path.split(/[\\/]/).pop() || "")
          .toLowerCase()
          .includes(search.trim().toLowerCase())
      : true,
  );

  const roomCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of records) {
      const rt = (r.room_type || "").trim();
      if (rt) c[rt] = (c[rt] || 0) + 1;
    }
    return c;
  }, [records]);

  const shownRecords =
    roomFilter === "__all__"
      ? records
      : records.filter((r) => (r.room_type || "") === roomFilter);

  function download(url: string) {
    window.open(url, "_blank");
  }

  async function doDeleteResult(rid: string, idx: number) {
    if (!active || !window.confirm("确认删除这张效果图？")) return;
    try {
      await api.deleteResult(active, rid, idx);
      toast.success("已删除");
      reload();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function doFav(rid: string, idx: number) {
    if (!active) return;
    try {
      const r = await api.favoriteResult(active, rid, idx);
      toast.success(r.favorite ? "已收藏" : "已取消收藏");
      reload();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function doDeleteRecord(rid: string) {
    if (!active || !window.confirm("确认删除整条记录（含其所有效果图引用）？")) return;
    try {
      await api.deleteRecord(active, rid);
      toast.success("已删除记录");
      reload();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function doReveal() {
    if (!active) return;
    try {
      const r = await api.reveal(active, reveal.rid, reveal.pw);
      setReveal((s) => ({ ...s, text: r.text }));
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function doEditSubmit() {
    if (!active) return;
    const t = edit.instruction.trim();
    if (!t) return;
    try {
      await api.recordEdit({
        json_path: active,
        record_id: edit.rid,
        result_index: edit.idx,
        instruction: t,
      });
      toast.success("已提交二改（在「生成」页可看进度，完成后回此刷新）");
      setEdit({ open: false, rid: "", idx: 0, instruction: "" });
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const roomChip = (active_: boolean) =>
    cn(
      "rounded-lg border px-[13px] py-1.5 text-[12.5px] font-semibold transition-colors",
      active_
        ? "border-primary bg-[#fbf3ee] text-[#a8472a]"
        : "border-border bg-card text-[#6b6356] hover:bg-[#f2e9e0]",
    );

  return (
    <div className="flex h-full overflow-hidden">
      {/* 左栏：文件列表 + 搜索 + 导出收藏夹 */}
      <aside className="flex w-[280px] flex-none flex-col border-r border-border bg-panel px-[14px] py-[16px]">
        <div className="relative mb-[9px]">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#9a9082" strokeWidth="2" strokeLinecap="round" className="absolute left-[11px] top-[11px]">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4-4" />
          </svg>
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索材料名…"
            className="h-9 rounded-[9px] bg-card pl-8 text-[13px]"
          />
        </div>
        <button
          onClick={() => download(api.exportFavoritesUrl())}
          className="mb-[11px] flex h-9 w-full items-center justify-center gap-1.5 rounded-[9px] border border-border bg-card text-[12.5px] font-bold text-[#a8472a] hover:bg-[#f2e9e0]"
        >
          ⭐ 导出收藏夹 PPTX
        </button>
        <div className="px-1 pb-1.5 text-[11px] font-semibold text-[#9a9082]">
          材料记录
        </div>
        <div className="flex flex-1 flex-col gap-0.5 overflow-y-auto">
          {visibleFiles.length === 0 && (
            <div className="px-2 py-1 text-xs text-[#9a9082]">无记录</div>
          )}
          {visibleFiles.map((f) => {
            const on = active === f.json_path;
            return (
              <button
                key={f.json_path}
                onClick={() => open(f.json_path)}
                title={f.json_path}
                className={cn(
                  "block w-full whitespace-normal break-words rounded-lg px-[10px] py-2 text-left text-[12.5px] leading-snug",
                  on
                    ? "bg-[#f2e9e0] font-bold text-[#a8472a]"
                    : "font-medium text-[#6b6356] hover:bg-[#f2e9e0]",
                )}
              >
                {f.json_path.split(/[\\/]/).pop()?.replace("_记录.json", "")}{" "}
                <span className="text-[#9a9082]">({f.labels.length})</span>
              </button>
            );
          })}
        </div>
      </aside>

      {/* 右栏 */}
      <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
        {!active ? (
          <div className="flex flex-1 items-center justify-center">
            <div className="text-center text-[#9a9082]">
              <svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#d3c8b3" strokeWidth="1.4" className="mx-auto mb-3">
                <rect x="3" y="4" width="18" height="6" rx="1.6" />
                <rect x="3" y="14" width="18" height="6" rx="1.6" />
              </svg>
              <div className="text-[13.5px] font-semibold">
                从左侧选择一个材料记录查看
              </div>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-none flex-wrap items-center justify-between gap-2.5 border-b border-border px-[22px] py-[14px]">
              <div className="flex flex-wrap gap-[7px]">
                <button
                  onClick={() => setRoomFilter("__all__")}
                  className={roomChip(roomFilter === "__all__")}
                >
                  全部房间
                </button>
                {Object.entries(roomCounts).map(([rt, n]) => (
                  <button
                    key={rt}
                    onClick={() => setRoomFilter(rt)}
                    className={roomChip(roomFilter === rt)}
                  >
                    {rt} ({n})
                  </button>
                ))}
              </div>
              <div className="flex gap-2">
                <button onClick={reload} className={toolBtn}>
                  刷新
                </button>
                <button onClick={() => download(api.exportHtmlUrl(active))} className={toolBtn}>
                  导出 HTML
                </button>
                <button onClick={() => download(api.exportPptxUrl(active))} className={toolBtn}>
                  导出 PPTX
                </button>
              </div>
            </div>

            <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-[22px] py-[18px]">
              {loading && <div className="text-sm text-[#9a9082]">加载中…</div>}
              {!loading &&
                shownRecords.map((r, i) => {
                  const rid = r.id || "";
                  return (
                    <div
                      key={rid || i}
                      className="rounded-[14px] border border-border bg-card p-[15px] shadow-[0_2px_8px_rgba(120,90,60,.05)]"
                    >
                      <div className="mb-3 flex items-start justify-between gap-2">
                        <span className="min-w-0 flex-1 break-words text-[13.5px] font-bold leading-snug text-[#2a241f]">
                          {rid || `记录 ${i + 1}`}
                          {r.room_type ? ` · ${r.room_type}` : ""}
                          {r.workflow_mode ? ` · ${String(r.workflow_mode)}` : ""}
                        </span>
                        <div className="flex flex-none gap-2.5 text-[14px] text-[#bcae97]">
                          <button
                            title="解密提示词"
                            onClick={() => setReveal({ open: true, rid, pw: "", text: "" })}
                            className="hover:text-[#2a241f]"
                          >
                            🔑
                          </button>
                          <button
                            title="删除记录"
                            onClick={() => doDeleteRecord(rid)}
                            className="hover:text-[#b5503a]"
                          >
                            🗑
                          </button>
                        </div>
                      </div>

                      <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-[11px]">
                        {(r.results || []).map((res, j) => {
                          const url = res.result_url || "";
                          const thumb = res.result_thumb || url;
                          return (
                            <div key={j}>
                              <div className="relative aspect-[4/3] overflow-hidden rounded-[10px] border border-border">
                                {url ? (
                                  <>
                                    <img
                                      src={api.imgUrl(thumb)}
                                      alt={res.model_label || "result"}
                                      onClick={() => setZoom(api.imgUrl(url))}
                                      className="absolute inset-0 h-full w-full cursor-zoom-in object-cover"
                                    />
                                    {res.model_label && (
                                      <span className="absolute left-[7px] top-[7px] rounded-md bg-[rgba(26,24,21,.55)] px-[7px] py-[2px] text-[10px] font-bold text-white">
                                        {res.model_label}
                                      </span>
                                    )}
                                  </>
                                ) : (
                                  <div className="flex h-full items-center justify-center bg-muted text-[11px] text-[#9a9082]">
                                    {res.has_inline ? "内联图(旧)" : "无图"}
                                  </div>
                                )}
                              </div>
                              <div className="mt-1.5 flex items-center justify-between text-[11px] text-[#9a9082]">
                                <span className="truncate">{res.model_label || ""}</span>
                                <span className="flex shrink-0 items-center gap-2.5 text-[12.5px]">
                                  <button
                                    title="收藏"
                                    onClick={() => doFav(rid, j)}
                                    className={
                                      res.favorite
                                        ? "text-primary"
                                        : "text-[#bcae97] hover:text-[#2a241f]"
                                    }
                                  >
                                    {res.favorite ? "★" : "☆"}
                                  </button>
                                  <button
                                    title="二改"
                                    onClick={() =>
                                      setEdit({ open: true, rid, idx: j, instruction: "" })
                                    }
                                    className="hover:text-[#2a241f]"
                                  >
                                    ✎
                                  </button>
                                  {url && (
                                    <button
                                      title="下载"
                                      onClick={() => download(api.imgUrl(url))}
                                      className="hover:text-[#2a241f]"
                                    >
                                      ↓
                                    </button>
                                  )}
                                  <button
                                    title="删除"
                                    onClick={() => doDeleteResult(rid, j)}
                                    className="hover:text-[#b5503a]"
                                  >
                                    🗑
                                  </button>
                                </span>
                              </div>
                              {res.comment ? (
                                <div className="mt-1 text-[11px] leading-snug text-[#857c6e]">
                                  💬 {res.comment}
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
            </div>
          </>
        )}
      </section>

      {/* 放大 */}
      <ImageZoom url={zoom} onClose={() => setZoom(null)} />

      {/* 解密 */}
      <Dialog
        open={reveal.open}
        onOpenChange={(o) => setReveal((s) => ({ ...s, open: o }))}
      >
        <DialogContent>
          <div className="space-y-3">
            <div className="text-[15px] font-bold">解密原始提示词</div>
            <Input
              type="password"
              value={reveal.pw}
              onChange={(e) => setReveal((s) => ({ ...s, pw: e.target.value }))}
              placeholder="输入密码"
              className="h-10 rounded-[10px] bg-[#faf7f0]"
            />
            <div className="flex justify-end">
              <button
                onClick={doReveal}
                className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-[#a8472a]"
              >
                🔓 解密
              </button>
            </div>
            {reveal.text && (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-[10px] bg-[#f2e9e0] p-3 text-xs text-[#2a241f]">
                {reveal.text}
              </pre>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* 记录内二改 */}
      <Dialog
        open={edit.open}
        onOpenChange={(o) => setEdit((s) => ({ ...s, open: o }))}
      >
        <DialogContent>
          <div className="space-y-3">
            <div className="text-[15px] font-bold">二改（对这张结果图做图生图编辑）</div>
            <Input
              value={edit.instruction}
              onChange={(e) =>
                setEdit((s) => ({ ...s, instruction: e.target.value }))
              }
              placeholder="编辑指令，例如：把沙发换成米白色布艺"
              className="h-10 rounded-[10px] bg-[#faf7f0]"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setEdit((s) => ({ ...s, open: false }))}
                className="h-9 rounded-[9px] border border-border bg-card px-4 text-[13px] font-semibold text-[#6b6356] hover:bg-[#f2e9e0]"
              >
                取消
              </button>
              <button
                onClick={doEditSubmit}
                className="h-9 rounded-[9px] bg-primary px-4 text-[13px] font-bold text-primary-foreground hover:bg-[#a8472a]"
              >
                提交
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

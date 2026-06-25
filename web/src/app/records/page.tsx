/* eslint-disable @next/next/no-img-element */
"use client";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { RecordEntry, RecordFile } from "@/lib/types";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { ImageZoom } from "@/components/ImageZoom";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

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

  async function open(jsonPath: string) {
    setActive(jsonPath);
    setRoomFilter("__all__");
    setLoading(true);
    try {
      setRecords(await api.loadRecord(jsonPath));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function reload() {
    if (!active) return;
    try {
      setRecords(await api.loadRecord(active));
    } catch (e) {
      toast.error((e as Error).message);
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

  return (
    <div className="mx-auto grid max-w-7xl grid-cols-1 gap-4 p-4 lg:grid-cols-[300px_1fr]">
      {/* 左栏：文件列表 + 搜索 + 导出收藏夹 */}
      <aside className="h-fit space-y-2 rounded-xl border bg-background p-2">
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="🔍 搜索材料名…"
          className="h-8"
        />
        <Button
          variant="outline"
          size="sm"
          className="w-full"
          onClick={() => download(api.exportFavoritesUrl())}
        >
          ⭐ 导出收藏夹 PPTX
        </Button>
        <div className="max-h-[70vh] space-y-0.5 overflow-auto">
          {visibleFiles.length === 0 && (
            <div className="px-2 py-1 text-xs text-muted-foreground">无记录</div>
          )}
          {visibleFiles.map((f) => (
            <button
              key={f.json_path}
              onClick={() => open(f.json_path)}
              title={f.json_path}
              className={`block w-full truncate rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted ${
                active === f.json_path ? "bg-muted font-medium" : ""
              }`}
            >
              {f.json_path.split(/[\\/]/).pop()?.replace("_记录.json", "")}{" "}
              <span className="text-muted-foreground">({f.labels.length})</span>
            </button>
          ))}
        </div>
      </aside>

      {/* 右栏 */}
      <section className="space-y-3">
        {!active && (
          <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
            选择左侧记录文件查看。
          </div>
        )}

        {active && (
          <div className="flex flex-wrap items-center gap-2">
            <Select value={roomFilter} onValueChange={(v) => setRoomFilter(v ?? "__all__")}>
              <SelectTrigger className="h-8 w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">🏠 全部房间</SelectItem>
                {Object.entries(roomCounts).map(([rt, n]) => (
                  <SelectItem key={rt} value={rt}>
                    {rt} ({n})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex-1" />
            <Button variant="outline" size="sm" onClick={reload}>
              刷新
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => download(api.exportHtmlUrl(active))}
            >
              导出 HTML
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => download(api.exportPptxUrl(active))}
            >
              导出 PPTX
            </Button>
          </div>
        )}

        {loading && <div className="text-sm text-muted-foreground">加载中…</div>}

        {!loading &&
          active &&
          shownRecords.map((r, i) => {
            const rid = r.id || "";
            return (
              <div key={rid || i} className="rounded-xl border bg-background p-3">
                <div className="mb-2 flex items-start justify-between gap-2">
                  <div className="min-w-0 text-sm">
                    <span className="font-medium">{rid || `记录 ${i + 1}`}</span>
                    {r.room_type ? (
                      <span className="text-muted-foreground"> · {r.room_type}</span>
                    ) : null}
                    {r.workflow_mode ? (
                      <span className="text-muted-foreground">
                        {" "}
                        · {String(r.workflow_mode)}
                      </span>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2"
                      title="解密提示词"
                      onClick={() =>
                        setReveal({ open: true, rid, pw: "", text: "" })
                      }
                    >
                      🔑
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-destructive"
                      title="删除记录"
                      onClick={() => doDeleteRecord(rid)}
                    >
                      🗑
                    </Button>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
                  {(r.results || []).map((res, j) => {
                    const url = res.result_url || "";
                    const thumb = res.result_thumb || url;
                    return (
                      <div key={j} className="space-y-1">
                        {url ? (
                          <img
                            src={api.imgUrl(thumb)}
                            alt={res.model_label || "result"}
                            onClick={() => setZoom(api.imgUrl(url))}
                            className="aspect-[4/3] w-full cursor-zoom-in rounded-lg border object-cover"
                          />
                        ) : (
                          <div className="flex aspect-[4/3] items-center justify-center rounded-lg border bg-muted text-[11px] text-muted-foreground">
                            {res.has_inline ? "内联图(旧)" : "无图"}
                          </div>
                        )}
                        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                          <span className="truncate">{res.model_label || ""}</span>
                          <span className="flex shrink-0 gap-1">
                            <button
                              title="收藏"
                              onClick={() => doFav(rid, j)}
                              className="hover:text-foreground"
                            >
                              {res.favorite ? "⭐" : "☆"}
                            </button>
                            <button
                              title="二改"
                              onClick={() =>
                                setEdit({
                                  open: true,
                                  rid,
                                  idx: j,
                                  instruction: "",
                                })
                              }
                              className="hover:text-foreground"
                            >
                              ✏️
                            </button>
                            {url && (
                              <button
                                title="下载"
                                onClick={() => download(api.imgUrl(url))}
                                className="hover:text-foreground"
                              >
                                ⬇️
                              </button>
                            )}
                            <button
                              title="删除"
                              onClick={() => doDeleteResult(rid, j)}
                              className="hover:text-destructive"
                            >
                              🗑
                            </button>
                          </span>
                        </div>
                        {res.comment ? (
                          <div className="text-[11px] text-muted-foreground">
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
            <div className="text-sm font-medium">解密原始提示词</div>
            <Input
              type="password"
              value={reveal.pw}
              onChange={(e) => setReveal((s) => ({ ...s, pw: e.target.value }))}
              placeholder="输入密码"
            />
            <div className="flex justify-end">
              <Button size="sm" onClick={doReveal}>
                🔓 解密
              </Button>
            </div>
            {reveal.text && (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">
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
            <div className="text-sm font-medium">二改（对这张结果图做图生图编辑）</div>
            <Input
              value={edit.instruction}
              onChange={(e) =>
                setEdit((s) => ({ ...s, instruction: e.target.value }))
              }
              placeholder="编辑指令，例如：把沙发换成米白色布艺"
            />
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEdit((s) => ({ ...s, open: false }))}
              >
                取消
              </Button>
              <Button size="sm" onClick={doEditSubmit}>
                提交
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

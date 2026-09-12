"use client";

import { api } from "@/lib/api";

import { Input } from "@/components/ui/input";

import { cn } from "@/lib/utils";

import type { RecordLibraryModel } from "./useRecordLibrary";

export function RecordSidebar({
  search,
  favoriteOnly,
  active,
  setSearch,
  setFavoriteOnly,
  open,
  visibleFiles,
  totalFavorites,
  download
}: Pick<RecordLibraryModel, "search" | "favoriteOnly" | "active" | "setSearch" | "setFavoriteOnly" | "open" | "visibleFiles" | "totalFavorites" | "download">) {
  return <>       {/* 左栏：文件列表 + 搜索 + 收藏筛选/导出 */}
    <aside
      className="flex w-[280px] flex-none flex-col border-r border-border bg-panel px-[14px] py-[16px] max-[1320px]:w-[232px] max-[1080px]:w-[210px]"
    >
      <div className="relative mb-[9px]">
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          className="absolute left-[11px] top-[11px] text-muted-foreground"
        >
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
        type="button"
        aria-pressed={favoriteOnly}
        onClick={() => setFavoriteOnly((on) => !on)}
        className={cn(
          "mb-2 flex h-9 w-full items-center justify-between rounded-[9px] border px-3 text-[12.5px] font-bold transition-colors",
          favoriteOnly
            ? "border-primary bg-primary-soft text-accent-foreground"
            : "border-border bg-card text-secondary-foreground hover:bg-accent",
        )}
      >
        <span>⭐ 只看收藏</span>
        <span className="text-[11px] tabular-nums text-muted-foreground">{totalFavorites}</span>
      </button>
      <button
        onClick={() => download(api.exportFavoritesUrl())}
        className="mb-[11px] flex h-9 w-full items-center justify-center gap-1.5 rounded-[9px] border border-border bg-card text-[12.5px] font-bold text-accent-foreground hover:bg-accent"
      >
        ⭐ 导出收藏夹 PPTX
      </button>
      <div className="px-1 pb-1.5 text-[11px] font-semibold text-muted-foreground">
        材料记录
      </div>
      <div className="flex flex-1 flex-col gap-0.5 overflow-y-auto">
        {visibleFiles.length === 0 && (
          <div className="px-2 py-1 text-xs text-muted-foreground">
            {favoriteOnly ? "没有收藏记录" : "无记录"}
          </div>
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
                  ? "bg-accent font-bold text-accent-foreground"
                  : "font-medium text-secondary-foreground hover:bg-accent",
              )}
            >
              {f.json_path.split(/[\\/]/).pop()?.replace("_记录.json", "")}{" "}
              <span className="text-muted-foreground">
                ({f.labels.length}{f.favorite_count ? ` · ⭐${f.favorite_count}` : ""})
              </span>
            </button>
          );
        })}
      </div>
    </aside>

  </>;
}

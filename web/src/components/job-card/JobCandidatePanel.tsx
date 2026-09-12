/* eslint-disable @next/next/no-img-element */
"use client";

import { Bookmark, Check, Columns2, Maximize2, Paintbrush, Palette, Star, X } from "lucide-react";

import { api } from "@/lib/api";

import { cn } from "@/lib/utils";

const imageToolBtn =
  "inline-flex h-[30px] items-center justify-center gap-1.5 rounded-lg border border-border bg-card px-2.5 text-[11.5px] font-semibold text-secondary-foreground transition-colors hover:border-primary/35 hover:bg-primary-soft hover:text-accent-foreground";

import type { JobCardModel } from "./useJobCard";
export function JobCandidatePanel({
  job,
  setZoom,
  setColorMatch,
  setInpaint,
  setFloorVisualize,
  setActiveModel,
  slots,
  activeSlot,
  activeCandidates,
  activeReview,
  reviewBusy,
  setView,
  terminal,
  setReviewStatus,
  toggleFavorite
}: Pick<JobCardModel, "job" | "setZoom" | "setColorMatch" | "setInpaint" | "setFloorVisualize" | "setActiveModel" | "slots" | "activeSlot" | "activeCandidates" | "activeReview" | "reviewBusy" | "setView" | "terminal" | "setReviewStatus" | "toggleFavorite">) {
  return <>       {slots.length > 0 && activeSlot && (
    <div className="mt-[11px]">
      <div className="flex rounded-[10px] bg-muted p-[3px]">
        {slots.map((slot) => (
          <button
            key={slot.key}
            type="button"
            onClick={() => setActiveModel(slot.key)}
            className={cn(
              "h-8 flex-1 rounded-lg text-[12px] font-bold transition-colors",
              activeSlot.key === slot.key
                ? "bg-card text-accent-foreground shadow-sm"
                : "text-muted-foreground hover:text-secondary-foreground",
            )}
          >
            {slot.name} · {slot.total} 张
          </button>
        ))}
      </div>

      <div className="relative mt-2.5 aspect-[4/3] cursor-zoom-in overflow-hidden rounded-[11px] border border-border bg-muted">
        <img
          src={api.imgUrl(activeSlot.thumb)}
          alt={activeSlot.name}
          onClick={() => setZoom(api.imgUrl(activeSlot.url))}
          className="absolute inset-0 h-full w-full object-cover"
        />
        {activeSlot.run.auto_color_status === "done" && (
          <span className="absolute left-2 top-2 rounded-md bg-[rgba(26,24,21,.55)] px-2 py-1 text-[10.5px] font-bold text-white backdrop-blur-[2px]">
            已自动校色
          </span>
        )}
        {activeReview.status !== "unreviewed" && (
          <span className={cn(
            "absolute right-2 top-2 rounded-full px-2.5 py-1 text-[10.5px] font-bold text-white",
            activeReview.status === "pass" && "bg-success",
            activeReview.status === "backup" && "bg-warn",
            activeReview.status === "rejected" && "bg-destructive",
          )}>
            {activeReview.status === "pass" ? "通过" : activeReview.status === "backup" ? "备选" : "淘汰"}
          </span>
        )}
      </div>

      <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span>{activeSlot.idx + 1} / {activeSlot.total}</span>
        <a href={api.imgUrl(activeSlot.url)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold hover:text-foreground">
          <Maximize2 size={12} />大图
        </a>
      </div>

      {activeCandidates.length > 1 && (
        <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
          {activeCandidates.map((candidate) => (
            <button
              key={`${activeSlot.key}-${candidate.idx}`}
              type="button"
              onClick={() => setView((state) => ({ ...state, [activeSlot.key]: candidate }))}
              className={cn(
                "relative size-[52px] flex-none overflow-hidden rounded-lg border-2",
                candidate.idx === activeSlot.idx ? "border-primary" : "border-transparent opacity-75 hover:opacity-100",
              )}
            >
              <img src={api.imgUrl(candidate.thumb)} alt={`${activeSlot.name} 候选 ${candidate.idx + 1}`} className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}

      {terminal && (
        <div className="mt-2.5 grid grid-cols-4 gap-1.5 border-t border-border/70 pt-2.5">
          {([
            ["pass", "通过", Check],
            ["backup", "备选", Bookmark],
            ["rejected", "淘汰", X],
          ] as const).map(([status, label, Icon]) => (
            <button
              key={status}
              type="button"
              disabled={reviewBusy}
              onClick={() => setReviewStatus(status)}
              className={cn(
                "inline-flex h-8 items-center justify-center gap-1 rounded-lg border text-[11.5px] font-bold transition-colors",
                activeReview.status === status
                  ? status === "pass"
                    ? "border-success bg-success text-white"
                    : status === "backup"
                      ? "border-warn bg-warn text-white"
                      : "border-destructive bg-destructive text-white"
                  : "border-border bg-card text-secondary-foreground hover:bg-accent",
              )}
            >
              <Icon size={13} />{label}
            </button>
          ))}
          <button
            type="button"
            disabled={reviewBusy}
            onClick={toggleFavorite}
            className={cn(
              "inline-flex h-8 items-center justify-center gap-1 rounded-lg border text-[11.5px] font-bold transition-colors",
              activeReview.favorite
                ? "border-primary bg-primary-soft text-accent-foreground"
                : "border-border bg-card text-secondary-foreground hover:bg-accent",
            )}
          >
            <Star size={13} fill={activeReview.favorite ? "currentColor" : "none"} />收藏
          </button>
        </div>
      )}

      <div className="mt-2 flex flex-wrap gap-1.5">
        {terminal && job.floor_path && activeSlot.url.startsWith("/outputs/") && (
          <button
            className={imageToolBtn}
            onClick={() => setFloorVisualize({ stage: activeSlot.key, srcUrl: activeSlot.url, imageRel: activeSlot.url.slice("/outputs/".length) })}
          >
            <Columns2 size={13} />贴地板
          </button>
        )}
        {terminal && activeSlot.url.startsWith("/outputs/") && (
          <button
            className={imageToolBtn}
            onClick={() => setInpaint({ stage: activeSlot.key, srcUrl: activeSlot.url, imageRel: activeSlot.url.slice("/outputs/".length) })}
          >
            <Paintbrush size={13} />修补
          </button>
        )}
        {terminal && job.floor_url && activeSlot.url.startsWith("/outputs/") && (
          <button
            className={imageToolBtn}
            onClick={() => setColorMatch({ stage: activeSlot.key, srcUrl: activeSlot.url, imageRel: activeSlot.url.slice("/outputs/".length) })}
          >
            <Palette size={13} />校色
          </button>
        )}
      </div>
    </div>
  )}

  </>;
}

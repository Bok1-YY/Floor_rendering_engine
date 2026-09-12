import type { ReviewStatus } from '@/lib/types';
import { cn } from '@/lib/utils';
export const toolBtn =
  "h-8 rounded-lg border border-border bg-card px-[13px] text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent";
export const resultToolBtn =
  "inline-flex h-[30px] items-center justify-center rounded-lg border border-border bg-card px-2.5 text-[11.5px] font-semibold text-secondary-foreground transition-colors hover:bg-accent hover:text-foreground";
export const REVIEW_TAGS = [
  "色偏",
  "缝太黑",
  "缝消失",
  "纹理不准",
  "空间太假",
  "地板占比低",
  "风格不对",
  "构图好",
  "客户可用",
];
export const REVIEW_STATUS: { value: ReviewStatus; label: string; color: string }[] = [
  { value: "unreviewed", label: "未评", color: "var(--muted-foreground)" },
  { value: "pass", label: "通过", color: "var(--success)" },
  { value: "backup", label: "备选", color: "var(--warn)" },
  { value: "rejected", label: "淘汰", color: "var(--destructive)" },
];
export const roomChip = (active_: boolean) =>
  cn(
    "rounded-lg border px-[13px] py-1.5 text-[12.5px] font-semibold transition-colors",
    active_
      ? "border-primary bg-primary-soft text-accent-foreground"
      : "border-border bg-card text-secondary-foreground hover:bg-accent",
  );
export const reviewChip = (active_: boolean) =>
  cn(
    "rounded-lg border px-[11px] py-1.5 text-[12px] font-semibold transition-colors",
    active_
      ? "border-primary bg-primary-soft text-accent-foreground"
      : "border-border bg-card text-secondary-foreground hover:bg-accent",
  );

"use client";

import { useEffect, useRef } from "react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export function GenerateStepCard({
  step,
  title,
  summary,
  open,
  complete,
  onToggle,
  children,
}: {
  step: 1 | 2 | 3;
  title: string;
  summary: string;
  open: boolean;
  complete: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const cardRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (open) cardRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [open]);

  return (
    <section ref={cardRef} className="flex-none overflow-hidden rounded-[14px] border border-border bg-card">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-[14px] py-[13px] text-left transition-colors hover:bg-accent/45"
      >
        <span
          className={cn(
            "flex size-[22px] flex-none items-center justify-center rounded-full text-[11px] font-extrabold",
            complete
              ? "bg-success text-white"
              : open
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground",
          )}
        >
          {complete ? <Check size={13} strokeWidth={2.8} /> : step}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-bold text-foreground">{title}</span>
          <span className="mt-0.5 block truncate text-[11.5px] text-muted-foreground" title={summary}>
            {summary}
          </span>
        </span>
        <ChevronDown
          size={16}
          className={cn(
            "flex-none text-muted-foreground transition-transform duration-150",
            open && "rotate-180",
          )}
        />
      </button>
      {open && <div className="animate-scfade border-t border-border p-[14px]">{children}</div>}
    </section>
  );
}

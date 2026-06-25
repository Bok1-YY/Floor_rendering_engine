"use client";
import { cn } from "@/lib/utils";

/** 设计稿区块点头标：● 标题 / ENGLISH（陶土红、加粗、字距） */
export function SectionHeader({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-[7px] text-[11px] font-extrabold tracking-[0.1em] text-[#a8472a]",
        className,
      )}
    >
      <span className="h-[5px] w-[5px] flex-none rounded-full bg-primary" />
      {children}
    </div>
  );
}

/** 设计稿分段控件（米色轨道，激活段奶油底+陶土字+微阴影） */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  className,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div className={cn("flex rounded-[10px] bg-[#efe9dc] p-[3px]", className)}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            className={cn(
              "h-[34px] flex-1 rounded-lg text-[12.5px] font-bold transition-colors",
              active
                ? "bg-card text-[#a8472a] shadow-[0_1px_3px_rgba(120,90,60,.12)]"
                : "text-[#857c6e] hover:text-[#6b6356]",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** 设计稿胶囊（选中=陶土实底白字，未选=描边奶油底） */
export function Pill({
  active,
  onClick,
  children,
  className,
}: {
  active?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-[5px] text-[12px] font-semibold transition-colors",
        active
          ? "border-primary bg-primary text-white"
          : "border-border bg-card text-[#6b6356] hover:bg-[#f2e9e0]",
        className,
      )}
    >
      {children}
    </button>
  );
}

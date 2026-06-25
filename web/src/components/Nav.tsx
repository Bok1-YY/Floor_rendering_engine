"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const LINKS: [string, string][] = [
  ["/", "生成"],
  ["/records", "记录"],
  ["/usage", "用量"],
  ["/settings", "设置"],
];

export function Nav() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4">
        <div className="flex items-baseline gap-2">
          <span className="text-base font-semibold">🪵 地板 AI 生图引擎</span>
          <span className="text-xs text-muted-foreground">商业版 · STEP 2</span>
        </div>
        <nav className="flex items-center gap-1">
          {LINKS.map(([href, label]) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  active
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}

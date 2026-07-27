"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { Dialog, DialogContent } from "@/components/ui/dialog";

type NavItem = {
  href: string;
  label: string;
  icon: React.ReactNode;
};

const NAV: NavItem[] = [
  {
    href: "/",
    label: "生成",
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3z" />
        <path d="M19 14l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2z" />
      </svg>
    ),
  },
  {
    href: "/records",
    label: "记录",
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="18" height="6" rx="1.6" />
        <rect x="3" y="14" width="18" height="6" rx="1.6" />
      </svg>
    ),
  },
  {
    href: "/review",
    label: "复盘",
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 6L9 17l-5-5" />
        <path d="M4 6h6M4 10h3" />
      </svg>
    ),
  },
  {
    href: "/usage",
    label: "用量",
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 20V10M12 20V4M19 20v-7" />
      </svg>
    ),
  },
  {
    href: "/settings",
    label: "设置",
    icon: (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 2.6 14H2.5a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 10 4.6V4.5a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7h.1a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.1 1z" />
      </svg>
    ),
  },
];

const TITLES: Record<string, [string, string]> = {
  "/": ["生成", "上传小样 → 智能配方 → 配置参数 → 出图"],
  "/records": ["记录", "历史出图记录 · 收藏与二改 · 导出"],
  "/review": ["复盘", "评审通过率 · 问题标签分布 · 好图样本库"],
  "/usage": ["用量", "出图统计 · 模型与线路明细"],
  "/settings": ["设置", "密钥 · 线路 · 网络策略"],
};

function isActive(href: string, path: string) {
  return href === "/" ? path === "/" : path.startsWith(href);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  // 静态导出 trailingSlash:true 时 usePathname() 带尾斜杠（/records/），查表前归一化
  const rawPath = usePathname();
  const path = rawPath !== "/" && rawPath.endsWith("/") ? rawPath.slice(0, -1) : rawPath;
  const [helpOpen, setHelpOpen] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);
  const [title, sub] = TITLES[path] ?? TITLES["/"];
  const { resolvedTheme, setTheme } = useTheme();

  useEffect(() => {
    let alive = true;
    const check = () => api.health().then(() => alive && setOnline(true)).catch(() => alive && setOnline(false));
    check();
    const timer = window.setInterval(check, 30_000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  return (
    <div className="flex h-full w-full overflow-hidden text-[14px] text-foreground">
      {/* ── 侧边栏 ── */}
      <aside className="flex w-[236px] flex-none flex-col border-r border-border bg-panel px-[14px] py-[18px]">
        <div className="flex items-center gap-[10px] px-1.5 pb-5">
          <div
            className="flex h-9 w-9 flex-none items-center justify-center rounded-[11px]"
            style={{
              background: "linear-gradient(150deg,#c15f3c,#a8472a)",
              boxShadow: "0 5px 14px rgba(193,95,60,.32)",
            }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.9" strokeLinecap="round">
              <path d="M3 8.5h18M3 14h18M8 4.5v15M14.5 4.5v15" />
            </svg>
          </div>
          <div className="leading-tight">
            <div className="text-[15px] font-extrabold tracking-tight">Floor AI</div>
            <div className="text-[10.5px] font-semibold tracking-wider text-muted-foreground">
              生图引擎 · 商业版
            </div>
          </div>
        </div>

        <nav className="flex flex-1 flex-col">
          {NAV.map((item) => {
            const active = isActive(item.href, path);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "mb-[3px] flex items-center gap-[11px] rounded-[11px] px-3 py-[9.5px] text-[13.5px] font-semibold transition-colors",
                  active
                    ? "bg-primary text-primary-foreground shadow-[0_5px_14px_rgba(193,95,60,.3)]"
                    : "text-secondary-foreground hover:bg-accent",
                )}
              >
                {item.icon}
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="mt-2 border-t border-border pt-3 pl-1.5">
          <div className="flex items-center gap-[10px]">
            <div className="flex h-[30px] w-[30px] items-center justify-center rounded-[9px] bg-success text-[13px] font-bold text-white">
              运
            </div>
            <div className="min-w-0 leading-tight">
              <div className="text-[12.5px] font-bold">运营工作台</div>
              <div className="text-[10.5px] text-muted-foreground">
                {online === null ? "正在检查服务" : online ? "本机服务在线" : "本机服务离线"}
              </div>
            </div>
          </div>
          <div className="mt-2 text-[9.5px] leading-relaxed text-muted-foreground">
            © 2026 Boki ·{" "}
            <a
              href="https://github.com/Bok1-YY/Floor_engine_Linux/blob/main/LICENSE"
              target="_blank"
              rel="noreferrer"
              className="underline-offset-2 hover:text-foreground hover:underline"
            >
              AGPL-3.0
            </a>{" "}
            · 无担保{" "}
            ·{" "}
            <a
              href="https://github.com/Bok1-YY/Floor_engine_Linux"
              target="_blank"
              rel="noreferrer"
              className="underline-offset-2 hover:text-foreground hover:underline"
            >
              源码
            </a>
          </div>
        </div>
      </aside>

      {/* ── 主区 ── */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <div className="flex h-14 flex-none items-center justify-between border-b border-border px-[22px] backdrop-blur-[8px] bg-glass">
          <div className="flex flex-col leading-tight">
            <span className="text-[15px] font-bold tracking-tight">{title}</span>
            <span className="text-[11.5px] text-muted-foreground">{sub}</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
              title="切换深浅主题"
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-card text-secondary-foreground hover:bg-accent"
            >
              <span className="hidden dark:inline-flex">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
                </svg>
              </span>
              <span className="inline-flex dark:hidden">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
                </svg>
              </span>
            </button>
            <button
              onClick={() => setHelpOpen(true)}
              className="flex h-8 items-center gap-1.5 rounded-lg border border-border bg-card px-[13px] text-[12.5px] font-semibold text-secondary-foreground hover:bg-accent"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <circle cx="12" cy="12" r="9" />
                <path d="M9.5 9a2.5 2.5 0 1 1 3.6 2.2c-.8.4-1.1.9-1.1 1.8M12 17h.01" />
              </svg>
              帮助
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </main>

      <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
        <DialogContent className="max-w-md">
          <div className="space-y-3 text-sm">
            <div className="text-[15px] font-bold">使用帮助</div>
            <ul className="list-disc space-y-1.5 pl-4 text-secondary-foreground">
              <li>
                <b className="text-foreground">生成</b>：上传地板小样 → 自动识色并推荐配方 →
                调参数 → 点「生成效果图」；右侧任务卡实时显示进度。
              </li>
              <li>
                <b className="text-foreground">批量</b>：同一地板对多个房间一次性提交。
              </li>
              <li>
                <b className="text-foreground">记录</b>：历史出图可收藏、二改、删除、备注，并导出
                HTML / PPTX 客户提案。
              </li>
              <li>
                <b className="text-foreground">设置</b>：填密钥、选线路；网络不稳可临时关
                TLS 校验或切「韧性」策略。
              </li>
            </ul>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

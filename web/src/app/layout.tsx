import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { ThemeProvider } from "@/components/ThemeProvider";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: "Floor AI · 生图引擎",
  description: "Floor AI 商业版前端",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // suppressHydrationWarning：next-themes 会在客户端往 <html> 写 class/style，属预期差异
    <html lang="zh-CN" className="h-full antialiased" suppressHydrationWarning>
      <body className="h-full overflow-hidden bg-background text-foreground">
        <ThemeProvider>
          <AppShell>{children}</AppShell>
          <Toaster position="top-center" />
        </ThemeProvider>
      </body>
    </html>
  );
}

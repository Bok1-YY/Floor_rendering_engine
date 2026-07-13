"use client";
// next-themes 接线：attribute="class" 往 <html> 写 .dark，令牌系统（globals.css）随之整站换肤。
// defaultTheme="light" 保持现状为默认；enableSystem 允许用户选「跟随系统」。
import { ThemeProvider as NextThemesProvider } from "next-themes";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="light"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  );
}

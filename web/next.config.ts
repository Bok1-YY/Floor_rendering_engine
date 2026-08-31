import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // 打包成「单一程序」用：静态导出到 out/，由 FastAPI 后端直接托管，
  // 不再需要 node 起 next start。详见 ../打包说明.md。
  output: "export",
  // 静态导出下无 Next 图片优化服务器，关掉优化直出原图（本项目未用 next/image，保险起见）。
  images: { unoptimized: true },
  // /settings -> /settings/index.html，配合 StaticFiles(html=True) 深链接刷新可命中。
  trailingSlash: true,
  // 避免桌面上其他 package-lock.json 让 Turbopack 误判 workspace 根目录。
  turbopack: { root: path.resolve(__dirname) },
};

export default nextConfig;

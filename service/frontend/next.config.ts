import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Проверочные сборки НЕ должны писать в .next живого dev-сервера (ломает его в 500).
  // CI/агенты: NEXT_BUILD_DIR=.next-build npm run build — dev не пострадает.
  distDir: process.env.NEXT_BUILD_DIR || ".next",
};

export default nextConfig;

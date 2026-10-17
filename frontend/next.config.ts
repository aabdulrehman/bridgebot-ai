import type { NextConfig } from "next";

/**
 * Static export: the PWA is 100% client-side, so it can be hosted for free on
 * Vercel, Cloudflare Pages, GitHub Pages — or served by the FastAPI backend
 * itself (backend mounts ../frontend/out when present).
 */
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;

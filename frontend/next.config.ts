import type { NextConfig } from "next";

const API_TARGET = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  typescript: {
    ignoreBuildErrors: true,
  },
  reactStrictMode: false,
  async rewrites() {
    // La API FastAPI se consume en el mismo origen mediante rewrites,
    // de modo que las cookies de sesión funcionan sin CORS en producción.
    return [
      { source: "/api/v1/:path*", destination: `${API_TARGET}/api/v1/:path*` },
      { source: "/api/docs", destination: `${API_TARGET}/api/docs` },
    ];
  },
};

export default nextConfig;

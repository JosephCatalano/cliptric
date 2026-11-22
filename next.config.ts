import type { NextConfig } from "next";
import { backendOrigin } from "./lib/backend";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  distDir: process.env.CLIPPING_TEST_DATA_DIR ? ".next-e2e" : ".next",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

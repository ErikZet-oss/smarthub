import type { NextConfig } from "next";

/** Rovnaká logika ako normalizeApiOrigin — next.config nemôže importovať @/ vždy spoľahlivo. */
function backendOrigin(): string {
  let s = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000")
    .trim()
    .replace(/\/+$/, "");
  if (s.toLowerCase().endsWith("/api")) {
    s = s.slice(0, -4).replace(/\/+$/, "");
  }
  return s || "http://127.0.0.1:8000";
}

const nextConfig: NextConfig = {
  async rewrites() {
    const origin = backendOrigin();
    return [
      {
        source: "/api-proxy/:path*",
        destination: `${origin}/:path*`,
      },
    ];
  },
};

export default nextConfig;

/** Predvolený pôvod FastAPI (127.0.0.1 — na Windows často lepšie ako localhost kvôli IPv6). */
export const DEFAULT_API_ORIGIN = "http://127.0.0.1:8000";

/** Same-origin proxy (next.config rewrites → FastAPI). Bez CORS v prehliadači. */
export const API_PROXY_PREFIX = "/api-proxy";

/**
 * Vyčistí základ URL backendu. Prázdny reťazec z .env (NEXT_PUBLIC_API_BASE_URL=)
 * by inak viedol k relatívnemu fetchu na Next namiesto FastAPI → 404 Not Found.
 */
export function normalizeApiOrigin(raw: string | undefined | null): string {
  let s = String(raw ?? "").trim().replace(/\/+$/, "");
  if (!s) {
    s = DEFAULT_API_ORIGIN;
  }
  if (s.toLowerCase().endsWith("/api")) {
    s = s.slice(0, -4).replace(/\/+$/, "");
  }
  if (!s) {
    s = DEFAULT_API_ORIGIN;
  }
  return s;
}

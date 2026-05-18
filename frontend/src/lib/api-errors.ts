/** Pravda, ak fetch skončil na sieti skôr než prišla HTTP odpoveď. */
export function isBrowserFetchNetworkError(message: string): boolean {
  const m = message.trim();
  return (
    m === "Failed to fetch" ||
    m.includes("NetworkError") ||
    m.includes("Network request failed") ||
    m.includes("Load failed") ||
    m.includes("fetch failed")
  );
}

import { API_PROXY_PREFIX } from "@/lib/api-origin";

export function apiUnreachableUserMessage(apiBase: string): string {
  if (apiBase === API_PROXY_PREFIX || apiBase.endsWith(API_PROXY_PREFIX)) {
    return (
      "Nepodarilo sa spojiť s backendom. Spusti FastAPI (uvicorn v priečinku backend) " +
      "a v .env.local / Render nastav NEXT_PUBLIC_API_BASE_URL na správnu URL API. " +
      "Po zmene reštartuj Next aj backend."
    );
  }
  const localApi =
    apiBase.includes("127.0.0.1") ||
    apiBase.includes("localhost") ||
    apiBase.includes("[::1]");
  let hint = "";
  if (typeof window !== "undefined" && localApi) {
    const host = window.location.hostname;
    if (host !== "localhost" && host !== "127.0.0.1" && host !== "[::1]") {
      hint =
        ` API (${apiBase}) smeruje na localhost, ale stránku máš otvorenú na ${host} — v .env.local / Render nastav NEXT_PUBLIC_API_BASE_URL na verejnú URL backendu.`;
    }
  }
  return (
    `Nepodarilo sa spojiť s backendom (${apiBase}).${hint} ` +
    `Spusti FastAPI (uvicorn v priečinku backend), over NEXT_PUBLIC_API_BASE_URL a po deployi reštartuj API.`
  );
}

export function formatApiFetchError(error: unknown, apiBase: string): string {
  if (error instanceof Error && isBrowserFetchNetworkError(error.message)) {
    return apiUnreachableUserMessage(apiBase);
  }
  return error instanceof Error ? error.message : "Neznáma chyba.";
}

/**
 * Robustne prečíta odpoveď zo servera ako JSON. Ak telo nie je platný JSON
 * (napr. Render proxy alebo crash workera vráti plain-text „Internal Server
 * Error"), vráti syntetický objekt `{ detail }` so skrátenou textovou
 * správou, aby front-end nepadol s hláškou „Unexpected token 'I' …".
 *
 * Telo sa číta práve raz — funkcia konzumuje response.body.
 */
export async function readApiJsonOrText(
  response: Response,
): Promise<{ ok: true; data: unknown } | { ok: false; detail: string }> {
  let raw = "";
  try {
    raw = await response.text();
  } catch {
    return {
      ok: false,
      detail: `HTTP ${response.status} ${response.statusText || ""}`.trim(),
    };
  }
  const trimmed = raw.trim();
  if (!trimmed) {
    return {
      ok: false,
      detail: `HTTP ${response.status} ${response.statusText || ""}`.trim(),
    };
  }
  try {
    return { ok: true, data: JSON.parse(trimmed) };
  } catch {
    const short = trimmed.length > 300 ? trimmed.slice(0, 300) + "…" : trimmed;
    return {
      ok: false,
      detail: `HTTP ${response.status}: ${short}`,
    };
  }
}

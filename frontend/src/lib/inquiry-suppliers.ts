export const INQUIRY_SUPPLIER_PREFS_KEY = "smarthub_inquiry_supplier_prefs_v1";

export type InquirySupplierOption = {
  id: number;
  name: string;
  logoUrl: string | null;
  isConnected: boolean;
};

export function supplierPrefsStorageKey(userId: number | null): string {
  return `${INQUIRY_SUPPLIER_PREFS_KEY}::${userId ?? "anon"}`;
}

export function loadInquirySupplierPrefs(userId: number | null): number[] | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(supplierPrefsStorageKey(userId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((id): id is number => typeof id === "number" && id > 0);
  } catch {
    return null;
  }
}

export function saveInquirySupplierPrefs(userId: number | null, ids: number[]) {
  if (typeof window === "undefined") return;
  localStorage.setItem(supplierPrefsStorageKey(userId), JSON.stringify(ids));
}

export function publicInquiryAssetUrl(
  apiBase: string,
  path: string | null | undefined,
): string | null {
  if (path == null || !String(path).trim()) return null;
  const p = String(path).trim();
  if (/^https?:\/\//i.test(p)) return p;
  return `${apiBase}${p.startsWith("/") ? p : `/${p}`}`;
}

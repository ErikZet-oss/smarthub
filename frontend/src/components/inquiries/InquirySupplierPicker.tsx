"use client";

import { Loader2, Store, Truck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { readApiJsonOrText } from "@/lib/api-errors";
import {
  loadInquirySupplierPrefs,
  publicInquiryAssetUrl,
  saveInquirySupplierPrefs,
  type InquirySupplierOption,
} from "@/lib/inquiry-suppliers";
import { cn } from "@/lib/utils";

type Props = {
  apiBase: string;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  userId: number | null;
  selectedIds: number[];
  onChange: (ids: number[]) => void;
};

function supplierInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return `${parts[0]![0] ?? ""}${parts[1]![0] ?? ""}`.toUpperCase();
}

export function InquirySupplierPicker({
  apiBase,
  apiFetch,
  userId,
  selectedIds,
  onChange,
}: Props) {
  const [suppliers, setSuppliers] = useState<InquirySupplierOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await apiFetch(`${apiBase}/api/suppliers`);
        const parsed = await readApiJsonOrText(res);
        if (!parsed.ok) throw new Error(parsed.detail);
        if (!res.ok) throw new Error("Nepodarilo sa načítať dodávateľov.");
        const data = parsed.data as Array<{
          id: number;
          name: string;
          logo_url?: string | null;
          is_connected?: boolean;
        }>;
        if (cancelled) return;
        const list = data
          .filter((s) => typeof s.id === "number" && s.name?.trim())
          .map((s) => ({
            id: s.id,
            name: s.name.trim(),
            logoUrl: s.logo_url ?? null,
            isConnected: Boolean(s.is_connected),
          }));
        setSuppliers(list);

        const saved = loadInquirySupplierPrefs(userId);
        if (saved && saved.length > 0) {
          const valid = saved.filter((id) => list.some((s) => s.id === id));
          onChange(valid.length > 0 ? valid : list.map((s) => s.id));
        } else if (selectedIds.length === 0 && list.length > 0) {
          onChange(list.map((s) => s.id));
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Chyba pri načítaní dodávateľov.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- init once
  }, [apiBase, apiFetch, userId]);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  const persistSelection = useCallback(
    (ids: number[]) => {
      onChange(ids);
      saveInquirySupplierPrefs(userId, ids);
    },
    [onChange, userId],
  );

  const toggleSupplier = (id: number) => {
    const next = new Set(selectedSet);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    persistSelection(Array.from(next));
  };

  const selectAll = () => persistSelection(suppliers.map((s) => s.id));
  const selectNone = () => persistSelection([]);

  const selectedCount = suppliers.filter((s) => selectedSet.has(s.id)).length;

  return (
    <Card className="overflow-hidden border-slate-200/80 shadow-sm">
      <div className="border-b border-sky-100/80 bg-gradient-to-r from-sky-50 via-white to-slate-50 px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-sky-100 text-sky-700 ring-1 ring-sky-200/60">
              <Truck className="h-5 w-5" aria-hidden />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-slate-900">
                Dodávatelia pre vyhľadávanie
              </h2>
              <p className="mt-0.5 text-xs text-slate-600">
                Vyber e-shopy, u ktorých sa majú pri dopyte hľadať ceny a sklad.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge className="bg-white text-slate-700">
              {selectedCount} / {suppliers.length || "—"} vybraných
            </Badge>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 bg-white text-xs"
              onClick={selectAll}
              disabled={loading || suppliers.length === 0}
            >
              Všetci
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 bg-white text-xs"
              onClick={selectNone}
              disabled={loading || suppliers.length === 0}
            >
              Žiadny
            </Button>
          </div>
        </div>
      </div>

      <div className="p-4 sm:p-5">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            Načítavam dodávateľov…
          </div>
        ) : error ? (
          <p className="text-sm text-red-600">{error}</p>
        ) : suppliers.length === 0 ? (
          <p className="text-sm text-slate-500">
            Zatiaľ nemáte nastavených dodávateľov. Pridajte ich v sekcii Dodávatelia.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {suppliers.map((supplier) => {
              const checked = selectedSet.has(supplier.id);
              const logoSrc = publicInquiryAssetUrl(apiBase, supplier.logoUrl);
              return (
                <label
                  key={supplier.id}
                  className={cn(
                    "group flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 transition-all",
                    checked
                      ? "border-sky-300 bg-sky-50/70 shadow-sm ring-1 ring-sky-200/70"
                      : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/80",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleSupplier(supplier.id)}
                    className="h-4 w-4 shrink-0 rounded border-slate-300 text-sky-600 focus:ring-sky-500"
                  />
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-slate-200/90 bg-white shadow-sm">
                    {logoSrc ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={logoSrc}
                        alt=""
                        className="h-full w-full object-contain p-1"
                      />
                    ) : (
                      <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                        {supplierInitials(supplier.name)}
                      </span>
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-900">
                      {supplier.name}
                    </p>
                    <p className="flex items-center gap-1 text-[11px] text-slate-500">
                      <Store className="h-3 w-3 shrink-0" aria-hidden />
                      {supplier.isConnected ? "Prihlásený" : "Bez prihlásenia"}
                    </p>
                  </div>
                </label>
              );
            })}
          </div>
        )}

        {selectedCount === 0 && !loading && suppliers.length > 0 ? (
          <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            Vyber aspoň jedného dodávateľa, inak nebude možné spustiť dopyt.
          </p>
        ) : null}
      </div>
    </Card>
  );
}

export function inquirySuppliersReady(selectedIds: number[]): boolean {
  return selectedIds.length > 0;
}

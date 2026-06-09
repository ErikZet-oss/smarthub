"use client";

import { Loader2, Truck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { readApiJsonOrText } from "@/lib/api-errors";
import {
  loadInquirySupplierPrefs,
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
          is_connected?: boolean;
        }>;
        if (cancelled) return;
        const list = data
          .filter((s) => typeof s.id === "number" && s.name?.trim())
          .map((s) => ({
            id: s.id,
            name: s.name.trim(),
            logoUrl: null,
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
      <div className="border-b border-sky-100/80 bg-gradient-to-r from-sky-50 via-white to-slate-50 px-2.5 py-2 md:px-5 md:py-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between md:gap-3">
          <div className="flex min-w-0 items-center gap-1.5 md:gap-2">
            <Truck className="h-3.5 w-3.5 shrink-0 text-sky-600 md:h-4 md:w-4" aria-hidden />
            <div className="min-w-0">
              <h2 className="text-xs font-semibold text-slate-900 md:text-sm">
                Dodávatelia
              </h2>
            </div>
            <Badge className="ml-auto bg-white px-1.5 py-0 text-[10px] text-slate-700 md:text-xs">
              {selectedCount}/{suppliers.length || "—"}
            </Badge>
          </div>
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 flex-1 bg-white px-2 text-[10px] md:h-7 md:flex-none md:text-xs"
              onClick={selectAll}
              disabled={loading || suppliers.length === 0}
            >
              Všetci
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 flex-1 bg-white px-2 text-[10px] md:h-7 md:flex-none md:text-xs"
              onClick={selectNone}
              disabled={loading || suppliers.length === 0}
            >
              Žiadny
            </Button>
          </div>
        </div>
      </div>

      <div className="px-2.5 py-2 md:px-5 md:py-3">
        {loading ? (
          <div className="flex items-center gap-1.5 text-xs text-slate-500 md:text-sm">
            <Loader2 className="h-3.5 w-3.5 animate-spin md:h-4 md:w-4" />
            Načítavam…
          </div>
        ) : error ? (
          <p className="text-xs text-red-600 md:text-sm">{error}</p>
        ) : suppliers.length === 0 ? (
          <p className="text-xs text-slate-500 md:text-sm">
            Zatiaľ nemáte nastavených dodávateľov.
          </p>
        ) : (
          <div className="grid grid-cols-3 gap-1 sm:grid-cols-2 sm:gap-2 md:flex md:flex-wrap md:gap-1.5">
            {suppliers.map((supplier) => {
              const checked = selectedSet.has(supplier.id);
              return (
                <label
                  key={supplier.id}
                  className={cn(
                    "inline-flex min-h-[32px] cursor-pointer items-center gap-1 rounded-md border px-1.5 py-1 text-[10px] transition-colors sm:min-h-[36px] sm:gap-1.5 sm:px-2 sm:text-xs md:min-h-0 md:rounded-lg md:py-1.5 md:text-sm",
                    checked
                      ? "border-sky-300 bg-sky-50 text-sky-900"
                      : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50 active:bg-slate-100",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleSupplier(supplier.id)}
                    className="h-3 w-3 shrink-0 rounded border-slate-300 text-sky-600 focus:ring-sky-500 md:h-3.5 md:w-3.5"
                  />
                  <span className="min-w-0 truncate font-medium leading-tight">{supplier.name}</span>
                </label>
              );
            })}
          </div>
        )}

        {selectedCount === 0 && !loading && suppliers.length > 0 ? (
          <p className="mt-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] text-amber-900 md:mt-2 md:rounded-lg md:px-3 md:py-1.5 md:text-xs">
            Vyber aspoň jedného dodávateľa.
          </p>
        ) : null}
      </div>
    </Card>
  );
}

export function inquirySuppliersReady(selectedIds: number[]): boolean {
  return selectedIds.length > 0;
}

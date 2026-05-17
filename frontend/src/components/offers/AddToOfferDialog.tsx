"use client";

import { Loader2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import type { OfferListItem } from "@/components/offers/types";
import { Button } from "@/components/ui/button";
import { formatApiFetchError } from "@/lib/api-errors";
import { cn } from "@/lib/utils";

export type AddToOfferPayload = {
  internal_code: string;
  product_id?: number | null;
  supplier_id: number;
  supplier_name: string;
  supplier_code: string | null;
  purchase_price_eur: number;
  description?: string;
};

type Props = {
  open: boolean;
  payload: AddToOfferPayload | null;
  apiBase: string;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  onAdded?: (offerId: number, offerNumber: string) => void;
};

function fmtEur(n: number) {
  return new Intl.NumberFormat("sk-SK", {
    style: "currency",
    currency: "EUR",
  }).format(n);
}

export function AddToOfferDialog({
  open,
  payload,
  apiBase,
  apiFetch,
  onClose,
  onAdded,
}: Props) {
  const [offers, setOffers] = useState<OfferListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`${apiBase}/api/offers`);
      if (!res.ok) throw new Error("Nepodarilo sa načítať ponuky.");
      const rows = (await res.json()) as OfferListItem[];
      setOffers(rows);
      const draft = rows.find((o) => o.status === "draft");
      setSelectedId(draft?.id ?? rows[0]?.id ?? null);
    } catch (e) {
      setError(formatApiFetchError(e, apiBase));
    } finally {
      setLoading(false);
    }
  }, [apiBase, apiFetch]);

  useEffect(() => {
    if (!open) return;
    void load();
  }, [open, load]);

  const submit = async () => {
    if (!payload || selectedId == null) return;
    const purchase = Number(payload.purchase_price_eur);
    if (!Number.isFinite(purchase) || purchase <= 0) {
      setError("Chýba platná nákupná cena — počkaj na načítanie ceny od dodávateľa.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiFetch(
        `${apiBase}/api/offers/${selectedId}/lines/from-catalog`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            internal_code: payload.internal_code,
            product_id: payload.product_id ?? undefined,
            supplier_id: payload.supplier_id,
            supplier_name: payload.supplier_name,
            supplier_code: payload.supplier_code,
            purchase_price_eur: purchase,
            quantity: 1,
            description: payload.description,
          }),
        },
      );
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { detail?: unknown };
        const detail = d.detail;
        throw new Error(
          typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail.map((x) => String(x)).join(", ")
              : "Pridanie do ponuky zlyhalo.",
        );
      }
      const picked = offers.find((o) => o.id === selectedId);
      onAdded?.(selectedId, picked?.offer_number ?? "");
      onClose();
    } catch (e) {
      setError(formatApiFetchError(e, apiBase));
    } finally {
      setSubmitting(false);
    }
  };

  if (!open || !payload) return null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-900/45 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <div>
            <h3 className="text-base font-semibold text-slate-900">Pridať do ponuky</h3>
            <p className="mt-0.5 text-xs text-slate-600">
              {payload.internal_code} · {payload.supplier_name}
              {payload.supplier_code ? ` · ${payload.supplier_code}` : ""}
            </p>
            <p className="mt-1 text-sm font-medium text-sky-800">
              Nákupná cena: {fmtEur(payload.purchase_price_eur)}
            </p>
          </div>
          <Button type="button" variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="max-h-[50vh] overflow-y-auto px-4 py-3">
          {error ? (
            <p className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900">
              {error}
            </p>
          ) : null}
          {loading ? (
            <p className="flex items-center gap-2 text-sm text-slate-600">
              <Loader2 className="h-4 w-4 animate-spin" /> Načítavam ponuky…
            </p>
          ) : offers.length === 0 ? (
            <p className="text-sm text-slate-600">
              Nemáte žiadnu ponuku. Vytvorte ju v sekcii Ponuky.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {offers.map((o) => (
                <li key={o.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(o.id)}
                    className={cn(
                      "w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors",
                      selectedId === o.id
                        ? "border-sky-400 bg-sky-50 ring-1 ring-sky-200"
                        : "border-slate-200 hover:border-slate-300 hover:bg-slate-50",
                    )}
                  >
                    <span className="font-medium text-slate-900">{o.offer_number}</span>
                    <span className="mt-0.5 block text-xs text-slate-600">{o.client_name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-200 px-4 py-3">
          <Button type="button" variant="outline" onClick={onClose}>
            Zrušiť
          </Button>
          <Button
            type="button"
            disabled={offers.length === 0 || selectedId == null || submitting}
            onClick={() => void submit()}
          >
            {submitting ? "Pridávam…" : "Pridať do ponuky"}
          </Button>
        </div>
      </div>
    </div>
  );
}

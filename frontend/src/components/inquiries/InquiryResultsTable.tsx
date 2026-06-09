"use client";

import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { publicInquiryAssetUrl } from "@/lib/inquiry-suppliers";
import { cn } from "@/lib/utils";
import type { InquiryLineRunResult, InquiryRunTaskResult } from "@/types/inquiry";

type Props = {
  apiBase: string;
  result: InquiryRunTaskResult;
};

function formatEur(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(4).replace(/\.?0+$/, "")} €`;
}

function statusBadge(row: InquiryLineRunResult) {
  if (row.status === "ok") {
    return (
      <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
        <CheckCircle2 className="mr-1 h-3 w-3" />
        OK
      </Badge>
    );
  }
  if (row.status === "no_stock" || (row.no_stock && row.best_offer)) {
    return (
      <Badge className="bg-amber-100 text-amber-900 hover:bg-amber-100">
        <AlertTriangle className="mr-1 h-3 w-3" />
        Nie je skladom
      </Badge>
    );
  }

  const labels: Record<InquiryLineRunResult["status"], string> = {
    ok: "OK",
    no_stock: "Nie je skladom",
    no_product: "Produkt nenájdený",
    no_mapping: "Bez mapovania",
    no_price: "Cena nedostupná",
    error: "Chyba",
  };
  const text = row.error?.trim() || labels[row.status] || "Chyba";

  return (
    <Badge className="border-red-200 bg-red-50 text-red-700" title={row.error ?? undefined}>
      {text}
    </Badge>
  );
}

function OfferRow({
  apiBase,
  offer,
  highlight,
}: {
  apiBase: string;
  offer: InquiryLineRunResult["offers"][number];
  highlight?: boolean;
}) {
  const logoSrc = publicInquiryAssetUrl(apiBase, offer.logo_url);
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-sm",
        highlight ? "border-sky-200 bg-sky-50/80" : "border-slate-100 bg-slate-50/50",
      )}
    >
      <div className="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded border bg-white">
        {logoSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={logoSrc} alt="" className="h-full w-full object-contain p-0.5" />
        ) : (
          <span className="text-[9px] font-semibold text-slate-400">
            {offer.supplier_name.slice(0, 2).toUpperCase()}
          </span>
        )}
      </div>
      <span className="font-medium text-slate-800">{offer.supplier_name}</span>
      {offer.error ? (
        <span className="text-xs text-red-600">{offer.error}</span>
      ) : (offer.stock ?? 0) <= 0 ? (
        <>
          <span className="text-slate-600">{formatEur(offer.price_eur)}</span>
          <span className="text-xs text-amber-700">Nie je skladom</span>
        </>
      ) : (
        <>
          <span className="text-slate-600">{formatEur(offer.price_eur)}</span>
          <span className="text-xs text-slate-500">sklad: {offer.stock}</span>
        </>
      )}
      {offer.supplier_product_url ? (
        <a
          href={offer.supplier_product_url}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto inline-flex items-center gap-1 text-xs text-sky-700 hover:underline"
        >
          Otvoriť
          <ExternalLink className="h-3 w-3" />
        </a>
      ) : null}
    </div>
  );
}

function ResultRow({ apiBase, row }: { apiBase: string; row: InquiryLineRunResult }) {
  const [open, setOpen] = useState(false);
  const best = row.best_offer;

  return (
    <div className="border-b border-slate-100 last:border-b-0">
      <div className="grid grid-cols-1 gap-2 px-3 py-3 sm:grid-cols-[2.5rem_1fr_auto] sm:items-center lg:grid-cols-[2.5rem_minmax(0,1.4fr)_minmax(0,0.7fr)_minmax(0,0.8fr)_minmax(0,0.5fr)_auto]">
        <button
          type="button"
          className="hidden text-slate-400 hover:text-slate-600 sm:inline-flex"
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? "Skryť ponuky" : "Zobraziť ponuky"}
        >
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-900" title={row.raw_text}>
            {row.raw_text}
          </p>
          <p className="text-xs text-slate-500">
            #{row.row_index}
            {row.internal_code ? ` · ${row.internal_code}` : ""}
            {row.quantity != null ? ` · ${row.quantity} ks` : ""}
          </p>
        </div>
        <div className="hidden text-sm text-slate-700 lg:block">
          {best ? best.supplier_name : "—"}
        </div>
        <div className="hidden text-sm font-medium text-slate-900 lg:block">
          {best ? formatEur(best.price_eur) : "—"}
        </div>
        <div className="hidden text-sm text-slate-600 lg:block">
          {row.line_total_eur != null ? formatEur(row.line_total_eur) : "—"}
        </div>
        <div className="flex items-center gap-2">
          {statusBadge(row)}
          <button
            type="button"
            className="text-slate-400 hover:text-slate-600 sm:hidden"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
        </div>
      </div>

      <div className="px-3 pb-3 sm:pl-12 lg:hidden">
        {best ? (
          <p className="text-xs text-slate-600">
            {best.supplier_name} · {formatEur(best.price_eur)} · spolu{" "}
            {formatEur(row.line_total_eur)}
          </p>
        ) : (
          <p className="text-xs text-red-600">
            {row.error ??
              (row.status === "no_stock" ? "Nie je skladom." : "Bez ponuky")}
          </p>
        )}
      </div>

      {open && row.offers.length > 0 ? (
        <div className="space-y-1.5 px-3 pb-3 sm:pl-12">
          {row.offers.map((offer) => (
            <OfferRow
              key={`${offer.supplier_id}-${offer.supplier_code}`}
              apiBase={apiBase}
              offer={offer}
              highlight={best?.supplier_id === offer.supplier_id}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function InquiryResultsTable({ apiBase, result }: Props) {
  return (
    <Card className="overflow-hidden border-slate-200/80 shadow-sm">
      <div className="border-b border-emerald-100/80 bg-gradient-to-r from-emerald-50 via-white to-slate-50 px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">Výsledok dopytu</h2>
            <p className="mt-0.5 text-xs text-slate-600">
              {result.rows_with_offer} / {result.total_rows} riadkov s cenou
              {result.rows_no_stock > 0
                ? ` · ${result.rows_no_stock} nie je skladom`
                : ""}
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-slate-500">Spolu</p>
            <p className="text-lg font-semibold text-emerald-800">
              {formatEur(result.total_eur)}
            </p>
          </div>
        </div>
      </div>

      <div className="hidden border-b border-slate-100 bg-slate-50/80 px-3 py-2 text-xs font-medium uppercase tracking-wide text-slate-500 lg:grid lg:grid-cols-[2.5rem_minmax(0,1.4fr)_minmax(0,0.7fr)_minmax(0,0.8fr)_minmax(0,0.5fr)_auto]">
        <span />
        <span>Položka</span>
        <span>Dodávateľ</span>
        <span>Cena / ks</span>
        <span>Spolu</span>
        <span>Stav</span>
      </div>

      <div>
        {result.rows.map((row) => (
          <ResultRow key={row.row_index} apiBase={apiBase} row={row} />
        ))}
      </div>
    </Card>
  );
}

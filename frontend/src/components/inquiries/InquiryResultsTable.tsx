"use client";

import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Download, ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  applyMarginPercent,
  effectiveRowMarginPercent,
  inquiryLineTotalEur,
  isSelectableInquiryOffer,
  parseMarginPercentInput,
  resolveActiveOffer,
} from "@/lib/inquiry-margin";
import {
  buildInquiryResultCsv,
  downloadInquiryResultCsv,
  inquiryResultCsvFilename,
} from "@/lib/inquiry-export-csv";
import { publicInquiryAssetUrl } from "@/lib/inquiry-suppliers";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type { InquiryLineRunResult, InquiryRunTaskResult, InquiryScrapedOffer } from "@/types/inquiry";

type Props = {
  apiBase: string;
  result: InquiryRunTaskResult;
};

const GRID_COLS =
  "lg:grid-cols-[2.5rem_minmax(0,1.4fr)_minmax(0,0.65fr)_minmax(0,0.75fr)_minmax(0,0.55fr)_3.5rem_minmax(0,0.55fr)_auto]";

function formatEur(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(4).replace(/\.?0+$/, "")} €`;
}

function priceUnitSuffix(
  supplierName: string | null | undefined,
  unit: string | null | undefined,
): string {
  const u = (unit || "").trim();
  if (u === "per_1_ks") return " / 1 ks";
  if (u === "per_100_ks" || u === "100") return " / 100 ks";
  const name = (supplierName || "").toLowerCase();
  if (name.includes("bmkco") || name.includes("bmk")) return "";
  return " / 100 ks";
}

function formatScrapePrice(
  price: number | null | undefined,
  supplierName?: string | null,
  priceUnit?: string | null,
): string {
  if (price == null || Number.isNaN(price)) return "—";
  return `${formatEur(price).replace(" €", "")}${priceUnitSuffix(supplierName, priceUnit)}`;
}

function marginInputClassName(focused?: boolean, mobile?: boolean): string {
  return cn(
    "rounded border border-slate-200 bg-white text-right tabular-nums text-slate-800",
    "placeholder:text-slate-400 focus:border-sky-400 focus:outline-none focus:ring-1 focus:ring-sky-200",
    mobile
      ? "h-7 w-11 px-1.5 text-xs md:h-9 md:w-14 md:px-2 md:text-sm"
      : "h-7 w-full min-w-[2.75rem] max-w-[3.25rem] px-1.5 text-xs",
    focused && "border-sky-300",
  );
}

function statusLabel(row: InquiryLineRunResult): string {
  if (row.error?.trim()) return row.error.trim();
  const labels: Record<InquiryLineRunResult["status"], string> = {
    ok: "OK",
    no_stock: "Nie je skladom",
    no_product: "Produkt v katalógu nenájdený",
    no_mapping: "Bez mapovania u dodávateľov",
    no_price: "Cena nedostupná",
    invalid_row: "Neúplný riadok",
    catalog_mismatch: "Nie je v katalógu",
    error: "Chyba",
  };
  return labels[row.status] ?? "Chyba";
}

function statusBadge(row: InquiryLineRunResult, compact?: boolean) {
  const compactCls = compact
    ? "h-5 shrink-0 rounded-md px-1.5 py-0 text-[10px] [&_svg]:mr-0.5 [&_svg]:h-2.5 [&_svg]:w-2.5"
    : "";

  if (row.status === "ok") {
    return (
      <Badge className={cn("bg-emerald-100 text-emerald-800 hover:bg-emerald-100", compactCls)}>
        <CheckCircle2 className="mr-1 h-3 w-3" />
        OK
      </Badge>
    );
  }
  if (row.status === "no_stock" || (row.no_stock && row.best_offer)) {
    return (
      <Badge className={cn("bg-amber-100 text-amber-900 hover:bg-amber-100", compactCls)}>
        <AlertTriangle className="mr-1 h-3 w-3" />
        {compact ? "Sklad" : "Nie je skladom"}
      </Badge>
    );
  }

  if (row.status === "catalog_mismatch") {
    return (
      <Badge
        className={cn("border-amber-200 bg-amber-50 text-amber-900", compactCls)}
        title={row.error ?? undefined}
      >
        {compact ? "Katalóg" : statusLabel(row)}
      </Badge>
    );
  }

  const text = statusLabel(row);
  const isSoftFail = row.status === "invalid_row";
  const display = compact && text.length > 12 ? `${text.slice(0, 10)}…` : text.length > 48 ? `${text.slice(0, 45)}…` : text;

  return (
    <Badge
      className={cn(
        isSoftFail
          ? "border-orange-200 bg-orange-50 text-orange-800"
          : "border-red-200 bg-red-50 text-red-700",
        compactCls,
      )}
      title={row.error ?? text}
    >
      {display}
    </Badge>
  );
}

function OfferRow({
  apiBase,
  offer,
  selected,
  onSelect,
}: {
  apiBase: string;
  offer: InquiryScrapedOffer;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const logoSrc = publicInquiryAssetUrl(apiBase, offer.logo_url);
  const selectable = isSelectableInquiryOffer(offer);
  const code = (offer.supplier_code || "").trim();

  return (
    <div
      role={selectable ? "button" : undefined}
      tabIndex={selectable ? 0 : undefined}
      onClick={selectable ? onSelect : undefined}
      onKeyDown={
        selectable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect?.();
              }
            }
          : undefined
      }
      className={cn(
        "flex flex-col gap-1.5 rounded-md border px-2 py-1.5 text-xs transition-colors sm:flex-row sm:flex-wrap sm:items-center sm:gap-2 sm:rounded-lg sm:px-3 sm:py-2 sm:text-sm",
        selected
          ? "border-sky-300 bg-sky-50/90 ring-1 ring-sky-200"
          : "border-slate-100 bg-slate-50/50",
        selectable && !selected && "cursor-pointer hover:border-sky-200 hover:bg-sky-50/50 active:bg-sky-50",
        !selectable && "opacity-80",
      )}
      title={selectable ? "Klikni pre výber tejto ponuky vo výsledku" : undefined}
    >
      <div className="flex min-w-0 items-center gap-2">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded border bg-white">
          {logoSrc ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={logoSrc} alt="" className="h-full w-full object-contain p-0.5" />
          ) : (
            <span className="text-[9px] font-semibold text-slate-400">
              {offer.supplier_name.slice(0, 2).toUpperCase()}
            </span>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-slate-800">{offer.supplier_name}</p>
          {code ? (
            <p className="truncate font-mono text-xs text-slate-500" title="Kód dodávateľa">
              {code}
            </p>
          ) : null}
        </div>
        {selected ? (
          <span className="shrink-0 text-xs font-medium text-sky-700">Vybrané</span>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 sm:contents">
        {offer.error ? (
          <span className="text-xs text-red-600">{offer.error}</span>
        ) : (offer.stock ?? 0) <= 0 ? (
          <>
            <span className="text-slate-600">
              {formatScrapePrice(offer.price_eur, offer.supplier_name, offer.price_unit)}
            </span>
            <span className="text-xs text-amber-700">Nie je skladom</span>
          </>
        ) : (
          <>
            <span className="font-medium tabular-nums text-slate-700">
              {formatScrapePrice(offer.price_eur, offer.supplier_name, offer.price_unit)}
            </span>
            <span className="text-xs text-slate-500">sklad: {offer.stock}</span>
          </>
        )}
        {offer.supplier_product_url ? (
          <a
            href={offer.supplier_product_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="inline-flex min-h-[36px] items-center gap-1 rounded-md px-2 text-xs font-medium text-sky-700 hover:bg-sky-50 hover:underline sm:ml-auto"
          >
            Otvoriť
            <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
      </div>
    </div>
  );
}

function ResultRow({
  apiBase,
  row,
  globalMargin,
  rowMargin,
  onRowMarginChange,
  selectedSupplierId,
  onSelectSupplier,
}: {
  apiBase: string;
  row: InquiryLineRunResult;
  globalMargin: string;
  rowMargin: string;
  onRowMarginChange: (value: string) => void;
  selectedSupplierId: number | null;
  onSelectSupplier: (supplierId: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const active = resolveActiveOffer(row, selectedSupplierId);
  const marginPct = effectiveRowMarginPercent(row.row_index, globalMargin, {
    [row.row_index]: rowMargin,
  });
  const showWithMargin = marginPct !== null;

  const purchaseLineTotal =
    active?.price_eur != null && active.price_eur > 0
      ? inquiryLineTotalEur(active.price_eur, row.quantity, active)
      : row.line_total_eur;

  const unitPrice =
    active?.price_eur != null && showWithMargin
      ? applyMarginPercent(active.price_eur, marginPct)
      : active?.price_eur ?? null;

  const lineTotal =
    purchaseLineTotal != null && showWithMargin
      ? applyMarginPercent(purchaseLineTotal, marginPct)
      : purchaseLineTotal;

  return (
    <div className="border-b border-slate-100 last:border-b-0">
      {/* Mobil: karta */}
      <div className="lg:hidden">
        <div className="px-2 py-2 md:px-3 md:py-2.5">
          <button
            type="button"
            className="flex w-full items-start gap-2 text-left active:opacity-80"
            onClick={() => row.offers.length > 0 && setOpen((v) => !v)}
            aria-expanded={open}
            disabled={row.offers.length === 0}
          >
            <span className="mt-0.5 shrink-0 text-slate-400">
              {row.offers.length > 0 ? (
                open ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )
              ) : (
                <span className="inline-block h-4 w-4" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <p className="line-clamp-2 text-xs font-medium leading-snug text-slate-900 md:text-sm">
                {row.raw_text}
              </p>
              <p className="mt-0.5 truncate text-[10px] text-slate-500 md:text-xs">
                #{row.row_index}
                {row.internal_code ? ` · ${row.internal_code}` : ""}
                {row.quantity != null ? ` · ${row.quantity} ks` : ""}
              </p>
            </div>
            <div className="shrink-0 pt-0.5">{statusBadge(row, true)}</div>
          </button>

          {active ? (
            <div className="mt-2 rounded-lg border border-slate-100 bg-slate-50/90 px-2.5 py-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                    {active.supplier_name}
                  </p>
                  <p
                    className={cn(
                      "mt-0.5 text-sm font-semibold tabular-nums leading-tight",
                      showWithMargin ? "text-sky-900" : "text-slate-900",
                    )}
                  >
                    {formatScrapePrice(unitPrice, active.supplier_name, active.price_unit)}
                  </p>
                  {showWithMargin ? (
                    <p className="text-[10px] tabular-nums text-slate-400 line-through">
                      {formatScrapePrice(active.price_eur, active.supplier_name, active.price_unit)}
                    </p>
                  ) : null}
                </div>
                <div className="shrink-0 text-right">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Spolu</p>
                  <p
                    className={cn(
                      "mt-0.5 text-sm font-bold tabular-nums leading-tight",
                      showWithMargin ? "text-sky-900" : "text-slate-800",
                    )}
                  >
                    {formatEur(lineTotal)}
                  </p>
                  {showWithMargin && purchaseLineTotal != null ? (
                    <p className="text-[10px] tabular-nums text-slate-400 line-through">
                      {formatEur(purchaseLineTotal)}
                    </p>
                  ) : null}
                </div>
              </div>
            </div>
          ) : (
            <div className="mt-2">{statusBadge(row)}</div>
          )}

          <div className="mt-2 flex items-center justify-between gap-2 border-t border-slate-100 pt-2">
            <label
              className="flex items-center gap-1.5"
              onClick={(e) => e.stopPropagation()}
            >
              <span className="text-[10px] font-medium text-slate-500">Marža</span>
              <input
                type="text"
                inputMode="decimal"
                placeholder="0"
                value={rowMargin}
                onChange={(e) => onRowMarginChange(e.target.value)}
                className={marginInputClassName(parseMarginPercentInput(rowMargin) !== null, true)}
                aria-label={`Marža % riadok ${row.row_index}`}
              />
              <span className="text-[10px] text-slate-400">%</span>
            </label>
            {row.offers.length > 0 ? (
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className="inline-flex min-h-[28px] items-center gap-0.5 rounded-md px-2 text-[10px] font-medium text-sky-700 hover:bg-sky-50 active:bg-sky-100 md:text-[11px]"
              >
                {open ? "Skryť" : "Ponuky"}
                <span className="tabular-nums text-slate-500">({row.offers.length})</span>
                {open ? (
                  <ChevronDown className="h-3 w-3" />
                ) : (
                  <ChevronRight className="h-3 w-3" />
                )}
              </button>
            ) : (
              <span className="text-[10px] text-slate-400">Bez ponúk</span>
            )}
          </div>

          {!active && row.error ? (
            <p className="mt-1.5 text-[10px] leading-snug text-slate-600 md:text-xs">{row.error}</p>
          ) : null}
        </div>

        {open && row.offers.length > 0 ? (
          <div className="space-y-1.5 border-t border-slate-100 bg-slate-50/60 px-2 py-2 md:space-y-2 md:px-3">
            <p className="text-[10px] text-slate-500">Klikni na dodávateľa pre výber.</p>
            {row.offers.map((offer) => (
              <OfferRow
                key={`${offer.supplier_id}-${offer.supplier_code}`}
                apiBase={apiBase}
                offer={offer}
                selected={active?.supplier_id === offer.supplier_id}
                onSelect={
                  isSelectableInquiryOffer(offer)
                    ? () => onSelectSupplier(offer.supplier_id)
                    : undefined
                }
              />
            ))}
          </div>
        ) : null}
      </div>

      {/* Desktop: tabuľkový riadok */}
      <div className="hidden lg:block">
      <div
        className={cn(
          "grid gap-2 px-3 py-3 items-center",
          GRID_COLS,
        )}
      >
        <button
          type="button"
          className="text-slate-400 hover:text-slate-600 inline-flex"
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
        <div className="text-sm text-slate-700">
          {active ? active.supplier_name : "—"}
        </div>
        <div>
          {active ? (
            <div>
              <p
                className={cn(
                  "text-sm font-medium tabular-nums",
                  showWithMargin ? "text-sky-900" : "text-slate-900",
                )}
              >
                {formatScrapePrice(unitPrice, active.supplier_name, active.price_unit)}
              </p>
              {showWithMargin ? (
                <p className="text-[10px] text-slate-400 line-through">
                  {formatScrapePrice(active.price_eur, active.supplier_name, active.price_unit)}
                </p>
              ) : null}
            </div>
          ) : (
            <span className="text-sm text-slate-400">—</span>
          )}
        </div>
        <div className="flex justify-end">
          <input
            type="text"
            inputMode="decimal"
            placeholder="%"
            value={rowMargin}
            onChange={(e) => onRowMarginChange(e.target.value)}
            className={marginInputClassName(parseMarginPercentInput(rowMargin) !== null)}
            title="Marža pre tento riadok (prepíše celkovú maržu)"
            aria-label={`Marža % riadok ${row.row_index}`}
          />
        </div>
        <div>
          {lineTotal != null ? (
            <div>
              <p
                className={cn(
                  "text-sm tabular-nums",
                  showWithMargin ? "font-medium text-sky-900" : "text-slate-600",
                )}
              >
                {formatEur(lineTotal)}
              </p>
              {showWithMargin && purchaseLineTotal != null ? (
                <p className="text-[10px] text-slate-400 line-through">{formatEur(purchaseLineTotal)}</p>
              ) : null}
            </div>
          ) : (
            <span className="text-sm text-slate-400">—</span>
          )}
        </div>
        <div className="flex min-w-0 items-center gap-2">
          {statusBadge(row)}
        </div>
      </div>

      {!active && row.error ? (
        <p className="px-3 pb-2 text-xs text-slate-600 pl-12">{row.error}</p>
      ) : null}

      {open && row.offers.length > 0 ? (
        <div className="space-y-1.5 px-3 pb-3 pl-12">
          <p className="text-[10px] text-slate-500">Klikni na dodávateľa pre výber do výsledku riadku.</p>
          {row.offers.map((offer) => (
            <OfferRow
              key={`${offer.supplier_id}-${offer.supplier_code}`}
              apiBase={apiBase}
              offer={offer}
              selected={active?.supplier_id === offer.supplier_id}
              onSelect={
                isSelectableInquiryOffer(offer)
                  ? () => onSelectSupplier(offer.supplier_id)
                  : undefined
              }
            />
          ))}
        </div>
      ) : null}
      </div>
    </div>
  );
}

export function InquiryResultsTable({ apiBase, result }: Props) {
  const [globalMargin, setGlobalMargin] = useState("");
  const [rowMargins, setRowMargins] = useState<Record<number, string>>({});
  const [selectedSupplierByRow, setSelectedSupplierByRow] = useState<Record<number, number>>({});

  const globalMarginPct = parseMarginPercentInput(globalMargin);

  const { purchaseTotal, displayTotal, hasAnyMargin } = useMemo(() => {
    let purchase = 0;
    let display = 0;
    let pricedRows = 0;
    let anyMargin = globalMarginPct !== null;

    for (const row of result.rows) {
      const rowMarginVal = parseMarginPercentInput(rowMargins[row.row_index] ?? "");
      if (rowMarginVal !== null) anyMargin = true;

      const active = resolveActiveOffer(row, selectedSupplierByRow[row.row_index] ?? null);
      const linePurchase =
        active?.price_eur != null && active.price_eur > 0
          ? inquiryLineTotalEur(active.price_eur, row.quantity, active)
          : row.line_total_eur;

      if (linePurchase == null || !Number.isFinite(linePurchase)) continue;
      pricedRows += 1;
      purchase += linePurchase;

      const m = effectiveRowMarginPercent(row.row_index, globalMargin, rowMargins);
      display += m !== null ? applyMarginPercent(linePurchase, m) : linePurchase;
    }

    return {
      purchaseTotal: pricedRows > 0 ? purchase : null,
      displayTotal: pricedRows > 0 ? display : null,
      hasAnyMargin: anyMargin,
    };
  }, [result.rows, globalMargin, rowMargins, globalMarginPct, selectedSupplierByRow]);

  const hasManualSelection = Object.keys(selectedSupplierByRow).length > 0;
  const headerTotal =
    hasAnyMargin || hasManualSelection
      ? displayTotal
      : result.total_eur ?? purchaseTotal;

  const handleExportCsv = () => {
    const csv = buildInquiryResultCsv({
      result,
      globalMargin,
      rowMargins,
      selectedSupplierByRow,
      purchaseTotal,
      displayTotal: headerTotal,
    });
    downloadInquiryResultCsv(inquiryResultCsvFilename(result), csv);
  };

  return (
    <Card className="overflow-hidden border-slate-200/80 shadow-sm">
      <div className="border-b border-emerald-100/80 bg-gradient-to-r from-emerald-50 via-white to-slate-50 px-2.5 py-2 md:px-5 md:py-4">
        <div className="flex flex-col gap-2 md:gap-4 lg:flex-row lg:flex-wrap lg:items-start lg:justify-between">
          <div className="min-w-0">
            <h2 className="text-xs font-semibold text-slate-900 md:text-sm">Výsledok dopytu</h2>
            <p className="mt-0.5 text-[10px] leading-snug text-slate-600 md:text-xs">
              {result.rows_with_offer} / {result.total_rows} riadkov s cenou
              {result.rows_no_stock > 0 ? ` · ${result.rows_no_stock} nie je skladom` : ""}
              {result.rows_failed > 0 ? ` · ${result.rows_failed} neúspešných` : ""}
            </p>
          </div>
          <div className="flex items-end justify-between gap-2 md:gap-3 lg:justify-end">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1 bg-white px-2 text-[10px] md:h-9 md:gap-2 md:px-3 md:text-sm"
              onClick={handleExportCsv}
            >
              <Download className="h-3 w-3 md:h-4 md:w-4" />
              CSV
            </Button>
            <div className="flex items-end gap-2 rounded-lg border border-emerald-100/80 bg-white/80 px-2 py-1.5 md:gap-4 md:border-0 md:bg-transparent md:p-0">
            <label className="flex flex-col gap-0.5 md:gap-1">
              <span className="text-[10px] text-slate-500 md:text-xs">Marža %</span>
              <input
                type="text"
                inputMode="decimal"
                placeholder="%"
                value={globalMargin}
                onChange={(e) => setGlobalMargin(e.target.value)}
                className={cn(marginInputClassName(globalMarginPct !== null, true), "w-16")}
                title="Marža pre všetky riadky (riadok môže prepísať)"
                aria-label="Celková marža percent"
              />
            </label>
            <div className="text-right">
              <p className="text-[10px] text-slate-500 md:text-xs">
                {hasAnyMargin ? "Spolu s maržou" : "Spolu"}
              </p>
              <p
                className={cn(
                  "text-base font-semibold tabular-nums md:text-lg",
                  hasAnyMargin ? "text-sky-900" : "text-emerald-800",
                )}
              >
                {formatEur(headerTotal)}
              </p>
              {hasAnyMargin && purchaseTotal != null ? (
                <p className="text-[10px] text-slate-400">
                  nákup {formatEur(purchaseTotal)}
                </p>
              ) : null}
            </div>
          </div>
        </div>
      </div>
      </div>

      <div
        className={cn(
          "hidden border-b border-slate-100 bg-slate-50/80 px-3 py-2 text-xs font-medium uppercase tracking-wide text-slate-500 lg:grid",
          GRID_COLS,
        )}
      >
        <span />
        <span>Položka</span>
        <span>Dodávateľ</span>
        <span>Cena / 100</span>
        <span className="text-right">Marža</span>
        <span>Spolu</span>
        <span>Stav</span>
      </div>

      <div>
        {result.rows.map((row) => (
          <ResultRow
            key={row.row_index}
            apiBase={apiBase}
            row={row}
            globalMargin={globalMargin}
            rowMargin={rowMargins[row.row_index] ?? ""}
            onRowMarginChange={(value) =>
              setRowMargins((prev) => ({ ...prev, [row.row_index]: value }))
            }
            selectedSupplierId={selectedSupplierByRow[row.row_index] ?? null}
            onSelectSupplier={(supplierId) =>
              setSelectedSupplierByRow((prev) => ({ ...prev, [row.row_index]: supplierId }))
            }
          />
        ))}
      </div>
    </Card>
  );
}

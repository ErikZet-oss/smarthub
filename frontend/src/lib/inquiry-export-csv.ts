import {
  applyMarginPercent,
  effectiveRowMarginPercent,
  inquiryLineTotalEur,
  resolveActiveOffer,
} from "@/lib/inquiry-margin";
import type { InquiryLineRunResult, InquiryRunTaskResult } from "@/types/inquiry";

const STATUS_LABELS: Record<InquiryLineRunResult["status"], string> = {
  ok: "OK",
  no_stock: "Nie je skladom",
  no_product: "Produkt v katalógu nenájdený",
  no_mapping: "Bez mapovania u dodávateľov",
  no_price: "Cena nedostupná",
  invalid_row: "Neúplný riadok",
  catalog_mismatch: "Nie je v katalógu",
  error: "Chyba",
};

function escapeCsvCell(value: string | number | null | undefined): string {
  if (value == null || value === "") return "";
  const s = String(value);
  if (/[",;\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function formatDecimal(value: number | null | undefined, digits = 4): string {
  if (value == null || !Number.isFinite(value)) return "";
  return value.toFixed(digits).replace(/\.?0+$/, "");
}

function priceUnitLabel(unit: string | null | undefined, supplierName: string | null): string {
  const u = (unit || "").trim();
  if (u === "per_1_ks") return "za 1 ks";
  if (u === "per_100_ks" || u === "100") return "za 100 ks";
  const name = (supplierName || "").toLowerCase();
  if (name.includes("bmkco") || name.includes("bmk")) return "za balenie";
  return "za 100 ks";
}

export type InquiryCsvExportOptions = {
  result: InquiryRunTaskResult;
  globalMargin: string;
  rowMargins: Record<number, string>;
  selectedSupplierByRow: Record<number, number>;
  purchaseTotal: number | null;
  displayTotal: number | null;
};

export function buildInquiryResultCsv(options: InquiryCsvExportOptions): string {
  const { result, globalMargin, rowMargins, selectedSupplierByRow, purchaseTotal, displayTotal } =
    options;

  const headers = [
    "Riadok",
    "Text dopytu",
    "Ks",
    "Norma",
    "Povrch",
    "Priemer",
    "Dĺžka",
    "Class",
    "Smart kód",
    "Stav",
    "Dodávateľ",
    "Kód dodávateľa",
    "Cena jednotková EUR",
    "Jednotka ceny",
    "Sklad",
    "Marža %",
    "Nákup spolu EUR",
    "Predaj spolu EUR",
    "Chyba",
    "URL produktu",
  ];

  const dataRows: string[][] = [];

  for (const row of result.rows) {
    const active = resolveActiveOffer(row, selectedSupplierByRow[row.row_index] ?? null);
    const marginPct = effectiveRowMarginPercent(row.row_index, globalMargin, rowMargins);

    const linePurchase =
      active?.price_eur != null && active.price_eur > 0
        ? inquiryLineTotalEur(active.price_eur, row.quantity, active)
        : row.line_total_eur;

    const lineDisplay =
      linePurchase != null && marginPct !== null
        ? applyMarginPercent(linePurchase, marginPct)
        : linePurchase;

    const unitPrice =
      active?.price_eur != null && marginPct != null
        ? applyMarginPercent(active.price_eur, marginPct)
        : active?.price_eur ?? null;

    dataRows.push([
      String(row.row_index),
      row.raw_text,
      row.quantity != null ? String(row.quantity) : "",
      row.norma ?? "",
      row.surface ?? "",
      row.diameter ?? "",
      row.length ?? "",
      row.v_class ?? "",
      row.internal_code ?? "",
      row.error?.trim() || STATUS_LABELS[row.status] || row.status,
      active?.supplier_name ?? "",
      active?.supplier_code ?? "",
      unitPrice != null ? formatDecimal(unitPrice) : "",
      active ? priceUnitLabel(active.price_unit, active.supplier_name) : "",
      active?.stock != null ? String(active.stock) : "",
      marginPct != null ? formatDecimal(marginPct, 2) : "",
      linePurchase != null ? formatDecimal(linePurchase) : "",
      lineDisplay != null ? formatDecimal(lineDisplay) : "",
      row.error ?? active?.error ?? "",
      active?.supplier_product_url ?? "",
    ]);
  }

  const lines: string[][] = [headers, ...dataRows];

  // Súhrn pod riadkami
  lines.push([]);
  lines.push(["Súhrn", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""]);
  lines.push([
    "Zdrojový súbor",
    result.source_filename || "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
  ]);
  lines.push([
    "Riadkov celkom",
    String(result.total_rows),
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
  ]);
  lines.push([
    "S cenou",
    String(result.rows_with_offer),
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
  ]);
  lines.push([
    "Nákup spolu EUR",
    purchaseTotal != null ? formatDecimal(purchaseTotal) : formatDecimal(result.total_eur),
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
  ]);
  lines.push([
    "Predaj spolu EUR",
    displayTotal != null ? formatDecimal(displayTotal) : "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
  ]);

  const body = lines.map((row) => row.map(escapeCsvCell).join(";")).join("\r\n");
  return `\uFEFF${body}`;
}

export function inquiryResultCsvFilename(result: InquiryRunTaskResult): string {
  const stamp = new Date().toISOString().slice(0, 10);
  const base = (result.source_filename || "dopyt")
    .replace(/\.[^.]+$/, "")
    .replace(/[^\w\-]+/g, "_")
    .slice(0, 48);
  return `${base || "dopyt"}_vysledok_${stamp}.csv`;
}

export function downloadInquiryResultCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

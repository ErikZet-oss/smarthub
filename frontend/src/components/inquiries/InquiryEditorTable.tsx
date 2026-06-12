"use client";

import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { SearchableSelect } from "@/components/SearchableSelect";
import {
  catalogMismatchFields,
  inquiryCatalogMismatchMessages,
  inquiryRequiredFields,
  normRequiresLength,
  normRequiresVClass,
  optionsWithCurrent,
  isInquiryRequiredField,
  type InquiryFilterField,
  type InquiryFilterOptions,
  type InquirySelectField,
} from "@/lib/inquiry-norm-rules";
import { cn } from "@/lib/utils";
import { inquiryMissingFields, type InquiryLineParsed } from "@/types/inquiry";

const EMPTY_OPTS: InquiryFilterOptions = {
  norma: [],
  surface: [],
  diameter: [],
  length: [],
  v_class: [],
  internal_code: [],
};

const FIELD_LABELS: Record<InquiryFilterField | "raw_text" | "internal_code", string> = {
  raw_text: "Text dopytu",
  norma: "Norma",
  surface: "Povrchová úprava",
  diameter: "Priemer",
  length: "Dĺžka",
  v_class: "Class",
  internal_code: "Číslo Smart",
  quantity: "Ks",
};

const SELECT_FIELDS: InquirySelectField[] = [
  "norma",
  "surface",
  "diameter",
  "length",
  "v_class",
  "internal_code",
];

/** Kratšie popisky pre úzke mobilné stĺpce (3 vedľa seba). */
const COMPACT_FIELD_LABELS: Record<(typeof SELECT_FIELDS)[number], string> = {
  norma: "Norma",
  surface: "Povrch",
  diameter: "Priem.",
  length: "Dĺžka",
  v_class: "Class",
  internal_code: "Smart č.",
};

type RowProps = {
  row: InquiryLineParsed;
  apiBase: string;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  onChange: (row: InquiryLineParsed) => void;
  onDelete: () => void;
  variant: "table" | "card";
};

function updateRowField(
  row: InquiryLineParsed,
  field: keyof InquiryLineParsed,
  value: string,
): InquiryLineParsed {
  const next = { ...row, parse_error: null as string | null, catalog_warnings: null };
  if (field === "quantity") {
    const n = value.trim() ? Number(value.replace(",", ".")) : null;
    next.quantity = n != null && !Number.isNaN(n) ? Math.max(0, Math.round(n)) : null;
    return next;
  }
  (next as Record<string, unknown>)[field] = value.trim() || null;
  return next;
}

function warningsEqual(a: string[] | null | undefined, b: string[] | null | undefined): boolean {
  const left = a ?? [];
  const right = b ?? [];
  if (left.length !== right.length) return false;
  return left.every((msg, i) => msg === right[i]);
}

function buildInquiryFilterPayload(row: InquiryLineParsed): Record<string, string | null> {
  const payload: Record<string, string | null> = {
    norma: row.norma || null,
    surface: row.surface || null,
    diameter: row.diameter || null,
    internal_code: row.internal_code || null,
  };
  if (normRequiresLength(row.norma, row.raw_text)) {
    payload.length = row.length || null;
  }
  if (normRequiresVClass(row.norma, row.raw_text)) {
    payload.v_class = row.v_class || null;
  }
  return payload;
}

function useInquiryEditorRowState({
  row,
  apiBase,
  apiFetch,
  onChange,
}: Pick<RowProps, "row" | "apiBase" | "apiFetch" | "onChange">) {
  const [opts, setOpts] = useState<InquiryFilterOptions>(EMPTY_OPTS);
  const required = new Set(inquiryRequiredFields(row.norma, row.raw_text));
  const missing = new Set(inquiryMissingFields(row));
  const catalogBad = new Set(catalogMismatchFields(row, opts));
  const catalogMessages = inquiryCatalogMismatchMessages(row, opts);
  const hasError =
    Boolean(row.parse_error) || missing.size > 0 || catalogMessages.length > 0;

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const res = await apiFetch(`${apiBase}/api/inquiries/filter-options/conditional`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(buildInquiryFilterPayload(row)),
          });
          if (!res.ok || cancelled) return;
          const data = (await res.json()) as Partial<InquiryFilterOptions>;
          if (cancelled) return;
          setOpts({
            norma: data.norma ?? [],
            surface: data.surface ?? [],
            diameter: data.diameter ?? [],
            length: data.length ?? [],
            v_class: data.v_class ?? [],
            internal_code: data.internal_code ?? [],
          });
        } catch {
          if (!cancelled) setOpts(EMPTY_OPTS);
        }
      })();
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [apiBase, apiFetch, row.norma, row.surface, row.diameter, row.length, row.v_class, row.internal_code]);

  useEffect(() => {
    const nextWarnings = catalogMessages.length ? catalogMessages : null;
    if (!warningsEqual(row.catalog_warnings, nextWarnings)) {
      onChange({ ...row, catalog_warnings: nextWarnings });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sync warnings when catalog opts change
  }, [catalogMessages.join("|"), row.row_index]);

  useEffect(() => {
    const vc = row.v_class?.trim();
    if (!vc) return;
    if (opts.v_class.length === 0 || !opts.v_class.includes(vc)) {
      onChange({ ...row, v_class: null, catalog_warnings: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- drop class not in catalog
  }, [opts.v_class.join("|"), row.norma, row.v_class]);

  useEffect(() => {
    if (row.internal_code?.trim()) return;
    if (opts.internal_code.length !== 1) return;
    if (missing.size > 0) return;
    onChange({ ...row, internal_code: opts.internal_code[0], catalog_warnings: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- auto-fill unique Smart number
  }, [opts.internal_code.join("|"), missing.size, row.norma, row.diameter, row.length, row.surface, row.v_class]);

  const handleField = async (field: keyof InquiryLineParsed, value: string) => {
    if (field === "internal_code" && value.trim()) {
      try {
        const res = await apiFetch(
          `${apiBase}/api/inquiries/catalog-product/${encodeURIComponent(value.trim())}`,
        );
        if (res.ok) {
          const product = (await res.json()) as Partial<InquiryLineParsed>;
          onChange({
            ...row,
            internal_code: product.internal_code ?? value.trim(),
            norma: product.norma ?? row.norma,
            surface: product.surface ?? row.surface,
            diameter: product.diameter ?? row.diameter,
            length: product.length ?? row.length,
            v_class: product.v_class ?? row.v_class,
            parse_error: null,
            catalog_warnings: null,
          });
          return;
        }
      } catch {
        /* fallback to plain field update */
      }
    }
    onChange(updateRowField(row, field, value));
  };

  const statusMessage =
    row.parse_error ??
    (catalogMessages[0] ?? null) ??
    (hasError ? "Doplniť" : null);

  const renderSelect = (field: InquirySelectField, compact?: boolean) => {
    const isRequired = isInquiryRequiredField(field) && required.has(field);
    const isMissing = isRequired && missing.has(field);
    const isCatalogBad = catalogBad.has(field);
    const cell = row[field];
    const skipLengthZero =
      field === "length" &&
      !normRequiresLength(row.norma, row.raw_text) &&
      (cell === "0" || cell === "");
    const skipVClass =
      field === "v_class" &&
      (!normRequiresVClass(row.norma, row.raw_text) ||
        opts.v_class.length === 0 ||
        (cell != null &&
          String(cell).trim() !== "" &&
          opts.v_class.length > 0 &&
          !opts.v_class.includes(String(cell))));
    const selectValue =
      skipLengthZero || skipVClass || cell == null ? "" : String(cell);
    return (
      <div key={field} className={compact ? "min-w-0 overflow-hidden" : undefined}>
        {compact ? (
          <label className="mb-px block truncate text-[8px] font-semibold uppercase leading-none tracking-tight text-slate-500">
            {COMPACT_FIELD_LABELS[field]}
          </label>
        ) : null}
        <SearchableSelect
          value={selectValue}
          onChange={(v) => handleField(field, v)}
          options={optionsWithCurrent(selectValue || null, opts[field])}
          emptyLabel={isRequired ? "…" : "—"}
          placeholder="Hľadať…"
          size={compact ? "compact" : "default"}
          className={cn(
            compact ? "w-full" : "h-8 min-w-[80px] text-xs",
            (isMissing || isCatalogBad) &&
              "[&_button]:border-amber-500 [&_button]:bg-amber-50",
            !isRequired && !isCatalogBad && "[&_button]:bg-slate-50/80",
          )}
        />
        {isCatalogBad ? (
          <p
            className="mt-px line-clamp-1 text-[8px] leading-tight text-amber-700 md:text-[10px]"
            title={catalogMessages.find((m) => m.includes(FIELD_LABELS[field]))}
          >
            Mimo katalógu
          </p>
        ) : null}
      </div>
    );
  };

  return {
    required,
    missing,
    hasError,
    statusMessage,
    catalogMessages,
    handleField,
    renderSelect,
  };
}

function InquiryEditorRow({ row, apiBase, apiFetch, onChange, onDelete, variant }: RowProps) {
  const {
    missing,
    statusMessage,
    catalogMessages,
    handleField,
    renderSelect,
  } = useInquiryEditorRowState({ row, apiBase, apiFetch, onChange });

  if (variant === "card") {
    return (
      <article
        className={cn(
          "rounded-lg border bg-white p-2 shadow-sm md:rounded-xl md:p-3",
          statusMessage && !row.parse_error && catalogMessages.length === 0
            ? "border-red-200"
            : statusMessage
              ? "border-amber-200"
              : "border-slate-200",
        )}
      >
        <div className="mb-1.5 flex items-start gap-1.5 border-b border-slate-100 pb-1.5 md:mb-3 md:gap-2 md:border-0 md:pb-0">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[10px] font-semibold text-slate-600 md:h-7 md:w-7 md:text-xs">
            {row.row_index}
          </span>
          <p className="min-w-0 flex-1 line-clamp-2 text-[11px] leading-snug text-slate-800 md:line-clamp-none md:text-sm">
            {row.raw_text}
          </p>
          <button
            type="button"
            onClick={onDelete}
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-red-50 hover:text-red-600 md:h-9 md:w-9 md:rounded-lg"
            title="Odstrániť riadok"
            aria-label="Odstrániť riadok"
          >
            <X className="h-3.5 w-3.5 md:h-4 md:w-4" />
          </button>
        </div>

        <div className="grid grid-cols-3 gap-x-1 gap-y-1.5 md:grid-cols-2 md:gap-2.5">
          {SELECT_FIELDS.map((field) => renderSelect(field, true))}
        </div>

        <div className="mt-1.5 grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2 md:mt-3 md:gap-3">
          <div>
            <label className="mb-px block text-[8px] font-semibold uppercase leading-none tracking-tight text-slate-500 md:mb-0.5 md:text-[9px]">
              {FIELD_LABELS.quantity}
            </label>
            <input
              type="number"
              min={1}
              inputMode="numeric"
              value={row.quantity ?? ""}
              onChange={(e) => handleField("quantity", e.target.value)}
              className={cn(
                "h-6 w-full rounded-md border px-1.5 text-[10px] md:h-10 md:rounded-lg md:px-3 md:text-sm",
                missing.has("quantity")
                  ? "border-red-400 bg-red-50 text-red-900"
                  : "border-slate-200 bg-white",
              )}
            />
          </div>
          <div className="text-right">
            <p className="mb-px text-[8px] font-semibold uppercase leading-none tracking-tight text-slate-500 md:mb-0.5 md:text-[9px]">
              Stav
            </p>
            {statusMessage ? (
              <span
                className={cn(
                  "inline-block max-w-[4.5rem] text-[9px] leading-tight md:max-w-[9rem] md:text-xs",
                  row.parse_error || catalogMessages.length ? "text-amber-700" : "text-red-600",
                )}
                title={catalogMessages.join("\n") || statusMessage}
              >
                {statusMessage.length > 28 ? `${statusMessage.slice(0, 28)}…` : statusMessage}
              </span>
            ) : (
              <span className="text-[10px] font-medium text-emerald-600 md:text-sm">OK</span>
            )}
          </div>
        </div>
      </article>
    );
  }

  return (
    <tr className="border-t border-slate-100">
      <td className="px-2 py-1.5 text-slate-500">{row.row_index}</td>
      <td className="max-w-[220px] px-2 py-1.5 text-slate-600" title={row.raw_text}>
        <span className="line-clamp-2">{row.raw_text}</span>
      </td>
      {SELECT_FIELDS.map((field) => (
        <td key={field} className="px-1 py-1">
          {renderSelect(field)}
        </td>
      ))}
      <td className="px-1 py-1">
        <input
          type="number"
          min={1}
          value={row.quantity ?? ""}
          onChange={(e) => handleField("quantity", e.target.value)}
          className={cn(
            "h-8 w-full min-w-[64px] rounded border px-2 text-xs",
            missing.has("quantity")
              ? "border-red-400 bg-red-50 text-red-900"
              : "border-slate-200 bg-white",
          )}
        />
      </td>
      <td className="max-w-[160px] px-2 py-1.5">
        {statusMessage ? (
          <span
            className={cn(
              "line-clamp-3",
              row.parse_error || catalogMessages.length ? "text-amber-700" : "text-red-600",
            )}
            title={catalogMessages.join("\n") || statusMessage}
          >
            {statusMessage.length > 56 ? `${statusMessage.slice(0, 56)}…` : statusMessage}
          </span>
        ) : (
          <span className="text-emerald-600">OK</span>
        )}
      </td>
      <td className="px-1 py-1 text-center">
        <button
          type="button"
          onClick={onDelete}
          className="inline-flex h-7 w-7 items-center justify-center rounded text-slate-400 hover:bg-red-50 hover:text-red-600"
          title="Odstrániť riadok"
          aria-label="Odstrániť riadok"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </td>
    </tr>
  );
}

type Props = {
  rows: InquiryLineParsed[];
  apiBase: string;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  onChange: (rows: InquiryLineParsed[]) => void;
};

export function InquiryEditorTable({ rows, apiBase, apiFetch, onChange }: Props) {
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const emitChange = useCallback(
    (next: InquiryLineParsed[]) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => onChange(next), 400);
    },
    [onChange],
  );

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  const handleRowChange = (updated: InquiryLineParsed) => {
    const next = rows.map((r) => (r.row_index === updated.row_index ? updated : r));
    emitChange(next);
  };

  const handleDelete = (rowIndex: number) => {
    onChange(rows.filter((r) => r.row_index !== rowIndex));
  };

  const headerFields: Array<InquirySelectField | "quantity"> = [
    ...SELECT_FIELDS,
    "quantity",
  ];

  const rowProps = (row: InquiryLineParsed) => ({
    row,
    apiBase,
    apiFetch,
    onChange: handleRowChange,
    onDelete: () => handleDelete(row.row_index),
  });

  return (
    <>
      {/* Mobil: karty */}
      <div className="space-y-2 md:hidden">
        <p className="px-0.5 text-xs font-medium text-slate-800">
          Riadky <span className="font-normal text-slate-500">({rows.length})</span>
        </p>
        {rows.map((row) => (
          <InquiryEditorRow key={row.row_index} {...rowProps(row)} variant="card" />
        ))}
      </div>

      {/* Desktop: tabuľka */}
      <div className="hidden overflow-x-auto rounded-lg border border-slate-200 bg-white md:block">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-2 py-2">#</th>
              <th className="min-w-[180px] px-2 py-2">{FIELD_LABELS.raw_text}</th>
              {headerFields.map((f) => (
                <th key={f} className="min-w-[88px] px-2 py-2">
                  {FIELD_LABELS[f]}
                </th>
              ))}
              <th className="px-2 py-2">Stav</th>
              <th className="w-8 px-1 py-2" aria-label="Odstrániť" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <InquiryEditorRow key={row.row_index} {...rowProps(row)} variant="table" />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

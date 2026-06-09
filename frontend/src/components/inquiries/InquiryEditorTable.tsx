"use client";

import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { SearchableSelect } from "@/components/SearchableSelect";
import {
  catalogMismatchFields,
  inquiryCatalogMismatchMessages,
  inquiryRequiredFields,
  type InquiryFilterField,
  type InquiryFilterOptions,
} from "@/lib/inquiry-norm-rules";
import { cn } from "@/lib/utils";
import { inquiryMissingFields, type InquiryLineParsed } from "@/types/inquiry";

const EMPTY_OPTS: InquiryFilterOptions = {
  norma: [],
  surface: [],
  diameter: [],
  length: [],
  v_class: [],
};

const FIELD_LABELS: Record<InquiryFilterField | "raw_text", string> = {
  raw_text: "Text dopytu",
  norma: "Norma",
  surface: "Povrchová úprava",
  diameter: "Priemer",
  length: "Dĺžka",
  v_class: "Class",
  quantity: "Ks",
};

const SELECT_FIELDS: (keyof InquiryFilterOptions)[] = [
  "norma",
  "surface",
  "diameter",
  "length",
  "v_class",
];

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
            body: JSON.stringify({
              norma: row.norma || null,
              surface: row.surface || null,
              diameter: row.diameter || null,
              length: row.length || null,
              v_class: row.v_class || null,
            }),
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
  }, [apiBase, apiFetch, row.norma, row.surface, row.diameter, row.length, row.v_class]);

  useEffect(() => {
    const nextWarnings = catalogMessages.length ? catalogMessages : null;
    if (!warningsEqual(row.catalog_warnings, nextWarnings)) {
      onChange({ ...row, catalog_warnings: nextWarnings });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sync warnings when catalog opts change
  }, [catalogMessages.join("|"), row.row_index]);

  const handleField = (field: keyof InquiryLineParsed, value: string) => {
    onChange(updateRowField(row, field, value));
  };

  const statusMessage =
    row.parse_error ??
    (catalogMessages[0] ?? null) ??
    (hasError ? "Doplniť" : null);

  const renderSelect = (field: keyof InquiryFilterOptions, compact?: boolean) => {
    const isRequired = required.has(field);
    const isMissing = isRequired && missing.has(field);
    const isCatalogBad = catalogBad.has(field);
    const cell = row[field];
    return (
      <div key={field} className={compact ? "min-w-0" : undefined}>
        {compact ? (
          <label className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-slate-500">
            {FIELD_LABELS[field]}
          </label>
        ) : null}
        <SearchableSelect
          value={cell == null ? "" : String(cell)}
          onChange={(v) => handleField(field, v)}
          options={opts[field]}
          emptyLabel={isRequired ? "Vyber…" : "—"}
          placeholder="Hľadať…"
          className={cn(
            compact ? "h-10 w-full text-sm" : "h-8 min-w-[80px] text-xs",
            (isMissing || isCatalogBad) &&
              "[&_button]:border-amber-500 [&_button]:bg-amber-50",
            !isRequired && !isCatalogBad && "[&_button]:bg-slate-50/80",
          )}
        />
        {isCatalogBad ? (
          <p
            className="mt-0.5 line-clamp-2 text-[10px] text-amber-700"
            title={catalogMessages.find((m) => m.includes(FIELD_LABELS[field as InquiryFilterField]))}
          >
            Nie je v katalógu
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
          "rounded-xl border bg-white p-3 shadow-sm",
          statusMessage && !row.parse_error && catalogMessages.length === 0
            ? "border-red-200"
            : statusMessage
              ? "border-amber-200"
              : "border-slate-200",
        )}
      >
        <div className="mb-3 flex items-start gap-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600">
            {row.row_index}
          </span>
          <p className="min-w-0 flex-1 text-sm leading-snug text-slate-800">{row.raw_text}</p>
          <button
            type="button"
            onClick={onDelete}
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-red-50 hover:text-red-600"
            title="Odstrániť riadok"
            aria-label="Odstrániť riadok"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-2.5">
          {SELECT_FIELDS.map((field) => renderSelect(field, true))}
        </div>

        <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto] items-end gap-3">
          <div>
            <label className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-slate-500">
              {FIELD_LABELS.quantity}
            </label>
            <input
              type="number"
              min={1}
              inputMode="numeric"
              value={row.quantity ?? ""}
              onChange={(e) => handleField("quantity", e.target.value)}
              className={cn(
                "h-10 w-full rounded-lg border px-3 text-sm",
                missing.has("quantity")
                  ? "border-red-400 bg-red-50 text-red-900"
                  : "border-slate-200 bg-white",
              )}
            />
          </div>
          <div className="text-right">
            <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-500">
              Stav
            </p>
            {statusMessage ? (
              <span
                className={cn(
                  "inline-block max-w-[9rem] text-xs leading-snug",
                  row.parse_error || catalogMessages.length ? "text-amber-700" : "text-red-600",
                )}
                title={catalogMessages.join("\n") || statusMessage}
              >
                {statusMessage.length > 48 ? `${statusMessage.slice(0, 48)}…` : statusMessage}
              </span>
            ) : (
              <span className="text-sm font-medium text-emerald-600">OK</span>
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

  const headerFields: Array<InquiryFilterField | "quantity"> = [
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
      <div className="space-y-3 md:hidden">
        <div className="flex items-center justify-between px-0.5">
          <p className="text-sm font-medium text-slate-800">
            Riadky dopytu <span className="text-slate-500">({rows.length})</span>
          </p>
        </div>
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

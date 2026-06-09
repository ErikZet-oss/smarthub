"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { SearchableSelect } from "@/components/SearchableSelect";
import {
  inquiryRequiredFields,
  optionsWithCurrent,
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
};

function updateRowField(
  row: InquiryLineParsed,
  field: keyof InquiryLineParsed,
  value: string,
): InquiryLineParsed {
  const next = { ...row, parse_error: null as string | null };
  if (field === "quantity") {
    const n = value.trim() ? Number(value.replace(",", ".")) : null;
    next.quantity = n != null && !Number.isNaN(n) ? Math.max(0, Math.round(n)) : null;
    return next;
  }
  (next as Record<string, unknown>)[field] = value.trim() || null;
  return next;
}

function InquiryEditorRow({ row, apiBase, apiFetch, onChange }: RowProps) {
  const [opts, setOpts] = useState<InquiryFilterOptions>(EMPTY_OPTS);
  const required = new Set(inquiryRequiredFields(row.norma, row.raw_text));
  const missing = new Set(inquiryMissingFields(row));
  const hasError = Boolean(row.parse_error) || missing.size > 0;

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

  const handleField = (field: keyof InquiryLineParsed, value: string) => {
    onChange(updateRowField(row, field, value));
  };

  return (
    <tr className="border-t border-slate-100">
      <td className="px-2 py-1.5 text-slate-500">{row.row_index}</td>
      <td className="max-w-[220px] px-2 py-1.5 text-slate-600" title={row.raw_text}>
        <span className="line-clamp-2">{row.raw_text}</span>
      </td>
      {SELECT_FIELDS.map((field) => {
        const isRequired = required.has(field);
        const isBad = isRequired && missing.has(field);
        const cell = row[field];
        const options = optionsWithCurrent(
          cell == null ? null : String(cell),
          opts[field],
        );
        return (
          <td key={field} className="px-1 py-1">
            <SearchableSelect
              value={cell == null ? "" : String(cell)}
              onChange={(v) => handleField(field, v)}
              options={options}
              emptyLabel={isRequired ? "Vyber…" : "—"}
              placeholder="Hľadať…"
              className={cn(
                "h-8 min-w-[80px] text-xs",
                isBad && "[&_button]:border-red-400 [&_button]:bg-red-50",
                !isRequired && "[&_button]:bg-slate-50/80",
              )}
            />
          </td>
        );
      })}
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
      <td className="max-w-[140px] px-2 py-1.5">
        {row.parse_error ? (
          <span className="line-clamp-2 text-red-600" title={row.parse_error}>
            {row.parse_error.length > 48
              ? `${row.parse_error.slice(0, 48)}…`
              : row.parse_error}
          </span>
        ) : hasError ? (
          <span className="text-red-600">Doplniť</span>
        ) : (
          <span className="text-emerald-600">OK</span>
        )}
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

  const headerFields: Array<InquiryFilterField | "quantity"> = [
    ...SELECT_FIELDS,
    "quantity",
  ];

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
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
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <InquiryEditorRow
              key={row.row_index}
              row={row}
              apiBase={apiBase}
              apiFetch={apiFetch}
              onChange={handleRowChange}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

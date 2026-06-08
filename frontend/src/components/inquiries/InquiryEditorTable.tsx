"use client";

import { useCallback, useEffect, useRef } from "react";

import {
  INQUIRY_REQUIRED_FIELDS,
  type InquiryLineParsed,
  type InquiryRequiredField,
  inquiryMissingFields,
} from "@/types/inquiry";
import { cn } from "@/lib/utils";

type Props = {
  rows: InquiryLineParsed[];
  onChange: (rows: InquiryLineParsed[]) => void;
};

const FIELD_LABELS: Record<InquiryRequiredField | "material" | "leading_standard" | "raw_text", string> = {
  raw_text: "Text dopytu",
  diameter: "Priemer",
  length: "Dĺžka",
  norm: "Norma",
  class: "Trieda",
  material: "Materiál",
  leading_standard: "Leading standard",
  quantity: "Ks",
};

function cellValue(row: InquiryLineParsed, field: keyof InquiryLineParsed): string {
  const v = row[field];
  if (v === null || v === undefined) return "";
  return String(v);
}

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

export function InquiryEditorTable({ rows, onChange }: Props) {
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

  const handleCell = (rowIndex: number, field: keyof InquiryLineParsed, value: string) => {
    const next = rows.map((r) =>
      r.row_index === rowIndex ? updateRowField(r, field, value) : r,
    );
    emitChange(next);
  };

  const editableFields: Array<keyof InquiryLineParsed> = [
    "diameter",
    "length",
    "norm",
    "class",
    "leading_standard",
    "material",
    "quantity",
  ];

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="min-w-full text-left text-xs">
        <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-2 py-2">#</th>
            <th className="min-w-[180px] px-2 py-2">{FIELD_LABELS.raw_text}</th>
            {editableFields.map((f) => (
              <th key={f} className="min-w-[88px] px-2 py-2">
                {FIELD_LABELS[f as keyof typeof FIELD_LABELS] ?? f}
              </th>
            ))}
            <th className="px-2 py-2">Stav</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const missing = new Set(inquiryMissingFields(row));
            const hasError = Boolean(row.parse_error) || missing.size > 0;
            return (
              <tr key={row.row_index} className="border-t border-slate-100">
                <td className="px-2 py-1.5 text-slate-500">{row.row_index}</td>
                <td className="max-w-[220px] px-2 py-1.5 text-slate-600" title={row.raw_text}>
                  <span className="line-clamp-2">{row.raw_text}</span>
                </td>
                {editableFields.map((field) => {
                  const isRequired = (INQUIRY_REQUIRED_FIELDS as readonly string[]).includes(
                    field,
                  );
                  const isBad = isRequired && missing.has(field as InquiryRequiredField);
                  return (
                    <td key={field} className="px-1 py-1">
                      <input
                        type={field === "quantity" ? "number" : "text"}
                        min={field === "quantity" ? 1 : undefined}
                        value={cellValue(row, field)}
                        onChange={(e) => handleCell(row.row_index, field, e.target.value)}
                        className={cn(
                          "h-8 w-full min-w-[80px] rounded border px-2 text-xs",
                          isBad
                            ? "border-red-400 bg-red-50 text-red-900"
                            : "border-slate-200 bg-white",
                        )}
                        aria-invalid={isBad}
                      />
                    </td>
                  );
                })}
                <td className="px-2 py-1.5">
                  {row.parse_error ? (
                    <span className="text-red-600" title={row.parse_error}>
                      Chyba AI
                    </span>
                  ) : hasError ? (
                    <span className="text-red-600">Doplniť</span>
                  ) : (
                    <span className="text-emerald-600">OK</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

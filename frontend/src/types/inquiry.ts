export type InquiryLineParsed = {
  row_index: number;
  raw_text: string;
  diameter: string | null;
  length: string | null;
  norm: string | null;
  class: string | null;
  leading_standard: string | null;
  material: string | null;
  quantity: number | null;
  parse_error: string | null;
};

export type InquiryDraft = {
  savedAt: string;
  sourceFileName: string;
  rows: InquiryLineParsed[];
  parseTaskId?: string;
};

export const INQUIRY_DRAFT_STORAGE_KEY = "smarthub_inquiry_draft_v1";

export const INQUIRY_REQUIRED_FIELDS = [
  "diameter",
  "length",
  "norm",
  "class",
  "quantity",
] as const;

export type InquiryRequiredField = (typeof INQUIRY_REQUIRED_FIELDS)[number];

export function inquiryMissingFields(row: InquiryLineParsed): InquiryRequiredField[] {
  const missing: InquiryRequiredField[] = [];
  if (!row.diameter?.trim()) missing.push("diameter");
  if (!row.length?.trim()) missing.push("length");
  if (!row.norm?.trim()) missing.push("norm");
  if (!row.class?.trim()) missing.push("class");
  if (row.quantity == null || row.quantity <= 0) missing.push("quantity");
  return missing;
}

export function inquiryRowIsValid(row: InquiryLineParsed): boolean {
  return !row.parse_error && inquiryMissingFields(row).length === 0;
}

export function normalizeInquiryRowFromApi(raw: Record<string, unknown>): InquiryLineParsed {
  return {
    row_index: Number(raw.row_index ?? 0),
    raw_text: String(raw.raw_text ?? ""),
    diameter: (raw.diameter as string | null) ?? null,
    length: (raw.length as string | null) ?? null,
    norm: (raw.norm as string | null) ?? null,
    class: (raw.class as string | null) ?? null,
    leading_standard: (raw.leading_standard as string | null) ?? null,
    material: (raw.material as string | null) ?? null,
    quantity:
      raw.quantity === null || raw.quantity === undefined
        ? null
        : Number(raw.quantity),
    parse_error: (raw.parse_error as string | null) ?? null,
  };
}

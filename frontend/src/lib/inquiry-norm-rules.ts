/** Zladené s backend `norm_rules.py`. */

const NORMS_WITHOUT_LENGTH_KEYS = new Set([
  "934",
  "DIN934",
  "985",
  "DIN985",
  "6923",
  "DIN6923",
  "125",
  "DIN125",
  "127",
  "DIN127",
  "433",
  "DIN433",
  "439",
  "DIN439",
  "315",
  "DIN315",
  "9021",
  "DIN9021",
  "798",
  "DIN798",
  "4032",
  "ISO4032",
  "7089",
  "ISO7089",
  "471",
  "DIN471",
  "472",
  "DIN472",
]);

const NORMS_WITHOUT_V_CLASS = new Set([
  "471",
  "DIN471",
  "472",
  "DIN472",
]);

const PIN_TEXT =
  /\b(kol[íi]k|capov[ýy]\s+kol[íi]k|valcov[ýy]\s+kol[íi]k|cylindrical\s+pin|dowel\s+pin|spring\s+pin)\b/i;

const NO_LENGTH_TEXT =
  /\b(matic(?:a|e|ou|i|ami)?|podložk(?:a|y|ou|ami)?|washer|mutter|nut)\b/i;

const SNAP_RING_TEXT =
  /\b(kru[žz]ok\s+poistn|kruzok\s+poistn|poistn(?:[ýy])?\s+kru[žz]ok|segerring|snap\s*ring)\b/i;

const BOLT_TEXT =
  /\b(skrutk(?:a|y|ou|ami)?|šroub|bolt|screw|vrut|skrutka)\b/i;

const NAIL_TEXT =
  /\b(klin(?:ec|ce|ca|cov|cové|cových)?|hreb(?:ík|ik|iky|íkov|ikov)?|hřeb(?:ík|ik|iky|íkov|ikov)?)\b/i;

const THREADED_ROD_TEXT =
  /závitov(?:á|é|ých|ou|e|y)?\s+ty|zavitov(?:a|e|ych|ou|y)?\s+ty|threaded\s+rod/i;

const NORMS_WITH_LENGTH_KEYS = new Set([
  "933",
  "DIN933",
  "975",
  "DIN975",
  "976",
  "DIN976",
  "931",
  "DIN931",
  "6914",
  "DIN6914",
  "1151",
  "DIN1151",
  "6325",
  "DIN6325",
  "7979",
  "DIN7979",
]);

export function searchKey(value: string | null | undefined): string {
  if (!value) return "";
  return value
    .toUpperCase()
    .trim()
    .replace(/[\s\-_./]+/g, "");
}

function normKey(norma: string | null | undefined): string {
  return searchKey(norma);
}

export function normRequiresLength(
  norma: string | null | undefined,
  rawText = "",
): boolean {
  const key = normKey(norma);
  if (NORMS_WITHOUT_LENGTH_KEYS.has(key)) return false;
  if (key.startsWith("DIN") && NORMS_WITHOUT_LENGTH_KEYS.has(key.slice(3)))
    return false;
  if (NO_LENGTH_TEXT.test(rawText)) return false;
  if (SNAP_RING_TEXT.test(rawText)) return false;
  if (PIN_TEXT.test(rawText)) return true;
  if (NORMS_WITH_LENGTH_KEYS.has(key)) return true;
  if (key.startsWith("DIN") && NORMS_WITH_LENGTH_KEYS.has(key.slice(3)))
    return true;
  if (THREADED_ROD_TEXT.test(rawText)) return true;
  if (NAIL_TEXT.test(rawText)) return true;
  return BOLT_TEXT.test(rawText);
}

export function normRequiresVClass(
  norma: string | null | undefined,
  rawText = "",
): boolean {
  const key = normKey(norma);
  if (NORMS_WITHOUT_V_CLASS.has(key)) return false;
  if (key.startsWith("DIN") && NORMS_WITHOUT_V_CLASS.has(key.slice(3)))
    return false;
  if (SNAP_RING_TEXT.test(rawText)) return false;
  if (PIN_TEXT.test(rawText)) {
    const key = normKey(norma);
    const base = key.startsWith("DIN") ? key.slice(3) : key;
    if (base.startsWith("6325") || base === "6325") return true;
    return false;
  }
  if (NORMS_WITHOUT_LENGTH_KEYS.has(key)) return true;
  if (key.startsWith("DIN") && NORMS_WITHOUT_LENGTH_KEYS.has(key.slice(3)))
    return true;
  if (NO_LENGTH_TEXT.test(rawText)) return true;
  return normRequiresLength(norma, rawText);
}

export type InquiryFilterField =
  | "norma"
  | "surface"
  | "diameter"
  | "length"
  | "v_class"
  | "quantity";

export function inquiryRequiredFields(
  norma: string | null | undefined,
  rawText = "",
): InquiryFilterField[] {
  const required: InquiryFilterField[] = [
    "norma",
    "surface",
    "diameter",
    "quantity",
  ];
  if (normRequiresLength(norma, rawText)) required.push("length");
  if (normRequiresVClass(norma, rawText)) required.push("v_class");
  return required;
}

export type InquiryFilterOptions = {
  norma: string[];
  surface: string[];
  diameter: string[];
  length: string[];
  v_class: string[];
  internal_code: string[];
};

/** Select polia v editore dopytu (filtre + voliteľné číslo Smart). */
export type InquirySelectField = keyof InquiryFilterOptions;

/** Povinné select polia (bez quantity a internal_code). */
export type InquiryRequiredSelectField = Exclude<InquiryFilterField, "quantity">;

export function isInquiryRequiredField(
  field: InquirySelectField,
): field is InquiryRequiredSelectField {
  return field !== "internal_code";
}

export function optionsWithCurrent(
  current: string | null | undefined,
  options: string[],
): string[] {
  const val = (current ?? "").trim();
  if (!val) return options;
  if (options.includes(val)) return options;
  return [val, ...options];
}

const CATALOG_FIELD_LABELS: Record<keyof InquiryFilterOptions, string> = {
  norma: "Norma",
  surface: "Povrch",
  diameter: "Priemer",
  length: "Dĺžka",
  v_class: "Class",
  internal_code: "Číslo Smart",
};

export function inquiryCatalogMismatchMessages(
  row: {
    norma: string | null;
    surface: string | null;
    diameter: string | null;
    length: string | null;
    v_class: string | null;
    internal_code: string | null;
  },
  opts: InquiryFilterOptions,
): string[] {
  const messages: string[] = [];
  for (const field of Object.keys(CATALOG_FIELD_LABELS) as (keyof InquiryFilterOptions)[]) {
    const val = String(row[field] ?? "").trim();
    if (!val) continue;
    const catalog = opts[field];
    if (catalog.length === 0) continue;
    if (!catalog.includes(val)) {
      messages.push(
        `${CATALOG_FIELD_LABELS[field]} „${val}" v katalógu pre túto kombináciu neexistuje`,
      );
    }
  }
  return messages;
}

export function catalogMismatchFields(
  row: {
    norma: string | null;
    surface: string | null;
    diameter: string | null;
    length: string | null;
    v_class: string | null;
    internal_code: string | null;
  },
  opts: InquiryFilterOptions,
): (keyof InquiryFilterOptions)[] {
  const bad: (keyof InquiryFilterOptions)[] = [];
  for (const field of Object.keys(CATALOG_FIELD_LABELS) as (keyof InquiryFilterOptions)[]) {
    const val = String(row[field] ?? "").trim();
    if (!val) continue;
    const catalog = opts[field];
    if (catalog.length === 0) continue;
    if (!catalog.includes(val)) bad.push(field);
  }
  return bad;
}

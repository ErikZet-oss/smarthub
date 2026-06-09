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
]);

const NO_LENGTH_TEXT =
  /\b(matic(?:a|e|ou|i|ami)?|podložk(?:a|y|ou|ami)?|washer|mutter|nut)\b/i;

const BOLT_TEXT =
  /\b(skrutk(?:a|y|ou|ami)?|šroub|bolt|screw|vrut|skrutka)\b/i;

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
  if (NORMS_WITH_LENGTH_KEYS.has(key)) return true;
  if (key.startsWith("DIN") && NORMS_WITH_LENGTH_KEYS.has(key.slice(3)))
    return true;
  if (THREADED_ROD_TEXT.test(rawText)) return true;
  return BOLT_TEXT.test(rawText);
}

export function normRequiresVClass(
  norma: string | null | undefined,
  rawText = "",
): boolean {
  const key = normKey(norma);
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
};

export function optionsWithCurrent(
  current: string | null | undefined,
  options: string[],
): string[] {
  const val = (current ?? "").trim();
  if (!val) return options;
  if (options.includes(val)) return options;
  return [val, ...options];
}

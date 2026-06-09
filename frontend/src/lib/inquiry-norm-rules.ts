/** Zladené s backend `norm_rules.py` — normy bez dĺžky (matice, podložky). */

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
  return BOLT_TEXT.test(rawText);
}

export function normRequiresVClass(
  norma: string | null | undefined,
  rawText = "",
): boolean {
  if (!normRequiresLength(norma, rawText)) return false;
  return true;
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
  const required: InquiryFilterField[] = ["norma", "diameter", "quantity"];
  if (normRequiresLength(norma, rawText)) required.push("length");
  if (normRequiresVClass(norma, rawText)) required.push("v_class");
  return required;
}

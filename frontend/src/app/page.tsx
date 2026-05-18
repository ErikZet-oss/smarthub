"use client";

import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CircleCheck,
  CircleHelp,
  DatabaseZap,
  ExternalLink,
  Eye,
  EyeOff,
  FileSpreadsheet,
  FileText,
  History,
  ImageIcon,
  KeyRound,
  Link2,
  List,
  Loader2,
  LogOut,
  Menu,
  Moon,
  PackageSearch,
  Plus,
  ShieldCheck,
  ShoppingCart,
  Terminal,
  Truck,
  Trash2,
  Sun,
  X,
} from "lucide-react";

import {
  AddToOfferDialog,
  type AddToOfferPayload,
} from "@/components/offers/AddToOfferDialog";
import { CompanySettingsAdmin } from "@/components/offers/CompanySettingsAdmin";
import { OffersPanel } from "@/components/offers/OffersPanel";
import { SearchableSelect } from "@/components/SearchableSelect";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { API_PROXY_PREFIX } from "@/lib/api-origin";
import { readApiJsonOrText } from "@/lib/api-errors";
import { cn } from "@/lib/utils";

type View =
  | "vyhladavanie"
  | "zoznamy"
  | "kosik"
  | "historia"
  | "ponuky"
  | "dodavatelia"
  | "parovanie"
  | "admin"
  | "dev";

const CART_HISTORY_STORAGE_KEY = "smart_procurement_cart_history_v1";
const CART_HISTORY_MAX = 500;

/**
 * Predvolená absolútna cesta k Gamechanger XLSX — backend musí súbor vidieť na disku.
 * Live (Render): typicky /opt/render/project/src/... Lokálne: nastav NEXT_PUBLIC_GAMECHANGER_XLSX_PATH v .env.local.
 */
const DEFAULT_GAMECHANGER_XLSX_PATH =
  (typeof process !== "undefined" &&
    process.env.NEXT_PUBLIC_GAMECHANGER_XLSX_PATH?.trim()) ||
  "data/Smart_data_Gamechanger.xlsx";

/** Poznámka k konkrétnej ponuke: interný kód produktu + id dodávateľa. */
function offerNoteStorageKey(internalCode: string, supplierId: number): string {
  return `${internalCode}::${supplierId}`;
}

/** Záznam o pridaní do košíka na B2B (ukladá sa lokálne v prehliadači). */
type CartHistoryEntry = {
  id: string;
  addedAtIso: string;
  internalCode: string;
  supplierId: number;
  supplierName: string;
  supplierCode: string;
  quantity: number;
  packagingVariantIndex: number | null;
  variantLabel: string | null;
  priceEur: number | null;
  priceUnit: string | null;
  logoUrl: string | null;
  norma: string | null;
  diameter: string | null;
  length: string | null;
  surface: string | null;
  yMoneyName: string | null;
  packQuantity: number | null;
  /** Poznámka k ponuke produktu u dodávateľa v čase pridania. */
  offerNote: string | null;
};

type AddToCartHistoryMeta = {
  internalCode: string;
  priceEur: number | null;
  priceUnit: string | null;
  logoUrl: string | null;
  norma: string | null;
  diameter: string | null;
  length: string | null;
  surface: string | null;
  yMoneyName: string | null;
  variantLabel: string | null;
  packQuantity: number | null;
  offerNote: string | null;
};

type RemoteCartOverviewRow = {
  supplier_id: number;
  name: string;
  logo_url: string | null;
  remote_supported: boolean;
  logged_in: boolean | null;
  total_eur: number | null;
  line_count: number;
  message: string | null;
  /** Odkaz na verejnú stránku košíka (doména z DB + /kosik). */
  web_cart_url: string;
  /** Limit z DB pre farbu tlačidla (doprava zdarma). */
  free_shipping_threshold_eur: number | null;
};

type RemoteCartOverviewUiRow = RemoteCartOverviewRow & {
  overviewLoading: boolean;
};

function normalizeRemoteCartOverviewRow(
  raw: Partial<RemoteCartOverviewRow> & { supplier_id: number },
  overviewLoading: boolean,
): RemoteCartOverviewUiRow {
  return {
    supplier_id: raw.supplier_id,
    name: raw.name ?? "",
    logo_url: raw.logo_url ?? null,
    remote_supported: Boolean(raw.remote_supported),
    logged_in: raw.logged_in ?? null,
    total_eur: raw.total_eur ?? null,
    line_count: raw.line_count ?? 0,
    message: raw.message ?? null,
    web_cart_url: raw.web_cart_url ?? "",
    free_shipping_threshold_eur: raw.free_shipping_threshold_eur ?? null,
    overviewLoading,
  };
}

function supplierListItemToRemoteCartPlaceholder(s: {
  id: number;
  name: string;
  logo_url?: string | null;
  free_shipping_threshold_eur?: number | null;
}): RemoteCartOverviewUiRow {
  return normalizeRemoteCartOverviewRow(
    {
      supplier_id: s.id,
      name: s.name,
      logo_url: s.logo_url ?? null,
      remote_supported: false,
      logged_in: null,
      total_eur: null,
      line_count: 0,
      message: null,
      web_cart_url: "",
      free_shipping_threshold_eur: s.free_shipping_threshold_eur ?? null,
    },
    true,
  );
}

type RemoteCartLineRow = {
  label: string;
  quantity: number;
  unit_price_eur: number | null;
  line_total_eur: number | null;
  variant_code: string | null;
};

type RemoteCartDetailPayload = {
  supplier_id: number;
  name: string;
  logo_url: string | null;
  remote_supported: boolean;
  logged_in: boolean | null;
  total_eur: number | null;
  lines: RemoteCartLineRow[];
  message: string | null;
};

function parseCartHistoryFromStorage(): CartHistoryEntry[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const raw = localStorage.getItem(CART_HISTORY_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const data = JSON.parse(raw) as unknown;
    if (!Array.isArray(data)) {
      return [];
    }
    const out: CartHistoryEntry[] = [];
    for (const row of data) {
      if (!row || typeof row !== "object") {
        continue;
      }
      const o = row as Record<string, unknown>;
      const id = typeof o.id === "string" ? o.id : null;
      const addedAtIso = typeof o.addedAtIso === "string" ? o.addedAtIso : null;
      if (!id || !addedAtIso) {
        continue;
      }
      out.push({
        id,
        addedAtIso,
        internalCode:
          typeof o.internalCode === "string" ? o.internalCode : "—",
        supplierId:
          typeof o.supplierId === "number" ? o.supplierId : Number(o.supplierId) || 0,
        supplierName:
          typeof o.supplierName === "string" ? o.supplierName : "—",
        supplierCode:
          typeof o.supplierCode === "string" ? o.supplierCode : "—",
        quantity:
          typeof o.quantity === "number" ? o.quantity : Number(o.quantity) || 0,
        packagingVariantIndex:
          o.packagingVariantIndex === null
            ? null
            : typeof o.packagingVariantIndex === "number"
              ? o.packagingVariantIndex
              : Number(o.packagingVariantIndex) || null,
        variantLabel:
          o.variantLabel === null || typeof o.variantLabel === "string"
            ? (o.variantLabel as string | null)
            : null,
        priceEur:
          o.priceEur === null || o.priceEur === undefined
            ? null
            : typeof o.priceEur === "number"
              ? o.priceEur
              : Number(o.priceEur) || null,
        priceUnit:
          o.priceUnit === null || typeof o.priceUnit === "string"
            ? (o.priceUnit as string | null)
            : null,
        logoUrl:
          o.logoUrl === null || typeof o.logoUrl === "string"
            ? (o.logoUrl as string | null)
            : null,
        norma:
          o.norma === null || typeof o.norma === "string"
            ? (o.norma as string | null)
            : null,
        diameter:
          o.diameter === null || typeof o.diameter === "string"
            ? (o.diameter as string | null)
            : null,
        length:
          o.length === null || typeof o.length === "string"
            ? (o.length as string | null)
            : null,
        surface:
          o.surface === null || typeof o.surface === "string"
            ? (o.surface as string | null)
            : null,
        yMoneyName:
          o.yMoneyName === null || typeof o.yMoneyName === "string"
            ? (o.yMoneyName as string | null)
            : o.y_money_name === null || typeof o.y_money_name === "string"
              ? (o.y_money_name as string | null)
              : null,
        packQuantity:
          o.packQuantity === null || o.packQuantity === undefined
            ? null
            : typeof o.packQuantity === "number"
              ? o.packQuantity
              : Number(o.packQuantity) || null,
        offerNote: (() => {
          if (typeof o.offerNote === "string") {
            return o.offerNote;
          }
          if (
            o.supplierNote === null ||
            o.supplierNote === undefined ||
            typeof o.supplierNote !== "string"
          ) {
            return null;
          }
          return o.supplierNote;
        })(),
      });
    }
    return out;
  } catch {
    return [];
  }
}

type DevLogEntry = {
  ts: string;
  level: string;
  source: string;
  message: string;
  supplier?: string | null;
  supplier_id?: number | null;
  run_id?: string | null;
  screenshot_url?: string | null;
};
type FilterField =
  | "code"
  | "norma"
  | "surface"
  | "diameter"
  | "length"
  | "v_class"
  | "y_money_name"
  | "image_filename";

type SupplierForm = {
  id?: number;
  name: string;
  shopUrl: string;
  username: string;
  password: string;
  isConnected: boolean;
  /** Názov stĺpca v Exceli, kde je kód tohto dodávateľa (napr. Fabory kód). */
  codeColumn: string;
  /** JSON pre Playwright — selektory prihlásenia, vyhľadávania, košíka. */
  cartConfigJson: string;
  /** Suma v EUR — od nej sa berie doprava zdarma (prázdne = bez prahu). */
  freeShippingThresholdEur: string;
  /** Poradie v zoznamoch (server); po zmene cez šípky sa uloží cez API. */
  sortOrder?: number;
  /** Relatívna cesta z API (`/supplier-logos/...`) alebo `null`. */
  logoUrl?: string | null;
};

type MappingProfile = {
  columns: string[];
  preview_rows: Array<Record<string, string>>;
  unique_values: Record<string, string[]>;
};

type ProductSearchRow = {
  product_id?: number | null;
  internal_code: string;
  norma: string | null;
  diameter: string | null;
  length: string | null;
  surface: string | null;
  v_class: string | null;
  y_money_name: string | null;
  image_filename?: string | null;
  offers: Array<{
    supplier: string;
    price_eur: number;
    stock: number;
    supplier_id?: number | null;
    supplier_code?: string | null;
    supplier_product_url?: string | null;
    logo_url?: string | null;
  }>;
};

type ProductListRow = {
  id: number;
  name: string;
  item_count: number;
  created_at: string;
};

type ProductListItemRow = {
  product_id: number;
  internal_code: string;
  norma: string | null;
  diameter: string | null;
  length: string | null;
  surface: string | null;
  v_class: string | null;
  y_money_name: string | null;
  image_filename?: string | null;
  added_at: string;
};

/** API môže občas vrátiť riadok bez `offers` — bez toho spadne render na `.map`. */
function normalizeProductSearchRows(rows: ProductSearchRow[]): ProductSearchRow[] {
  return rows.map((row) => ({
    ...row,
    offers: Array.isArray(row.offers) ? row.offers : [],
  }));
}

type FilterOptions = {
  norma: string[];
  surface: string[];
  diameter: string[];
  length: string[];
  v_class: string[];
  y_money_name: string[];
};

/** Zabráni zbytočným re-renderom SearchableSelect pri rovnakých možnostiach. */
function filterOptionsArraysEqual(a: FilterOptions, b: FilterOptions): boolean {
  const keys: (keyof FilterOptions)[] = [
    "norma",
    "surface",
    "diameter",
    "length",
    "v_class",
    "y_money_name",
  ];
  for (const k of keys) {
    const va = a[k];
    const vb = b[k];
    if (va.length !== vb.length) {
      return false;
    }
    for (let i = 0; i < va.length; i++) {
      if (va[i] !== vb[i]) {
        return false;
      }
    }
  }
  return true;
}

type SearchFiltersState = {
  code: string;
  norma: string;
  surface: string;
  diameter: string;
  length: string;
  v_class: string;
  y_money_name: string;
};

const initialSearchFilters: SearchFiltersState = {
  code: "",
  norma: "",
  surface: "",
  diameter: "",
  length: "",
  v_class: "",
  y_money_name: "",
};

/** Excel: stĺpec V = Class, stĺpec Y = Money názov (hlavičky v súbore). */
const EXCEL_COL_V = "V";
const EXCEL_COL_W = "W";
const EXCEL_COL_Y = "Y";

/** Same-origin proxy → FastAPI (next.config rewrites), bez CORS „Failed to fetch“. */
const API_BASE = API_PROXY_PREFIX;

/** Absolútna URL pre statické assety API (logo dodávateľa atď.). */
/** Posledný segment cesty (názov súboru) — zobrazovanie v párovaní. */
function excelBasename(path: string): string {
  const t = path.trim();
  if (!t) {
    return "";
  }
  const norm = t.replace(/\\/g, "/");
  const seg = norm.split("/").filter(Boolean);
  return seg.length ? seg[seg.length - 1]! : t;
}

function publicApiAssetUrl(path: string | null | undefined): string | null {
  if (path == null || !String(path).trim()) {
    return null;
  }
  const p = String(path).trim();
  if (/^https?:\/\//i.test(p)) {
    return p;
  }
  return `${API_BASE}${p.startsWith("/") ? p : `/${p}`}`;
}

function productImagePublicUrl(fileName: string | null | undefined): string | null {
  const raw = (fileName ?? "").trim();
  if (!raw) return null;
  const base = raw.replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? "";
  if (!base) return null;
  return `${API_BASE}/product-images/${encodeURIComponent(base)}`;
}

/** Deep link na B2B produkt často skončí loginom; otvárame radšej doménu e-shopu. */
function supplierSafeExternalUrl(
  productUrl: string | null | undefined,
): string | null {
  const raw = (productUrl || "").trim();
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return `${u.origin}/`;
  } catch {
    return raw;
  }
}

type SelectFilterKey =
  | "norma"
  | "surface"
  | "diameter"
  | "length"
  | "v_class"
  | "y_money_name";

/** Zhoda s backend `FIELD_DEFAULTS` — ak z `/mapping/fields` prídu prázdne reťazce. */
const FILTER_COLUMN_DEFAULTS: Record<SelectFilterKey, string> = {
  norma: "Leading standard",
  surface: "Surface treatments (long)",
  diameter: "Diameter [M/Tr]",
  length: "Length [mm]",
  v_class: "Class",
  y_money_name: "Money názov",
};

function pruneSelectFilters(
  current: Pick<SearchFiltersState, SelectFilterKey>,
  opts: FilterOptions,
): Pick<SearchFiltersState, SelectFilterKey> {
  const next = { ...current };
  if (next.norma && !opts.norma.includes(next.norma)) {
    next.norma = "";
  }
  if (next.surface && !opts.surface.includes(next.surface)) {
    next.surface = "";
  }
  if (next.diameter && !opts.diameter.includes(next.diameter)) {
    next.diameter = "";
  }
  if (next.length && !opts.length.includes(next.length)) {
    next.length = "";
  }
  if (next.v_class && !opts.v_class.includes(next.v_class)) {
    next.v_class = "";
  }
  if (next.y_money_name && !opts.y_money_name.includes(next.y_money_name)) {
    next.y_money_name = "";
  }
  return next;
}

/** Nájde kľúč v `unique_values` (presný alebo len veľkosť písmen), aby sedelo mapovanie z DB aj hlavička zo súboru. */
function resolveFilterColumnKeyInProfile(
  field: SelectFilterKey,
  fields: Record<FilterField, string>,
  profile: MappingProfile | null,
): string | null {
  if (!profile?.unique_values) {
    return null;
  }
  const tryKey = (raw: string | undefined): string | null => {
    const key = raw?.trim();
    if (!key) {
      return null;
    }
    const u = profile.unique_values;
    if (u[key]?.length) {
      return key;
    }
    const lower = key.toLowerCase();
    for (const k of Object.keys(u)) {
      if (k.toLowerCase() === lower && u[k]?.length) {
        return k;
      }
    }
    return null;
  };
  const fromField = tryKey(fields[field]);
  if (fromField) {
    return fromField;
  }
  return tryKey(FILTER_COLUMN_DEFAULTS[field]);
}

/**
 * Zlúči možnosti filtrov z DB (kaskáda) s hodnotami z profilu Excelu.
 * Ak nie je aktívna kaskáda (žiadny filter), zoberie zjednotenie — v Exceli môže byť viac unikátov ako v orezanom výbere z DB.
 */
function mergeConditionalFilterOptionsWithExcel(
  db: FilterOptions,
  profile: MappingProfile | null,
  fields: Record<FilterField, string>,
  cascadeActive: boolean,
): FilterOptions {
  const fromExcel = (field: SelectFilterKey): string[] => {
    if (!profile?.unique_values) {
      return [];
    }
    const col = resolveFilterColumnKeyInProfile(field, fields, profile);
    if (!col) {
      return [];
    }
    const vals = profile.unique_values[col];
    if (!vals?.length) {
      return [];
    }
    return [...vals].sort((a, b) => a.localeCompare(b, "sk"));
  };

  const pick = (field: SelectFilterKey, dbVals: string[]): string[] => {
    const ex = fromExcel(field);
    if (!cascadeActive && (dbVals.length > 0 || ex.length > 0)) {
      return Array.from(new Set([...dbVals, ...ex])).sort((a, b) =>
        a.localeCompare(b, "sk"),
      );
    }
    if (dbVals.length > 0) {
      return dbVals;
    }
    return ex;
  };

  return {
    norma: pick("norma", db.norma),
    surface: pick("surface", db.surface),
    diameter: pick("diameter", db.diameter),
    length: pick("length", db.length),
    v_class: pick("v_class", db.v_class),
    y_money_name: pick("y_money_name", db.y_money_name),
  };
}

/** Normalizácia názvu: „HOPE fix“ → rovnaká logika ako backend pre Hopefix HTTP. */
function supplierNameCompactLower(name: string | null | undefined): string {
  return (name ?? "").toLowerCase().replace(/\s+/g, "");
}

/** Dodávatelia, pri ktorých zobrazujeme stav prihlásenia zo scrape. */
function supplierShowsScrapeLoginBadge(supplierName: string): boolean {
  const c = supplierNameCompactLower(supplierName);
  return (
    c.includes("fabory") ||
    c.includes("mekrs") ||
    c.includes("hopefix") ||
    c.includes("haspl") ||
    c.includes("inoxmare") ||
    c.includes("bmkco") ||
    c.includes("bmco") ||
    c.includes("halfmann") ||
    c.includes("argip") ||
    c.includes("schachermayer") ||
    c.includes("valenta")
  );
}

const defaultSuppliers: SupplierForm[] = [
  {
    name: "Fabory",
    shopUrl: "",
    username: "",
    password: "",
    isConnected: true,
    codeColumn: "",
    cartConfigJson: "",
    freeShippingThresholdEur: "",
  },
  {
    name: "Wurth",
    shopUrl: "",
    username: "",
    password: "",
    isConnected: true,
    codeColumn: "",
    cartConfigJson: "",
    freeShippingThresholdEur: "",
  },
  {
    name: "Bossard",
    shopUrl: "",
    username: "",
    password: "",
    isConnected: false,
    codeColumn: "",
    cartConfigJson: "",
    freeShippingThresholdEur: "",
  },
];

/** Pozadie tlačidla „košík na eshope“: zelené ak je súčet košíka vyšší ako prah dopravy zdarma. */
function remoteCartEshopButtonClass(
  totalEur: number | null | undefined,
  freeShippingThresholdEur: number | null | undefined,
): string {
  const thr = freeShippingThresholdEur;
  const tot = totalEur;
  const above =
    thr != null &&
    Number.isFinite(thr) &&
    thr > 0 &&
    tot != null &&
    Number.isFinite(tot) &&
    tot > thr;
  /* ! — outline variant má bg-white; treba prepísať pozadie. */
  if (above) {
    return "!border-emerald-600 !bg-emerald-600 !text-white shadow-sm hover:!bg-emerald-700 hover:!text-white";
  }
  return "!border-sky-300 !bg-sky-200 !text-slate-800 shadow-sm hover:!bg-sky-300";
}

function formatApiDetail(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail.map((item) => JSON.stringify(item)).join("; ");
  }
  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }
  return "Neznáma chyba";
}

/** Pravda, ak `fetch()` skončil na sieti skôr než prišla HTTP odpoveď (prehliadač často: „Failed to fetch“). */
function isBrowserFetchNetworkError(message: string): boolean {
  const m = message.trim();
  return (
    m === "Failed to fetch" ||
    m.includes("NetworkError") ||
    m.includes("Network request failed") ||
    m.includes("Load failed") ||
    m.includes("fetch failed")
  );
}

/** Krátky návod pri nedostupnom API (namiesto anglickej hlášky z prehliadača). */
function apiUnreachableUserMessage(apiBase: string): string {
  return (
    `Nepodarilo sa spojiť s backendom (${apiBase}). ` +
    `Zvyčajne to znamená, že FastAPI ešte nebeží, beží na inom porte, alebo v .env.local nie je správna adresa ` +
    `(NEXT_PUBLIC_API_BASE_URL). Používaj v prehliadači rovnakú doménu ako v URL API (localhost vs 127.0.0.1). ` +
    `Ak spúšťaš backend a frontend naraz, prvý pokus po štarte občas zlyhá — chvíľu počkaj a spusti vyhľadávanie znova.`
  );
}

/** Riadok variantu balenia (napr. Mekrs modal „Vyberte variantu“). */
type PackagingVariantRow = {
  label?: string | null;
  pack_quantity?: number | null;
  price_eur?: number | null;
  raw_price?: string | null;
  stock?: number | null;
  raw_stock?: string | null;
  /** Mekrs HTTP: UUID variantu pre POST /api/cart/.../add */
  mekrs_variant_id?: string | null;
  /** Hopefix HTTP: product_id pre POST /api/add_to_cart */
  hopefix_product_id?: string | null;
  hopefix_package_type?: string | null;
  /** Hopefix HTTP: cesta katalógu (Referer), zvyčajne z packaging_variants po scrape */
  hopefix_referer_path?: string | null;
  /** Haspl HTTP: Sylius variant ``code`` pre POST …/orders/…/items */
  haspl_variant_code?: string | null;
  /** Inoxmare HTTP: Magento ``product`` ID a relatívna cesta PDP. */
  inoxmare_product_id?: string | null;
  inoxmare_referer_path?: string | null;
  /** Haspl: „Balení obsahuje …“ z API (voliteľné, v tabuľke sa zobrazí skôr ``pack_quantity``). */
  raw_pack_quantity?: string | null;
  /** Mekrs API: napr. eur / czk — číslo v price_eur je v tejto mene */
  currency_code?: string | null;
  currency_symbol?: string | null;
  /** Napr. per_sks (Haspl), per_100_ks (Mekrs) — suffix za sumou */
  price_unit?: string | null;
  /** Mekrs: „Skladem N balení“ z modálu / API, len ak je k dispozícii */
  mekrs_package_stock_text?: string | null;
  /** Argip HTTP: SKU pre GraphQL košík (vybraný variant / konfigurácia). */
  argip_sku?: string | null;
  /** Počet ks v obchodnom balení (Argip `package` z API; nie MOQ cenovej hladiny). */
  shop_pack_quantity?: number | null;
};

type SupplierScrapeState = {
  loading: boolean;
  price_eur?: number | null;
  /** Názov z PDP (Fabory: <h1>…). */
  product_title?: string | null;
  /** Text ceny zo scrapu (napr. Hopefix OOS „0,00 €“), ak treba živý stav bez čísla vo variante. */
  raw_price?: string | null;
  /** Napr. per_100_ks (Mekrs), per_sks (Haspl). */
  price_unit?: string | null;
  stock?: number | null;
  /** Text zo stránky (napr. „Na sklade“), ak nie je číselný sklad. */
  raw_stock?: string | null;
  /** Počet kusov v balení (z e-shopu), ak je v JSON pack_quantity_selector. */
  pack_quantity?: number | null;
  raw_pack_quantity?: string | null;
  /** Argip: ks v balení na e-shope (pre nápovedu pri košíku). */
  shop_pack_quantity?: number | null;
  /** Viac možností balenia — zobrazí sa viac košíkov v detaile. */
  packaging_variants?: PackagingVariantRow[] | null;
  /** Mekrs HTTP: false = z API price (bez DPH); true = núdzovo priceWithVAT. */
  price_includes_vat?: boolean | null;
  currency_code?: string | null;
  currency_symbol?: string | null;
  error?: string | null;
  hint?: string | null;
  /** Stav po pokuse o prihlásenie v Playwright (len ak API vráti pole). */
  logged_in?: boolean | null;
  login_hint?: string | null;
};

function scrapeCacheKey(internalCode: string, supplierId: number): string {
  return `${internalCode}:${supplierId}`;
}

function supplierNameIsMekrs(name: string | null | undefined): boolean {
  return Boolean(name && name.toLowerCase().includes("mekrs"));
}

/** Mekrs: živý sklad 0 alebo text „nie je skladem“ — v UI ako červené „Nie je skladom“. */
function mekrsIsOutOfStock(scrape: SupplierScrapeState | undefined): boolean {
  if (!scrape || scrape.loading || scrape.error) {
    return false;
  }
  const qty = scrape.stock;
  if (qty != null && Number.isFinite(qty) && qty <= 0) {
    return true;
  }
  const raw = (scrape.raw_stock || "").trim().toLowerCase();
  if (!raw) {
    return false;
  }
  if (
    raw.includes("není skladem") ||
    raw.includes("neni skladem") ||
    raw.includes("nie je skladom") ||
    raw.includes("nie je na sklade")
  ) {
    return true;
  }
  if (
    /\b0\s*ks\b/.test(raw) &&
    (raw.includes("skladem") || raw.includes("celkem"))
  ) {
    return true;
  }
  return false;
}

/** Hopefix: sklad 0 alebo český text nedostupnosti — červené „Nie je skladom“, cena 0 €. */
function hopefixIsOutOfStock(scrape: SupplierScrapeState | undefined): boolean {
  if (!scrape || scrape.loading || scrape.error) {
    return false;
  }
  const v0 =
    Array.isArray(scrape.packaging_variants) &&
    scrape.packaging_variants.length >= 1
      ? scrape.packaging_variants[0]
      : null;
  const raw = (
    (scrape.raw_stock || "").trim() ||
    (v0?.raw_stock || "").trim()
  ).toLowerCase();
  if (raw) {
    if (
      raw.includes("není skladem") ||
      raw.includes("neni skladem") ||
      raw.includes("nie je skladom") ||
      raw.includes("nie je na sklade") ||
      raw.includes("vyprodáno") ||
      raw.includes("vyprodano") ||
      raw.includes("není na sklad") ||
      raw.includes("neni na sklad") ||
      raw.includes("nedostupné") ||
      raw.includes("nedostupne") ||
      raw.includes("momentálně nedostupné") ||
      raw.includes("momentálne nedostupné") ||
      raw.includes("ne skladem")
    ) {
      return true;
    }
  }
  const qty =
    scrape.stock != null && Number.isFinite(scrape.stock)
      ? scrape.stock
      : v0?.stock != null && Number.isFinite(v0.stock)
        ? v0.stock
        : null;
  if (qty != null && qty <= 0) {
    return true;
  }
  return false;
}

/** Živý stav riadka (súhrnný scrape alebo zvolený variant) — košík a „Nie je skladom“. */
function cartRowLiveOutOfStock(
  scrape: SupplierScrapeState | undefined,
  supplier: string | null | undefined,
  activePv: {
    stock?: number | null;
    raw_stock?: string | null;
  } | null,
  usesHttpCartVariants: boolean,
): boolean {
  if (!scrape || scrape.loading || scrape.error) {
    return false;
  }
  if (usesHttpCartVariants && activePv) {
    const eff: SupplierScrapeState = {
      ...scrape,
      stock: activePv.stock ?? scrape.stock,
      raw_stock: activePv.raw_stock ?? scrape.raw_stock,
    };
    return supplierNameIsMekrs(supplier)
      ? mekrsIsOutOfStock(eff)
      : hopefixIsOutOfStock(eff);
  }
  return supplierNameIsMekrs(supplier)
    ? mekrsIsOutOfStock(scrape)
    : hopefixIsOutOfStock(scrape);
}

function supplierNameIsHopefix(name: string | null | undefined): boolean {
  return supplierNameCompactLower(name).includes("hopefix");
}

function supplierNameIsHaspl(name: string | null | undefined): boolean {
  return Boolean(name && name.toLowerCase().includes("haspl"));
}

function supplierNameIsInoxmare(name: string | null | undefined): boolean {
  const c = supplierNameCompactLower(name);
  /** „Inox“ = skrátený názov pre Inox Mare (inoxmare.com) v zozname dodávateľov. */
  return c.includes("inoxmare") || c === "inox";
}

function supplierNameIsFabory(name: string | null | undefined): boolean {
  return supplierNameCompactLower(name).includes("fabory");
}

function supplierNameIsHalfmann(name: string | null | undefined): boolean {
  return supplierNameCompactLower(name).includes("halfmann");
}

function supplierNameIsArgip(name: string | null | undefined): boolean {
  return supplierNameCompactLower(name).includes("argip");
}

/** Pod logo: len krátky názov produktu — bez balenia, zátvoriek „(… ks)“ a „… ks“ na konci riadku. */
function faboryUiProductTitleOnly(raw: string): string {
  const first = (raw || "").replace(/\r/g, "").split("\n")[0]?.trim() ?? "";
  if (!first) {
    return "";
  }
  let t = first;
  t = t.replace(/^balení\s*\([^)]+\)\s*/i, "").trim();
  t = t.replace(/\s*\(\s*\d+[\s\u00a0]*ks\s*\)\s*$/i, "").trim();
  t = t.replace(/\s+\d+[\s\u00a0]*ks\s*$/i, "").trim();
  return (t || first).trim();
}

/** Fabory: farba textu stavu skladu zo živého scrape. */
function faboryStockDisplayClass(displayText: string): string | undefined {
  const s = displayText.trim().toLowerCase();
  if (!s) {
    return undefined;
  }
  if (s.includes("nie je skladom") || s.includes("nie je na sklade")) {
    return "font-medium text-red-600";
  }
  if (s.includes("čiastočne skladom")) {
    return "font-medium text-orange-600";
  }
  return undefined;
}

/** Rovnaká kompaktná typografia bunky „Balenie“ ako Haspl (názov + ks). */
function supplierUsesHasplStylePackLabel(name: string | null | undefined): boolean {
  return (
    supplierNameIsHaspl(name) ||
    supplierNameIsHopefix(name) ||
    supplierNameIsFabory(name) ||
    supplierNameIsInoxmare(name)
  );
}

/** Zobrazenie cien zo scrapu/API — 4 desatinné miesta, bez dodatočného zaokrúhľovania v JS. */
function formatScrapePriceAmount(value: number): string {
  return value.toFixed(2);
}

/** Jednotná prípona pri jednotkových/katalógových cenách v UI (nezmení uložené `price_unit` v histórii). */
const SCRAPE_PRICE_DISPLAY_SUFFIX = " / 100";

/** Suffix za sumou. `compact`: krátky tvar „/ 100“ v úzkom súhrne (Mekrs, Argip hlavička). */
function scrapePriceUnitSuffix(
  supplierName: string | null | undefined,
  unit: string | null | undefined,
  compact = false,
): string {
  const u = (unit || "").trim();
  if (u === "per_1_ks") {
    return " / 1 ks";
  }
  if (u === "per_100_ks") {
    return compact ? SCRAPE_PRICE_DISPLAY_SUFFIX : " / 100 ks";
  }
  if (supplierNameIsArgip(supplierName)) {
    return compact ? SCRAPE_PRICE_DISPLAY_SUFFIX : " / 100 ks";
  }
  return SCRAPE_PRICE_DISPLAY_SUFFIX;
}

/** Celé číslo s medzerami po trojiciach zprava (ako na mekrs.cz: „2 411 998“). */
function formatIntegerCsThousands(value: number): string {
  if (!Number.isFinite(value)) {
    return String(value);
  }
  const sign = value < 0 ? "-" : "";
  const abs = Math.trunc(Math.abs(value));
  if (abs === 0) {
    return sign + "0";
  }
  const s = String(abs);
  const chunks: string[] = [];
  let i = s.length;
  while (i > 0) {
    const start = Math.max(0, i - 3);
    chunks.unshift(s.slice(start, i));
    i = start;
  }
  return sign + chunks.join(" ");
}

function formatKsQuantity(value: number): string {
  return `${formatIntegerCsThousands(value)} ks`;
}

/** V texte zo servera (napr. „Skladem celkem 2411998 ks“) naformátuje dlhé číselné úseky. */
function formatDigitsInTextCsThousands(text: string): string {
  return text.replace(/\d{4,}/g, (run) => {
    const n = parseInt(run, 10);
    if (!Number.isFinite(n) || String(n) !== run) {
      return run;
    }
    return formatIntegerCsThousands(n);
  });
}

/** Jednotný modrý gradient pre všetkých dodávateľov + jemnejší border. */
function supplierDetailHeaderBarClass(_offerIndex: number): string {
  return "border-sky-200/45 bg-gradient-to-r from-sky-200/80 via-sky-50/45 to-blue-100/35 ring-sky-200/32";
}

const SUPPLIER_DETAIL_CARD_RINGS: readonly string[] = [
  "ring-slate-200/55",
  "ring-sky-200/62",
  "ring-emerald-200/56",
  "ring-violet-200/54",
];

function supplierDetailCardRingClass(offerIndex: number): string {
  return SUPPLIER_DETAIL_CARD_RINGS[
    offerIndex % SUPPLIER_DETAIL_CARD_RINGS.length
  ]!;
}

/** Mekrs: jeden riadok — krátky názov (tmavosivý) + množstvo v balení, bez „Nelze rozbalit“ atď. */
function mekrsVariantNameAndPackLine(
  label: string | null | undefined,
  packQuantity: number | null | undefined,
): { name: string; packText: string } {
  const raw = (label ?? "").replace(/\r\n/g, "\n").trim();
  const cleaned = raw
    .replace(/\n+/g, " ")
    .replace(/\s+/g, " ")
    .replace(/\s*·\s*Nelze rozbalit.*/gi, "")
    .replace(/\s*Nelze rozbalit.*/gi, "")
    .trim();
  const segs = cleaned.split(/\s*—\s*/).map((p) => p.trim()).filter(Boolean);
  let name = (segs[0] ?? cleaned).trim();

  let pq: number | null =
    typeof packQuantity === "number" &&
    Number.isFinite(packQuantity) &&
    packQuantity >= 1
      ? Math.floor(packQuantity)
      : null;
  if (pq == null && segs.length > 1) {
    const m = segs.slice(1).join(" ").match(/(\d[\d\s]*)\s*ks\b/i);
    if (m) {
      const n = parseInt(m[1].replace(/\s/g, ""), 10);
      if (Number.isFinite(n) && n >= 1) pq = n;
    }
  }
  if (pq == null) {
    const m = cleaned.match(/(\d[\d\s]*)\s*ks\b/i);
    if (m) {
      const n = parseInt(m[1].replace(/\s/g, ""), 10);
      if (Number.isFinite(n) && n >= 1) pq = n;
    }
  }

  const packText = pq != null ? formatKsQuantity(pq) : "";

  if (pq != null) {
    name = name
      .replace(
        new RegExp(
          `\\s*[-–—]?\\s*Balen[ií]\\s*\\(\\s*${pq}\\s*ks\\s*\\)`,
          "gi",
        ),
        "",
      )
      .replace(new RegExp(`\\(\\s*${pq}\\s*ks\\s*\\)\\s*$`, "i"), "")
      .replace(/\s*—\s*$/u, "")
      .trim();
  }

  return { name: name || cleaned, packText };
}

function MekrsVariantLabelCell({
  label,
  packQuantity,
  packageStockText,
}: {
  label?: string | null;
  packQuantity?: number | null;
  packageStockText?: string | null;
}) {
  const { name, packText } = mekrsVariantNameAndPackLine(label, packQuantity);
  const pkgLine = (packageStockText ?? "").trim();
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
      <span
        className="min-w-0 max-w-[min(100%,11rem)] truncate text-[11px] font-medium leading-tight text-slate-600 sm:max-w-[15rem]"
        title={name}
      >
        {name}
      </span>
      {packText ? (
        <span className="shrink-0 whitespace-nowrap text-xs font-semibold tabular-nums text-slate-900">
          {packText}
        </span>
      ) : null}
      {pkgLine ? (
        <span
          className="min-w-0 max-w-full text-[11px] font-normal leading-tight text-slate-600"
          title={pkgLine}
        >
          {pkgLine}
        </span>
      ) : null}
    </div>
  );
}

function mekrsEffectivePackageStockText(
  row: PackagingVariantRow | null | undefined,
  allRows: PackagingVariantRow[] | null | undefined,
  totalStock: number | null | undefined,
): string | null {
  const direct = (row?.mekrs_package_stock_text ?? "").trim();
  if (direct) return direct;
  if (!row || !Array.isArray(allRows) || allRows.length < 2) return null;
  if (typeof totalStock !== "number" || !Number.isFinite(totalStock) || totalStock < 1) {
    return null;
  }
  const pq = row.pack_quantity;
  if (typeof pq !== "number" || !Number.isFinite(pq) || pq < 2) return null;
  const packageRows = allRows.filter((r) => {
    const q = r.pack_quantity;
    return typeof q === "number" && Number.isFinite(q) && q >= 2;
  });
  if (packageRows.length < 2) return null;
  const packQs = Array.from(
    new Set(
      packageRows
        .map((r) => r.pack_quantity)
        .filter(
          (q): q is number =>
            typeof q === "number" && Number.isFinite(q) && q >= 2,
        )
        .map((q) => Math.trunc(q)),
    ),
  ).sort((a, b) => b - a);
  if (packQs.length < 2) return null;
  const t = Math.trunc(totalStock);
  const base = packQs[0];
  const q = Math.trunc(pq);
  const remainder = t % base;
  if (q === base) {
    const n = Math.floor(t / base);
    if (n < 1) return null;
    return `Skladem ${formatIntegerCsThousands(n)} balení`;
  }
  if (remainder > 0 && q === remainder) {
    return "Skladem 1 balení";
  }
  return null;
}

/** Haspl: rovnaká typografia ako Mekrs — dlhý názov (11px slate-600) + ks v balení (semibold). */
function HasplVariantLabelCell({
  label,
  packQuantity,
  rawPackQuantity,
}: {
  label?: string | null;
  packQuantity?: number | null;
  rawPackQuantity?: string | null;
}) {
  const name = (label ?? "").replace(/\s+/g, " ").trim() || "—";
  let packText = "";
  const pq =
    typeof packQuantity === "number" &&
    Number.isFinite(packQuantity) &&
    packQuantity >= 1
      ? Math.floor(packQuantity)
      : null;
  if (pq != null) {
    packText = formatKsQuantity(pq);
  } else {
    const raw = rawPackQuantity?.trim();
    if (raw) {
      const m = raw.replace(/\s/g, "").match(/^(\d+)/);
      if (m) {
        const n = parseInt(m[1], 10);
        if (Number.isFinite(n) && n >= 1) {
          packText = formatKsQuantity(n);
        }
      }
    }
  }
  return (
    <div className="flex min-w-0 flex-nowrap items-baseline gap-x-1.5">
      <span
        className="min-w-0 max-w-[min(100%,11rem)] truncate text-[11px] font-medium leading-tight text-slate-600 sm:max-w-[15rem]"
        title={name}
      >
        {name}
      </span>
      {packText ? (
        <span className="shrink-0 whitespace-nowrap text-xs font-semibold tabular-nums text-slate-900">
          {packText}
        </span>
      ) : null}
    </div>
  );
}

function cartStorageKey(
  supplierId: number,
  supplierCode: string,
  packagingVariantIndex: number | null,
): string {
  const base = `${supplierId}:${supplierCode}`;
  if (packagingVariantIndex != null && packagingVariantIndex >= 0) {
    return `${base}:v:${packagingVariantIndex}`;
  }
  return base;
}

function effectiveCartQty(
  cartKey: string,
  cartMap: Record<string, number>,
  pack: number | null,
): number {
  const stored = cartMap[cartKey];
  if (stored !== undefined) return stored;
  if (pack != null && pack >= 1) return pack;
  return 1;
}

/** Násobok balenia: najbližší vyšší (alebo rovný) násobok hodnoty pack. */
function snapToPackQuantity(raw: number, pack: number | null): number {
  const p = pack != null && pack >= 1 ? pack : null;
  let q = Math.floor(raw);
  if (!Number.isFinite(q)) {
    q = p ?? 1;
  }
  if (p == null) {
    return Math.min(Math.max(1, q), 999_999);
  }
  if (q < p) {
    q = p;
  }
  const snapped = Math.ceil(q / p) * p;
  return Math.min(snapped, 999_999);
}

/** +/- pri košíku: krok = veľkosť balenia (násobok), alebo 1 ks. */
function bumpCartQuantity(
  current: number,
  pack: number | null,
  direction: 1 | -1,
): number {
  const p = pack != null && pack >= 1 ? pack : null;
  const cur = Math.floor(Number.isFinite(current) ? current : (p ?? 1));
  if (p == null) {
    const n = cur + direction;
    return Math.min(999_999, Math.max(1, n));
  }
  if (direction > 0) {
    return Math.min(999_999, cur + p);
  }
  return Math.max(p, cur - p);
}

/** Veľkosť balenia z API alebo prvé číslo z raw_pack_quantity (value z inputu). */
function packSizeFromScrape(
  scrape: SupplierScrapeState | undefined,
): number | null {
  if (!scrape || scrape.loading) return null;
  if (scrape.pack_quantity != null && scrape.pack_quantity >= 1) {
    return scrape.pack_quantity;
  }
  const raw = scrape.raw_pack_quantity?.trim();
  if (!raw) return null;
  const m = raw.replace(/\s/g, "").match(/^(\d+)/);
  if (!m) return null;
  const n = parseInt(m[1], 10);
  return Number.isFinite(n) && n >= 1 ? n : null;
}

type ProductSupplierExpandedTableRowProps = {
  product: ProductSearchRow;
  colSpan: number;
  scrapeByKey: Record<string, SupplierScrapeState>;
  cartQuantityByKey: Record<string, number>;
  setCartQuantityByKey: Dispatch<SetStateAction<Record<string, number>>>;
  packVariantIndexByKey: Record<string, number>;
  setPackVariantIndexByKey: Dispatch<SetStateAction<Record<string, number>>>;
  cartFeedback: Record<string, string>;
  cartAddSuccessByKey: Record<string, boolean>;
  offerNotesByKey: Record<string, string>;
  setOfferNotesByKey: Dispatch<SetStateAction<Record<string, string>>>;
  onRequestAddToOffer?: (payload: AddToOfferPayload) => void;
  addToCart: (
    supplierId: number,
    supplierCode: string,
    supplierName: string,
    quantity: number,
    packagingVariantIndex?: number | null,
    historyMeta?: AddToCartHistoryMeta | null,
    mekrsProductVariantId?: string | null,
    hopefixProductId?: string | null,
    hopefixPackageType?: string | null,
    hopefixRefererPath?: string | null,
    hasplVariantCode?: string | null,
    inoxmareProductId?: string | null,
    inoxmareRefererPath?: string | null,
  ) => Promise<void>;
};

function ProductSupplierExpandedTableRow({
  product,
  colSpan,
  scrapeByKey,
  cartQuantityByKey,
  setCartQuantityByKey,
  packVariantIndexByKey,
  setPackVariantIndexByKey,
  cartFeedback,
  cartAddSuccessByKey,
  offerNotesByKey,
  setOfferNotesByKey,
  onRequestAddToOffer,
  addToCart,
}: ProductSupplierExpandedTableRowProps) {
  return (
                            <tr className="bg-slate-50/80">
                              <td className="px-2.5 py-2 sm:px-3 sm:py-2.5" colSpan={colSpan}>
                                <div className="overflow-hidden rounded-lg border border-slate-200/90 bg-white shadow-sm ring-1 ring-slate-100/80">
                                  <div className="border-b border-slate-200/70 bg-gradient-to-r from-slate-50 via-white to-sky-50/35 px-2.5 py-1.5 sm:px-3 sm:py-2">
                                    <p
                                      className="inline-block max-w-full cursor-help text-[10px] font-semibold uppercase tracking-wider text-sky-900/70 underline decoration-dotted decoration-slate-400/70 underline-offset-2 sm:text-[11px]"
                                      title="Po otvorení riadku sa pre každého dodávateľa spustí Playwright (cena / sklad podľa JSON selektorov). Kód dodávateľa je z mapovania v databáze."
                                    >
                                      Detail dodávateľov
                                    </p>
                                  </div>
                                  <div className="flex flex-col gap-2 p-2 sm:gap-2 sm:p-2.5">
                                    {product.offers.map((offer, offerIndex) => {
                                      const scrape =
                                        offer.supplier_id != null
                                          ? scrapeByKey[
                                              scrapeCacheKey(
                                                product.internal_code,
                                                offer.supplier_id,
                                              )
                                            ]
                                          : undefined;
                                      const scrapePv0 =
                                        scrape &&
                                        !scrape.loading &&
                                        Array.isArray(scrape.packaging_variants) &&
                                        scrape.packaging_variants.length >= 1
                                          ? scrape.packaging_variants[0]
                                          : undefined;
                                      const mekrsNoStock =
                                        supplierNameIsMekrs(offer.supplier) &&
                                        mekrsIsOutOfStock(scrape);
                                      const hopefixNoStock =
                                        supplierNameIsHopefix(offer.supplier) &&
                                        hopefixIsOutOfStock(scrape);
                                      const displayPrice =
                                        mekrsNoStock
                                          ? null
                                          : hopefixNoStock
                                            ? 0
                                            : scrape &&
                                                !scrape.loading &&
                                                scrape.price_eur != null
                                              ? scrape.price_eur
                                              : scrape &&
                                                  !scrape.loading &&
                                                  scrapePv0?.price_eur != null
                                                ? scrapePv0.price_eur
                                                : offer.price_eur;
                                      const displayStock =
                                        scrape &&
                                        !scrape.loading &&
                                        scrape.stock != null
                                          ? scrape.stock
                                          : scrape &&
                                              !scrape.loading &&
                                              scrapePv0?.stock != null
                                            ? scrapePv0.stock
                                            : offer.stock;
                                      const hasLiveStockQty =
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            (scrape.stock != null ||
                                              scrapePv0?.stock != null),
                                        );
                                      const hasLiveStockText =
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            scrape.stock == null &&
                                            scrapePv0?.stock == null &&
                                            (Boolean(scrape.raw_stock?.trim()) ||
                                              Boolean(
                                                (scrapePv0?.raw_stock || "").trim(),
                                              )),
                                        );
                                      const stockLive =
                                        hasLiveStockQty || hasLiveStockText;
                                      const stockDisplayText =
                                        scrape &&
                                        !scrape.loading &&
                                        scrape.stock != null
                                          ? formatKsQuantity(scrape.stock)
                                          : scrape &&
                                              !scrape.loading &&
                                              scrapePv0?.stock != null
                                            ? formatKsQuantity(scrapePv0.stock)
                                            : scrape &&
                                                !scrape.loading &&
                                                scrape.raw_stock?.trim()
                                              ? formatDigitsInTextCsThousands(
                                                  scrape.raw_stock.trim(),
                                                )
                                              : scrape &&
                                                  !scrape.loading &&
                                                  (scrapePv0?.raw_stock || "").trim()
                                                ? formatDigitsInTextCsThousands(
                                                    (scrapePv0?.raw_stock || "").trim(),
                                                  )
                                                : typeof displayStock === "number" &&
                                                    Number.isFinite(displayStock)
                                                  ? formatKsQuantity(displayStock)
                                                  : `${displayStock} ks`;
                                      const hasLivePriceSignal = Boolean(
                                        scrape &&
                                          !scrape.loading &&
                                          !scrape.error &&
                                          (scrape.price_eur != null ||
                                            Boolean(
                                              (scrape.raw_price || "").trim(),
                                            ) ||
                                            Boolean(
                                              scrapePv0 &&
                                                (scrapePv0.price_eur != null ||
                                                  Boolean(
                                                    (scrapePv0.raw_price || "").trim(),
                                                  )),
                                            )),
                                      );
                                      const priceLive =
                                        Boolean(
                                          hasLivePriceSignal && !mekrsNoStock,
                                        );
                                      const sid = offer.supplier_id;
                                      const scode = offer.supplier_code?.trim() ?? "";
                                      const cartKey =
                                        sid != null && scode
                                          ? cartStorageKey(sid, scode, null)
                                          : "";
                                      const packSize = packSizeFromScrape(scrape);
                                      const canCart =
                                        offer.supplier_id != null &&
                                        Boolean(offer.supplier_code?.trim());
                                      const pvars = scrape?.packaging_variants;
                                      const multiPack =
                                        Boolean(
                                          Array.isArray(pvars) &&
                                            pvars.length > 1 &&
                                            scrape &&
                                            !scrape.loading,
                                        );
                                      const mekrsHttpVariants =
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            supplierNameIsMekrs(
                                              offer.supplier,
                                            ) &&
                                            Array.isArray(pvars) &&
                                            pvars.length >= 1 &&
                                            pvars.some((pv) =>
                                              Boolean(
                                                (pv.mekrs_variant_id || "").trim(),
                                              ),
                                            ),
                                        );
                                      const hopefixHttpVariants =
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            supplierNameIsHopefix(
                                              offer.supplier,
                                            ) &&
                                            Array.isArray(pvars) &&
                                            pvars.length >= 1 &&
                                            pvars.some((pv) =>
                                              Boolean(
                                                (pv.hopefix_product_id || "").trim(),
                                              ),
                                            ),
                                        );
                                      const hasplHttpVariants =
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            supplierNameIsHaspl(
                                              offer.supplier,
                                            ) &&
                                            Array.isArray(pvars) &&
                                            pvars.length >= 1 &&
                                            pvars.some((pv) =>
                                              Boolean(
                                                (pv.haspl_variant_code || "").trim(),
                                              ),
                                            ),
                                        );
                                      const inoxmareHttpVariants =
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            supplierNameIsInoxmare(
                                              offer.supplier,
                                            ) &&
                                            Array.isArray(pvars) &&
                                            pvars.length >= 1 &&
                                            pvars.some(
                                              (pv) =>
                                                Boolean(
                                                  (
                                                    pv.inoxmare_product_id || ""
                                                  ).trim(),
                                                ) &&
                                                Boolean(
                                                  (
                                                    pv.inoxmare_referer_path ||
                                                    ""
                                                  ).trim(),
                                                ),
                                            ),
                                        );
                                      const argipHttpVariants =
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            supplierNameIsArgip(
                                              offer.supplier,
                                            ) &&
                                            Array.isArray(pvars) &&
                                            pvars.length >= 1 &&
                                            pvars.some((pv) =>
                                              Boolean(
                                                (pv.argip_sku || "").trim(),
                                              ),
                                            ),
                                        );
                                      const usesHttpCartVariants =
                                        mekrsHttpVariants ||
                                        hopefixHttpVariants ||
                                        hasplHttpVariants ||
                                        inoxmareHttpVariants ||
                                        argipHttpVariants;
                                      /** Hopefix: HTTP môže vrátiť variant bez product_id — stále treba riadok pre živú cenu/sklad. */
                                      const hopefixHasPackagingFromScrape =
                                        supplierNameIsHopefix(
                                          offer.supplier,
                                        ) &&
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            !scrape.error &&
                                            Array.isArray(pvars) &&
                                            pvars.length >= 1,
                                        );
                                      const showPackSelector =
                                        multiPack ||
                                        usesHttpCartVariants ||
                                        hopefixHasPackagingFromScrape;
                                      const selViRaw =
                                        cartKey && showPackSelector && pvars
                                          ? (packVariantIndexByKey[cartKey] ?? 0)
                                          : 0;
                                      const selVi =
                                        showPackSelector && pvars
                                          ? Math.min(
                                              Math.max(0, selViRaw),
                                              pvars.length - 1,
                                            )
                                          : 0;
                                      const activePv =
                                        showPackSelector && pvars
                                          ? pvars[selVi]
                                          : null;
                                      /** Mekrs: len súhrnný sklad (API už neposiela ks po variantoch). */
                                      const mekrsStockSummaryOnly =
                                        supplierNameIsMekrs(offer.supplier);
                                      const hasplStockTextPerRow =
                                        supplierNameIsHaspl(offer.supplier);
                                      const rowPrice =
                                        hopefixNoStock
                                          ? 0
                                          : mekrsNoStock
                                            ? null
                                            : activePv?.price_eur != null
                                              ? activePv.price_eur
                                              : displayPrice;
                                      const rowPriceSymbol =
                                        activePv?.currency_symbol?.trim() ||
                                        scrape?.currency_symbol?.trim() ||
                                        "€";
                                      const rowPriceUnit =
                                        activePv?.price_unit?.trim() ||
                                        scrape?.price_unit?.trim() ||
                                        null;
                                      const rowPriceSuffix = scrapePriceUnitSuffix(
                                        offer.supplier,
                                        rowPriceUnit,
                                        true,
                                      );
                                      const rowStockText =
                                        mekrsStockSummaryOnly
                                          ? stockDisplayText
                                          : hasplStockTextPerRow && activePv
                                            ? activePv.raw_stock?.trim() || "—"
                                            : usesHttpCartVariants && activePv
                                              ? activePv.stock != null
                                                ? formatKsQuantity(activePv.stock)
                                                : activePv.raw_stock?.trim()
                                                  ? formatDigitsInTextCsThousands(
                                                      activePv.raw_stock.trim(),
                                                    )
                                                  : "—"
                                              : stockDisplayText;
                                      /** Hopefix/Mekrs HTTP: pri OOS môže mať súhrn 0 € / 0 ks len na scrape, nie v riadku variantu. */
                                      const rowPriceLive =
                                        showPackSelector && activePv
                                          ? Boolean(
                                              scrape &&
                                                !scrape.loading &&
                                                !scrape.error &&
                                                !mekrsNoStock &&
                                                (activePv.price_eur != null ||
                                                  Boolean(
                                                    (activePv.raw_price || "")
                                                      .trim(),
                                                  ) ||
                                                  (supplierNameIsHopefix(
                                                    offer.supplier,
                                                  ) &&
                                                    hasLivePriceSignal)),
                                            )
                                          : priceLive;
                                      const hasLiveStockSignalSummary =
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            !scrape.error &&
                                            (scrape.stock != null ||
                                              Boolean(
                                                (scrape.raw_stock || "").trim(),
                                              ) ||
                                              (scrapePv0 != null &&
                                                (scrapePv0.stock != null ||
                                                  Boolean(
                                                    (scrapePv0.raw_stock || "")
                                                      .trim(),
                                                  )))),
                                        );
                                      const rowStockLive =
                                        mekrsStockSummaryOnly
                                          ? stockLive
                                          : hasplStockTextPerRow && activePv
                                            ? Boolean(activePv.raw_stock?.trim())
                                            : usesHttpCartVariants && activePv
                                              ? activePv.stock != null ||
                                                Boolean(
                                                  activePv.raw_stock?.trim(),
                                                ) ||
                                                (supplierNameIsHopefix(
                                                  offer.supplier,
                                                ) &&
                                                  hasLiveStockSignalSummary)
                                              : stockLive;
                                      const scraperApplicable =
                                        offer.supplier_id != null &&
                                        Boolean(offer.supplier_code?.trim());
                                      const priceUiLoading = Boolean(
                                        scraperApplicable && scrape?.loading,
                                      );
                                      const stockUiLoading = priceUiLoading;
                                      const offerLiveOutOfStock =
                                        scraperApplicable &&
                                        !stockUiLoading &&
                                        cartRowLiveOutOfStock(
                                          scrape,
                                          offer.supplier,
                                          usesHttpCartVariants && activePv
                                            ? activePv
                                            : null,
                                          usesHttpCartVariants,
                                        );
                                      /** Prihlásený scrape, ale v odpovedi nie je cena ani sklad (napr. riadok v katalógu sa nenašiel). */
                                      const liveScrapeMissingOffer =
                                        scraperApplicable &&
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            !scrape.error &&
                                            scrape.logged_in === true &&
                                            !hasLivePriceSignal &&
                                            !hasLiveStockSignalSummary &&
                                            supplierShowsScrapeLoginBadge(
                                              offer.supplier,
                                            ),
                                        );
                                      const offerStockUiBlocked =
                                        offerLiveOutOfStock ||
                                        liveScrapeMissingOffer;
                                      /** Hopefix HTTP: bez product_id API košík nepridá; sklad/cena môžu byť z katalógu bez košíka v HTML. */
                                      const hopefixActiveMissingProductId =
                                        supplierNameIsHopefix(offer.supplier) &&
                                        scraperApplicable &&
                                        Boolean(
                                          scrape &&
                                            !scrape.loading &&
                                            !scrape.error,
                                        ) &&
                                        activePv != null &&
                                        !String(
                                          activePv.hopefix_product_id ?? "",
                                        ).trim();
                                      const offerCartUiBlocked =
                                        offerStockUiBlocked ||
                                        hopefixActiveMissingProductId;
                                      const hopefixPriceStockIncomplete =
                                        hopefixActiveMissingProductId &&
                                        !offerLiveOutOfStock;
                                      const faboryStockCls =
                                        supplierNameIsFabory(offer.supplier)
                                          ? faboryStockDisplayClass(
                                              String(rowStockText),
                                            )
                                          : undefined;
                                      const stockSummaryText = offerStockUiBlocked
                                        ? liveScrapeMissingOffer
                                          ? displayStock != null &&
                                              Number.isFinite(displayStock)
                                            ? `${formatKsQuantity(displayStock)} · katalóg`
                                            : "Ponuka sa nenašla"
                                          : "Nie je na sklade"
                                        : rowStockText;
                                      const stockSummaryClass = cn(
                                        faboryStockCls,
                                        offerLiveOutOfStock &&
                                          "font-semibold text-red-600",
                                        liveScrapeMissingOffer &&
                                          !offerLiveOutOfStock &&
                                          "font-medium text-amber-800",
                                        hopefixPriceStockIncomplete &&
                                          "font-medium text-amber-800",
                                      );
                                      const rowPack =
                                        activePv != null &&
                                        activePv.pack_quantity != null &&
                                        activePv.pack_quantity >= 1
                                          ? activePv.pack_quantity
                                          : packSize;
                                      const argipShopPackQty =
                                        supplierNameIsArgip(offer.supplier) &&
                                        activePv != null &&
                                        activePv.shop_pack_quantity != null &&
                                        activePv.shop_pack_quantity >= 1
                                          ? activePv.shop_pack_quantity
                                          : supplierNameIsArgip(offer.supplier) &&
                                              scrape?.shop_pack_quantity != null &&
                                              scrape.shop_pack_quantity >= 1
                                            ? scrape.shop_pack_quantity
                                            : null;
                                      const faboryTitleFromPdp =
                                        supplierNameIsFabory(offer.supplier) &&
                                        scrape &&
                                        !scrape.loading
                                          ? (scrape.product_title || "").trim()
                                          : "";
                                      const faboryLineLabelRaw =
                                        supplierNameIsFabory(offer.supplier) &&
                                        Array.isArray(pvars) &&
                                        pvars.length >= 1
                                          ? (
                                              pvars[
                                                Math.min(
                                                  selVi,
                                                  pvars.length - 1,
                                                )
                                              ]?.label ||
                                              pvars[0]?.label ||
                                              ""
                                            ).trim()
                                          : "";
                                      const faboryLineLabel = faboryTitleFromPdp
                                        ? faboryTitleFromPdp
                                        : faboryLineLabelRaw
                                          ? faboryUiProductTitleOnly(faboryLineLabelRaw)
                                          : "";
                                      const supplierProductTitle =
                                        scrape && !scrape.loading
                                          ? (scrape.product_title || "").trim()
                                          : "";
                                      const supplierHeader = (
                                        <div
                                          className={cn(
                                            "flex min-w-0 flex-1 flex-wrap items-start gap-0.5 rounded-md border p-1 shadow-sm ring-1 sm:gap-1.5 sm:p-1.5 sm:flex-nowrap sm:items-center",
                                            supplierDetailHeaderBarClass(offerIndex),
                                          )}
                                        >
                                          <div
                                            className="flex h-6 w-6 shrink-0 items-center justify-center overflow-hidden rounded-md border border-slate-200/90 bg-white shadow-sm ring-1 ring-slate-100/60 sm:h-9 sm:w-9"
                                            title="Logo dodávateľa"
                                          >
                                            {publicApiAssetUrl(offer.logo_url) ? (
                                              // eslint-disable-next-line @next/next/no-img-element
                                              <img
                                                src={
                                                  publicApiAssetUrl(
                                                    offer.logo_url,
                                                  )!
                                                }
                                                alt=""
                                                className="h-full w-full object-contain p-0.5"
                                              />
                                            ) : (
                                              <span className="px-0.5 text-[8px] font-medium uppercase tracking-wide text-slate-400">
                                                —
                                              </span>
                                            )}
                                          </div>
                                          <div className="min-w-0 flex-1">
                                            <div className="flex flex-wrap items-center gap-x-1 gap-y-0.5">
                                              <p className="min-w-0 max-w-[7.4rem] truncate text-[10px] font-semibold leading-tight text-slate-900 sm:max-w-[13rem] sm:text-[13px]">
                                                {offer.supplier}
                                              </p>
                                              {supplierShowsScrapeLoginBadge(
                                                offer.supplier,
                                              ) &&
                                              scrape &&
                                              !scrape.loading &&
                                              typeof scrape.logged_in ===
                                                "boolean" ? (
                                                <span
                                                  className={cn(
                                                    "shrink-0 rounded-full border px-1.5 py-px text-[9px] font-semibold shadow-sm sm:text-[10px]",
                                                    scrape.logged_in
                                                      ? "border-emerald-200/80 bg-emerald-50 text-emerald-900"
                                                      : "border-rose-200/80 bg-rose-50 text-rose-900",
                                                  )}
                                                >
                                                  {scrape.logged_in
                                                    ? "Prihlásený"
                                                    : "Neprihlásený"}
                                                </span>
                                              ) : null}
                                            </div>
                                            <div className="flex items-center gap-1 text-[9px] leading-tight text-slate-500 sm:text-[11px]">
                                              <span className="shrink-0">Kód:</span>
                                              <span className="truncate">
                                                {offer.supplier_code?.trim()
                                                  ? offer.supplier_code
                                                  : "—"}
                                              </span>
                                              {offer.supplier_product_url?.trim() ? (
                                                <button
                                                  type="button"
                                                  title="Otvoriť e-shop dodávateľa (kód sa skopíruje do schránky)"
                                                  className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border border-slate-300/80 bg-white/90 text-slate-600 transition hover:border-sky-300 hover:text-sky-700 sm:h-4.5 sm:w-4.5"
                                                  onClick={(event) => {
                                                    event.stopPropagation();
                                                    const code =
                                                      offer.supplier_code?.trim() || "";
                                                    if (code && navigator?.clipboard?.writeText) {
                                                      void navigator.clipboard
                                                        .writeText(code)
                                                        .catch(() => undefined);
                                                    }
                                                    const target = supplierSafeExternalUrl(
                                                      offer.supplier_product_url,
                                                    );
                                                    if (target) {
                                                      window.open(
                                                        target,
                                                        "_blank",
                                                        "noopener,noreferrer",
                                                      );
                                                    }
                                                  }}
                                                >
                                                  <ExternalLink className="h-2.5 w-2.5 sm:h-3 sm:w-3" />
                                                </button>
                                              ) : null}
                                            </div>
                                          </div>
                                          {sid != null ? (
                                            <div className="ml-auto flex w-auto shrink-0 items-center justify-end gap-1 self-center">
                                              {onRequestAddToOffer &&
                                              displayPrice != null &&
                                              Number.isFinite(displayPrice) &&
                                              displayPrice > 0 ? (
                                                <button
                                                  type="button"
                                                  title="Pridať do cenovej ponuky"
                                                  className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border border-sky-300/90 bg-sky-50 text-sky-700 transition hover:bg-sky-100 sm:h-6 sm:w-6"
                                                  onClick={(event) => {
                                                    event.stopPropagation();
                                                    onRequestAddToOffer({
                                                      internal_code: product.internal_code,
                                                      product_id: product.product_id,
                                                      supplier_id: sid,
                                                      supplier_name: offer.supplier,
                                                      supplier_code:
                                                        offer.supplier_code ?? null,
                                                      purchase_price_eur: displayPrice,
                                                    });
                                                  }}
                                                >
                                                  <Plus className="h-3 w-3 sm:h-3.5 sm:w-3.5" />
                                                </button>
                                              ) : null}
                                              <label
                                                className="sr-only"
                                                htmlFor={`offer-note-${product.internal_code}-${sid}-${offerIndex}`}
                                              >
                                                Poznámka k ponuke
                                              </label>
                                              <input
                                                id={`offer-note-${product.internal_code}-${sid}-${offerIndex}`}
                                                type="text"
                                                value={
                                                  offerNotesByKey[
                                                    offerNoteStorageKey(
                                                      product.internal_code,
                                                      sid,
                                                    )
                                                  ] ?? ""
                                                }
                                                onChange={(event) => {
                                                  const nk = offerNoteStorageKey(
                                                    product.internal_code,
                                                    sid,
                                                  );
                                                  const v = event.target.value;
                                                  setOfferNotesByKey((prev) => {
                                                    const next = { ...prev };
                                                    if (!v.trim()) {
                                                      delete next[nk];
                                                    } else {
                                                      next[nk] = v;
                                                    }
                                                    return next;
                                                  });
                                                }}
                                                spellCheck={true}
                                                placeholder="Poznámka"
                                                title="Uloží sa v prehliadači; pri Košíku sa skopíruje do záznamu."
                                                className="h-5 w-[5.8rem] rounded border border-slate-200/90 bg-white px-1 text-[9px] leading-tight text-slate-800 shadow-sm placeholder:text-slate-400 focus:border-sky-400 focus:outline-none focus:ring-1 focus:ring-sky-300/55 sm:h-6 sm:w-[9rem] sm:px-1.5 sm:text-[11px]"
                                              />
                                            </div>
                                          ) : null}
                                        </div>
                                      );
                                      return (
                                        <div
                                          key={`${product.internal_code}-${offer.supplier}-${offer.supplier_code ?? offerIndex}`}
                                          className={cn(
                                            "min-w-0 rounded-md border border-slate-200/80 bg-gradient-to-b from-white via-slate-50/25 to-slate-100/35 p-1 shadow-sm ring-1 sm:rounded-lg sm:p-2",
                                            supplierDetailCardRingClass(offerIndex),
                                          )}
                                        >
                                          <div className="flex flex-col gap-1 lg:flex-row lg:items-start lg:gap-3">
                                            <div className="flex min-w-0 flex-1 flex-col gap-0.5 sm:gap-1">
                                              {supplierHeader}
                                              {supplierProductTitle &&
                                              !(supplierNameIsFabory(offer.supplier) && !showPackSelector) ? (
                                                <p
                                                  className="min-w-0 break-words text-[9px] font-normal leading-snug text-slate-600 sm:text-[11px]"
                                                  title={supplierProductTitle}
                                                >
                                                  {supplierProductTitle}
                                                </p>
                                              ) : null}
                                              {faboryLineLabel &&
                                              !showPackSelector ? (
                                                <p
                                                  className="min-w-0 break-words text-[9px] font-normal leading-snug text-slate-600 sm:text-[11px]"
                                                  title={faboryLineLabel}
                                                >
                                                  {faboryLineLabel}
                                                </p>
                                              ) : null}
                                              {usesHttpCartVariants &&
                                              pvars &&
                                              cartKey ? (
                                                <div className="max-w-2xl overflow-hidden overflow-x-auto rounded-md border border-slate-200/90 bg-white shadow-sm ring-1 ring-slate-100/60">
                                                  <table className="w-full table-fixed border-collapse text-left text-xs sm:text-[13px]">
                                                    <colgroup>
                                                      <col className="w-[52%]" />
                                                      <col className="w-[22%]" />
                                                      {!supplierNameIsMekrs(
                                                        offer.supplier,
                                                      ) ? (
                                                        <col className="w-[18%]" />
                                                      ) : null}
                                                      <col
                                                        className={
                                                          supplierNameIsMekrs(
                                                            offer.supplier,
                                                          )
                                                            ? "w-[26%]"
                                                            : "w-[8%]"
                                                        }
                                                      />
                                                    </colgroup>
                                                    <thead>
                                                      <tr className="border-b border-slate-200/80 bg-slate-100/70 text-[9px] uppercase tracking-wide text-slate-600 sm:text-[10px]">
                                                        <th className="px-1.5 py-1 font-medium sm:px-2 sm:py-1.5">
                                                          Balenie
                                                        </th>
                                                        <th className="px-1.5 py-1 font-medium sm:px-2 sm:py-1.5">
                                                          Cena
                                                        </th>
                                                        {!supplierNameIsMekrs(
                                                          offer.supplier,
                                                        ) ? (
                                                          <th className="px-1.5 py-1 font-medium sm:px-2 sm:py-1.5">
                                                            Sklad
                                                          </th>
                                                        ) : null}
                                                        <th className="w-8 px-1 py-1 font-medium sm:w-10 sm:px-1.5">
                                                          &nbsp;
                                                        </th>
                                                      </tr>
                                                    </thead>
                                                    <tbody>
                                                      {pvars.map((pv, vi) => {
                                                        const groupName = `pack-var-${product.internal_code}-${offer.supplier_id}-${offerIndex}`;
                                                        const inputId = `${groupName}-tbl-${vi}`;
                                                        const picked = selVi === vi;
                                                        const pickVariant = () => {
                                                          if (!cartKey) return;
                                                          setPackVariantIndexByKey(
                                                            (prev) => ({
                                                              ...prev,
                                                              [cartKey]: vi,
                                                            }),
                                                          );
                                                          const pk =
                                                            pvars[vi]
                                                              ?.pack_quantity;
                                                          const p =
                                                            typeof pk ===
                                                              "number" &&
                                                            pk >= 1
                                                              ? pk
                                                              : 1;
                                                          setCartQuantityByKey(
                                                            (prev) => {
                                                              let next: number;
                                                              if (p === 1) {
                                                                next = 1;
                                                              } else {
                                                                const cur =
                                                                  prev[
                                                                    cartKey
                                                                  ] ?? p;
                                                                next =
                                                                  snapToPackQuantity(
                                                                    cur,
                                                                    p,
                                                                  );
                                                              }
                                                              return {
                                                                ...prev,
                                                                [cartKey]: next,
                                                              };
                                                            },
                                                          );
                                                        };
                                                        const stockCell =
                                                          pv.stock != null
                                                            ? formatKsQuantity(
                                                                pv.stock,
                                                              )
                                                            : pv.raw_stock?.trim()
                                                              ? formatDigitsInTextCsThousands(
                                                                  pv.raw_stock.trim(),
                                                                )
                                                              : "—";
                                                        const hideRowStock =
                                                          supplierNameIsMekrs(
                                                            offer.supplier,
                                                          );
                                                        return (
                                                          <tr
                                                            key={vi}
                                                            className={cn(
                                                              "cursor-pointer border-b border-slate-50 last:border-b-0 select-none",
                                                              picked
                                                                ? "bg-sky-50/80 hover:bg-sky-100/70"
                                                                : "hover:bg-slate-50/90",
                                                            )}
                                                            onClick={() =>
                                                              pickVariant()
                                                            }
                                                          >
                                                            <td
                                                              className={cn(
                                                                "min-w-0 break-words px-1 py-0.5 align-middle text-[10px] leading-tight sm:px-2 sm:py-1.5 sm:text-[13px]",
                                                                supplierNameIsMekrs(
                                                                  offer.supplier,
                                                                ) ||
                                                                  supplierUsesHasplStylePackLabel(
                                                                    offer.supplier,
                                                                  ) ||
                                                                  supplierNameIsArgip(
                                                                    offer.supplier,
                                                                  )
                                                                  ? "text-left"
                                                                  : "font-medium leading-snug text-slate-900",
                                                              )}
                                                            >
                                                              {supplierNameIsMekrs(
                                                                offer.supplier,
                                                              ) ? (
                                                                <MekrsVariantLabelCell
                                                                  label={pv.label}
                                                                  packQuantity={
                                                                    pv.pack_quantity
                                                                  }
                                                                  packageStockText={
                                                                    mekrsEffectivePackageStockText(
                                                                      pv,
                                                                      pvars,
                                                                      scrape?.stock,
                                                                    ) ??
                                                                    pv.mekrs_package_stock_text
                                                                  }
                                                                />
                                                              ) : supplierUsesHasplStylePackLabel(
                                                                  offer.supplier,
                                                                ) ? (
                                                                <HasplVariantLabelCell
                                                                  label={pv.label}
                                                                  packQuantity={
                                                                    pv.pack_quantity
                                                                  }
                                                                  rawPackQuantity={
                                                                    pv.raw_pack_quantity
                                                                  }
                                                                />
                                                              ) : supplierNameIsArgip(
                                                                  offer.supplier,
                                                                ) ? (
                                                                <div className="text-[8px] font-normal leading-snug text-slate-600 sm:text-[9px]">
                                                                  {(pv.raw_pack_quantity || "").trim() ||
                                                                    (pv.pack_quantity != null &&
                                                                    pv.pack_quantity >= 1
                                                                      ? `od ${pv.pack_quantity} ks`
                                                                      : `Variant ${vi + 1}`)}
                                                                </div>
                                                              ) : (
                                                                pv.label?.trim() ||
                                                                `Variant ${vi + 1}`
                                                              )}
                                                            </td>
                                                            <td className="px-1 py-0.5 align-middle tabular-nums text-[10px] text-slate-900 sm:px-2 sm:py-1.5 sm:text-[13px]">
                                                              {pv.price_eur !=
                                                              null ? (
                                                                <>
                                                                  {formatScrapePriceAmount(
                                                                    pv.price_eur,
                                                                  )}{" "}
                                                                  {pv.currency_symbol?.trim() ||
                                                                    "€"}
                                                                  <span className="text-[9px] font-normal text-slate-500 sm:text-[11px]">
                                                                    {scrapePriceUnitSuffix(
                                                                      offer.supplier,
                                                                      pv.price_unit ??
                                                                        scrape?.price_unit,
                                                                      false,
                                                                    )}
                                                                  </span>
                                                                </>
                                                              ) : (
                                                                "—"
                                                              )}
                                                            </td>
                                                            {!hideRowStock ? (
                                                              <td
                                                                className={cn(
                                                                  "px-1 py-0.5 align-middle text-[10px] text-slate-800 sm:px-2 sm:py-1.5 sm:text-[13px]",
                                                                  supplierNameIsFabory(
                                                                    offer.supplier,
                                                                  ) &&
                                                                    faboryStockDisplayClass(
                                                                      stockCell,
                                                                    ),
                                                                )}
                                                              >
                                                                {stockCell}
                                                              </td>
                                                            ) : null}
                                                            <td className="px-0.5 py-0.5 align-middle sm:px-1.5 sm:py-1.5">
                                                              <input
                                                                id={inputId}
                                                                type="radio"
                                                                name={groupName}
                                                                checked={picked}
                                                                onChange={
                                                                  pickVariant
                                                                }
                                                                aria-label={`Vybrať variant ${vi + 1}`}
                                                                className="h-3 w-3 border-slate-300 text-sky-600 focus:ring-sky-500 sm:h-3.5 sm:w-3.5"
                                                              />
                                                            </td>
                                                          </tr>
                                                        );
                                                      })}
                                                    </tbody>
                                                  </table>
                                                </div>
                                              ) : multiPack && pvars && cartKey ? (
                                                <div
                                                  className="max-w-lg"
                                                  role="radiogroup"
                                                  aria-labelledby={`pack-var-legend-${product.internal_code}-${offer.supplier_id}-${offerIndex}`}
                                                >
                                                  <p
                                                    id={`pack-var-legend-${product.internal_code}-${offer.supplier_id}-${offerIndex}`}
                                                    className="mb-1 text-[9px] font-semibold uppercase tracking-wider text-slate-500 sm:text-[10px]"
                                                  >
                                                    Balenie (ako na e-shope)
                                                  </p>
                                                  <div className="flex flex-col gap-1">
                                                    {pvars.map((pv, vi) => {
                                                      const groupName = `pack-var-${product.internal_code}-${offer.supplier_id}-${offerIndex}`;
                                                      const inputId = `${groupName}-${vi}`;
                                                      const picked = selVi === vi;
                                                      const pickVariant = () => {
                                                        if (!cartKey) return;
                                                        setPackVariantIndexByKey(
                                                          (prev) => ({
                                                            ...prev,
                                                            [cartKey]: vi,
                                                          }),
                                                        );
                                                        const pk =
                                                          pvars[vi]
                                                            ?.pack_quantity;
                                                        const p =
                                                          typeof pk ===
                                                            "number" &&
                                                          pk >= 1
                                                            ? pk
                                                            : 1;
                                                        setCartQuantityByKey(
                                                          (prev) => {
                                                            let next: number;
                                                            if (p === 1) {
                                                              /** Jednotková položka: vždy 1 ks v poli, nie zvyšok z väčšieho balenia. */
                                                              next = 1;
                                                            } else {
                                                              const cur =
                                                                prev[
                                                                  cartKey
                                                                ] ?? p;
                                                              next =
                                                                snapToPackQuantity(
                                                                  cur,
                                                                  p,
                                                                );
                                                            }
                                                            return {
                                                              ...prev,
                                                              [cartKey]: next,
                                                            };
                                                          },
                                                        );
                                                      };
                                                      return (
                                                        <label
                                                          key={vi}
                                                          htmlFor={inputId}
                                                          className={cn(
                                                            "flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1.5 text-xs shadow-sm transition-colors sm:gap-2 sm:px-2.5 sm:text-[13px]",
                                                            picked
                                                              ? "border-sky-400 bg-sky-50/90 ring-1 ring-sky-200/80"
                                                              : "border-slate-200/90 bg-white hover:border-slate-300 hover:bg-slate-50/50",
                                                          )}
                                                        >
                                                          <input
                                                            id={inputId}
                                                            type="radio"
                                                            name={groupName}
                                                            checked={picked}
                                                            onChange={
                                                              pickVariant
                                                            }
                                                            className="h-3.5 w-3.5 shrink-0 border-slate-300 text-sky-600 focus:ring-sky-500 sm:h-4 sm:w-4"
                                                          />
                                                          <div
                                                            className={cn(
                                                              "min-w-0 flex-1 leading-tight",
                                                              supplierNameIsMekrs(
                                                                offer.supplier,
                                                              ) ||
                                                                supplierUsesHasplStylePackLabel(
                                                                  offer.supplier,
                                                                ) ||
                                                                supplierNameIsArgip(
                                                                  offer.supplier,
                                                                )
                                                                ? ""
                                                                : "text-sm font-semibold text-slate-900",
                                                            )}
                                                          >
                                                            {supplierNameIsMekrs(
                                                              offer.supplier,
                                                            ) ? (
                                                              <MekrsVariantLabelCell
                                                                label={pv.label}
                                                                packQuantity={
                                                                  pv.pack_quantity
                                                                }
                                                                packageStockText={
                                                                  mekrsEffectivePackageStockText(
                                                                    pv,
                                                                    pvars,
                                                                    scrape?.stock,
                                                                  ) ??
                                                                  pv.mekrs_package_stock_text
                                                                }
                                                              />
                                                            ) : supplierUsesHasplStylePackLabel(
                                                                offer.supplier,
                                                              ) ? (
                                                              <HasplVariantLabelCell
                                                                label={pv.label}
                                                                packQuantity={
                                                                  pv.pack_quantity
                                                                }
                                                                rawPackQuantity={
                                                                  pv.raw_pack_quantity
                                                                }
                                                              />
                                                            ) : supplierNameIsArgip(
                                                                offer.supplier,
                                                              ) ? (
                                                              <div className="space-y-0.5">
                                                                <div className="text-[8px] font-normal leading-snug text-slate-600 sm:text-[9px]">
                                                                  {(pv.raw_pack_quantity || "").trim() ||
                                                                    (pv.pack_quantity != null &&
                                                                    pv.pack_quantity >= 1
                                                                      ? `od ${pv.pack_quantity} ks`
                                                                      : `Variant ${vi + 1}`)}
                                                                </div>
                                                                <div className="text-[10px] font-medium tabular-nums text-slate-800 sm:text-[11px]">
                                                                  {pv.price_eur != null &&
                                                                  Number.isFinite(
                                                                    pv.price_eur,
                                                                  ) ? (
                                                                    <>
                                                                      {formatScrapePriceAmount(
                                                                        pv.price_eur,
                                                                      )}{" "}
                                                                      {pv.currency_symbol?.trim() ||
                                                                        "€"}
                                                                      <span className="font-normal text-slate-500">
                                                                        {scrapePriceUnitSuffix(
                                                                          offer.supplier,
                                                                          pv.price_unit ??
                                                                            scrape?.price_unit,
                                                                          false,
                                                                        )}
                                                                      </span>
                                                                    </>
                                                                  ) : (
                                                                    <span className="text-slate-500">
                                                                      Cena —
                                                                    </span>
                                                                  )}
                                                                </div>
                                                              </div>
                                                            ) : (
                                                              pv.label?.trim() ||
                                                              `Variant ${vi + 1}`
                                                            )}
                                                          </div>
                                                        </label>
                                                      );
                                                    })}
                                                  </div>
                                                </div>
                                              ) : null}
                                            </div>
                                            <div className="flex w-full flex-col gap-1 lg:max-w-[min(100%,20rem)] lg:shrink-0 lg:items-stretch">
                                              <div
                                                className={cn(
                                                  "rounded-md border bg-white/95 p-1 shadow-sm ring-1 sm:p-2",
                                                  offerLiveOutOfStock
                                                    ? "border-red-200/90 bg-red-50/45 ring-red-100/70"
                                                    : hopefixPriceStockIncomplete
                                                      ? "border-amber-200/90 bg-amber-50/40 ring-amber-100/70"
                                                      : "border-slate-200/80 ring-slate-100/60",
                                                )}
                                              >
                                              <div className="grid grid-cols-1 gap-1 text-[10px] sm:flex sm:flex-nowrap sm:items-center sm:justify-between sm:gap-x-2 sm:gap-y-0 sm:text-[13px]">
                                                <div
                                                  className={cn(
                                                    "flex min-w-0 items-center justify-between gap-x-2 rounded-md border px-1.5 py-1 text-left sm:flex-1 sm:flex-wrap sm:justify-start sm:gap-x-1 sm:rounded-none sm:border-0 sm:px-0 sm:py-0",
                                                    offerLiveOutOfStock
                                                      ? "border-red-100/90 bg-red-50/60 sm:border-0 sm:bg-transparent"
                                                      : hopefixPriceStockIncomplete
                                                        ? "border-amber-100/90 bg-amber-50/50 sm:border-0 sm:bg-transparent"
                                                        : "border-slate-100 bg-slate-50/70 sm:border-0 sm:bg-transparent",
                                                  )}
                                                >
                                                  <span
                                                    className={cn(
                                                      "mr-0.5 text-[8px] font-semibold uppercase tracking-wider sm:mr-1 sm:text-[10px]",
                                                      offerLiveOutOfStock
                                                        ? "text-red-600"
                                                        : hopefixPriceStockIncomplete
                                                          ? "text-amber-700"
                                                          : "text-slate-500",
                                                    )}
                                                  >
                                                    Cena
                                                  </span>
                                                  <span
                                                    className={cn(
                                                      "tabular-nums",
                                                      offerLiveOutOfStock
                                                        ? "font-medium text-red-600"
                                                        : hopefixPriceStockIncomplete
                                                          ? "font-medium text-amber-800"
                                                          : "text-slate-900",
                                                    )}
                                                  >
                                                    {!scraperApplicable ? (
                                                      <>
                                                        {rowPrice != null &&
                                                        Number.isFinite(rowPrice) ? (
                                                          <>
                                                            {formatScrapePriceAmount(
                                                              rowPrice,
                                                            )}{" "}
                                                            {rowPriceSymbol}
                                                            <span
                                                              className={cn(
                                                                "text-[9px] font-normal sm:text-[11px]",
                                                                offerLiveOutOfStock
                                                                  ? "text-red-500"
                                                                  : hopefixPriceStockIncomplete
                                                                    ? "text-amber-600"
                                                                    : "text-slate-500",
                                                              )}
                                                            >
                                                              {rowPriceSuffix}
                                                            </span>
                                                          </>
                                                        ) : (
                                                          "—"
                                                        )}
                                                      </>
                                                    ) : priceUiLoading ? (
                                                      <Loader2 className="inline h-3.5 w-3.5 animate-spin text-sky-600 align-middle sm:h-4 sm:w-4" />
                                                    ) : rowPrice != null &&
                                                      Number.isFinite(rowPrice) ? (
                                                      <>
                                                        {formatScrapePriceAmount(
                                                          rowPrice,
                                                        )}{" "}
                                                        {rowPriceSymbol}
                                                        <span
                                                          className={cn(
                                                            "text-[9px] font-normal sm:text-[11px]",
                                                            offerLiveOutOfStock
                                                              ? "text-red-500"
                                                              : hopefixPriceStockIncomplete
                                                                ? "text-amber-600"
                                                                : "text-slate-500",
                                                          )}
                                                        >
                                                          {rowPriceSuffix}
                                                        </span>
                                                      </>
                                                    ) : (
                                                      "—"
                                                    )}
                                                  </span>
                                                  {!scraperApplicable ? null : priceUiLoading ? (
                                                    <span className="ml-0.5 text-[9px] text-slate-400 sm:ml-1 sm:text-xs">
                                                      načítavam…
                                                    </span>
                                                  ) : rowPriceLive &&
                                                    !offerLiveOutOfStock &&
                                                    !hopefixActiveMissingProductId ? (
                                                    <span
                                                      className="ml-0.5 inline-flex shrink-0 items-center align-middle"
                                                      title="Živá cena z e-shopu"
                                                      aria-label="Živá cena z e-shopu"
                                                    >
                                                      <CircleCheck
                                                        className="h-3 w-3 text-emerald-600 sm:h-3.5 sm:w-3.5"
                                                        strokeWidth={2.25}
                                                        aria-hidden
                                                      />
                                                    </span>
                                                  ) : rowPrice != null &&
                                                    Number.isFinite(rowPrice) &&
                                                    !offerLiveOutOfStock ? (
                                                    <span
                                                      className="ml-0.5 text-[10px] text-slate-400 sm:ml-1 sm:text-xs"
                                                      title="Cena z lokálnych dát (nie živý e-shop)"
                                                    >
                                                      katalóg
                                                    </span>
                                                  ) : null}
                                                </div>
                                                <div
                                                  className={cn(
                                                    "flex min-w-0 items-center justify-between gap-x-2 rounded-md border px-1.5 py-1 text-right sm:flex-1 sm:flex-wrap sm:justify-end sm:gap-x-1 sm:rounded-none sm:border-0 sm:px-0 sm:py-0",
                                                    offerLiveOutOfStock
                                                      ? "border-red-100/90 bg-red-50/60 sm:border-0 sm:bg-transparent"
                                                      : hopefixPriceStockIncomplete
                                                        ? "border-amber-100/90 bg-amber-50/50 sm:border-0 sm:bg-transparent"
                                                        : "border-slate-100 bg-slate-50/70 sm:border-0 sm:bg-transparent",
                                                  )}
                                                >
                                                    <span
                                                      className={cn(
                                                        "mr-0.5 text-[8px] font-semibold uppercase tracking-wider sm:mr-1 sm:text-[10px]",
                                                        offerLiveOutOfStock
                                                          ? "text-red-600"
                                                          : hopefixPriceStockIncomplete
                                                            ? "text-amber-700"
                                                            : "text-slate-500",
                                                      )}
                                                    >
                                                    Sklad
                                                  </span>
                                                  <span
                                                    className={cn(
                                                      offerLiveOutOfStock
                                                        ? "font-medium text-red-600"
                                                        : hopefixPriceStockIncomplete
                                                          ? "font-medium text-amber-800"
                                                          : "text-slate-900",
                                                    )}
                                                  >
                                                    {!scraperApplicable ? (
                                                      stockSummaryClass ? (
                                                        <span
                                                          className={
                                                            stockSummaryClass
                                                          }
                                                        >
                                                          {stockSummaryText}
                                                        </span>
                                                      ) : (
                                                        stockSummaryText
                                                      )
                                                    ) : stockUiLoading ? (
                                                      <Loader2 className="inline h-3.5 w-3.5 animate-spin text-sky-600 align-middle sm:h-4 sm:w-4" />
                                                    ) : rowStockLive ||
                                                      liveScrapeMissingOffer ? (
                                                      stockSummaryClass ? (
                                                        <span
                                                          className={
                                                            stockSummaryClass
                                                          }
                                                        >
                                                          {stockSummaryText}
                                                        </span>
                                                      ) : (
                                                        stockSummaryText
                                                      )
                                                    ) : (
                                                      "—"
                                                    )}
                                                  </span>
                                                  {!scraperApplicable ? null : stockUiLoading ? (
                                                    <span className="ml-0.5 text-[9px] text-slate-400 sm:ml-1 sm:text-xs">
                                                      načítavam…
                                                    </span>
                                                  ) : rowStockLive &&
                                                    !offerCartUiBlocked ? (
                                                    <span
                                                      className="ml-0.5 inline-flex shrink-0 items-center align-middle"
                                                      title="Živý sklad z e-shopu"
                                                      aria-label="Živý sklad z e-shopu"
                                                    >
                                                      <CircleCheck
                                                        className="h-3 w-3 text-emerald-600 sm:h-3.5 sm:w-3.5"
                                                        strokeWidth={2.25}
                                                        aria-hidden
                                                      />
                                                    </span>
                                                  ) : liveScrapeMissingOffer &&
                                                    displayStock != null &&
                                                    Number.isFinite(
                                                      displayStock,
                                                    ) &&
                                                    !offerLiveOutOfStock ? (
                                                    <span
                                                      className="ml-0.5 text-[9px] text-slate-400 sm:ml-1 sm:text-xs"
                                                      title="Sklad z lokálnych dát (nie živý e-shop)"
                                                    >
                                                      katalóg
                                                    </span>
                                                  ) : !rowStockLive &&
                                                    !liveScrapeMissingOffer ? (
                                                    <span className="ml-0.5 text-[9px] text-slate-400 sm:ml-1 sm:text-xs">
                                                      demo
                                                    </span>
                                                  ) : null}
                                                </div>
                                              </div>
                                              </div>
                                              <div
                                                className={cn(
                                                  "flex w-full flex-col items-stretch gap-1 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end sm:gap-1",
                                                  canCart &&
                                                    offerCartUiBlocked &&
                                                    "rounded-md bg-slate-200/80 px-1.5 py-1 ring-1 ring-slate-300/60",
                                                )}
                                                title={
                                                  canCart && offerCartUiBlocked
                                                    ? hopefixActiveMissingProductId &&
                                                        !offerStockUiBlocked
                                                      ? "V údajoch riadku chýba product_id — API košík nie je k dispozícii (nápoveda nižšie)."
                                                      : liveScrapeMissingOffer
                                                        ? "V e-shope sa nepodarilo načítať ponuku — košík nie je k dispozícii"
                                                        : "Produkt nie je na sklade — košík nie je k dispozícii"
                                                    : undefined
                                                }
                                              >
                                                {canCart ? (
                                                  <>
                                                    <div
                                                      className={cn(
                                                        "flex w-full flex-col items-stretch gap-0.5 rounded-md border px-1.5 py-1 shadow-sm ring-1 sm:w-auto sm:items-start sm:px-2 sm:py-1",
                                                        offerCartUiBlocked
                                                          ? "border-slate-300/80 bg-slate-100/90 ring-slate-200/60"
                                                          : "border-slate-200/90 bg-gradient-to-b from-slate-50 to-white ring-slate-100/50",
                                                      )}
                                                    >
                                                      <div className="flex w-full items-center justify-center gap-0.5 sm:justify-start sm:gap-1">
                                                        <label
                                                          className="sr-only"
                                                          htmlFor={`cart-qty-${product.internal_code}-${offer.supplier_id}-${offerIndex}`}
                                                        >
                                                          Množstvo (ks)
                                                        </label>
                                                        <button
                                                          type="button"
                                                          disabled={offerCartUiBlocked}
                                                          aria-label="Znížiť množstvo"
                                                          title="Znížiť množstvo"
                                                          className={cn(
                                                            "flex h-8 w-7 shrink-0 items-center justify-center rounded border text-slate-600 transition-colors sm:h-7 sm:w-6",
                                                            offerCartUiBlocked
                                                              ? "cursor-not-allowed border-slate-300 bg-slate-200/80 text-slate-400"
                                                              : "border-slate-200 bg-white hover:bg-slate-50 active:bg-slate-100",
                                                          )}
                                                          onClick={() => {
                                                            const cur =
                                                              effectiveCartQty(
                                                                cartKey,
                                                                cartQuantityByKey,
                                                                rowPack,
                                                              );
                                                            const v =
                                                              snapToPackQuantity(
                                                                bumpCartQuantity(
                                                                  cur,
                                                                  rowPack,
                                                                  -1,
                                                                ),
                                                                rowPack,
                                                              );
                                                            setCartQuantityByKey(
                                                              (prev) => ({
                                                                ...prev,
                                                                [cartKey]: v,
                                                              }),
                                                            );
                                                          }}
                                                        >
                                                          <ChevronDown
                                                            className="h-4 w-4"
                                                            aria-hidden
                                                          />
                                                        </button>
                                                        <input
                                                          id={`cart-qty-${product.internal_code}-${offer.supplier_id}-${offerIndex}`}
                                                          type="number"
                                                          inputMode="numeric"
                                                          min={rowPack ?? 1}
                                                          step={rowPack ?? 1}
                                                          title={
                                                            supplierNameIsArgip(
                                                              offer.supplier,
                                                            ) &&
                                                            argipShopPackQty !=
                                                              null
                                                              ? `Množstvo v ks — násobok ${formatIntegerCsThousands(rowPack ?? 1)}; v balení ${formatIntegerCsThousands(argipShopPackQty)} ks`
                                                              : rowPack !=
                                                                  null
                                                                ? `Množstvo v ks (násobok balenia ${formatIntegerCsThousands(rowPack)} ks)`
                                                                : "Množstvo na pridanie do košíka"
                                                          }
                                                          disabled={
                                                            offerCartUiBlocked
                                                          }
                                                          className={cn(
                                                            "h-8 w-[4.6rem] rounded border px-1 text-center text-xs tabular-nums shadow-sm focus:outline-none focus:ring-1 sm:h-7 sm:w-[4.1rem] sm:px-1 sm:text-xs",
                                                            "[appearance:textfield] [-moz-appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none",
                                                            offerCartUiBlocked
                                                              ? "cursor-not-allowed border-slate-300 bg-slate-200/80 text-slate-500 focus:border-slate-300 focus:ring-0"
                                                              : "border-slate-200 bg-white text-slate-800 focus:border-slate-200 focus:ring-slate-200/80",
                                                          )}
                                                          value={effectiveCartQty(
                                                            cartKey,
                                                            cartQuantityByKey,
                                                            rowPack,
                                                          )}
                                                          onChange={(e) => {
                                                            const n = parseInt(
                                                              e.target.value,
                                                              10,
                                                            );
                                                            const v =
                                                              snapToPackQuantity(
                                                                Number.isFinite(
                                                                  n,
                                                                )
                                                                  ? n
                                                                  : rowPack ??
                                                                      1,
                                                                rowPack,
                                                              );
                                                            setCartQuantityByKey(
                                                              (prev) => ({
                                                                ...prev,
                                                                [cartKey]: v,
                                                              }),
                                                            );
                                                          }}
                                                        />
                                                        <button
                                                          type="button"
                                                          disabled={
                                                            offerCartUiBlocked
                                                          }
                                                          aria-label="Zvýšiť množstvo"
                                                          title="Zvýšiť množstvo"
                                                          className={cn(
                                                            "flex h-8 w-7 shrink-0 items-center justify-center rounded border text-slate-600 transition-colors sm:h-7 sm:w-6",
                                                            offerCartUiBlocked
                                                              ? "cursor-not-allowed border-slate-300 bg-slate-200/80 text-slate-400"
                                                              : "border-slate-200 bg-white hover:bg-slate-50 active:bg-slate-100",
                                                          )}
                                                          onClick={() => {
                                                            const cur =
                                                              effectiveCartQty(
                                                                cartKey,
                                                                cartQuantityByKey,
                                                                rowPack,
                                                              );
                                                            const v =
                                                              snapToPackQuantity(
                                                                bumpCartQuantity(
                                                                  cur,
                                                                  rowPack,
                                                                  1,
                                                                ),
                                                                rowPack,
                                                              );
                                                            setCartQuantityByKey(
                                                              (prev) => ({
                                                                ...prev,
                                                                [cartKey]: v,
                                                              }),
                                                            );
                                                          }}
                                                        >
                                                          <ChevronUp
                                                            className="h-4 w-4"
                                                            aria-hidden
                                                          />
                                                        </button>
                                                        <span className="text-[10px] font-medium text-slate-600 sm:text-xs">
                                                          ks
                                                        </span>
                                                      </div>
                                                      {supplierNameIsArgip(
                                                        offer.supplier,
                                                      ) &&
                                                      argipShopPackQty !=
                                                        null ? (
                                                        <>
                                                          <span
                                                            className="max-w-[4.2rem] truncate text-center text-[8px] leading-tight text-slate-500 sm:max-w-none sm:text-[10px]"
                                                            title="Počet kusov v jednom balení na e-shope"
                                                          >
                                                            (
                                                            {formatIntegerCsThousands(
                                                              argipShopPackQty,
                                                            )}
                                                            &nbsp;ks/bal.)
                                                          </span>
                                                          {rowPack != null &&
                                                          rowPack !==
                                                            argipShopPackQty ? (
                                                            <span
                                                              className="text-[8px] tabular-nums text-slate-400 sm:text-[10px]"
                                                              title="Minimálne množstvo / krok pre zvolenú cenovú hladinu"
                                                            >
                                                              ×{rowPack}
                                                            </span>
                                                          ) : null}
                                                        </>
                                                      ) : rowPack != null ? (
                                                        <span
                                                          className="hidden text-[10px] text-slate-500 sm:inline"
                                                          title="Objednávka v násobkoch balenia."
                                                        >
                                                          ×{rowPack}
                                                        </span>
                                                      ) : null}
                                                    </div>
                                                    <Button
                                                      type="button"
                                                      size="sm"
                                                      variant={
                                                        cartAddSuccessByKey[
                                                          cartKey
                                                        ]
                                                          ? "default"
                                                          : offerCartUiBlocked
                                                            ? "secondary"
                                                            : "default"
                                                      }
                                                      disabled={Boolean(
                                                        cartFeedback[cartKey] ||
                                                          offerCartUiBlocked,
                                                      )}
                                                      className={cn(
                                                        "h-8 w-full shrink-0 gap-1 px-2 text-xs shadow-sm sm:h-9 sm:w-auto sm:px-3 sm:text-sm",
                                                        !offerCartUiBlocked &&
                                                          !cartAddSuccessByKey[
                                                            cartKey
                                                          ] &&
                                                          "shadow-sky-600/15",
                                                        offerCartUiBlocked &&
                                                          !cartAddSuccessByKey[
                                                            cartKey
                                                          ] &&
                                                          "border border-slate-300 bg-slate-300 text-slate-700 shadow-none hover:bg-slate-300 disabled:cursor-not-allowed disabled:opacity-100",
                                                        cartAddSuccessByKey[cartKey] &&
                                                          "border border-emerald-600 bg-emerald-50 text-emerald-900 shadow-sm hover:bg-emerald-100",
                                                      )}
                                                      title={
                                                        offerCartUiBlocked
                                                          ? hopefixActiveMissingProductId &&
                                                              !offerStockUiBlocked
                                                            ? "Chýba product_id — API košík nie je k dispozícii"
                                                            : liveScrapeMissingOffer
                                                              ? "Ponuka z e-shopu sa nenašla"
                                                              : "Produkt nie je na sklade"
                                                          : "Vložiť do košíka"
                                                      }
                                                      onClick={() =>
                                                        void addToCart(
                                                          offer.supplier_id!,
                                                          offer.supplier_code!.trim(),
                                                          offer.supplier,
                                                          snapToPackQuantity(
                                                            effectiveCartQty(
                                                              cartKey,
                                                              cartQuantityByKey,
                                                              rowPack,
                                                            ),
                                                            rowPack,
                                                          ),
                                                          multiPack &&
                                                            !usesHttpCartVariants
                                                            ? selVi
                                                            : usesHttpCartVariants &&
                                                                (inoxmareHttpVariants ||
                                                                  argipHttpVariants)
                                                              ? selVi
                                                              : null,
                                                          {
                                                            internalCode:
                                                              product.internal_code,
                                                            priceEur:
                                                              Number.isFinite(
                                                                rowPrice,
                                                              )
                                                                ? rowPrice
                                                                : null,
                                                            priceUnit:
                                                              rowPriceUnit ??
                                                              scrape?.price_unit ??
                                                              null,
                                                            logoUrl:
                                                              offer.logo_url ??
                                                              null,
                                                            norma:
                                                              product.norma ?? null,
                                                            diameter:
                                                              product.diameter ??
                                                              null,
                                                            length:
                                                              product.length ??
                                                              null,
                                                            surface:
                                                              product.surface ??
                                                              null,
                                                            yMoneyName:
                                                              product.y_money_name ??
                                                              null,
                                                            variantLabel:
                                                              showPackSelector &&
                                                              pvars
                                                                ? pvars[
                                                                    selVi
                                                                  ]?.label?.trim() ||
                                                                  `Variant ${selVi + 1}`
                                                                : null,
                                                            packQuantity:
                                                              rowPack ?? null,
                                                            offerNote:
                                                              sid != null
                                                                ? (
                                                                    offerNotesByKey[
                                                                      offerNoteStorageKey(
                                                                        product.internal_code,
                                                                        sid,
                                                                      )
                                                                    ] ?? ""
                                                                  ).trim() || null
                                                                : null,
                                                          },
                                                          mekrsHttpVariants &&
                                                            activePv
                                                            ? (activePv.mekrs_variant_id ??
                                                              null)
                                                            : null,
                                                          supplierNameIsHopefix(
                                                            offer.supplier,
                                                          ) && activePv
                                                            ? (activePv.hopefix_product_id ??
                                                              null)
                                                            : null,
                                                          supplierNameIsHopefix(
                                                            offer.supplier,
                                                          ) && activePv
                                                            ? (activePv.hopefix_package_type ??
                                                              null)
                                                            : null,
                                                          supplierNameIsHopefix(
                                                            offer.supplier,
                                                          ) && activePv
                                                            ? (activePv.hopefix_referer_path ??
                                                              null)
                                                            : null,
                                                          hasplHttpVariants &&
                                                            activePv
                                                            ? (activePv.haspl_variant_code ??
                                                              null)
                                                            : null,
                                                          inoxmareHttpVariants &&
                                                            activePv
                                                            ? (activePv.inoxmare_product_id ??
                                                              null)
                                                            : null,
                                                          inoxmareHttpVariants &&
                                                            activePv
                                                            ? (activePv.inoxmare_referer_path ??
                                                              null)
                                                            : null,
                                                        )
                                                      }
                                                    >
                                                      {cartAddSuccessByKey[cartKey] ? (
                                                        <Check
                                                          className="h-4 w-4 text-emerald-600"
                                                          strokeWidth={2.5}
                                                        />
                                                      ) : (
                                                        <ShoppingCart
                                                          className={cn(
                                                            "h-4 w-4",
                                                            offerCartUiBlocked &&
                                                              "text-slate-600",
                                                          )}
                                                        />
                                                      )}
                                                      <span>
                                                        {cartAddSuccessByKey[cartKey]
                                                          ? "Pridané"
                                                          : "Košík"}
                                                      </span>
                                                    </Button>
                                                  </>
                                                ) : (
                                                  <p className="rounded-md border border-amber-200/80 bg-amber-50/80 px-1.5 py-0.5 text-right text-[9px] font-medium text-amber-950 sm:text-[11px]">
                                                    Košík nedostupný
                                                  </p>
                                                )}
                                              </div>
                                            </div>
                                          </div>
                                          {scrape?.loading ? (
                                            <p className="mt-1 flex items-center gap-1 rounded-md border border-sky-200/70 bg-sky-50/60 px-1.5 py-0.5 text-[9px] font-medium text-sky-900 sm:mt-1.5 sm:px-2 sm:py-1 sm:text-[11px]">
                                              <Loader2 className="h-3 w-3 shrink-0 animate-spin text-sky-600" />
                                              Načítavam cenu a sklad…
                                            </p>
                                          ) : null}
                                          {scrape?.error ? (
                                            <p className="mt-1 rounded-md border border-rose-200/80 bg-rose-50/70 px-1.5 py-0.5 text-[9px] leading-snug text-rose-900 sm:mt-1.5 sm:px-2 sm:py-1 sm:text-[11px]">
                                              {scrape.error}
                                            </p>
                                          ) : null}
                                          {scrape?.hint && !scrape?.error ? (
                                            <p className="mt-1 rounded-md border border-amber-200/80 bg-amber-50/60 px-1.5 py-0.5 text-[9px] leading-snug text-amber-950 sm:mt-1.5 sm:px-2 sm:py-1 sm:text-[11px]">
                                              {scrape.hint}
                                            </p>
                                          ) : null}
                                          {supplierShowsScrapeLoginBadge(
                                            offer.supplier,
                                          ) &&
                                          scrape?.login_hint &&
                                          !scrape?.loading &&
                                          scrape?.logged_in === false ? (
                                            <p className="mt-1 rounded-md border border-slate-200/90 bg-slate-50/80 px-1.5 py-0.5 text-[9px] leading-snug text-slate-700 sm:mt-1.5 sm:px-2 sm:py-1 sm:text-[11px]">
                                              {scrape.login_hint}
                                            </p>
                                          ) : null}
                                          {canCart &&
                                          sid != null &&
                                          scode &&
                                          cartFeedback[
                                            cartStorageKey(sid, scode, null)
                                          ] ? (
                                            <p className="mt-1 rounded-md border border-slate-200/90 bg-slate-50/80 px-1.5 py-0.5 text-[9px] text-slate-700 sm:mt-1.5 sm:px-2 sm:py-1 sm:text-[11px]">
                                              {
                                                cartFeedback[
                                                  cartStorageKey(
                                                    sid,
                                                    scode,
                                                    null,
                                                  )
                                                ]
                                              }
                                            </p>
                                          ) : null}
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              </td>
                            </tr>
  );
}

export default function Home() {
  const [activeView, setActiveView] = useState<View>("vyhladavanie");
  const [navCollapsed, setNavCollapsed] = useState(true);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [themeMode, setThemeMode] = useState<"light" | "dark">("light");
  const [openProduct, setOpenProduct] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [saveState, setSaveState] = useState<Record<number, string>>({});
  const [supplierForms, setSupplierForms] = useState<SupplierForm[]>(defaultSuppliers);
  /** Kľúče rozbalených kariet dodávateľov v sekcii Dodávatelia (kompaktný zoznam). */
  const [expandedSupplierKeys, setExpandedSupplierKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const [suppliersExcelPanelOpen, setSuppliersExcelPanelOpen] = useState(true);
  const [suppliersShippingHintOpen, setSuppliersShippingHintOpen] = useState(false);
  const [supplierReorderBusy, setSupplierReorderBusy] = useState(false);
  useEffect(() => {
    const stored =
      typeof window !== "undefined" ?
        window.localStorage.getItem("smarthub_theme_mode")
      : null;
    if (stored === "dark" || stored === "light") {
      setThemeMode(stored);
      return;
    }
    if (
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
    ) {
      setThemeMode("dark");
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("smarthub_theme_mode", themeMode);
  }, [themeMode]);

  const [excelFilePath, setExcelFilePath] = useState(DEFAULT_GAMECHANGER_XLSX_PATH);
  const [sheetName, setSheetName] = useState("DIN");

  /** Nahradí zastarané / Render-only cesty lokálnou predvolenou. */
  useEffect(() => {
    if (typeof window === "undefined") return;
    setExcelFilePath((prev) => {
      const norm = prev.trim();
      if (!norm) {
        return DEFAULT_GAMECHANGER_XLSX_PATH;
      }
      if (norm.includes("/opt/render/project")) {
        return DEFAULT_GAMECHANGER_XLSX_PATH;
      }
      const normWin = norm.replace(/\//g, "\\");
      const base = normWin.split("\\").pop() ?? "";
      const looksLegacy =
        (normWin.includes("..") &&
          base.toLowerCase() === "smart_data_gamechanger.xlsx") ||
        normWin.toLowerCase() === "smart_data_gamechanger.xlsx";
      return looksLegacy ? DEFAULT_GAMECHANGER_XLSX_PATH : prev;
    });
  }, []);
  const [mappingProfile, setMappingProfile] = useState<MappingProfile | null>(null);
  const [mappingStatus, setMappingStatus] = useState("");
  const [mappingProfileLoading, setMappingProfileLoading] = useState(false);
  const [excelImportRunning, setExcelImportRunning] = useState(false);
  const [excelImportProgressPct, setExcelImportProgressPct] = useState<number | null>(null);
  const [fieldToColumn, setFieldToColumn] = useState<Record<FilterField, string>>({
    code: "",
    norma: "",
    surface: "",
    diameter: "",
    length: "",
    v_class: "",
    y_money_name: "",
    image_filename: "",
  });
  const [imagePreview, setImagePreview] = useState<{
    url: string;
    code: string;
    filename: string;
  } | null>(null);
  const [searchFilters, setSearchFilters] = useState({ ...initialSearchFilters });
  const [debouncedCode, setDebouncedCode] = useState("");
  const [searchTick, setSearchTick] = useState(0);
  const [filterOptions, setFilterOptions] = useState<FilterOptions>({
    norma: [],
    surface: [],
    diameter: [],
    length: [],
    v_class: [],
    y_money_name: [],
  });
  const [searchResults, setSearchResults] = useState<ProductSearchRow[]>([]);
  const [productLists, setProductLists] = useState<ProductListRow[]>([]);
  const [activeListId, setActiveListId] = useState<number | null>(null);
  const [activeListItems, setActiveListItems] = useState<ProductListItemRow[]>([]);
  const [newListName, setNewListName] = useState("");
  const [listStatus, setListStatus] = useState("");
  const [listPicker, setListPicker] = useState<{
    internalCode: string;
    listId: number | null;
  } | null>(null);
  const [listOpenProductRow, setListOpenProductRow] =
    useState<ProductSearchRow | null>(null);
  const searchResultsRef = useRef<ProductSearchRow[]>([]);
  searchResultsRef.current = searchResults;
  const listOpenProductRef = useRef<ProductSearchRow | null>(null);
  listOpenProductRef.current = listOpenProductRow;
  const [searchMessage, setSearchMessage] = useState("");
  const [cartFeedback, setCartFeedback] = useState<Record<string, string>>({});
  /** Po úspešnom POST /cart/add — kľúč ako pri cartFeedback; po čase sa vymaže. */
  const [cartAddSuccessByKey, setCartAddSuccessByKey] = useState<
    Record<string, boolean>
  >({});
  const cartSuccessClearTimersRef = useRef<Record<string, number>>({});
  /** Množstvo do košíka podľa `${supplierId}:${supplierCode}`; predvolene 1. */
  const [cartQuantityByKey, setCartQuantityByKey] = useState<
    Record<string, number>
  >({});
  /** Index variantu balenia v `packaging_variants` — kľúč `${supplierId}:${supplierCode}`. */
  const [packVariantIndexByKey, setPackVariantIndexByKey] = useState<
    Record<string, number>
  >({});
  const [scrapeByKey, setScrapeByKey] = useState<
    Record<string, SupplierScrapeState>
  >({});
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [devLogEntries, setDevLogEntries] = useState<DevLogEntry[]>([]);
  const [devLogPaused, setDevLogPaused] = useState(false);
  const [devLogError, setDevLogError] = useState<string | null>(null);
  const [devSupplierFilter, setDevSupplierFilter] = useState("all");
  /** Režim screenshotov Playwright (Dev API); "" = ešte nenačítané. */
  const [devStepScreenshotMode, setDevStepScreenshotMode] = useState<
    "on" | "off" | "env" | ""
  >("");
  const devLogScrollRef = useRef<HTMLDivElement | null>(null);
  /** Ak false, používateľ posunul nahor — pri pollovaní logov nepresúvaj späť na koniec. */
  const devLogFollowTailRef = useRef(true);

  const [cartHistory, setCartHistory] = useState<CartHistoryEntry[]>([]);
  const [cartHistoryReady, setCartHistoryReady] = useState(false);
  /** Len do refreshu stránky — neukladá sa (história košíka poznámku stále uloží pri kliku). */
  const [offerNotesByKey, setOfferNotesByKey] = useState<Record<string, string>>({});
  const [cartHistoryNotesOnly, setCartHistoryNotesOnly] = useState(false);
  const [remoteCartRows, setRemoteCartRows] = useState<RemoteCartOverviewUiRow[]>([]);
  /** Načítanie zoznamu dodávateľov z DB (rýchle). */
  const [remoteCartLoading, setRemoteCartLoading] = useState(false);
  const [remoteCartFetchError, setRemoteCartFetchError] = useState<string | null>(
    null,
  );
  const [expandedRemoteSupplierId, setExpandedRemoteSupplierId] = useState<
    number | null
  >(null);
  const [remoteDetailBySupplierId, setRemoteDetailBySupplierId] = useState<
    Record<number, RemoteCartDetailPayload>
  >({});
  const [remoteDetailLoadingId, setRemoteDetailLoadingId] = useState<number | null>(
    null,
  );
  const [remoteCartRefreshTick, setRemoteCartRefreshTick] = useState(0);
  const remoteCartRowsRef = useRef<RemoteCartOverviewUiRow[]>([]);
  remoteCartRowsRef.current = remoteCartRows;
  /** Ďalší fetch prehľadu košíka obíde serverovú cache (`?refresh=1`). */
  const remoteCartNextFetchBypassCacheRef = useRef(false);

  /** Bearer token pre FastAPI (rovnaký JWT ako v httpOnly cookie). */
  const [apiToken, setApiToken] = useState<string | null>(null);
  /** Po prvom dokončení GET /api/auth/session — aby sa vyhľadávanie nespúšťalo bez tokenu a neprepísalo výsledky 401. */
  const [authSessionReady, setAuthSessionReady] = useState(false);
  /** Prvý kombinovaný `/api/bootstrap/search` volaj len raz — pri ďalších filtroch už klasický 2-step flow. */
  const bootstrapDoneRef = useRef(false);
  /** Next vráti `error: config` keď chýba alebo je krátke SMARTHUB_AUTH_SECRET. */
  const [authConfigError, setAuthConfigError] = useState(false);
  const [isAppAdmin, setIsAppAdmin] = useState(false);
  const [adminUsers, setAdminUsers] = useState<
    Array<{ id: number; username: string; display_label: string | null; is_admin: boolean }>
  >([]);
  const [adminUsersError, setAdminUsersError] = useState<string | null>(null);
  const [newBranchUsername, setNewBranchUsername] = useState("");
  const [newBranchPassword, setNewBranchPassword] = useState("");
  const [newBranchLabel, setNewBranchLabel] = useState("");
  const [adminUserSubmitting, setAdminUserSubmitting] = useState(false);
  const [companyConfigured, setCompanyConfigured] = useState<boolean | null>(null);
  const [addToOfferOpen, setAddToOfferOpen] = useState(false);
  const [addToOfferPayload, setAddToOfferPayload] = useState<AddToOfferPayload | null>(
    null,
  );
  const [addToOfferFeedback, setAddToOfferFeedback] = useState<string | null>(null);

  const apiFetch = useCallback(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      if (apiToken) {
        headers.set("Authorization", `Bearer ${apiToken}`);
      }
      return fetch(input, { ...init, headers });
    },
    [apiToken],
  );

  useEffect(() => {
    // Auth session a backend health pingujeme paralelne. /api/health prebudí Render dyno,
    // takže keď používateľ zafiltruje, backend už nie je studený.
    void fetch(`${API_BASE}/api/health`, { cache: "no-store" }).catch(() => {});
    void fetch("/api/auth/session")
      .then((r) => r.json())
      .then(
        (d: {
          token?: string | null;
          isAdmin?: boolean;
          error?: string;
        }) => {
          setApiToken(typeof d.token === "string" ? d.token : null);
          setIsAppAdmin(Boolean(d.isAdmin));
          setAuthConfigError(d.error === "config");
        },
      )
      .catch(() => {
        setApiToken(null);
        setIsAppAdmin(false);
        setAuthConfigError(false);
      })
      .finally(() => {
        setAuthSessionReady(true);
      });
  }, []);

  /** Pobočka môže meniť len prihlasovacie údaje do e-shopov; šablónu dodávateľa len admin. */
  const supplierTemplateLocked = !isAppAdmin;

  const refetchSuppliersList = useCallback(async () => {
    try {
      const response = await apiFetch(`${API_BASE}/api/suppliers`);
      if (!response.ok) {
        return;
      }
      const data = (await response.json()) as Array<{
        id: number;
        name: string;
        shop_url: string;
        username: string;
        password: string;
        is_connected: boolean;
        code_column: string | null;
        cart_config_json: string | null;
        logo_url?: string | null;
        free_shipping_threshold_eur?: number | null;
        sort_order?: number | null;
      }>;
      if (data.length === 0) {
        return;
      }
      setSupplierForms(
        data.map((supplier) => ({
          id: supplier.id,
          name: supplier.name,
          shopUrl: supplier.shop_url,
          username: supplier.username,
          password: supplier.password,
          isConnected: supplier.is_connected,
          codeColumn: supplier.code_column ?? "",
          cartConfigJson: supplier.cart_config_json ?? "",
          freeShippingThresholdEur:
            supplier.free_shipping_threshold_eur != null &&
            Number.isFinite(supplier.free_shipping_threshold_eur)
              ? String(supplier.free_shipping_threshold_eur)
              : "",
          sortOrder:
            supplier.sort_order != null && Number.isFinite(supplier.sort_order)
              ? supplier.sort_order
              : 0,
          logoUrl: supplier.logo_url ?? null,
        })),
      );
    } catch {
      // Keep fallback suppliers if API is unreachable.
    }
  }, [apiFetch]);

  const commitSupplierOrder = useCallback(
    async (orderedIds: number[]) => {
      setSupplierReorderBusy(true);
      try {
        const response = await apiFetch(`${API_BASE}/api/suppliers/reorder`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ordered_supplier_ids: orderedIds }),
        });
        const payload = (await response.json().catch(() => ({}))) as {
          detail?: unknown;
        };
        if (!response.ok) {
          throw new Error(formatApiDetail(payload.detail));
        }
      } finally {
        setSupplierReorderBusy(false);
      }
    },
    [apiFetch],
  );

  const cartHistoryFiltered = useMemo(() => {
    if (!cartHistoryNotesOnly) {
      return cartHistory;
    }
    return cartHistory.filter((e) => Boolean(e.offerNote?.trim()));
  }, [cartHistory, cartHistoryNotesOnly]);

  const fetchRemoteCartOverviewForSupplier = useCallback(
    async (supplierId: number, bypassCache: boolean) => {
      const url = bypassCache
        ? `${API_BASE}/api/cart/remote/${supplierId}/overview?refresh=1`
        : `${API_BASE}/api/cart/remote/${supplierId}/overview`;
      const response = await apiFetch(url);
      const payload = (await response.json()) as Partial<RemoteCartOverviewRow> & {
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(formatApiDetail(payload.detail));
      }
      return normalizeRemoteCartOverviewRow(
        payload as RemoteCartOverviewRow & { supplier_id: number },
        false,
      );
    },
    [apiFetch],
  );

  const loadRemoteCartOverviews = useCallback(
    (supplierIds: number[], bypassCache: boolean, cancelled: () => boolean) => {
      for (const supplierId of supplierIds) {
        void (async () => {
          try {
            const row = await fetchRemoteCartOverviewForSupplier(
              supplierId,
              bypassCache,
            );
            if (cancelled()) {
              return;
            }
            setRemoteCartRows((prev) =>
              prev.map((r) => (r.supplier_id === supplierId ? row : r)),
            );
          } catch (err) {
            if (cancelled()) {
              return;
            }
            setRemoteCartRows((prev) =>
              prev.map((r) =>
                r.supplier_id === supplierId
                  ? {
                      ...r,
                      overviewLoading: false,
                      remote_supported: false,
                      logged_in: false,
                      message:
                        err instanceof Error
                          ? err.message
                          : "Chyba načítania košíka.",
                    }
                  : r,
              ),
            );
          }
        })();
      }
    },
    [fetchRemoteCartOverviewForSupplier],
  );

  useEffect(() => {
    if (activeView !== "kosik") {
      return;
    }
    let cancelled = false;
    const isCancelled = () => cancelled;
    const bypassCache = remoteCartNextFetchBypassCacheRef.current;
    setRemoteCartFetchError(null);

    const existing = remoteCartRowsRef.current;
    if (existing.length > 0) {
      setRemoteCartRows(
        existing.map((r) => ({ ...r, overviewLoading: true })),
      );
      loadRemoteCartOverviews(
        existing.map((r) => r.supplier_id),
        bypassCache,
        isCancelled,
      );
      if (bypassCache) {
        remoteCartNextFetchBypassCacheRef.current = false;
      }
      return () => {
        cancelled = true;
      };
    }

    setRemoteCartLoading(true);
    void (async () => {
      try {
        const response = await apiFetch(`${API_BASE}/api/suppliers`);
        const payload = (await response.json()) as
          | Array<{
              id: number;
              name: string;
              logo_url?: string | null;
              free_shipping_threshold_eur?: number | null;
            }>
          | { detail?: unknown };
        if (!response.ok) {
          throw new Error(
            formatApiDetail(
              !Array.isArray(payload) ? payload.detail : "Chyba načítania dodávateľov.",
            ),
          );
        }
        if (cancelled || !Array.isArray(payload)) {
          return;
        }
        const suppliers = payload;
        setRemoteCartRows(suppliers.map(supplierListItemToRemoteCartPlaceholder));
        setRemoteCartLoading(false);
        loadRemoteCartOverviews(
          suppliers.map((s) => s.id),
          bypassCache,
          isCancelled,
        );
      } catch (err) {
        if (!cancelled) {
          setRemoteCartFetchError(
            err instanceof Error ? err.message : "Chyba načítania košíkov.",
          );
          setRemoteCartRows([]);
          setRemoteCartLoading(false);
        }
      } finally {
        if (bypassCache) {
          remoteCartNextFetchBypassCacheRef.current = false;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    activeView,
    remoteCartRefreshTick,
    apiFetch,
    loadRemoteCartOverviews,
  ]);

  const toggleRemoteSupplierCart = (supplierId: number) => {
    if (expandedRemoteSupplierId === supplierId) {
      setExpandedRemoteSupplierId(null);
      return;
    }
    setExpandedRemoteSupplierId(supplierId);
    setRemoteDetailLoadingId(supplierId);
    void (async () => {
      try {
        const response = await apiFetch(
          `${API_BASE}/api/cart/remote/${supplierId}`,
        );
        const payload = (await response.json()) as RemoteCartDetailPayload & {
          detail?: unknown;
        };
        if (!response.ok) {
          throw new Error(formatApiDetail(payload.detail));
        }
        setRemoteDetailBySupplierId((prev) => ({
          ...prev,
          [supplierId]: {
            supplier_id: payload.supplier_id,
            name: payload.name,
            logo_url: payload.logo_url ?? null,
            remote_supported: Boolean(payload.remote_supported),
            logged_in: payload.logged_in ?? null,
            total_eur: payload.total_eur ?? null,
            lines: Array.isArray(payload.lines) ? payload.lines : [],
            message: payload.message ?? null,
          },
        }));
      } catch (err) {
        setRemoteDetailBySupplierId((prev) => ({
          ...prev,
          [supplierId]: {
            supplier_id: supplierId,
            name: "",
            logo_url: null,
            remote_supported: false,
            logged_in: false,
            total_eur: null,
            lines: [],
            message:
              err instanceof Error ? err.message : "Chyba načítania detailu.",
          },
        }));
      } finally {
        setRemoteDetailLoadingId(null);
      }
    })();
  };

  useEffect(() => {
    setCartHistory(parseCartHistoryFromStorage());
    setCartHistoryReady(true);
  }, []);

  useEffect(() => {
    if (!cartHistoryReady) {
      return;
    }
    try {
      localStorage.setItem(
        CART_HISTORY_STORAGE_KEY,
        JSON.stringify(cartHistory),
      );
    } catch {
      // úložisko plné / súkromné okno
    }
  }, [cartHistory, cartHistoryReady]);

  useEffect(() => {
    return () => {
      const timers = cartSuccessClearTimersRef.current;
      for (const id of Object.values(timers)) {
        window.clearTimeout(id);
      }
      cartSuccessClearTimersRef.current = {};
    };
  }, []);

  const devSupplierOptions = useMemo(() => {
    return Array.from(
      new Set(
        devLogEntries
          .map((entry) => entry.supplier?.trim())
          .filter((value): value is string => Boolean(value)),
      ),
    ).sort((a, b) => a.localeCompare(b));
  }, [devLogEntries]);

  const visibleDevLogs = useMemo(() => {
    if (devSupplierFilter === "all") {
      return devLogEntries;
    }
    return devLogEntries.filter((entry) => entry.supplier === devSupplierFilter);
  }, [devLogEntries, devSupplierFilter]);

  const handleDevLogScroll = useCallback(() => {
    const el = devLogScrollRef.current;
    if (!el) {
      return;
    }
    const slack = 96;
    const fromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    devLogFollowTailRef.current = fromBottom <= slack;
  }, []);

  useEffect(() => {
    if (activeView !== "dev" || devLogPaused) {
      return;
    }
    if (!devLogFollowTailRef.current) {
      return;
    }
    const id = requestAnimationFrame(() => {
      const wrap = devLogScrollRef.current;
      if (wrap) {
        wrap.scrollTop = wrap.scrollHeight;
      }
    });
    return () => cancelAnimationFrame(id);
  }, [devLogEntries, visibleDevLogs, activeView, devLogPaused]);

  /** Pri otvorení Dev panelu ukáž najnovšie riadky (inak by scroll ostal hore bez zmeny dát). */
  useEffect(() => {
    if (activeView !== "dev") {
      return;
    }
    devLogFollowTailRef.current = true;
    const id = requestAnimationFrame(() => {
      const wrap = devLogScrollRef.current;
      if (wrap) {
        wrap.scrollTop = wrap.scrollHeight;
      }
    });
    return () => cancelAnimationFrame(id);
  }, [activeView]);

  /** Po vymazaní logu alebo zmene dodávateľa môže ostať filter, ktorý nesedí so žiadnym riadkom. */
  useEffect(() => {
    if (devSupplierFilter === "all") {
      return;
    }
    const hasMatch = devLogEntries.some(
      (entry) => entry.supplier?.trim() === devSupplierFilter,
    );
    if (!hasMatch) {
      setDevSupplierFilter("all");
    }
  }, [devLogEntries, devSupplierFilter]);

  const matchedCount = useMemo(() => {
    return Object.values(fieldToColumn).filter(Boolean).length;
  }, [fieldToColumn]);

  const isFieldMapped = (field: FilterField) =>
    Boolean(fieldToColumn[field]?.trim());

  const showSurfaceCol = isFieldMapped("surface");

  /** Class (V) + Money názov (Y) sú v tabuľke vždy (dáta z DB); mapovanie určuje Excel. */
  const searchTableColSpan =
    5 + (showSurfaceCol ? 1 : 0) + 3;

  useEffect(() => {
    const id = window.setTimeout(() => {
      setDebouncedCode(searchFilters.code);
    }, 320);
    return () => window.clearTimeout(id);
  }, [searchFilters.code]);

  useEffect(() => {
    if (!toastMessage) {
      return;
    }
    const id = window.setTimeout(() => setToastMessage(null), 5000);
    return () => window.clearTimeout(id);
  }, [toastMessage]);

  useEffect(() => {
    setOpenProduct(null);
    setListOpenProductRow(null);
  }, [activeView]);

  useEffect(() => {
    if (activeView !== "zoznamy") {
      return;
    }
    setOpenProduct(null);
    setListOpenProductRow(null);
  }, [activeListId, activeView]);

  useEffect(() => {
    if (activeView !== "zoznamy") {
      return;
    }
    if (!openProduct) {
      setListOpenProductRow(null);
      return;
    }
    const code = openProduct.trim();
    if (!code) {
      setListOpenProductRow(null);
      return;
    }
    setListOpenProductRow(null);
    let cancelled = false;
    void (async () => {
      try {
        const r = await apiFetch(`${API_BASE}/api/products/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            code,
            norma: null,
            surface: null,
            diameter: null,
            length: null,
            v_class: null,
            y_money_name: null,
            prefetch_live_prices: false,
            limit: 1,
          }),
        });
        const payload = (await r.json()) as ProductSearchRow[] | { detail?: unknown };
        if (!r.ok) {
          throw new Error(
            "detail" in payload ? formatApiDetail(payload.detail) : "Chyba vyhľadávania.",
          );
        }
        const rows = normalizeProductSearchRows(payload as ProductSearchRow[]);
        const product =
          rows.find((x) => x.internal_code === code) ?? rows[0] ?? null;
        if (!cancelled) {
          setListOpenProductRow(product);
        }
      } catch {
        if (!cancelled) {
          setListOpenProductRow(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeView, openProduct, apiFetch]);

  useEffect(() => {
    if (!openProduct) {
      return;
    }
    if (activeView !== "vyhladavanie" && activeView !== "zoznamy") {
      return;
    }
    const product: ProductSearchRow | null =
      activeView === "vyhladavanie"
        ? (searchResultsRef.current.find(
            (p) => p.internal_code === openProduct,
          ) ?? null)
        : (() => {
            const row = listOpenProductRef.current;
            return row && row.internal_code === openProduct ? row : null;
          })();
    if (!product) {
      return;
    }

    for (const offer of product.offers) {
      if (offer.supplier_id == null || !offer.supplier_code?.trim()) {
        continue;
      }
      const key = scrapeCacheKey(openProduct, offer.supplier_id);
      setScrapeByKey((prev) => ({
        ...prev,
        [key]: { loading: true, error: null },
      }));

      const sid = offer.supplier_id;
      const code = offer.supplier_code.trim();

      void (async () => {
        try {
          const response = await apiFetch(
            `${API_BASE}/api/scraper/supplier-data`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ supplier_id: sid, supplier_code: code }),
            },
          );
          // Render proxy / worker crash niekedy vráti plain-text „Internal
          // Server Error" — bežný JSON.parse by to zhodil na hláške
          // „Unexpected token 'I' …". `readApiJsonOrText` to robí robustne.
          const parsed = await readApiJsonOrText(response);
          if (!parsed.ok) {
            setScrapeByKey((p) => ({
              ...p,
              [key]: {
                loading: false,
                error: parsed.detail,
                logged_in: false,
                login_hint: null,
              },
            }));
            return;
          }
          const payload = parsed.data as {
            product_title?: string | null;
            price_eur?: number | null;
            raw_price?: string | null;
            price_unit?: string | null;
            stock?: number | null;
            raw_stock?: string | null;
            pack_quantity?: number | null;
            raw_pack_quantity?: string | null;
            shop_pack_quantity?: number | null;
            packaging_variants?: PackagingVariantRow[] | null;
            price_includes_vat?: boolean | null;
            currency_code?: string | null;
            currency_symbol?: string | null;
            detail?: unknown;
            hint?: string | null;
            logged_in?: boolean;
            login_hint?: string | null;
          };

          if (!response.ok) {
            setScrapeByKey((p) => ({
              ...p,
              [key]: {
                loading: false,
                error: formatApiDetail(payload.detail),
                logged_in: false,
                login_hint: payload.login_hint ?? null,
              },
            }));
            return;
          }

          const loggedIn =
            typeof payload.logged_in === "boolean" ? payload.logged_in : null;
          setScrapeByKey((p) => ({
            ...p,
            [key]: {
              loading: false,
              error: null,
              product_title: payload.product_title ?? null,
              price_eur: payload.price_eur ?? null,
              raw_price: payload.raw_price ?? null,
              price_unit: payload.price_unit ?? null,
              stock: payload.stock ?? null,
              raw_stock: payload.raw_stock ?? null,
              pack_quantity: payload.pack_quantity ?? null,
              raw_pack_quantity: payload.raw_pack_quantity ?? null,
              shop_pack_quantity: payload.shop_pack_quantity ?? null,
              packaging_variants: payload.packaging_variants ?? null,
              price_includes_vat: payload.price_includes_vat ?? null,
              currency_code: payload.currency_code ?? null,
              currency_symbol: payload.currency_symbol ?? null,
              hint: payload.hint ?? null,
              logged_in: loggedIn,
              login_hint: payload.login_hint ?? null,
            },
          }));
          const pvars = payload.packaging_variants;
          const pvarsNeedVariantPick =
            Array.isArray(pvars) &&
            (pvars.length > 1 ||
              pvars.some(
                (row) =>
                  Boolean((row.mekrs_variant_id || "").trim()) ||
                  Boolean((row.hopefix_product_id || "").trim()) ||
                  Boolean((row.haspl_variant_code || "").trim()) ||
                  Boolean((row.inoxmare_product_id || "").trim()) ||
                  Boolean((row.argip_sku || "").trim()),
              ));
          if (pvarsNeedVariantPick) {
            const baseCk = cartStorageKey(sid, code, null);
            const argipShopPk0 =
              supplierNameIsArgip(offer.supplier) &&
              pvars[0]?.shop_pack_quantity != null &&
              pvars[0]?.shop_pack_quantity >= 1
                ? pvars[0].shop_pack_quantity
                : null;
            const pk0 =
              argipShopPk0 ??
              (pvars[0]?.pack_quantity != null && pvars[0]?.pack_quantity >= 1
                ? pvars[0].pack_quantity
                : null);
            if (typeof pk0 === "number" && pk0 >= 1) {
              setCartQuantityByKey((prev) => {
                if (prev[baseCk] !== undefined && !(supplierNameIsArgip(offer.supplier) && prev[baseCk] === 1 && pk0 > 1)) {
                  return prev;
                }
                return { ...prev, [baseCk]: pk0 };
              });
            }
            setPackVariantIndexByKey((prev) => {
              if (prev[baseCk] !== undefined) {
                return prev;
              }
              return { ...prev, [baseCk]: 0 };
            });
          } else {
            const pkFromApi =
              payload.pack_quantity != null && payload.pack_quantity >= 1
                ? payload.pack_quantity
                : null;
            const argipShopPk =
              supplierNameIsArgip(offer.supplier) &&
              payload.shop_pack_quantity != null &&
              payload.shop_pack_quantity >= 1
                ? payload.shop_pack_quantity
                : null;
            let pkFromRaw: number | null = null;
            if (pkFromApi == null && payload.raw_pack_quantity?.trim()) {
              const m = payload.raw_pack_quantity
                .trim()
                .replace(/\s/g, "")
                .match(/^(\d+)/);
              if (m) {
                const n = parseInt(m[1], 10);
                if (Number.isFinite(n) && n >= 1) pkFromRaw = n;
              }
            }
            const pkPrefill = argipShopPk ?? pkFromApi ?? pkFromRaw;
            if (pkPrefill != null && pkPrefill >= 1) {
              const cartKey = cartStorageKey(sid, code, null);
              setCartQuantityByKey((prev) => {
                if (prev[cartKey] !== undefined && !(supplierNameIsArgip(offer.supplier) && prev[cartKey] === 1 && pkPrefill > 1)) {
                  return prev;
                }
                return { ...prev, [cartKey]: pkPrefill };
              });
            }
          }
        } catch (error) {
          setScrapeByKey((p) => ({
            ...p,
            [key]: {
              loading: false,
              error:
                error instanceof Error
                  ? error.message
                  : "Nepodarilo sa načítať údaje.",
              logged_in: false,
            },
          }));
        }
      })();
    }
  }, [openProduct, activeView, listOpenProductRow, apiFetch]);

  const refreshDevStepScreenshots = useCallback(async () => {
    try {
      const response = await apiFetch(`${API_BASE}/api/dev/step-screenshots`);
      const payload = (await response.json()) as {
        override?: boolean | null;
      };
      if (!response.ok) {
        throw new Error("step-screenshots");
      }
      if (payload.override === true) {
        setDevStepScreenshotMode("on");
      } else if (payload.override === false) {
        setDevStepScreenshotMode("off");
      } else {
        setDevStepScreenshotMode("env");
      }
    } catch {
      setDevStepScreenshotMode("env");
    }
  }, [apiFetch]);

  const loadProductLists = useCallback(async () => {
    try {
      const r = await apiFetch(`${API_BASE}/api/lists`);
      if (!r.ok) {
        throw new Error("Nepodarilo sa načítať zoznamy.");
      }
      const payload = (await r.json()) as { lists?: ProductListRow[] };
      const rows = Array.isArray(payload.lists) ? payload.lists : [];
      setProductLists(rows);
      if (rows.length === 0) {
        setActiveListId(null);
        setActiveListItems([]);
        return;
      }
      const firstId = rows[0]?.id ?? null;
      setActiveListId((prev) => {
        if (prev != null && rows.some((x) => x.id === prev)) return prev;
        return firstId;
      });
    } catch (error) {
      setListStatus(error instanceof Error ? error.message : "Chyba zoznamov.");
    }
  }, [apiFetch]);

  const loadListDetail = useCallback(
    async (listId: number) => {
      try {
        const r = await apiFetch(`${API_BASE}/api/lists/${listId}`);
        if (!r.ok) {
          throw new Error("Nepodarilo sa načítať položky zoznamu.");
        }
        const payload = (await r.json()) as { items?: ProductListItemRow[] };
        setActiveListItems(Array.isArray(payload.items) ? payload.items : []);
      } catch (error) {
        setListStatus(
          error instanceof Error ? error.message : "Chyba položiek zoznamu.",
        );
      }
    },
    [apiFetch],
  );

  useEffect(() => {
    if (activeView !== "zoznamy") return;
    void loadProductLists();
  }, [activeView, loadProductLists]);

  useEffect(() => {
    if (!apiToken) return;
    void loadProductLists();
  }, [apiToken, loadProductLists]);

  useEffect(() => {
    if (activeView !== "zoznamy" || activeListId == null) return;
    void loadListDetail(activeListId);
  }, [activeView, activeListId, loadListDetail]);

  useEffect(() => {
    if (activeView === "dev") {
      void refreshDevStepScreenshots();
    }
  }, [activeView, refreshDevStepScreenshots]);

  const applyDevStepScreenshotMode = async (mode: "on" | "off" | "env") => {
    const override = mode === "env" ? null : mode === "on";
    try {
      const response = await apiFetch(`${API_BASE}/api/dev/step-screenshots`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ override }),
      });
      const payload = (await response.json()) as {
        override?: boolean | null;
      };
      if (!response.ok) {
        throw new Error("save");
      }
      if (payload.override === true) {
        setDevStepScreenshotMode("on");
      } else if (payload.override === false) {
        setDevStepScreenshotMode("off");
      } else {
        setDevStepScreenshotMode("env");
      }
    } catch {
      setToastMessage("Nepodarilo sa uložiť nastavenie screenshotov.");
    }
  };

  useEffect(() => {
    if (activeView !== "dev" || devLogPaused) {
      return;
    }
    const load = async () => {
      try {
        const response = await apiFetch(`${API_BASE}/api/dev/logs?limit=4000`);
        const payload = (await response.json()) as { logs?: DevLogEntry[] };
        if (!response.ok) {
          throw new Error("Nepodarilo sa načítať log.");
        }
        setDevLogEntries(payload.logs ?? []);
        setDevLogError(null);
      } catch (error) {
        setDevLogError(
          error instanceof Error ? error.message : "Chyba pri načítaní logu.",
        );
      }
    };
    void load();
    const id = window.setInterval(() => void load(), 1200);
    return () => window.clearInterval(id);
  }, [activeView, devLogPaused, apiFetch]);

  useEffect(() => {
    if (activeView === "admin" && !isAppAdmin) {
      setActiveView("vyhladavanie");
    }
  }, [activeView, isAppAdmin]);

  useEffect(() => {
    if (activeView !== "admin" || !isAppAdmin || !apiToken) {
      return;
    }
    let cancelled = false;
    void (async () => {
      setAdminUsersError(null);
      try {
        const r = await apiFetch(`${API_BASE}/api/admin/users`);
        const payload = (await r.json()) as
          | Array<{
              id: number;
              username: string;
              display_label: string | null;
              is_admin: boolean;
            }>
          | { detail?: unknown };
        if (!r.ok) {
          throw new Error(formatApiDetail((payload as { detail?: unknown }).detail));
        }
        if (!cancelled) {
          setAdminUsers(
            payload as Array<{
              id: number;
              username: string;
              display_label: string | null;
              is_admin: boolean;
            }>,
          );
        }
      } catch (e) {
        if (!cancelled) {
          setAdminUsersError(
            e instanceof Error ? e.message : "Nepodarilo sa načítať používateľov.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeView, isAppAdmin, apiToken, apiFetch]);

  useEffect(() => {
    if (
      (activeView !== "ponuky" && activeView !== "admin") ||
      !apiToken ||
      !authSessionReady
    ) {
      return;
    }
    void apiFetch(`${API_BASE}/api/company-settings`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { company_name?: string } | null) => {
        setCompanyConfigured(Boolean(d?.company_name?.trim()));
      })
      .catch(() => setCompanyConfigured(null));
  }, [activeView, apiToken, authSessionReady, apiFetch]);

  const refreshDevLogs = async () => {
    try {
      const response = await apiFetch(`${API_BASE}/api/dev/logs?limit=4000`);
      const payload = (await response.json()) as { logs?: DevLogEntry[] };
      if (!response.ok) {
        throw new Error("Nepodarilo sa načítať log.");
      }
      setDevLogEntries(payload.logs ?? []);
      setDevLogError(null);
    } catch (error) {
      setDevLogError(
        error instanceof Error ? error.message : "Chyba pri načítaní logu.",
      );
    }
  };

  const clearDevLogs = async () => {
    try {
      const response = await apiFetch(`${API_BASE}/api/dev/logs`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error("Nepodarilo sa vymazať log.");
      }
      setDevLogEntries([]);
      setDevSupplierFilter("all");
      setDevLogError(null);
    } catch (error) {
      setDevLogError(
        error instanceof Error ? error.message : "Chyba pri mazaní logu.",
      );
    }
  };

  const saveFieldMapping = async () => {
    if (!isAppAdmin) {
      setMappingStatus("Uložiť mapovanie polí môže len administrátor.");
      return;
    }
    setMappingStatus("Ukladám mapovanie…");
    try {
      const response = await apiFetch(`${API_BASE}/api/mapping/fields`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: fieldToColumn.code || null,
          norma: fieldToColumn.norma || null,
          surface: fieldToColumn.surface || null,
          diameter: fieldToColumn.diameter || null,
          length: fieldToColumn.length || null,
          v_class: fieldToColumn.v_class || null,
          y_money_name: fieldToColumn.y_money_name || null,
          image_filename: fieldToColumn.image_filename || null,
        }),
      });
      const data = (await response.json()) as { ok?: boolean; detail?: string };
      if (!response.ok) {
        throw new Error(data.detail ?? "Nepodarilo sa uložiť mapovanie.");
      }
      setMappingStatus(
        "Mapovanie uložené. Ak si zmenil stĺpce, znova spusti import Excelu do databázy.",
      );
    } catch (error) {
      setMappingStatus(
        error instanceof Error ? error.message : "Chyba pri ukladaní mapovania.",
      );
    }
  };

  const loadFieldMapping = useCallback(async () => {
    try {
      const response = await apiFetch(`${API_BASE}/api/mapping/fields`);
      if (!response.ok) {
        return;
      }
      const data = (await response.json()) as Record<string, string | null>;
      setFieldToColumn({
        code: data.code ?? "",
        norma: data.norma ?? "",
        surface: data.surface ?? "",
        diameter: data.diameter ?? "",
        length: data.length ?? "",
        v_class: data.v_class ?? "",
        y_money_name: data.y_money_name ?? "",
        image_filename: data.image_filename ?? "",
      });
    } catch {
      // ignore
    }
  }, [apiFetch]);

  useEffect(() => {
    void loadFieldMapping();
  }, [loadFieldMapping]);

  /** Profil stĺpcov z Excelu (unique_values) — inak sú filtre prázdne pri prázdnej DB alebo bez kliku „Načítať stĺpce“. */
  useEffect(() => {
    const path = excelFilePath.trim();
    const sheet = (sheetName || "DIN").trim() || "DIN";
    if (!path) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const response = await apiFetch(`${API_BASE}/api/mapping/profile`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            file_path: path,
            sheet_name: sheet,
          }),
        });
        const payload = (await response.json()) as
          | MappingProfile
          | { detail?: string };
        if (!response.ok || cancelled) {
          return;
        }
        setMappingProfile(payload as MappingProfile);
      } catch {
        // nesprávna cesta / API — ticho; používateľ uvidí stav pri ručnom „Načítať stĺpce“
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [excelFilePath, sheetName, apiFetch]);

  useEffect(() => {
    if (activeView !== "vyhladavanie") {
      return;
    }
    if (!authSessionReady) {
      setSearchMessage("Načítavam prihlásenie…");
      return;
    }
    if (authConfigError) {
      setSearchMessage(
        "Chýba SMARTHUB_AUTH_SECRET (min. 16 znakov) v .env.local — rovnaká hodnota ako na FastAPI. Bez toho API neprijme požiadavky.",
      );
      setSearchResults([]);
      return;
    }
    if (!apiToken) {
      setSearchMessage("Pre vyhľadávanie sa prihlás (alebo obnov stránku po prihlásení).");
      setSearchResults([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      const apiBody = {
        code: debouncedCode.trim() || null,
        norma: searchFilters.norma || null,
        surface: searchFilters.surface || null,
        diameter: searchFilters.diameter || null,
        length: searchFilters.length || null,
        v_class: searchFilters.v_class || null,
        y_money_name: searchFilters.y_money_name || null,
        prefetch_live_prices: false,
      };
      const noFiltersActive =
        !apiBody.code &&
        !apiBody.norma &&
        !apiBody.surface &&
        !apiBody.diameter &&
        !apiBody.length &&
        !apiBody.v_class &&
        !apiBody.y_money_name;

      // Cold-start optimalizácia: pri prvom otvorení Vyhľadávania bez aktívnych filtrov
      // urobíme jediný request /api/bootstrap/search, ktorý vráti filter-options aj
      // prvých 25 produktov. Ušetrí to 1 round-trip + 1 SELECT cez celú DB tabuľku.
      if (!bootstrapDoneRef.current && noFiltersActive) {
        try {
          setSearchMessage("Pripájam sa k API…");
          const bootRes = await apiFetch(`${API_BASE}/api/bootstrap/search`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: null, limit: 25 }),
          });
          if (cancelled) return;
          if (bootRes.ok) {
            const boot = (await bootRes.json()) as {
              filter_options?: Partial<FilterOptions>;
              products?: ProductSearchRow[];
            };
            if (cancelled) return;
            const rawOpts = boot.filter_options ?? {};
            const opts = mergeConditionalFilterOptionsWithExcel(
              {
                norma: rawOpts.norma ?? [],
                surface: rawOpts.surface ?? [],
                diameter: rawOpts.diameter ?? [],
                length: rawOpts.length ?? [],
                v_class: rawOpts.v_class ?? [],
                y_money_name: rawOpts.y_money_name ?? [],
              },
              mappingProfile,
              fieldToColumn,
              false,
            );
            setFilterOptions((prev) =>
              filterOptionsArraysEqual(prev, opts) ? prev : opts,
            );
            const rows = normalizeProductSearchRows(boot.products ?? []);
            setSearchResults(rows);
            setSearchMessage(
              rows.length === 0
                ? "Žiadne záznamy v databáze. Spusti import Excelu (Mapovanie)."
                : `Nájdených záznamov: ${rows.length}.`,
            );
            setOpenProduct(null);
            bootstrapDoneRef.current = true;
            return;
          }
          // Fallback na klasický 2-step flow nižšie.
        } catch (error) {
          if (cancelled) return;
          const raw = error instanceof Error ? error.message : "";
          if (isBrowserFetchNetworkError(raw)) {
            setSearchMessage(apiUnreachableUserMessage(API_BASE));
            setSearchResults([]);
            return;
          }
          // Inak skúsime klasický flow nižšie (bootstrap nemusí byť ešte deploynutý).
        }
      }

      setSearchMessage(bootstrapDoneRef.current ? "Hľadám…" : "Pripájam sa k API…");
      try {
        const optRes = await apiFetch(
          `${API_BASE}/api/products/filter-options/conditional`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(apiBody),
          },
        );
        if (!optRes.ok || cancelled) {
          if (!cancelled) {
            setSearchResults([]);
            setOpenProduct(null);
            setSearchMessage(
              `Backend nedostupný (HTTP ${optRes.status}). Spusti API (${API_BASE}) alebo skontroluj .env.local.`,
            );
          }
          return;
        }
        const rawOpts = (await optRes.json()) as Partial<FilterOptions>;
        if (cancelled) {
          return;
        }
        const cascadeActive =
          Boolean(debouncedCode.trim()) ||
          Boolean(searchFilters.norma) ||
          Boolean(searchFilters.surface) ||
          Boolean(searchFilters.diameter) ||
          Boolean(searchFilters.length) ||
          Boolean(searchFilters.v_class) ||
          Boolean(searchFilters.y_money_name);
        const opts = mergeConditionalFilterOptionsWithExcel(
          {
            norma: rawOpts.norma ?? [],
            surface: rawOpts.surface ?? [],
            diameter: rawOpts.diameter ?? [],
            length: rawOpts.length ?? [],
            v_class: rawOpts.v_class ?? [],
            y_money_name: rawOpts.y_money_name ?? [],
          },
          mappingProfile,
          fieldToColumn,
          cascadeActive,
        );
        setFilterOptions((prev) =>
          filterOptionsArraysEqual(prev, opts) ? prev : opts,
        );

        const selectSnapshot = {
          norma: searchFilters.norma,
          surface: searchFilters.surface,
          diameter: searchFilters.diameter,
          length: searchFilters.length,
          v_class: searchFilters.v_class,
          y_money_name: searchFilters.y_money_name,
        };
        const pruned = pruneSelectFilters(selectSnapshot, opts);
        if (
          pruned.norma !== selectSnapshot.norma ||
          pruned.surface !== selectSnapshot.surface ||
          pruned.diameter !== selectSnapshot.diameter ||
          pruned.length !== selectSnapshot.length ||
          pruned.v_class !== selectSnapshot.v_class ||
          pruned.y_money_name !== selectSnapshot.y_money_name
        ) {
          setSearchFilters((prev) => ({ ...prev, ...pruned }));
          return;
        }

        setSearchMessage("Hľadám…");
        const initialLimit = bootstrapDoneRef.current ? 50 : 25;
        const searchRes = await apiFetch(`${API_BASE}/api/products/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...apiBody, limit: initialLimit }),
        });
        const payload = (await searchRes.json()) as
          | ProductSearchRow[]
          | { detail?: string };
        if (!searchRes.ok || cancelled) {
          throw new Error(
            "detail" in payload && payload.detail
              ? String(payload.detail)
              : "Chyba vyhľadávania.",
          );
        }
        const rows = normalizeProductSearchRows(payload as ProductSearchRow[]);
        if (cancelled) {
          return;
        }
        setSearchResults(rows);
        setSearchMessage(
          rows.length === 0
            ? "Žiadne záznamy v databáze pre tento filter. Skontroluj import Excelu (backend) a mapovanie stĺpcov."
            : `Nájdených záznamov: ${rows.length}.`,
        );
        setOpenProduct(null);
        bootstrapDoneRef.current = true;
      } catch (error) {
        if (!cancelled) {
          const raw =
            error instanceof Error ? error.message : "Chyba vyhľadávania.";
          setSearchMessage(
            isBrowserFetchNetworkError(raw)
              ? apiUnreachableUserMessage(API_BASE)
              : raw || "Chyba vyhľadávania.",
          );
          setSearchResults([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    activeView,
    debouncedCode,
    searchFilters.norma,
    searchFilters.surface,
    searchFilters.diameter,
    searchFilters.length,
    searchFilters.v_class,
    searchFilters.y_money_name,
    searchTick,
    fieldToColumn,
    mappingProfile,
    apiFetch,
    authSessionReady,
    authConfigError,
    apiToken,
  ]);

  useEffect(() => {
    if (activeView !== "dodavatelia") {
      return;
    }
    void refetchSuppliersList();
  }, [activeView, refetchSuppliersList]);

  const addToCart = async (
    supplierId: number,
    supplierCode: string,
    supplierName: string,
    quantity: number,
    packagingVariantIndex: number | null = null,
    historyMeta: AddToCartHistoryMeta | null = null,
    mekrsProductVariantId: string | null = null,
    hopefixProductId: string | null = null,
    hopefixPackageType: string | null = null,
    hopefixRefererPath: string | null = null,
    hasplVariantCode: string | null = null,
    inoxmareProductId: string | null = null,
    inoxmareRefererPath: string | null = null,
  ) => {
    const feedbackKey = cartStorageKey(supplierId, supplierCode, null);
    const qty =
      Number.isFinite(quantity) && quantity >= 1
        ? Math.min(Math.floor(quantity), 999_999)
        : 1;
    const prevTimer = cartSuccessClearTimersRef.current[feedbackKey];
    if (prevTimer != null) {
      window.clearTimeout(prevTimer);
      delete cartSuccessClearTimersRef.current[feedbackKey];
    }
    setCartAddSuccessByKey((prev) => {
      if (!(feedbackKey in prev)) {
        return prev;
      }
      const next = { ...prev };
      delete next[feedbackKey];
      return next;
    });
    const busyMsg =
      mekrsProductVariantId?.trim() ||
      hopefixProductId?.trim() ||
      hasplVariantCode?.trim() ||
      inoxmareProductId?.trim()
        ? "Pridávam do košíka (API)…"
        : "Spúšťam prehliadač…";
    setCartFeedback((prev) => ({
      ...prev,
      [feedbackKey]: busyMsg,
    }));
    try {
      const response = await apiFetch(`${API_BASE}/api/cart/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          supplier_id: supplierId,
          supplier_code: supplierCode,
          quantity: qty,
          packaging_variant_index: packagingVariantIndex,
          mekrs_product_variant_id: mekrsProductVariantId?.trim() || null,
          hopefix_product_id: hopefixProductId?.trim() || null,
          hopefix_package_type: hopefixPackageType?.trim() || null,
          hopefix_referer_path: hopefixRefererPath?.trim() || null,
          haspl_variant_code: hasplVariantCode?.trim() || null,
          inoxmare_product_id: inoxmareProductId?.trim() || null,
          inoxmare_referer_path: inoxmareRefererPath?.trim() || null,
        }),
      });
      const parsed = await readApiJsonOrText(response);
      if (!parsed.ok) {
        throw new Error(parsed.detail);
      }
      const payload = parsed.data as { ok?: boolean; detail?: unknown };
      if (!response.ok) {
        throw new Error(formatApiDetail(payload.detail));
      }
      setCartFeedback((prev) => {
        const next = { ...prev };
        delete next[feedbackKey];
        return next;
      });
      setCartAddSuccessByKey((prev) => ({ ...prev, [feedbackKey]: true }));
      const clearId = window.setTimeout(() => {
        setCartAddSuccessByKey((prev) => {
          if (!prev[feedbackKey]) {
            return prev;
          }
          const next = { ...prev };
          delete next[feedbackKey];
          return next;
        });
        delete cartSuccessClearTimersRef.current[feedbackKey];
      }, 10_000);
      cartSuccessClearTimersRef.current[feedbackKey] = clearId;
      if (historyMeta) {
        const hid =
          typeof crypto !== "undefined" && "randomUUID" in crypto
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
        const offerNoteSnap = historyMeta.offerNote?.trim() || null;
        setCartHistory((prev) =>
          [
            {
              id: hid,
              addedAtIso: new Date().toISOString(),
              internalCode: historyMeta.internalCode,
              supplierId,
              supplierName,
              supplierCode,
              quantity: qty,
              packagingVariantIndex: packagingVariantIndex,
              variantLabel: historyMeta.variantLabel,
              priceEur: historyMeta.priceEur,
              priceUnit: historyMeta.priceUnit,
              logoUrl: historyMeta.logoUrl,
              norma: historyMeta.norma,
              diameter: historyMeta.diameter,
              length: historyMeta.length,
              surface: historyMeta.surface,
              yMoneyName: historyMeta.yMoneyName,
              packQuantity: historyMeta.packQuantity,
              offerNote: offerNoteSnap,
            },
            ...prev,
          ].slice(0, CART_HISTORY_MAX),
        );
      }
      setToastMessage(
        qty === 1
          ? `Produkt bol úspešne pridaný do košíka u ${supplierName}`
          : `${formatIntegerCsThousands(qty)} ks bolo pridaných do košíka u ${supplierName}`,
      );
    } catch (error) {
      setCartFeedback((prev) => ({
        ...prev,
        [feedbackKey]:
          error instanceof Error ? error.message : "Nepodarilo sa pridať do košíka.",
      }));
    }
  };

  const updateSupplierField = (
    index: number,
    field:
      | "name"
      | "shopUrl"
      | "username"
      | "password"
      | "codeColumn"
      | "cartConfigJson"
      | "freeShippingThresholdEur",
    value: string,
  ) => {
    setSupplierForms((prev) =>
      prev.map((supplier, rowIndex) =>
        rowIndex === index ? { ...supplier, [field]: value } : supplier,
      ),
    );
  };

  const moveSupplierRow = async (index: number, direction: -1 | 1) => {
    if (!isAppAdmin) {
      return;
    }
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= supplierForms.length) {
      return;
    }
    if (!supplierForms.every((s) => s.id != null)) {
      setToastMessage(
        "Poradie môžeš meniť až po uložení všetkých nových dodávateľov.",
      );
      return;
    }
    const prevSnap = supplierForms;
    const copy = [...prevSnap];
    const a = copy[index]!;
    const b = copy[nextIndex]!;
    copy[index] = b;
    copy[nextIndex] = a;
    setSupplierForms(copy);
    try {
      await commitSupplierOrder(copy.map((s) => Number(s.id)));
    } catch (error) {
      setSupplierForms(prevSnap);
      setToastMessage(
        error instanceof Error ? error.message : "Nepodarilo sa uložiť poradie.",
      );
    }
  };

  const supplierListCanReorder = useMemo(
    () => isAppAdmin && supplierForms.every((s) => s.id != null),
    [isAppAdmin, supplierForms],
  );

  const addSupplierForm = () => {
    if (!isAppAdmin) {
      setToastMessage("Nového dodávateľa môže pridať iba administrátor.");
      return;
    }
    setSupplierForms((prev) => [
      ...prev,
      {
        name: "",
        shopUrl: "",
        username: "",
        password: "",
        isConnected: false,
        codeColumn: "",
        cartConfigJson: "",
        freeShippingThresholdEur: "",
        logoUrl: null,
      },
    ]);
  };

  const supplierCardKey = (supplier: SupplierForm, index: number) =>
    supplier.id != null ? `s-${supplier.id}` : `i-${index}`;

  const toggleSupplierCard = (key: string) => {
    setExpandedSupplierKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const saveSupplier = async (index: number) => {
    const data = supplierForms[index];
    const stateKey = data.id ?? index;
    if (!data.shopUrl || !data.username || !data.password) {
      setSaveState((prev) => ({
        ...prev,
        [stateKey]: "Vypln prosim URL, pouzivatela aj heslo.",
      }));
      return;
    }
    setSaveState((prev) => ({ ...prev, [stateKey]: "Ukladam..." }));
    try {
      if (!isAppAdmin && !data.id) {
        setSaveState((prev) => ({
          ...prev,
          [stateKey]:
            "Nového dodávateľa môže pridať len administrátor. Uloženie šablóny je v sekcii Dodávatelia (admin).",
        }));
        return;
      }
      if (!isAppAdmin && data.id) {
        const credRes = await apiFetch(
          `${API_BASE}/api/users/me/supplier-credentials`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              supplier_id: data.id,
              username: data.username,
              password: data.password,
            }),
          },
        );
        const credPayload = (await credRes.json()) as { detail?: unknown };
        if (!credRes.ok) {
          throw new Error(formatApiDetail(credPayload.detail));
        }
        setSaveState((prev) => ({
          ...prev,
          [stateKey]: "Prihlasovacie udaje pobočky uložené.",
        }));
        return;
      }
      const thrRaw = data.freeShippingThresholdEur
        .trim()
        .replace(/\s/g, "")
        .replace(",", ".");
      const thrParsed =
        thrRaw === ""
          ? null
          : Number.isFinite(Number(thrRaw)) && Number(thrRaw) >= 0
            ? Number(thrRaw)
            : null;
      const response = await apiFetch(`${API_BASE}/api/suppliers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: data.id,
          name: data.name,
          shop_url: data.shopUrl,
          username: data.username,
          password: data.password,
          is_connected: data.isConnected,
          code_column: data.codeColumn || null,
          cart_config_json: data.cartConfigJson.trim() || null,
          free_shipping_threshold_eur: thrParsed,
        }),
      });

      const payload = (await response.json()) as
        | {
            id: number;
            name: string;
            shop_url: string;
            is_connected: boolean;
            code_column?: string | null;
            cart_config_json?: string | null;
            logo_url?: string | null;
            free_shipping_threshold_eur?: number | null;
            sort_order?: number | null;
          }
        | { detail?: unknown };

      if (!response.ok) {
        throw new Error(
          "detail" in payload
            ? formatApiDetail(payload.detail)
            : "Nepodarilo sa ulozit pristup.",
        );
      }

      const saved = payload as {
        id: number;
        name: string;
        shop_url: string;
        is_connected: boolean;
        code_column?: string | null;
        cart_config_json?: string | null;
        logo_url?: string | null;
        free_shipping_threshold_eur?: number | null;
        sort_order?: number | null;
      };

      setSaveState((prev) => ({
        ...prev,
        [stateKey]: "Dodavatel bol uspesne ulozeny.",
      }));

      setSupplierForms((prev) =>
        prev.map((supplier, rowIndex) =>
          rowIndex === index
            ? {
                ...supplier,
                id: saved.id,
                name: saved.name,
                shopUrl: saved.shop_url,
                isConnected: saved.is_connected,
                codeColumn: saved.code_column ?? supplier.codeColumn,
                cartConfigJson: saved.cart_config_json ?? supplier.cartConfigJson,
                freeShippingThresholdEur:
                  saved.free_shipping_threshold_eur != null &&
                  Number.isFinite(saved.free_shipping_threshold_eur)
                    ? String(saved.free_shipping_threshold_eur)
                    : "",
                logoUrl:
                  saved.logo_url !== undefined
                    ? saved.logo_url
                    : supplier.logoUrl,
                sortOrder:
                  saved.sort_order != null && Number.isFinite(saved.sort_order)
                    ? saved.sort_order
                    : supplier.sortOrder,
              }
            : supplier,
        ),
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Nepodarilo sa ulozit pristup.";
      setSaveState((prev) => ({ ...prev, [stateKey]: message }));
    }
  };

  const createBranchAccount = async () => {
    const u = newBranchUsername.trim();
    const p = newBranchPassword;
    if (!u || !p) {
      setAdminUsersError("Vyplň prihlasovacie meno a heslo pre novú pobočku.");
      return;
    }
    setAdminUserSubmitting(true);
    setAdminUsersError(null);
    try {
      const r = await apiFetch(`${API_BASE}/api/admin/users`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: u,
          password: p,
          display_label: newBranchLabel.trim() || null,
        }),
      });
      const payload = (await r.json().catch(() => ({}))) as { detail?: unknown };
      if (!r.ok) {
        throw new Error(formatApiDetail(payload.detail));
      }
      setNewBranchUsername("");
      setNewBranchPassword("");
      setNewBranchLabel("");
      const listRes = await apiFetch(`${API_BASE}/api/admin/users`);
      if (listRes.ok) {
        setAdminUsers(
          (await listRes.json()) as Array<{
            id: number;
            username: string;
            display_label: string | null;
            is_admin: boolean;
          }>,
        );
      }
    } catch (e) {
      setAdminUsersError(
        e instanceof Error ? e.message : "Nepodarilo sa vytvoriť účet.",
      );
    } finally {
      setAdminUserSubmitting(false);
    }
  };

  const deleteSupplier = async (index: number) => {
    const row = supplierForms[index];
    if (!row.id) {
      if (
        !window.confirm(
          "Odstrániť tento neuložený riadok z formulára?",
        )
      ) {
        return;
      }
      setSupplierForms((prev) => prev.filter((_, i) => i !== index));
      setSaveState((prev) => {
        const next = { ...prev };
        delete next[index];
        return next;
      });
      return;
    }
    if (!isAppAdmin) {
      const sid = row.id ?? index;
      setSaveState((prev) => ({
        ...prev,
        [sid]:
          "Odstrániť dodávateľa z centrálnej šablóny môže len administrátor.",
      }));
      return;
    }
    if (
      !window.confirm(
        `Naozaj odstrániť dodávateľa „${row.name}“? Zmažú sa aj všetky mapovania produktov na neho.`,
      )
    ) {
      return;
    }
    const stateKey = row.id;
    const supplierId = Number(row.id);
    if (!Number.isInteger(supplierId) || supplierId < 1) {
      setSaveState((prev) => ({
        ...prev,
        [stateKey]:
          "Neplatné ID dodávateľa. Otvor znova sekciu Dodávatelia (načíta sa z API) a skús odstránenie.",
      }));
      return;
    }
    setSaveState((prev) => ({ ...prev, [stateKey]: "Mažem..." }));
    try {
      const detailFromPayload = (payload: { detail?: unknown }) =>
        "detail" in payload && payload.detail !== undefined
          ? formatApiDetail(payload.detail)
          : "";
      const isFastApiNotFoundRoute = (status: number, raw: string) =>
        status === 404 && /^not found$/i.test(raw.trim());
      const isSupplierMissingInDb = (status: number, raw: string) => {
        if (status !== 404) {
          return false;
        }
        const t = raw.trim().toLowerCase();
        return (
          t.includes("neexistuje") ||
          /^supplier not found/i.test(raw.trim())
        );
      };
      const attempts: Array<{ label: string; run: () => Promise<Response> }> = [
        {
          label: "POST /api/suppliers/remove",
          run: () =>
            apiFetch(`${API_BASE}/api/suppliers/remove`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ supplier_id: supplierId }),
            }),
        },
        {
          label: `DELETE /api/suppliers/${supplierId}`,
          run: () =>
            apiFetch(`${API_BASE}/api/suppliers/${supplierId}`, {
              method: "DELETE",
            }),
        },
        {
          label: "POST /api/supplier/remove",
          run: () =>
            apiFetch(`${API_BASE}/api/supplier/remove`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ supplier_id: supplierId }),
            }),
        },
      ];
      let response: Response | null = null;
      let payload: { detail?: unknown } = {};
      for (const step of attempts) {
        response = await step.run();
        payload = (await response.json().catch(() => ({}))) as {
          detail?: unknown;
        };
        const raw = detailFromPayload(payload);
        if (response.ok) {
          break;
        }
        if (isSupplierMissingInDb(response.status, raw)) {
          throw new Error(
            raw ||
              "Dodávateľ v databáze neexistuje (zastaralý zoznam). Obnov stránku alebo znova otvor Dodávatelia.",
          );
        }
        const tryNext =
          response.status === 404 &&
          (isFastApiNotFoundRoute(response.status, raw) || raw.trim() === "");
        if (tryNext) {
          continue;
        }
        throw new Error(raw || `HTTP ${response.status}`);
      }
      if (!response?.ok) {
        throw new Error(
          `Backend neodpovedá na odstránenie dodávateľa (${attempts.map((a) => a.label).join(" → ")}). Spusti uvicorn z priečinka backend a over NEXT_PUBLIC_API_BASE_URL (teraz ${API_BASE}).`,
        );
      }
      setSupplierForms((prev) => prev.filter((_, i) => i !== index));
      setSaveState((prev) => {
        const next = { ...prev };
        delete next[stateKey];
        return next;
      });
      setExpandedRemoteSupplierId((cur) => (cur === row.id ? null : cur));
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Nepodarilo sa odstrániť dodávateľa.";
      setSaveState((prev) => ({ ...prev, [stateKey]: message }));
    }
  };

  const uploadSupplierLogo = async (index: number, file: File) => {
    const row = supplierForms[index];
    if (!row.id) {
      setToastMessage("Najprv ulož dodávateľa, potom môžeš nahrať logo.");
      return;
    }
    const form = new FormData();
    form.append("file", file);
    try {
      const response = await apiFetch(`${API_BASE}/api/suppliers/${row.id}/logo`, {
        method: "POST",
        body: form,
      });
      const payload = (await response.json()) as
        | { ok?: boolean; logo_url?: string | null; detail?: unknown }
        | { detail?: unknown };
      if (!response.ok) {
        throw new Error(formatApiDetail((payload as { detail?: unknown }).detail));
      }
      const logoUrl = (payload as { logo_url?: string | null }).logo_url ?? null;
      setSupplierForms((prev) =>
        prev.map((s, i) => (i === index ? { ...s, logoUrl } : s)),
      );
      setToastMessage("Logo bolo nahraté.");
    } catch (error) {
      setToastMessage(
        error instanceof Error ? error.message : "Nepodarilo sa nahrať logo.",
      );
    }
  };

  const removeSupplierLogo = async (index: number) => {
    const row = supplierForms[index];
    if (!row.id) {
      return;
    }
    try {
      const response = await apiFetch(`${API_BASE}/api/suppliers/${row.id}/logo`, {
        method: "DELETE",
      });
      const payload = (await response.json()) as { detail?: unknown };
      if (!response.ok) {
        throw new Error(formatApiDetail(payload.detail));
      }
      setSupplierForms((prev) =>
        prev.map((s, i) => (i === index ? { ...s, logoUrl: null } : s)),
      );
      setToastMessage("Logo bolo odstránené.");
    } catch (error) {
      setToastMessage(
        error instanceof Error ? error.message : "Nepodarilo sa zmazať logo.",
      );
    }
  };

  const loadMappingProfile = async () => {
    setMappingProfileLoading(true);
    setMappingStatus("Nacitavam stlpce zo suboru...");
    try {
      const response = await apiFetch(`${API_BASE}/api/mapping/profile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_path: excelFilePath,
          sheet_name: sheetName,
        }),
      });

      const payload = (await response.json()) as
        | MappingProfile
        | { detail?: string };
      if (!response.ok) {
        throw new Error(
          "detail" in payload && payload.detail
            ? payload.detail
            : "Nepodarilo sa nacitat profil stlpcov.",
        );
      }

      setMappingProfile(payload as MappingProfile);
      setMappingStatus("Profil stlpcov uspesne nacitany.");
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Nepodarilo sa nacitat profil stlpcov.";
      setMappingStatus(message);
    } finally {
      setMappingProfileLoading(false);
    }
  };

  const runExcelImport = async () => {
    if (!isAppAdmin) {
      setMappingStatus("Import do databázy môže spustiť len administrátor.");
      setExcelImportProgressPct(null);
      return;
    }
    const path = excelFilePath.trim();
    if (!path) {
      setMappingStatus("Zadaj cestu k XLSX súboru.");
      setExcelImportProgressPct(null);
      return;
    }
    setExcelImportRunning(true);
    setExcelImportProgressPct(0);
    setMappingStatus("Importujem Excel do databázy…");
    try {
      const sheet = sheetName.trim() || "DIN";
      const startResponse = await apiFetch(`${API_BASE}/api/import/excel/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: path, sheet_name: sheet }),
      });
      const startPayload = (await startResponse.json()) as {
        detail?: unknown;
        task_id?: string;
      };
      if (!startResponse.ok || !startPayload.task_id) {
        throw new Error(formatApiDetail(startPayload.detail));
      }
      const taskId = startPayload.task_id;
      type ImportTaskResponse = {
        detail?: unknown;
        state?: string;
        progress_pct?: number;
        rows_scanned?: number;
        total_rows?: number;
        error?: string;
        result?: {
          products_upserted?: number;
          products_legacy_removed?: number;
          suppliers_upserted?: number;
          mappings_upserted?: number;
          rows_scanned?: number;
          total_rows?: number;
          file_resolved?: string;
          warnings?: string[];
        };
      };
      let finalPayload: ImportTaskResponse | null = null;
      while (true) {
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
        const statusResponse = await apiFetch(`${API_BASE}/api/import/excel/${taskId}`);
        const statusPayload = (await statusResponse.json()) as ImportTaskResponse;
        if (!statusResponse.ok) {
          throw new Error(formatApiDetail(statusPayload.detail));
        }
        const scanned = statusPayload.rows_scanned ?? 0;
        const total = statusPayload.total_rows ?? 0;
        const pct =
          typeof statusPayload.progress_pct === "number" ?
            statusPayload.progress_pct
          : total > 0 ?
            Math.min(100, Math.round((scanned / total) * 100))
          : 0;
        setExcelImportProgressPct(pct);
        setMappingStatus(
          total > 0 ?
            `Importujem Excel… ${pct}% (${scanned}/${total} riadkov)`
          : `Importujem Excel… spracovaných ${scanned} riadkov`,
        );
        if (statusPayload.state === "done") {
          finalPayload = statusPayload;
          break;
        }
        if (statusPayload.state === "error") {
          throw new Error(statusPayload.error || "Import zlyhal.");
        }
      }
      const payload = (finalPayload?.result ?? {}) as {
        products_upserted?: number;
        products_legacy_removed?: number;
        suppliers_upserted?: number;
        mappings_upserted?: number;
        rows_scanned?: number;
        total_rows?: number;
        file_resolved?: string;
        warnings?: string[];
      };
      const prods = payload.products_upserted ?? 0;
      const legacyRemoved = payload.products_legacy_removed ?? 0;
      const scanned = payload.rows_scanned ?? 0;
      const total = payload.total_rows ?? 0;
      const fileUsed = (payload.file_resolved || path).trim();
      setExcelImportProgressPct(100);
      const warnBlock =
        payload.warnings?.length ?
          `\n\nVarovanie: ${payload.warnings.join("\n\n")}`
        : "";
      setMappingStatus(
        prods === 0
          ? `Import z listu „${sheet}“: 0 produktov (naskenovaných riadkov: ${scanned}/${total || "?"}). Súbor: ${fileUsed}. Skontroluj list, mapovanie „Kód“ a či riadky majú vyplnený interný kód.`
          : `Import z listu „${sheet}“ hotový: ${prods} produktov` +
              (legacyRemoved > 0
                ? `, odstránených ${legacyRemoved} starých krátkych kódov`
                : "") +
              `, ${payload.suppliers_upserted ?? 0} dodávateľov, ` +
              `${payload.mappings_upserted ?? 0} väzieb kódom, ` +
              `riadkov: ${scanned}/${total || "?"}. Súbor: ${fileUsed}.${warnBlock}`,
      );
      setSearchTick((t) => t + 1);
      void refetchSuppliersList();
      void loadFieldMapping();
      void loadMappingProfile();
    } catch (error) {
      const raw = error instanceof Error ? error.message : "";
      const isNetwork =
        raw === "Failed to fetch" ||
        raw.includes("NetworkError") ||
        raw.includes("Load failed");
      setMappingStatus(
        isNetwork
          ? `Nepodarilo sa spojiť s API (${API_BASE}). Spusti backend (uvicorn), v .env.local musí sedieť tá istá adresa/port ako API, a otvor frontend rovnako (localhost vs 127.0.0.1 vs IP v sieti). Po zmene CORS reštartuj API.`
          : raw || "Import zlyhal.",
      );
      setExcelImportProgressPct(null);
    } finally {
      setExcelImportRunning(false);
    }
  };

  const dropdownOptions = (field: FilterField) => {
    const column = fieldToColumn[field];
    if (!column || !mappingProfile) {
      return [];
    }
    return mappingProfile.unique_values[column] ?? [];
  };

  const createProductList = async () => {
    const name = newListName.trim();
    if (!name) {
      setListStatus("Zadaj názov nového zoznamu.");
      return;
    }
    setListStatus("Vytváram zoznam…");
    try {
      const r = await apiFetch(`${API_BASE}/api/lists`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const payload = (await r.json()) as { id?: number; detail?: unknown };
      if (!r.ok) throw new Error(formatApiDetail(payload.detail));
      setNewListName("");
      await loadProductLists();
      if (typeof payload.id === "number") {
        setActiveListId(payload.id);
      }
      setListStatus("Zoznam vytvorený.");
    } catch (error) {
      setListStatus(error instanceof Error ? error.message : "Chyba vytvorenia.");
    }
  };

  const renameProductList = async (listId: number, name: string) => {
    const next = name.trim();
    if (!next) return;
    try {
      const r = await apiFetch(`${API_BASE}/api/lists/${listId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: next }),
      });
      const payload = (await r.json()) as { detail?: unknown };
      if (!r.ok) throw new Error(formatApiDetail(payload.detail));
      await loadProductLists();
      setListStatus("Zoznam premenovaný.");
    } catch (error) {
      setListStatus(error instanceof Error ? error.message : "Chyba premenovania.");
    }
  };

  const deleteProductList = async (listId: number) => {
    try {
      const r = await apiFetch(`${API_BASE}/api/lists/${listId}`, {
        method: "DELETE",
      });
      const payload = (await r.json()) as { detail?: unknown };
      if (!r.ok) throw new Error(formatApiDetail(payload.detail));
      await loadProductLists();
      setListStatus("Zoznam odstránený.");
    } catch (error) {
      setListStatus(error instanceof Error ? error.message : "Chyba mazania.");
    }
  };

  const addProductToList = async (listId: number, internalCode: string) => {
    try {
      const r = await apiFetch(`${API_BASE}/api/lists/${listId}/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ internal_code: internalCode }),
      });
      const payload = (await r.json()) as { detail?: unknown };
      if (!r.ok) throw new Error(formatApiDetail(payload.detail));
      if (activeListId === listId) {
        await loadListDetail(listId);
      }
      await loadProductLists();
      setListStatus(`Produkt ${internalCode} pridaný do zoznamu.`);
    } catch (error) {
      setListStatus(error instanceof Error ? error.message : "Chyba pridania.");
    }
  };

  const removeProductFromList = async (listId: number, productId: number) => {
    try {
      const r = await apiFetch(`${API_BASE}/api/lists/${listId}/items/${productId}`, {
        method: "DELETE",
      });
      const payload = (await r.json()) as { detail?: unknown };
      if (!r.ok) throw new Error(formatApiDetail(payload.detail));
      if (activeListId === listId) {
        await loadListDetail(listId);
      }
      await loadProductLists();
      setListStatus("Produkt odstránený zo zoznamu.");
    } catch (error) {
      setListStatus(error instanceof Error ? error.message : "Chyba odobratia.");
    }
  };

  return (
    <div
      className={cn(
        "min-h-screen w-full bg-slate-50 text-slate-900",
        themeMode === "dark" && "smarthub-dark",
      )}
    >
      {toastMessage ? (
        <div
          className="fixed left-2 right-2 top-3 z-[100] max-w-sm rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 shadow-lg sm:left-auto sm:right-4 sm:top-4"
          role="status"
        >
          {toastMessage}
        </div>
      ) : null}
      <div className="min-h-screen w-full md:grid md:grid-cols-[auto_1fr]">
        <aside
          className={cn(
            "sticky top-0 z-30 hidden w-full shrink-0 flex-col border-b border-slate-800 bg-slate-900 text-slate-100 md:flex md:h-screen md:max-h-screen md:overflow-y-auto md:border-b-0 md:border-r md:transition-[width,padding] md:duration-200 md:ease-out",
            navCollapsed ? "px-2 py-2 md:w-[56px] md:px-2 md:py-4" : "px-3 py-3 md:w-[260px] md:px-4 md:py-6",
          )}
        >
          <div
            className={cn(
              "mb-2 flex items-center md:mb-3",
              navCollapsed ? "justify-center" : "justify-end",
            )}
          >
            <button
              type="button"
              onClick={() => setNavCollapsed((v) => !v)}
              className="hidden rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-100 md:inline-flex"
              aria-expanded={!navCollapsed}
              aria-label={navCollapsed ? "Rozbaliť menu" : "Zbaliť menu"}
              title={navCollapsed ? "Rozbaliť menu" : "Zbaliť menu"}
            >
              {navCollapsed ? (
                <ChevronRight className="h-5 w-5" aria-hidden />
              ) : (
                <ChevronLeft className="h-5 w-5" aria-hidden />
              )}
            </button>
          </div>

          {!navCollapsed ? (
            <div className="mb-2 hidden items-center gap-2 md:mb-8 md:flex">
              <DatabaseZap className="h-5 w-5 shrink-0 text-sky-400" />
              <div className="min-w-0">
                <p className="text-sm font-semibold">Smarthub</p>
                <p className="text-xs text-slate-400">Interny porovnavac cien</p>
              </div>
            </div>
          ) : null}

          <nav className="flex gap-1 overflow-x-auto pb-1 md:block md:space-y-2 md:overflow-visible md:pb-0">
            {[
              { id: "vyhladavanie", label: "Vyhladavanie", icon: PackageSearch },
              { id: "zoznamy", label: "Zoznamy", icon: List },
              { id: "kosik", label: "Košík", icon: ShoppingCart },
              { id: "historia", label: "História", icon: History },
              { id: "ponuky", label: "Ponuky", icon: FileText },
            ].map((item) => {
              const Icon = item.icon;
              const active = activeView === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  title={navCollapsed ? item.label : undefined}
                  onClick={() => setActiveView(item.id as View)}
                  className={cn(
                    "flex shrink-0 items-center rounded-lg text-sm transition-colors md:w-full",
                    navCollapsed
                      ? "justify-center px-2 py-2 md:px-2 md:py-2.5"
                      : "gap-2 px-2.5 py-2 md:gap-3 md:px-3 md:py-2",
                    active
                      ? "bg-sky-600 text-white"
                      : "text-slate-300 hover:bg-slate-800 hover:text-slate-100",
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <span className={cn(navCollapsed ? "md:hidden" : "")}>{item.label}</span>
                </button>
              );
            })}
          </nav>

          <nav
            className={cn(
              "mt-2 flex gap-1 overflow-x-auto border-t border-slate-700/80 pt-2 md:mt-auto md:block md:space-y-2 md:overflow-visible",
              navCollapsed ? "md:pt-3" : "md:pt-4",
            )}
          >
            {[
              { id: "dodavatelia", label: "Dodavatelia", icon: Truck },
              { id: "parovanie", label: "Parovanie", icon: Link2 },
              ...(isAppAdmin
                ? [{ id: "admin" as const, label: "Admin", icon: KeyRound }]
                : []),
              { id: "dev", label: "Dev / log", icon: Terminal },
            ].map((item) => {
              const Icon = item.icon;
              const active = activeView === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  title={navCollapsed ? item.label : undefined}
                  onClick={() => setActiveView(item.id as View)}
                  className={cn(
                    "flex shrink-0 items-center rounded-lg text-sm transition-colors md:w-full",
                    navCollapsed
                      ? "justify-center px-2 py-2 md:px-2 md:py-2.5"
                      : "gap-2 px-2.5 py-2 md:gap-3 md:px-3 md:py-2",
                    active
                      ? item.id === "dev"
                        ? "bg-amber-600 text-white"
                        : item.id === "admin"
                          ? "bg-violet-600 text-white"
                          : "bg-sky-600 text-white"
                      : "text-slate-300 hover:bg-slate-800 hover:text-slate-100",
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <span className={cn(navCollapsed ? "md:hidden" : "")}>{item.label}</span>
                </button>
              );
            })}
          </nav>
          <div
            className={cn(
              "mt-2 border-t border-slate-700/80 pt-2 md:mt-2",
              navCollapsed ? "md:pt-2" : "md:pt-3",
            )}
          >
            <button
              type="button"
              title={navCollapsed ? "Prepnúť tému" : undefined}
              onClick={() =>
                setThemeMode((prev) => (prev === "dark" ? "light" : "dark"))
              }
              className={cn(
                "mb-1 flex w-full items-center rounded-lg text-sm text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-100",
                navCollapsed
                  ? "justify-center px-2 py-2 md:px-2 md:py-2.5"
                  : "gap-2 px-2.5 py-2 md:gap-3 md:px-3 md:py-2",
              )}
            >
              {themeMode === "dark" ? (
                <Sun className="h-4 w-4 shrink-0" aria-hidden />
              ) : (
                <Moon className="h-4 w-4 shrink-0" aria-hidden />
              )}
              <span className={cn(navCollapsed ? "md:hidden" : "")}>
                {themeMode === "dark" ? "Svetlý mód" : "Tmavý mód"}
              </span>
            </button>
            <button
              type="button"
              title={navCollapsed ? "Odhlásiť" : undefined}
              onClick={() => {
                void fetch("/api/auth/logout", { method: "POST" }).finally(
                  () => {
                    window.location.href = "/login";
                  },
                );
              }}
              className={cn(
                "flex w-full items-center rounded-lg text-sm text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-100",
                navCollapsed
                  ? "justify-center px-2 py-2 md:px-2 md:py-2.5"
                  : "gap-2 px-2.5 py-2 md:gap-3 md:px-3 md:py-2",
              )}
            >
              <LogOut className="h-4 w-4 shrink-0" aria-hidden />
              <span className={cn(navCollapsed ? "md:hidden" : "")}>Odhlásiť</span>
            </button>
          </div>
        </aside>

        <main className="min-w-0 p-2.5 pb-24 sm:p-4 sm:pb-24 md:p-8 md:pb-8">
          {addToOfferFeedback ? (
            <p
              className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900"
              role="status"
            >
              {addToOfferFeedback}
            </p>
          ) : null}
          {activeView === "vyhladavanie" && (
            <section className="space-y-3 sm:space-y-4">
              <Card className="relative z-20 overflow-visible p-0 shadow-sm ring-1 ring-slate-100/80">
                <div className="border-b border-sky-300/70 bg-gradient-to-r from-sky-100/95 via-sky-100/85 to-sky-200/55 px-3 py-2.5 sm:px-5 sm:py-3.5">
                  <div className="flex flex-wrap items-center justify-between gap-1.5 sm:gap-2">
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-800 sm:gap-2 sm:text-sm">
                      <PackageSearch className="h-4 w-4 shrink-0 text-sky-600" />
                      Filtre (podľa mapovania z Párovania)
                      <button
                        type="button"
                        className="-m-0.5 inline-flex shrink-0 rounded-full p-1 text-slate-500 transition-colors hover:bg-sky-200/50 hover:text-sky-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/80"
                        title="Zobrazia sa len polia priradené v Párovaní (vrátane Class / stĺpec V a Money názov / stĺpec Y). Rozbalovače: možnosti z databázy, kaskáda podľa ostatných filtrov. Kód filtruje počas písania (~0,3 s)."
                        aria-label="Zobrazia sa len polia priradené v Párovaní (vrátane Class / stĺpec V a Money názov / stĺpec Y). Rozbalovače: možnosti z databázy, kaskáda podľa ostatných filtrov. Kód filtruje počas písania (približne 0,3 sekundy)."
                      >
                        <CircleHelp
                          className="h-4 w-4"
                          strokeWidth={2}
                          aria-hidden
                        />
                      </button>
                    </div>
                    <Button
                      type="button"
                      variant="default"
                      size="sm"
                      className="h-8 px-2.5 text-xs shadow-sm shadow-sky-600/20 sm:h-9 sm:px-3 sm:text-sm"
                      onClick={() => {
                        setSearchFilters({ ...initialSearchFilters });
                        setDebouncedCode("");
                      }}
                    >
                      Vymazať filtre
                    </Button>
                  </div>
                </div>
                <div className="p-2.5 sm:p-4">
                {matchedCount === 0 && (
                  <p className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                    Zatiaľ nie je uložené žiadne mapovanie. Otvor sekciu Párovanie,
                    vyber stĺpce a klikni „Potvrdiť mapovanie“.
                  </p>
                )}
                <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                  {isFieldMapped("code") && (
                    <div className="min-w-0 space-y-1">
                      <label className="text-xs text-slate-600">Kód</label>
                      <Input
                        placeholder="Časť interného kódu…"
                        value={searchFilters.code}
                        onChange={(event) =>
                          setSearchFilters((prev) => ({
                            ...prev,
                            code: event.target.value,
                          }))
                        }
                      />
                    </div>
                  )}
                  {isFieldMapped("norma") && (
                    <div className="min-w-0 space-y-1">
                      <label
                        htmlFor="search-filter-norma"
                        className="text-xs text-slate-600"
                      >
                        Norma
                      </label>
                      <SearchableSelect
                        id="search-filter-norma"
                        value={searchFilters.norma}
                        onChange={(norma) =>
                          setSearchFilters((prev) => ({ ...prev, norma }))
                        }
                        options={filterOptions.norma}
                      />
                    </div>
                  )}
                  {isFieldMapped("surface") && (
                    <div className="min-w-0 space-y-1">
                      <label
                        htmlFor="search-filter-surface"
                        className="text-xs text-slate-600"
                      >
                        Povrchová úprava
                      </label>
                      <SearchableSelect
                        id="search-filter-surface"
                        value={searchFilters.surface}
                        onChange={(surface) =>
                          setSearchFilters((prev) => ({ ...prev, surface }))
                        }
                        options={filterOptions.surface}
                      />
                    </div>
                  )}
                  {isFieldMapped("diameter") && (
                    <div className="min-w-0 space-y-1">
                      <label
                        htmlFor="search-filter-diameter"
                        className="text-xs text-slate-600"
                      >
                        Priemer
                      </label>
                      <SearchableSelect
                        id="search-filter-diameter"
                        value={searchFilters.diameter}
                        onChange={(diameter) =>
                          setSearchFilters((prev) => ({ ...prev, diameter }))
                        }
                        options={filterOptions.diameter}
                      />
                    </div>
                  )}
                  {isFieldMapped("length") && (
                    <div className="min-w-0 space-y-1">
                      <label
                        htmlFor="search-filter-length"
                        className="text-xs text-slate-600"
                      >
                        Dĺžka
                      </label>
                      <SearchableSelect
                        id="search-filter-length"
                        value={searchFilters.length}
                        onChange={(length) =>
                          setSearchFilters((prev) => ({ ...prev, length }))
                        }
                        options={filterOptions.length}
                      />
                    </div>
                  )}
                  {isFieldMapped("v_class") && (
                    <div className="min-w-0 space-y-1">
                      <label
                        htmlFor="search-filter-v-class"
                        className="text-xs text-slate-600"
                      >
                        Class
                      </label>
                      <SearchableSelect
                        id="search-filter-v-class"
                        value={searchFilters.v_class}
                        onChange={(v) =>
                          setSearchFilters((prev) => ({ ...prev, v_class: v }))
                        }
                        options={filterOptions.v_class}
                      />
                    </div>
                  )}
                  {isFieldMapped("y_money_name") && (
                    <div className="col-span-2 min-w-0 space-y-1 lg:col-span-1">
                      <label
                        htmlFor="search-filter-y-money"
                        className="text-xs text-slate-600"
                      >
                        Money názov
                      </label>
                      <SearchableSelect
                        id="search-filter-y-money"
                        value={searchFilters.y_money_name}
                        onChange={(v) =>
                          setSearchFilters((prev) => ({
                            ...prev,
                            y_money_name: v,
                          }))
                        }
                        options={filterOptions.y_money_name}
                      />
                    </div>
                  )}
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5 sm:mt-3 sm:gap-2">
                  {searchFilters.code.trim() && (
                    <Badge>Kód obsahuje: {searchFilters.code.trim()}</Badge>
                  )}
                  {searchFilters.norma && <Badge>Norma: {searchFilters.norma}</Badge>}
                  {searchFilters.surface && (
                    <Badge>Povrch: {searchFilters.surface}</Badge>
                  )}
                  {searchFilters.diameter && (
                    <Badge>Priemer: {searchFilters.diameter}</Badge>
                  )}
                  {searchFilters.length && <Badge>Dĺžka: {searchFilters.length}</Badge>}
                  {searchFilters.v_class && (
                    <Badge>Class: {searchFilters.v_class}</Badge>
                  )}
                  {searchFilters.y_money_name && (
                    <Badge>Money názov: {searchFilters.y_money_name}</Badge>
                  )}
                </div>
                {searchMessage && (
                  <p className="mt-2 text-xs text-slate-600">{searchMessage}</p>
                )}
                </div>
              </Card>

              <Card className="overflow-hidden p-0">
                <div className="overflow-x-auto overflow-y-hidden">
                <table className="w-full text-xs sm:text-sm">
                  <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500">
                    <tr>
                      <th className="px-2 py-1.5 sm:px-3 sm:py-2" />
                      <th className="px-2 py-1.5 sm:px-3 sm:py-2">Kód</th>
                      <th className="hidden px-2 py-1.5 sm:table-cell sm:px-3 sm:py-2">Norma</th>
                      <th className="px-2 py-1.5 sm:px-3 sm:py-2">Priemer</th>
                      <th className="px-2 py-1.5 sm:px-3 sm:py-2">Dĺžka</th>
                      {showSurfaceCol && (
                        <th className="hidden px-2 py-1.5 sm:table-cell sm:px-3 sm:py-2">Povrch</th>
                      )}
                      <th className="hidden px-2 py-1.5 md:table-cell sm:px-3 sm:py-2">Class</th>
                      <th className="hidden px-2 py-1.5 md:table-cell sm:px-3 sm:py-2">Money názov</th>
                      <th className="px-2 py-1.5 text-center sm:px-3 sm:py-2">+</th>
                    </tr>
                  </thead>
                  <tbody>
                    {searchResults.map((product) => {
                      const isOpen = openProduct === product.internal_code;
                      const productImageUrl = productImagePublicUrl(
                        product.image_filename ?? null,
                      );
                      const colSpan = searchTableColSpan;
                      return (
                        <Fragment key={product.internal_code}>
                          <tr
                            className={cn(
                              "border-t border-slate-200 cursor-pointer transition-colors hover:bg-slate-50",
                              isOpen && "bg-slate-50/70",
                            )}
                            tabIndex={0}
                            role="button"
                            aria-expanded={isOpen}
                            aria-label={
                              isOpen
                                ? "Skryť detail dodávateľov"
                                : "Zobraziť detail dodávateľov"
                            }
                            onClick={() =>
                              setOpenProduct(isOpen ? null : product.internal_code)
                            }
                            onKeyDown={(event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                setOpenProduct(isOpen ? null : product.internal_code);
                              }
                            }}
                          >
                            <td className="px-2 py-1.5 align-middle text-slate-500 sm:px-3 sm:py-2">
                              <div className="flex items-center gap-1">
                                {isOpen ? (
                                  <ChevronDown className="h-4 w-4" />
                                ) : (
                                  <ChevronRight className="h-4 w-4" />
                                )}
                                {productImageUrl ? (
                                  <button
                                    type="button"
                                    className="inline-flex h-6 w-6 items-center justify-center rounded text-slate-500 hover:bg-slate-200/70 hover:text-slate-700"
                                    title={`Zobraziť obrázok: ${product.image_filename ?? ""}`}
                                    aria-label={`Zobraziť obrázok produktu ${product.internal_code}`}
                                    onClick={(event) => {
                                      event.preventDefault();
                                      event.stopPropagation();
                                      setImagePreview({
                                        url: productImageUrl,
                                        code: product.internal_code,
                                        filename:
                                          (product.image_filename ?? "").trim() || "obrázok",
                                      });
                                    }}
                                  >
                                    <ImageIcon className="h-4 w-4" />
                                  </button>
                                ) : null}
                              </div>
                            </td>
                            <td className="px-2 py-1.5 font-medium sm:px-3 sm:py-2">{product.internal_code}</td>
                            <td className="hidden px-2 py-1.5 sm:table-cell sm:px-3 sm:py-2">{product.norma ?? "—"}</td>
                            <td className="px-2 py-1.5 sm:px-3 sm:py-2">{product.diameter ?? "—"}</td>
                            <td className="px-2 py-1.5 sm:px-3 sm:py-2">{product.length ?? "—"}</td>
                            {showSurfaceCol && (
                              <td className="hidden px-2 py-1.5 sm:table-cell sm:px-3 sm:py-2">{product.surface ?? "—"}</td>
                            )}
                            <td className="hidden px-2 py-1.5 md:table-cell sm:px-3 sm:py-2">{product.v_class ?? "—"}</td>
                            <td className="hidden px-2 py-1.5 md:table-cell sm:px-3 sm:py-2">
                              {product.y_money_name ?? "—"}
                            </td>
                            <td className="px-2 py-1.5 text-center sm:px-3 sm:py-2">
                              <button
                                type="button"
                                className="inline-flex h-8 w-8 items-center justify-center rounded border border-slate-300 bg-white text-slate-600 hover:bg-slate-100 sm:h-7 sm:w-7"
                                title="Pridať produkt do zoznamu"
                                onClick={(event) => {
                                  event.preventDefault();
                                  event.stopPropagation();
                                  setListPicker({
                                    internalCode: product.internal_code,
                                    listId: productLists[0]?.id ?? null,
                                  });
                                }}
                              >
                                <Plus className="h-3.5 w-3.5" />
                              </button>
                            </td>
                          </tr>
                          {isOpen ? (
                          <ProductSupplierExpandedTableRow
                            product={product}
                            colSpan={colSpan}
                            scrapeByKey={scrapeByKey}
                            cartQuantityByKey={cartQuantityByKey}
                            setCartQuantityByKey={setCartQuantityByKey}
                            packVariantIndexByKey={packVariantIndexByKey}
                            setPackVariantIndexByKey={setPackVariantIndexByKey}
                            cartFeedback={cartFeedback}
                            cartAddSuccessByKey={cartAddSuccessByKey}
                            offerNotesByKey={offerNotesByKey}
                            setOfferNotesByKey={setOfferNotesByKey}
                            onRequestAddToOffer={(p) => {
                              setAddToOfferPayload(p);
                              setAddToOfferOpen(true);
                            }}
                            addToCart={addToCart}
                          />
                        ) : null}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
                </div>
                {searchResults.length === 0 && (
                  <p className="px-4 py-6 text-center text-sm text-slate-600">
                    Žiadne riadky pre aktuálne filtre.
                  </p>
                )}
              </Card>
            </section>
          )}

          {activeView === "zoznamy" && (
            <section className="space-y-4">
              <Card className="p-3 sm:p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    value={newListName}
                    onChange={(e) => setNewListName(e.target.value)}
                    placeholder="Názov nového zoznamu"
                    className="w-full sm:max-w-sm"
                  />
                  <Button type="button" onClick={() => void createProductList()}>
                    <Plus className="mr-1 h-4 w-4" />
                    Vytvoriť zoznam
                  </Button>
                </div>
                {listStatus ? (
                  <p className="mt-2 text-xs text-slate-600">{listStatus}</p>
                ) : null}
              </Card>

              <div className="grid gap-4 lg:grid-cols-[260px,1fr]">
                <Card className="p-3">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Moje zoznamy
                  </p>
                  <div className="space-y-2">
                    {productLists.map((l) => (
                      <div
                        key={l.id}
                        className={cn(
                          "rounded-md border p-2",
                          activeListId === l.id
                            ? "border-sky-300 bg-sky-50"
                            : "border-slate-200 bg-white",
                        )}
                      >
                        <button
                          type="button"
                          className="w-full text-left"
                          onClick={() => setActiveListId(l.id)}
                        >
                          <div className="text-sm font-medium text-slate-900">{l.name}</div>
                          <div className="text-xs text-slate-500">{l.item_count} položiek</div>
                        </button>
                        <div className="mt-2 flex gap-1">
                          <Button
                            type="button"
                            variant="outline"
                            className="h-8 px-2.5 text-xs sm:h-7 sm:px-2 sm:text-[11px]"
                            onClick={() => {
                              const n = window.prompt("Nový názov zoznamu", l.name);
                              if (n && n.trim()) void renameProductList(l.id, n);
                            }}
                          >
                            Upraviť
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            className="h-8 border-red-200 px-2.5 text-xs text-red-700 sm:h-7 sm:px-2 sm:text-[11px]"
                            onClick={() => void deleteProductList(l.id)}
                          >
                            <Trash2 className="mr-1 h-3.5 w-3.5" />
                            Zmazať
                          </Button>
                        </div>
                      </div>
                    ))}
                    {productLists.length === 0 ? (
                      <p className="text-xs text-slate-500">Zatiaľ nemáš žiadny zoznam.</p>
                    ) : null}
                  </div>
                </Card>

                <Card className="overflow-hidden p-0">
                  <div className="overflow-x-auto">
                  <table className="w-full min-w-[760px] text-sm">
                    <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500">
                      <tr>
                        <th className="px-3 py-2" />
                        <th className="px-3 py-2">Kód</th>
                        <th className="px-3 py-2">Norma</th>
                        <th className="px-3 py-2">Priemer</th>
                        <th className="px-3 py-2">Dĺžka</th>
                        {showSurfaceCol ? (
                          <th className="px-3 py-2">Povrch</th>
                        ) : null}
                        <th className="px-3 py-2">Class</th>
                        <th className="px-3 py-2">Money názov</th>
                        <th className="w-20 px-3 py-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {activeListItems.map((item) => {
                        const isOpen = openProduct === item.internal_code;
                        const listColSpan = searchTableColSpan;
                        const listImageUrl = productImagePublicUrl(
                          item.image_filename ?? null,
                        );
                        const detailProduct =
                          listOpenProductRow &&
                          listOpenProductRow.internal_code === item.internal_code
                            ? listOpenProductRow
                            : null;
                        return (
                          <Fragment key={item.product_id}>
                            <tr
                              className={cn(
                                "cursor-pointer border-t border-slate-200 transition-colors hover:bg-slate-50",
                                isOpen && "bg-slate-50/70",
                              )}
                              tabIndex={0}
                              role="button"
                              aria-expanded={isOpen}
                              aria-label={
                                isOpen
                                  ? "Skryť detail dodávateľov"
                                  : "Zobraziť detail dodávateľov"
                              }
                              onClick={() =>
                                setOpenProduct(isOpen ? null : item.internal_code)
                              }
                              onKeyDown={(event) => {
                                if (event.key === "Enter" || event.key === " ") {
                                  event.preventDefault();
                                  setOpenProduct(isOpen ? null : item.internal_code);
                                }
                              }}
                            >
                              <td className="px-3 py-2 align-middle text-slate-500">
                                <div className="flex items-center gap-1">
                                  {isOpen ? (
                                    <ChevronDown className="h-4 w-4" />
                                  ) : (
                                    <ChevronRight className="h-4 w-4" />
                                  )}
                                  {listImageUrl ? (
                                    <button
                                      type="button"
                                      className="inline-flex h-6 w-6 items-center justify-center rounded text-slate-500 hover:bg-slate-200/70 hover:text-slate-700"
                                      title={`Zobraziť obrázok: ${item.image_filename ?? ""}`}
                                      aria-label={`Zobraziť obrázok produktu ${item.internal_code}`}
                                      onClick={(event) => {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        setImagePreview({
                                          url: listImageUrl,
                                          code: item.internal_code,
                                          filename:
                                            (item.image_filename ?? "").trim() || "obrázok",
                                        });
                                      }}
                                    >
                                      <ImageIcon className="h-4 w-4" />
                                    </button>
                                  ) : null}
                                </div>
                              </td>
                              <td className="px-3 py-2 font-medium">{item.internal_code}</td>
                              <td className="px-3 py-2">{item.norma ?? "—"}</td>
                              <td className="px-3 py-2">{item.diameter ?? "—"}</td>
                              <td className="px-3 py-2">{item.length ?? "—"}</td>
                              {showSurfaceCol ? (
                                <td className="px-3 py-2">{item.surface ?? "—"}</td>
                              ) : null}
                              <td className="px-3 py-2">{item.v_class ?? "—"}</td>
                              <td className="px-3 py-2">{item.y_money_name ?? "—"}</td>
                              <td className="px-3 py-2">
                                {activeListId != null ? (
                                  <div className="flex justify-end">
                                    <Button
                                      type="button"
                                      variant="outline"
                                      className="h-8 border-red-200 px-2.5 text-xs text-red-700 sm:h-7 sm:px-2 sm:text-[11px]"
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        void removeProductFromList(
                                          activeListId,
                                          item.product_id,
                                        );
                                      }}
                                    >
                                      <Trash2 className="mr-1 h-3.5 w-3.5" />
                                      Odobrať
                                    </Button>
                                  </div>
                                ) : null}
                              </td>
                            </tr>
                            {isOpen && !detailProduct ? (
                              <tr className="bg-slate-50/80">
                                <td
                                  className="px-3 py-3 text-center text-xs text-slate-500"
                                  colSpan={listColSpan}
                                >
                                  <span className="inline-flex items-center gap-2">
                                    <Loader2 className="h-4 w-4 animate-spin text-sky-600" />
                                    Načítavam detail produktu…
                                  </span>
                                </td>
                              </tr>
                            ) : null}
                            {isOpen && detailProduct ? (
                              <ProductSupplierExpandedTableRow
                                product={detailProduct}
                                colSpan={listColSpan}
                                scrapeByKey={scrapeByKey}
                                cartQuantityByKey={cartQuantityByKey}
                                setCartQuantityByKey={setCartQuantityByKey}
                                packVariantIndexByKey={packVariantIndexByKey}
                                setPackVariantIndexByKey={setPackVariantIndexByKey}
                                cartFeedback={cartFeedback}
                                cartAddSuccessByKey={cartAddSuccessByKey}
                                offerNotesByKey={offerNotesByKey}
                                setOfferNotesByKey={setOfferNotesByKey}
                                onRequestAddToOffer={(p) => {
                                  setAddToOfferPayload(p);
                                  setAddToOfferOpen(true);
                                }}
                                addToCart={addToCart}
                              />
                            ) : null}
                          </Fragment>
                        );
                      })}
                      {activeListItems.length === 0 ? (
                        <tr>
                          <td
                            className="px-3 py-6 text-center text-sm text-slate-500"
                            colSpan={searchTableColSpan}
                          >
                            Vyber zoznam alebo pridaj produkt vo Vyhľadávaní cez tlačidlo +.
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                  </div>
                </Card>
              </div>
            </section>
          )}

          {activeView === "kosik" && (
            <section className="space-y-4">
              <Card className="p-4">
                <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="mb-1 flex items-center gap-2 text-sm font-medium text-slate-800">
                      <ShoppingCart className="h-4 w-4 text-sky-600" />
                      Košík u dodávateľov
                    </div>
                    <p className="max-w-2xl text-xs text-slate-600">
                      Zoznam dodávateľov sa zobrazí hneď; súhrn košíka (Haspl, Mekrs, Argip, Schachermayer, Valenta, Halfmann, Fabory, Hopefix, BMKCO) sa načíta
                      postupne pre každého. U ostatných dodávateľov zatiaľ nie je čítanie košíka
                      napojené. Tlačidlo Obnoviť znovu stiahne košíky z e-shopov (preskočí cache).
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={remoteCartLoading}
                    onClick={() => {
                      remoteCartNextFetchBypassCacheRef.current = true;
                      setExpandedRemoteSupplierId(null);
                      setRemoteDetailBySupplierId({});
                      setRemoteCartRows((prev) =>
                        prev.map((r) => ({ ...r, overviewLoading: true })),
                      );
                      setRemoteCartRefreshTick((t) => t + 1);
                    }}
                  >
                    Obnoviť
                  </Button>
                </div>
                {remoteCartFetchError ? (
                  <p className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
                    {remoteCartFetchError}
                  </p>
                ) : null}
                {remoteCartLoading ? (
                  <p className="flex items-center gap-2 text-sm text-slate-600">
                    <Loader2 className="h-4 w-4 animate-spin text-sky-600" />
                    Načítavam zoznam dodávateľov…
                  </p>
                ) : remoteCartRows.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-600">
                    V databáze nie sú žiadni dodávatelia. Pridaj ich v sekcii Dodávatelia.
                  </p>
                ) : (
                  <div className="overflow-x-auto rounded-lg border border-slate-200">
                    <table className="w-full min-w-[860px] text-left text-sm">
                      <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                        <tr>
                          <th className="w-10 px-3 py-2 font-medium" aria-hidden />
                          <th className="px-3 py-2 font-medium">Logo</th>
                          <th className="px-3 py-2 font-medium">Dodávateľ</th>
                          <th className="px-3 py-2 font-medium">Stav prihlásenia</th>
                          <th className="whitespace-nowrap px-3 py-2 text-right font-medium">
                            Položiek
                          </th>
                          <th className="whitespace-nowrap px-3 py-2 text-right font-medium">
                            Celkom (€)
                          </th>
                          <th className="whitespace-nowrap px-3 py-2 text-center font-medium">
                            E-shop
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {remoteCartRows.map((row) => {
                          const open = expandedRemoteSupplierId === row.supplier_id;
                          const detail = remoteDetailBySupplierId[row.supplier_id];
                          return (
                            <Fragment key={row.supplier_id}>
                              <tr
                                className={cn(
                                  "cursor-pointer border-t border-slate-200 text-slate-800 transition-colors",
                                  open ? "bg-sky-50/70" : "hover:bg-slate-50",
                                )}
                                onClick={() =>
                                  toggleRemoteSupplierCart(row.supplier_id)
                                }
                              >
                                <td className="px-2 py-2.5 align-middle text-slate-400">
                                  <ChevronDown
                                    className={cn(
                                      "h-4 w-4 shrink-0 transition-transform",
                                      open && "rotate-180",
                                    )}
                                    aria-hidden
                                  />
                                </td>
                                <td className="px-3 py-2 align-middle">
                                  <div className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-md border border-slate-200 bg-white">
                                    {publicApiAssetUrl(row.logo_url) ? (
                                      // eslint-disable-next-line @next/next/no-img-element
                                      <img
                                        src={publicApiAssetUrl(row.logo_url)!}
                                        alt=""
                                        className="h-full w-full object-contain p-0.5"
                                      />
                                    ) : (
                                      <span className="text-[9px] font-medium uppercase text-slate-400">
                                        —
                                      </span>
                                    )}
                                  </div>
                                </td>
                                <td className="px-3 py-2 align-middle font-medium">
                                  {row.name}
                                </td>
                                <td
                                  className={cn(
                                    "max-w-[280px] px-3 py-2 align-middle text-xs",
                                    !row.overviewLoading &&
                                      row.remote_supported &&
                                      row.logged_in === true &&
                                      "text-emerald-800",
                                    !row.overviewLoading &&
                                      row.remote_supported &&
                                      row.logged_in === false &&
                                      "text-rose-700",
                                    !row.overviewLoading &&
                                      !row.remote_supported &&
                                      "text-slate-600",
                                    row.overviewLoading && "text-slate-500",
                                  )}
                                >
                                  {row.overviewLoading ? (
                                    <span className="inline-flex items-center gap-1.5">
                                      <Loader2
                                        className="h-3.5 w-3.5 shrink-0 animate-spin text-sky-600"
                                        aria-hidden
                                      />
                                      Načítavam košík…
                                    </span>
                                  ) : row.remote_supported && row.logged_in === true
                                    ? row.message?.trim() ||
                                      (row.line_count === 0
                                        ? "Košík je prázdny"
                                        : "Prihlásený")
                                    : row.remote_supported && row.logged_in === false
                                      ? row.message?.trim() || "Chyba prihlásenia / API"
                                      : row.message?.trim() ||
                                        "Čítanie košíka nie je pre tohto dodávateľa k dispozícii."}
                                </td>
                                <td className="whitespace-nowrap px-3 py-2 align-middle text-right tabular-nums text-slate-700">
                                  {row.overviewLoading ? (
                                    <Loader2
                                      className="ml-auto h-3.5 w-3.5 animate-spin text-sky-600"
                                      aria-label="Načítavam"
                                    />
                                  ) : (
                                    row.line_count
                                  )}
                                </td>
                                <td className="whitespace-nowrap px-3 py-2 align-middle text-right tabular-nums font-medium text-slate-900">
                                  {row.overviewLoading ? (
                                    <Loader2
                                      className="ml-auto h-3.5 w-3.5 animate-spin text-sky-600"
                                      aria-label="Načítavam"
                                    />
                                  ) : row.total_eur != null &&
                                      Number.isFinite(row.total_eur) ?
                                    `${formatScrapePriceAmount(row.total_eur)} €`
                                  : "—"}
                                </td>
                                <td className="px-2 py-2 align-middle text-center">
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    className={cn(
                                      "h-8 gap-1 whitespace-nowrap px-2.5 text-xs font-medium",
                                      remoteCartEshopButtonClass(
                                        row.total_eur,
                                        row.free_shipping_threshold_eur,
                                      ),
                                    )}
                                    disabled={
                                      row.overviewLoading ||
                                      !(row.web_cart_url ?? "").trim()
                                    }
                                    title={
                                      (row.free_shipping_threshold_eur != null &&
                                        Number.isFinite(row.free_shipping_threshold_eur)
                                        ? `Prah dopravy zdarma: ${formatScrapePriceAmount(row.free_shipping_threshold_eur)} €`
                                        : "Prah dopravy zdarma nie je nastavený") +
                                      " — otvorí stránku košíka na eshope."
                                    }
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      const u = (row.web_cart_url ?? "").trim();
                                      if (!u) {
                                        return;
                                      }
                                      window.open(u, "_blank", "noopener,noreferrer");
                                    }}
                                  >
                                    <ExternalLink
                                      className="h-3.5 w-3.5 shrink-0"
                                      aria-hidden
                                    />
                                    Košík
                                  </Button>
                                </td>
                              </tr>
                              {open ? (
                                <tr className="border-t border-slate-100 bg-slate-50/90">
                                  <td colSpan={7} className="px-4 py-3 align-top">
                                    {remoteDetailLoadingId === row.supplier_id ? (
                                      <p className="flex items-center gap-2 text-xs text-slate-600">
                                        <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-600" />
                                        Načítavam položky…
                                      </p>
                                    ) : detail?.message &&
                                      (!detail.lines || detail.lines.length === 0) ? (
                                      <p
                                        className={cn(
                                          "text-xs",
                                          detail.logged_in === true &&
                                            /prázdny/i.test(detail.message)
                                            ? "text-slate-600"
                                            : "text-rose-700",
                                        )}
                                      >
                                        {detail.message}
                                      </p>
                                    ) : detail &&
                                      detail.logged_in === true &&
                                      (!detail.lines || detail.lines.length === 0) ? (
                                      <p className="text-xs text-slate-600">
                                        Košík je prázdny.
                                      </p>
                                    ) : detail && detail.lines.length > 0 ? (
                                      <div className="space-y-2">
                                        <p className="text-xs font-medium text-slate-700">
                                          Položky v košíku
                                        </p>
                                        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
                                          <table className="w-full min-w-[640px] text-left text-xs">
                                            <thead className="border-b border-slate-100 bg-slate-50 text-slate-600">
                                              <tr>
                                                <th className="px-3 py-1.5 font-medium">
                                                  Názov
                                                </th>
                                                <th className="whitespace-nowrap px-3 py-1.5 font-medium">
                                                  Kód
                                                </th>
                                                <th className="whitespace-nowrap px-3 py-1.5 text-right font-medium">
                                                  Ks
                                                </th>
                                                <th className="whitespace-nowrap px-3 py-1.5 text-right font-medium">
                                                  Cena / 100
                                                </th>
                                                <th className="whitespace-nowrap px-3 py-1.5 text-right font-medium">
                                                  Spolu
                                                </th>
                                              </tr>
                                            </thead>
                                            <tbody>
                                              {detail.lines.map((ln, i) => (
                                                <tr
                                                  key={`${row.supplier_id}-ln-${i}`}
                                                  className="border-t border-slate-100"
                                                >
                                                  <td className="px-3 py-2 align-middle text-slate-800">
                                                    <span className="line-clamp-2">
                                                      {ln.label}
                                                    </span>
                                                  </td>
                                                  <td className="max-w-[200px] whitespace-normal break-all px-3 py-2 align-middle font-mono text-[11px] text-slate-700">
                                                    {ln.variant_code?.trim() || "—"}
                                                  </td>
                                                  <td className="whitespace-nowrap px-3 py-2 align-middle text-right tabular-nums">
                                                    {formatIntegerCsThousands(ln.quantity)}
                                                  </td>
                                                  <td className="whitespace-nowrap px-3 py-2 align-middle text-right tabular-nums text-slate-700">
                                                    {ln.unit_price_eur != null &&
                                                    Number.isFinite(ln.unit_price_eur)
                                                      ? `${formatScrapePriceAmount(ln.unit_price_eur)} €${SCRAPE_PRICE_DISPLAY_SUFFIX}`
                                                      : "—"}
                                                  </td>
                                                  <td className="whitespace-nowrap px-3 py-2 align-middle text-right tabular-nums font-medium text-slate-900">
                                                    {ln.line_total_eur != null &&
                                                    Number.isFinite(ln.line_total_eur)
                                                      ? `${formatScrapePriceAmount(ln.line_total_eur)} €`
                                                      : "—"}
                                                  </td>
                                                </tr>
                                              ))}
                                            </tbody>
                                          </table>
                                        </div>
                                        {detail.total_eur != null &&
                                        Number.isFinite(detail.total_eur) ? (
                                          <p className="text-right text-xs text-slate-700">
                                            Suma:{" "}
                                            <span className="font-semibold tabular-nums text-slate-900">
                                              {formatScrapePriceAmount(detail.total_eur)} €
                                            </span>
                                          </p>
                                        ) : null}
                                      </div>
                                    ) : (
                                      <p className="text-xs text-slate-600">
                                        Košík je prázdny.
                                      </p>
                                    )}
                                  </td>
                                </tr>
                              ) : null}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </section>
          )}

          {activeView === "historia" && (
            <section className="space-y-4">
              <Card className="p-4">
                <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="mb-1 flex items-center gap-2 text-sm font-medium text-slate-800">
                      <History className="h-4 w-4 text-sky-600" />
                      História pridaní do košíka (B2B)
                    </div>
                    <p className="max-w-2xl text-xs text-slate-600">
                      Záznamy z úspešného tlačidla „Košík“ vo Vyhľadávaní — uložené len v
                      tomto prehliadači (localStorage). Slúži na kontrolu počas dňa; nie
                      je to obsah reálneho košíka na e-shope.
                    </p>
                  </div>
                  <div className="flex flex-col items-stretch gap-2 sm:items-end">
                    <label className="flex cursor-pointer select-none items-center gap-2 text-xs text-slate-700">
                      <input
                        type="checkbox"
                        checked={cartHistoryNotesOnly}
                        onChange={(event) =>
                          setCartHistoryNotesOnly(event.target.checked)
                        }
                        disabled={cartHistory.length === 0}
                        className="h-3.5 w-3.5 shrink-0 rounded border-slate-300 text-sky-600 focus:ring-sky-500"
                      />
                      Len záznamy s poznámkou
                    </label>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={cartHistory.length === 0}
                      onClick={() => {
                        if (
                          typeof window !== "undefined" &&
                          !window.confirm(
                            "Vymazať celú históriu pridaní v tejto aplikácii?",
                          )
                        ) {
                          return;
                        }
                        setCartHistory([]);
                      }}
                    >
                      Vymazať históriu
                    </Button>
                  </div>
                </div>
                {cartHistory.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-600">
                    Zatiaľ žiadne záznamy. Po úspešnom pridaní produktu cez sekciu
                    Vyhľadávanie sa tu zobrazí čas, dodávateľ, poznámka k ponuke (ak si ju
                    vyplnil pri produkte), cena a množstvo.
                  </p>
                ) : cartHistoryFiltered.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-amber-200 bg-amber-50/80 px-4 py-8 text-center text-sm text-slate-700">
                    Pri zvolenom filtri nie sú žiadne záznamy s vyplnenou poznámkou. Zruš
                    voľbu „Len záznamy s poznámkou“ alebo dopln poznámku pri ďalšom
                    pridaní do košíka.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {cartHistoryNotesOnly ? (
                      <p className="text-xs text-slate-600">
                        Zobrazených{" "}
                        <span className="font-medium tabular-nums text-slate-800">
                          {cartHistoryFiltered.length}
                        </span>{" "}
                        z{" "}
                        <span className="tabular-nums">
                          {cartHistory.length}
                        </span>{" "}
                        záznamov.
                      </p>
                    ) : null}
                    <div className="overflow-x-auto rounded-lg border border-slate-200">
                    <table className="w-full min-w-[880px] text-left text-sm">
                      <thead className="bg-slate-100 text-xs uppercase text-slate-600">
                        <tr>
                          <th className="whitespace-nowrap px-3 py-2">Čas</th>
                          <th className="px-3 py-2">Dodávateľ</th>
                          <th className="max-w-[200px] px-3 py-2">Poznámka</th>
                          <th className="px-3 py-2">Interný kód</th>
                          <th className="px-3 py-2">Kód dodávateľa</th>
                          <th className="whitespace-nowrap px-3 py-2 text-right">
                            Množstvo
                          </th>
                          <th className="whitespace-nowrap px-3 py-2 text-right">
                            Cena / 100
                          </th>
                          <th className="px-3 py-2">Názov money</th>
                        </tr>
                      </thead>
                      <tbody>
                        {cartHistoryFiltered.map((entry) => {
                          const t = new Date(entry.addedAtIso);
                          const timeStr = Number.isNaN(t.getTime())
                            ? entry.addedAtIso
                            : t.toLocaleString("sk-SK", {
                                dateStyle: "short",
                                timeStyle: "short",
                              });
                          const priceStr =
                            entry.priceEur != null && Number.isFinite(entry.priceEur)
                              ? `${formatScrapePriceAmount(entry.priceEur)} €`
                              : "—";
                          return (
                            <tr
                              key={entry.id}
                              className="border-t border-slate-200 text-slate-800"
                            >
                              <td className="whitespace-nowrap px-3 py-2.5 align-middle text-xs text-slate-600">
                                {timeStr}
                              </td>
                              <td className="max-w-[200px] px-3 py-2 align-middle">
                                <div className="truncate font-medium">
                                  {entry.supplierName}
                                </div>
                              </td>
                              <td className="max-w-[200px] px-3 py-2 align-top text-xs text-slate-700">
                                {entry.offerNote?.trim() ? (
                                  <span className="whitespace-pre-wrap break-words">
                                    {entry.offerNote.trim()}
                                  </span>
                                ) : (
                                  <span className="text-slate-400">—</span>
                                )}
                              </td>
                              <td className="px-3 py-2 align-middle font-mono text-xs">
                                {entry.internalCode}
                              </td>
                              <td className="px-3 py-2 align-middle font-mono text-xs">
                                {entry.supplierCode}
                              </td>
                              <td className="whitespace-nowrap px-3 py-2 align-middle text-right text-xs tabular-nums text-slate-600">
                                {formatKsQuantity(entry.quantity)}
                              </td>
                              <td className="whitespace-nowrap px-3 py-2 align-middle text-right text-xs tabular-nums text-slate-600">
                                {priceStr !== "—" ? (
                                  <>
                                    {priceStr}
                                    <span className="font-normal text-slate-500">
                                      {SCRAPE_PRICE_DISPLAY_SUFFIX}
                                    </span>
                                  </>
                                ) : (
                                  "—"
                                )}
                              </td>
                              <td className="max-w-[220px] px-3 py-2 align-middle text-xs text-slate-600">
                                {entry.yMoneyName?.trim() || "—"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    </div>
                  </div>
                )}
              </Card>
            </section>
          )}

          {activeView === "dodavatelia" && (
            <section className="space-y-3">
              <Card className="overflow-hidden border-slate-200/90 p-0 shadow-sm">
                <button
                  type="button"
                  className="flex w-full items-center gap-2.5 border-b border-slate-100 bg-slate-50/80 px-3 py-2 text-left transition-colors hover:bg-slate-100/80"
                  onClick={() => setSuppliersExcelPanelOpen((o) => !o)}
                  aria-expanded={suppliersExcelPanelOpen}
                >
                  <FileSpreadsheet className="h-4 w-4 shrink-0 text-sky-600" aria-hidden />
                  <span className="text-sm font-semibold text-slate-900">
                    Excel — stĺpce pre kódy dodávateľov
                  </span>
                  {mappingProfile ? (
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800">
                      {mappingProfile.columns.length} stĺp.
                    </span>
                  ) : null}
                  <ChevronRight
                    className={cn(
                      "ml-auto h-4 w-4 shrink-0 text-slate-500 transition-transform",
                      suppliersExcelPanelOpen && "rotate-90",
                    )}
                    aria-hidden
                  />
                </button>
                {suppliersExcelPanelOpen ? (
                  <div className="space-y-2 p-3">
                    <p className="text-[11px] leading-snug text-slate-600">
                      Rovnaká cesta a sheet ako pri párovaní; potom vyber stĺpec kódu pri
                      každom dodávateľovi.
                    </p>
                    <div className="grid gap-2 sm:grid-cols-[1fr_minmax(0,140px)_auto] sm:items-end">
                      <Input
                        value={excelFilePath}
                        onChange={(event) => setExcelFilePath(event.target.value)}
                        placeholder={DEFAULT_GAMECHANGER_XLSX_PATH}
                        className="h-9 font-mono text-xs"
                        readOnly={supplierTemplateLocked}
                      />
                      <Input
                        value={sheetName}
                        onChange={(event) => setSheetName(event.target.value)}
                        placeholder="Sheet"
                        className="h-9 text-xs"
                        readOnly={supplierTemplateLocked}
                      />
                      <Button
                        type="button"
                        size="sm"
                        className="h-9 shrink-0"
                        disabled={supplierTemplateLocked}
                        onClick={() => void loadMappingProfile()}
                      >
                        Načítať
                      </Button>
                    </div>
                    {mappingProfile ? (
                      <p className="rounded border border-emerald-200/80 bg-emerald-50/60 px-2 py-1 text-[11px] text-emerald-900">
                        Načítaných {mappingProfile.columns.length} stĺpcov.
                      </p>
                    ) : (
                      <p className="rounded border border-amber-200/80 bg-amber-50/50 px-2 py-1 text-[11px] text-amber-950">
                        Stĺpce ešte nie sú — „Načítať“ alebo sekcia Párovanie.
                      </p>
                    )}
                  </div>
                ) : null}
              </Card>

              <div className="flex flex-wrap items-stretch gap-2 rounded-lg border border-sky-200/60 bg-sky-50/40 px-2 py-1.5">
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1 text-left hover:bg-white/60"
                  onClick={() => setSuppliersShippingHintOpen((o) => !o)}
                  aria-expanded={suppliersShippingHintOpen}
                >
                  <Truck className="h-4 w-4 shrink-0 text-sky-600" aria-hidden />
                  <span className="truncate text-sm font-medium text-slate-800">
                    Doprava zdarma (nápoveda)
                  </span>
                  <ChevronRight
                    className={cn(
                      "ml-auto h-4 w-4 shrink-0 text-slate-500 transition-transform",
                      suppliersShippingHintOpen && "rotate-90",
                    )}
                    aria-hidden
                  />
                </button>
                {isAppAdmin ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-8 shrink-0 border-sky-200 text-xs"
                    onClick={addSupplierForm}
                  >
                    + Dodávateľ
                  </Button>
                ) : null}
              </div>
              {suppliersShippingHintOpen ? (
                <p className="rounded-md border border-slate-100 bg-white px-3 py-2 text-xs leading-relaxed text-slate-600 shadow-sm">
                  Pri dodávateľovi zadaj sumu v € — v sekcii{" "}
                  <strong className="font-medium text-slate-800">Košík</strong> bude tlačidlo
                  na eshop <span className="text-emerald-700">zelené</span>, ak je súčet nad
                  prahom, inak <span className="text-sky-800">modré</span>.
                </p>
              ) : null}

              <div className="space-y-2">
                {supplierForms.map((supplier, index) => {
                  const rowKey = supplierCardKey(supplier, index);
                  const supplierExpanded = expandedSupplierKeys.has(rowKey);
                  return (
                    <Card
                      key={`${supplier.id ?? "new"}-${index}`}
                      className="overflow-hidden border-slate-200/90 p-0 shadow-sm"
                    >
                      <div className="flex w-full items-stretch">
                        <button
                          type="button"
                          className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-2 text-left transition-colors hover:bg-slate-50/90"
                          onClick={() => toggleSupplierCard(rowKey)}
                          aria-expanded={supplierExpanded}
                        >
                          <div
                            className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-md border border-slate-200 bg-white"
                            title="Logo"
                          >
                            {publicApiAssetUrl(supplier.logoUrl) ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img
                                src={publicApiAssetUrl(supplier.logoUrl)!}
                                alt=""
                                className="h-full w-full object-contain p-0.5"
                              />
                            ) : (
                              <Truck className="h-4 w-4 text-slate-300" aria-hidden />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-semibold text-slate-900">
                              {supplier.name || "Nový dodávateľ"}
                            </div>
                            {(supplier.codeColumn ?? "").trim() ? (
                              <div className="truncate text-[10px] text-slate-500">
                                Excel: {supplier.codeColumn}
                              </div>
                            ) : null}
                          </div>
                          <Badge
                            className={cn(
                              "shrink-0 gap-1 border px-1.5 py-0 text-[10px] font-normal leading-tight",
                              supplier.isConnected
                                ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                                : "border-amber-200 bg-amber-50 text-amber-950",
                            )}
                          >
                            <span
                              className={cn(
                                "h-1.5 w-1.5 shrink-0 rounded-full",
                                supplier.isConnected ? "bg-emerald-500" : "bg-amber-500",
                              )}
                            />
                            {supplier.isConnected ? "Pripoj." : "Neprip."}
                          </Badge>
                        </button>
                        {supplierListCanReorder ? (
                          <div className="flex shrink-0 flex-col justify-center border-l border-slate-100 py-0.5 pr-0.5">
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 shrink-0"
                              disabled={
                                index === 0 || supplierReorderBusy
                              }
                              aria-label="Posunúť dodávateľa vyššie"
                              onClick={() => void moveSupplierRow(index, -1)}
                            >
                              <ChevronUp className="h-4 w-4" aria-hidden />
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 shrink-0"
                              disabled={
                                index >= supplierForms.length - 1 ||
                                supplierReorderBusy
                              }
                              aria-label="Posunúť dodávateľa nižšie"
                              onClick={() => void moveSupplierRow(index, 1)}
                            >
                              <ChevronDown className="h-4 w-4" aria-hidden />
                            </Button>
                          </div>
                        ) : null}
                        <button
                          type="button"
                          className="flex shrink-0 items-center border-l border-slate-100 px-2 transition-colors hover:bg-slate-50/90"
                          onClick={() => toggleSupplierCard(rowKey)}
                          aria-label={
                            supplierExpanded ? "Zbaliť detail" : "Rozbaliť detail"
                          }
                        >
                          <ChevronRight
                            className={cn(
                              "h-4 w-4 shrink-0 text-slate-400 transition-transform",
                              supplierExpanded && "rotate-90",
                            )}
                            aria-hidden
                          />
                        </button>
                      </div>

                      {supplierExpanded ? (
                        <div className="space-y-3 border-t border-slate-100 px-3 pb-3 pt-2">
                          <div className="flex flex-wrap items-start gap-3 rounded-lg border border-slate-100 bg-slate-50/50 p-2">
                            <div
                              className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-slate-200 bg-white"
                              title="Logo dodávateľa"
                            >
                              {publicApiAssetUrl(supplier.logoUrl) ? (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img
                                  src={publicApiAssetUrl(supplier.logoUrl)!}
                                  alt=""
                                  className="h-full w-full object-contain p-1"
                                />
                              ) : (
                                <span className="px-1 text-center text-[8px] font-medium uppercase text-slate-400">
                                  Bez loga
                                </span>
                              )}
                            </div>
                            <div className="min-w-0 flex-1 space-y-1">
                              <label className="text-[11px] font-medium text-slate-600">
                                Logo (max 2 MB)
                              </label>
                              <div className="flex flex-wrap gap-1">
                                <input
                                  type="file"
                                  accept="image/png,image/jpeg,image/webp,image/gif"
                                  disabled={supplierTemplateLocked}
                                  className="max-w-full text-[11px] file:mr-1 file:rounded file:bg-sky-600 file:px-2 file:py-1 file:text-[11px] file:text-white disabled:opacity-50"
                                  onChange={(event) => {
                                    const file = event.target.files?.[0];
                                    event.target.value = "";
                                    if (file) void uploadSupplierLogo(index, file);
                                  }}
                                />
                                {supplier.id &&
                                supplier.logoUrl &&
                                !supplierTemplateLocked ? (
                                  <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    className="h-7 text-[11px]"
                                    onClick={() => void removeSupplierLogo(index)}
                                  >
                                    Zmazať logo
                                  </Button>
                                ) : null}
                              </div>
                              {!supplier.id ? (
                                <p className="text-[10px] text-amber-800">
                                  Logo po prvom uložení.
                                </p>
                              ) : null}
                            </div>
                          </div>

                          <div className="space-y-1">
                            <label className="text-[11px] font-medium text-slate-700">
                              Stĺpec kódu (Excel)
                            </label>
                            {(supplier.codeColumn ?? "").trim() ? (
                              <p className="rounded border border-sky-100 bg-sky-50/60 px-2 py-1 text-[11px] text-sky-900">
                                <span className="font-medium">{supplier.codeColumn}</span>
                              </p>
                            ) : (
                              <p className="text-[11px] text-slate-500">— nepriradené —</p>
                            )}
                            <select
                              value={supplier.codeColumn ?? ""}
                              onChange={(event) =>
                                updateSupplierField(index, "codeColumn", event.target.value)
                              }
                              disabled={supplierTemplateLocked}
                              className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              <option value="">— nepriradené —</option>
                              {(supplier.codeColumn ?? "").trim() &&
                              !(mappingProfile?.columns ?? []).includes(
                                supplier.codeColumn ?? "",
                              ) ? (
                                <option value={supplier.codeColumn ?? ""}>
                                  {supplier.codeColumn} (uložené)
                                </option>
                              ) : null}
                              {(mappingProfile?.columns ?? []).map((column) => (
                                <option key={column} value={column}>
                                  {column}
                                </option>
                              ))}
                            </select>
                            {!mappingProfile?.columns?.length ? (
                              <p className="text-[10px] text-slate-500">
                                Na výber stĺpcov najprv „Načítať“ v bloku Excel vyššie.
                              </p>
                            ) : null}
                          </div>

                          <div className="grid gap-2 sm:grid-cols-2">
                            <div className="space-y-1">
                              <label className="text-[11px] font-medium text-slate-700">
                                Názov
                              </label>
                              <Input
                                className="h-9 text-xs"
                                placeholder="Názov dodávateľa"
                                value={supplier.name ?? ""}
                                onChange={(event) =>
                                  updateSupplierField(index, "name", event.target.value)
                                }
                                readOnly={supplierTemplateLocked}
                              />
                            </div>
                            <div className="space-y-1">
                              <label className="text-[11px] font-medium text-slate-700">
                                URL e-shopu
                              </label>
                              <Input
                                className="h-9 font-mono text-xs"
                                placeholder="https://…"
                                value={supplier.shopUrl ?? ""}
                                onChange={(event) =>
                                  updateSupplierField(index, "shopUrl", event.target.value)
                                }
                                readOnly={supplierTemplateLocked}
                              />
                            </div>
                          </div>
                          <label className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700">
                            <input
                              type="checkbox"
                              checked={Boolean(supplier.isConnected)}
                              disabled={supplierTemplateLocked}
                              onChange={(event) =>
                                setSupplierForms((prev) =>
                                  prev.map((row, rowIndex) =>
                                    rowIndex === index
                                      ? { ...row, isConnected: event.target.checked }
                                      : row,
                                  ),
                                )
                              }
                              className="h-4 w-4 rounded border-slate-300 text-sky-600 focus:ring-sky-500 disabled:opacity-50"
                            />
                            <span>
                              Zobraziť vo vyhľadávaní
                            </span>
                          </label>

                          <div className="rounded-lg border border-sky-100 bg-sky-50/30 p-2">
                            <label className="text-[11px] font-medium text-slate-800">
                              Doprava zdarma od (€)
                            </label>
                            <Input
                              type="text"
                              inputMode="decimal"
                              className="mt-1 h-9 max-w-[160px] text-xs"
                              placeholder="napr. 150"
                              value={supplier.freeShippingThresholdEur ?? ""}
                              onChange={(event) =>
                                updateSupplierField(
                                  index,
                                  "freeShippingThresholdEur",
                                  event.target.value,
                                )
                              }
                              readOnly={supplierTemplateLocked}
                            />
                            <p className="mt-1 text-[10px] text-slate-500">
                              Košík na eshope: doména +{" "}
                              <span className="font-mono">/kosik</span>
                            </p>
                          </div>

                          <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50/50 p-2">
                            <div className="space-y-1">
                              <label className="text-[11px] font-medium text-slate-700">
                                Používateľ
                              </label>
                              <Input
                                className="h-9 text-xs"
                                placeholder="username"
                                value={supplier.username ?? ""}
                                onChange={(event) =>
                                  updateSupplierField(index, "username", event.target.value)
                                }
                              />
                            </div>
                            <div className="space-y-1">
                              <label className="text-[11px] font-medium text-slate-700">
                                Heslo
                              </label>
                              <div className="relative">
                                <Input
                                  type={showPassword ? "text" : "password"}
                                  className="h-9 pr-9 text-xs"
                                  placeholder="••••••••"
                                  value={supplier.password ?? ""}
                                  onChange={(event) =>
                                    updateSupplierField(index, "password", event.target.value)
                                  }
                                />
                                <button
                                  type="button"
                                  onClick={() => setShowPassword((value) => !value)}
                                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-slate-500 hover:bg-slate-200/80"
                                >
                                  {showPassword ? (
                                    <EyeOff className="h-3.5 w-3.5" />
                                  ) : (
                                    <Eye className="h-3.5 w-3.5" />
                                  )}
                                </button>
                              </div>
                            </div>
                          </div>

                          <div className="space-y-1 rounded-lg border border-slate-200 bg-white p-2">
                            <label className="text-[11px] font-medium text-slate-800">
                              Konfigurácia košíka (JSON, Playwright)
                            </label>
                            <p className="text-[10px] text-slate-500">
                              Na backende:{" "}
                              <code className="rounded bg-slate-100 px-0.5">
                                playwright install chromium
                              </code>
                              ; test: env{" "}
                              <code className="rounded bg-slate-100 px-0.5">
                                CART_AUTOMATION_DRY_RUN=1
                              </code>
                            </p>
                            <textarea
                              rows={6}
                              value={supplier.cartConfigJson ?? ""}
                              onChange={(event) =>
                                updateSupplierField(
                                  index,
                                  "cartConfigJson",
                                  event.target.value,
                                )
                              }
                              readOnly={supplierTemplateLocked}
                              spellCheck={false}
                              className="mt-1 w-full resize-y rounded-md border border-slate-300 px-2 py-1.5 font-mono text-[11px] text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 read-only:bg-slate-50"
                              placeholder={`{\n  "login_url": "https://obchod.sk/prihlasenie",\n  "username_selector": "#email",\n  "headless": true\n}`}
                            />
                          </div>
                        </div>
                      ) : null}

                      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 bg-slate-50/50 px-2.5 py-2">
                        <Button
                          size="sm"
                          className="h-8 text-xs shadow-sm shadow-sky-600/10"
                          onClick={() => void saveSupplier(index)}
                        >
                          {supplierTemplateLocked
                            ? "Uložiť prihlásenie"
                            : "Uložiť prístup"}
                        </Button>
                        {isAppAdmin ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="h-8 border-red-200 text-xs text-red-700 hover:bg-red-50"
                            onClick={() => void deleteSupplier(index)}
                          >
                            Odstrániť
                          </Button>
                        ) : null}
                        {saveState[supplier.id ?? index] ? (
                          <span className="text-[11px] text-slate-600">
                            {saveState[supplier.id ?? index]}
                          </span>
                        ) : null}
                      </div>
                    </Card>
                  );
                })}
              </div>
            </section>
          )}

          {activeView === "parovanie" && (
            <section className="space-y-6">
              <p
                className="inline-block max-w-full cursor-help text-sm leading-relaxed text-slate-600 underline decoration-dotted decoration-slate-400/70 underline-offset-2"
                title="V Párovaní zadáš cestu k Excelu, načítaš stĺpce a importuješ produkty, dodávateľov a väzby kódov do lokálnej databázy. Mapovanie polí určuje, čo sa zobrazí a filtruje vo Vyhľadávaní a aké kódy sa použijú pri ponukách dodávateľov."
              >
                Riadenie nakupu, skladov a mapovania produktov.
              </p>
              <div className="grid gap-6 lg:grid-cols-2">
              <Card className="overflow-hidden p-0 shadow-sm">
                <div className="border-b border-slate-200/80 bg-gradient-to-r from-slate-50 to-sky-50/40 px-5 py-4">
                  <CardTitle className="text-base font-semibold text-slate-900">
                    Zdrojový Excel
                  </CardTitle>
                  <p className="mt-1 text-sm text-slate-600">
                    Cesta k súboru, náhľad stĺpcov a import do databázy.
                  </p>
                </div>
                <div className="space-y-5 p-5">
                  <div className="rounded-xl border border-sky-200/90 bg-gradient-to-br from-sky-50/90 via-white to-white p-4 shadow-sm ring-1 ring-sky-100/60">
                    <div className="flex gap-4">
                      <div
                        className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-sky-600 text-white shadow-md shadow-sky-600/25"
                        aria-hidden
                      >
                        <FileSpreadsheet className="h-6 w-6" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-[11px] font-semibold uppercase tracking-wider text-sky-900/70">
                          Aktuálne nastavený súbor
                        </p>
                        {excelFilePath.trim() ? (
                          <>
                            <p
                              className="mt-1 truncate text-lg font-semibold tracking-tight text-slate-900"
                              title={excelBasename(excelFilePath)}
                            >
                              {excelBasename(excelFilePath)}
                            </p>
                            <p
                              className="mt-2 max-h-24 overflow-y-auto break-all rounded-md border border-slate-200/80 bg-white/90 px-2.5 py-2 font-mono text-[11px] leading-snug text-slate-600"
                              title={excelFilePath.trim()}
                            >
                              {excelFilePath.trim()}
                            </p>
                          </>
                        ) : (
                          <p className="mt-2 text-sm text-slate-500">
                            Zatiaľ nie je zadaná cesta — dopln ju v poli nižšie.
                          </p>
                        )}
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <Badge className="border-sky-200 bg-white/90 font-normal text-slate-700">
                            Náhľad: list „{sheetName.trim() || "—"}“
                          </Badge>
                          <Badge className="border-0 bg-slate-800 font-normal text-white hover:bg-slate-800">
                            Import: ten istý list („{sheetName.trim() || "DIN"}“)
                          </Badge>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="grid gap-4 sm:grid-cols-1">
                    <div className="space-y-2">
                      <label
                        htmlFor="parovanie-excel-path"
                        className="text-xs font-medium text-slate-700"
                      >
                        Úplná cesta k súboru (.xlsx)
                      </label>
                      <Input
                        id="parovanie-excel-path"
                        value={excelFilePath}
                        onChange={(event) => setExcelFilePath(event.target.value)}
                        placeholder={DEFAULT_GAMECHANGER_XLSX_PATH}
                        className="font-mono text-sm"
                        spellCheck={false}
                        autoComplete="off"
                        readOnly={!isAppAdmin}
                      />
                    </div>
                    <div className="space-y-2">
                      <label
                        htmlFor="parovanie-sheet"
                        className="text-xs font-medium text-slate-700"
                      >
                        List (náhľad aj import)
                      </label>
                      <Input
                        id="parovanie-sheet"
                        value={sheetName}
                        onChange={(event) => setSheetName(event.target.value)}
                        placeholder="napr. DIN"
                        className="max-w-xs"
                        readOnly={!isAppAdmin}
                      />
                      <p className="text-[11px] text-slate-500">
                        Prázdne pole = list <span className="font-mono">DIN</span>.
                      </p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      disabled={!isAppAdmin || mappingProfileLoading || excelImportRunning}
                      onClick={() => void loadMappingProfile()}
                      className="gap-2"
                    >
                      {mappingProfileLoading ? (
                        <Loader2 className="h-4 w-4 animate-spin opacity-90" />
                      ) : (
                        <FileSpreadsheet className="h-4 w-4 opacity-90" />
                      )}
                      Načítať stĺpce
                    </Button>
                    <Button
                      type="button"
                      disabled={!isAppAdmin || excelImportRunning || mappingProfileLoading}
                      className="gap-2 bg-emerald-600 text-white hover:bg-emerald-700"
                      onClick={() => void runExcelImport()}
                    >
                      {excelImportRunning ? (
                        <Loader2 className="h-4 w-4 animate-spin opacity-90" />
                      ) : (
                        <DatabaseZap className="h-4 w-4 opacity-90" />
                      )}
                      {excelImportRunning ? "Importujem…" : "Importovať do databázy"}
                    </Button>
                  </div>

                  <div className="rounded-lg border border-amber-200/90 bg-amber-50/60 px-3.5 py-2.5 text-xs leading-relaxed text-amber-950">
                    <strong className="font-semibold">Ako to funguje:</strong> „Načítať
                    stĺpce“ aj „Importovať do databázy“ používajú{" "}
                    <strong>ten istý list</strong> z poľa vyššie (predvolene{" "}
                    <span className="font-mono">DIN</span>). Hlavičky v tom liste musia sedieť
                    s mapovaním. Pred importom potvrď mapovanie vpravo.
                  </div>

                  {mappingStatus ? (
                    <div
                      className={cn(
                        "rounded-lg border px-3.5 py-2.5 text-sm leading-snug",
                        mappingStatus.includes("hotový") ||
                          mappingStatus.includes("uložené") ||
                          mappingStatus.includes("úspešne") ||
                          mappingStatus.includes("uspesne")
                          ? "border-emerald-200 bg-emerald-50/80 text-emerald-950"
                          : mappingStatus.includes("Importujem") ||
                              mappingStatus.includes("Nacitavam") ||
                              mappingStatus.includes("Načítavam") ||
                              mappingStatus.includes("Ukladám")
                            ? "border-sky-200 bg-sky-50/80 text-sky-950"
                            : "border-rose-200 bg-rose-50/80 text-rose-950",
                      )}
                    >
                      {mappingStatus}
                      {mappingProfileLoading ? (
                        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-sky-200/70">
                          <div className="h-full w-1/2 animate-pulse rounded-full bg-sky-600" />
                        </div>
                      ) : null}
                      {excelImportRunning || excelImportProgressPct !== null ? (
                        <div className="mt-2">
                          <div className="mb-1 flex items-center justify-between text-[11px] opacity-80">
                            <span>Priebeh importu</span>
                            <span>{Math.max(0, Math.min(100, excelImportProgressPct ?? 0))}%</span>
                          </div>
                          <div className="h-1.5 w-full overflow-hidden rounded-full bg-sky-200/70">
                            <div
                              className="h-full rounded-full bg-sky-600 transition-all duration-300"
                              style={{
                                width: `${Math.max(0, Math.min(100, excelImportProgressPct ?? 0))}%`,
                              }}
                            />
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  <div>
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Náhľad dát
                      </p>
                      {mappingProfile ? (
                        <span className="text-[11px] text-slate-500">
                          {mappingProfile.columns.length} stĺpcov · prvých 6 + 4 riadky
                        </span>
                      ) : null}
                    </div>
                    {mappingProfile &&
                    mappingProfile.columns.length > 0 &&
                    mappingProfile.preview_rows.length > 0 ? (
                      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
                        <table className="w-full text-sm">
                          <thead className="bg-slate-100/90 text-left text-xs font-medium text-slate-600">
                            <tr>
                              {mappingProfile.columns.slice(0, 6).map((column) => (
                                <th key={column} className="px-3 py-2.5">
                                  {column}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody className="text-slate-800">
                            {mappingProfile.preview_rows.slice(0, 4).map((row, rowIndex) => (
                              <tr
                                key={rowIndex}
                                className="border-t border-slate-100 odd:bg-slate-50/40"
                              >
                                {mappingProfile.columns.slice(0, 6).map((column) => (
                                  <td
                                    key={`${rowIndex}-${column}`}
                                    className="max-w-[10rem] truncate px-3 py-2 text-xs"
                                    title={row[column] || undefined}
                                  >
                                    {row[column] || "—"}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <div className="flex min-h-[140px] flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-200 bg-slate-50/50 px-4 py-8 text-center">
                        <FileSpreadsheet className="mb-2 h-8 w-8 text-slate-300" />
                        <p className="text-sm font-medium text-slate-600">
                          Zatiaľ žiadny náhľad
                        </p>
                        <p className="mt-1 max-w-sm text-xs text-slate-500">
                          Zadaj cestu k .xlsx a klikni na{" "}
                          <span className="font-medium text-slate-700">Načítať stĺpce</span>.
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              </Card>

              <Card className="overflow-hidden p-0 shadow-sm">
                <div className="border-b border-slate-200/90 bg-gradient-to-r from-slate-50 to-emerald-50/30 px-5 py-4">
                  <CardTitle className="text-base font-semibold text-slate-900">
                    Mapovanie interných polí
                  </CardTitle>
                  <p className="mt-1 text-sm text-slate-600">
                    Každému poľu v databáze vyber zodpovedajúci stĺpec z Excelu (presný názov
                    hlavičky).
                  </p>
                  <div className="mt-4 flex items-center gap-3">
                    <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-200">
                      <div
                        className="h-full rounded-full bg-emerald-500 transition-[width] duration-300 ease-out"
                        style={{
                          width: `${Math.min(100, (matchedCount / 8) * 100)}%`,
                        }}
                      />
                    </div>
                    <span className="shrink-0 tabular-nums text-xs font-semibold text-slate-700">
                      {matchedCount} / 8
                    </span>
                  </div>
                </div>

                <div className="space-y-3 p-5">
                  {(
                    [
                      { field: "code" as const, label: "Kód (interný)" },
                      { field: "norma" as const, label: "Norma" },
                      { field: "surface" as const, label: "Povrchová úprava" },
                      { field: "diameter" as const, label: "Priemer" },
                      { field: "length" as const, label: "Dĺžka" },
                      {
                        field: "v_class" as const,
                        label: `Class (stĺpec ${EXCEL_COL_V})`,
                      },
                      {
                        field: "y_money_name" as const,
                        label: `Money názov (stĺpec ${EXCEL_COL_Y})`,
                      },
                      {
                        field: "image_filename" as const,
                        label: `Obrázok súbor (stĺpec ${EXCEL_COL_W})`,
                      },
                    ] as const
                  ).map(({ field, label }, index) => {
                    const typedField = field as FilterField;
                    const value = fieldToColumn[typedField];
                    const mapped = value.length > 0;
                    const selectId = `parovanie-map-${field}`;
                    return (
                      <div
                        key={field}
                        className={cn(
                          "rounded-xl border-2 p-4 transition-colors",
                          mapped
                            ? "border-emerald-300/90 bg-emerald-50/50 shadow-sm"
                            : "border-slate-200 bg-white",
                        )}
                      >
                        <div className="mb-3 flex items-start justify-between gap-3">
                          <div className="flex min-w-0 flex-1 items-center gap-3">
                            <span
                              className={cn(
                                "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-sm font-bold tabular-nums",
                                mapped
                                  ? "bg-emerald-600 text-white shadow-sm"
                                  : "bg-slate-200 text-slate-700",
                              )}
                              aria-hidden
                            >
                              {index + 1}
                            </span>
                            <div className="min-w-0">
                              <label
                                htmlFor={selectId}
                                className="block text-sm font-semibold text-slate-900"
                              >
                                {label}
                              </label>
                              <p className="mt-0.5 truncate text-[11px] text-slate-500">
                                {mapped ? (
                                  <>
                                    Stĺpec:{" "}
                                    <span className="font-medium text-slate-700" title={value}>
                                      {value}
                                    </span>
                                  </>
                                ) : (
                                  "Vyber stĺpec z Excelu"
                                )}
                              </p>
                            </div>
                          </div>
                          {mapped ? (
                            <Check
                              className="h-5 w-5 shrink-0 text-emerald-600"
                              strokeWidth={2.5}
                              aria-hidden
                            />
                          ) : null}
                        </div>
                        <select
                          id={selectId}
                          value={value}
                          onChange={(event) =>
                            setFieldToColumn((prev) => ({
                              ...prev,
                              [typedField]: event.target.value,
                            }))
                          }
                          disabled={!isAppAdmin}
                          className="h-11 w-full min-w-0 max-w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          <option value="">— Nevybrané —</option>
                          {(mappingProfile?.columns ?? []).map((column) => (
                            <option key={column} value={column}>
                              {column}
                            </option>
                          ))}
                        </select>
                      </div>
                    );
                  })}
                </div>

                <div className="border-t border-slate-200 bg-slate-50/50 px-5 py-4">
                  <Button
                    className="w-full gap-2"
                    type="button"
                    disabled={!isAppAdmin}
                    onClick={() => void saveFieldMapping()}
                  >
                    <ShieldCheck className="h-4 w-4" />
                    Potvrdiť mapovanie
                  </Button>
                </div>

                <div className="space-y-4 border-t border-slate-200 bg-slate-50/30 px-5 py-5">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">
                      Ukážka hodnôt z Excelu
                    </p>
                    <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                      Vyžaduje{" "}
                      <span className="font-medium text-slate-600">Načítať stĺpce</span> vľavo.
                      Nie sú z databázy po importe.
                    </p>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {(
                      [
                        ["Kód", "code"],
                        ["Norma", "norma"],
                        ["Povrchová úprava", "surface"],
                        ["Priemer", "diameter"],
                        ["Dĺžka", "length"],
                        [`Class (stĺpec ${EXCEL_COL_V})`, "v_class"],
                        [`Money názov (stĺpec ${EXCEL_COL_Y})`, "y_money_name"],
                        [`Obrázok súbor (stĺpec ${EXCEL_COL_W})`, "image_filename"],
                      ] as Array<[string, FilterField]>
                    ).map(([label, field]) => {
                      const opts = dropdownOptions(field);
                      const mapped = isFieldMapped(field);
                      return (
                        <div key={field} className="space-y-1.5 rounded-lg border border-slate-200/80 bg-white p-3 shadow-sm">
                          <p className="text-xs font-medium text-slate-700">
                            {label}
                            {!mapped ? (
                              <span className="ml-1 font-normal text-amber-700">· nemapované</span>
                            ) : opts.length === 0 ? (
                              <span className="ml-1 font-normal text-slate-400">
                                · bez hodnôt v náhľade
                              </span>
                            ) : null}
                          </p>
                          <select
                            className="h-10 w-full min-w-0 max-w-full rounded-md border border-slate-300 bg-white px-2.5 text-xs text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400"
                            disabled={!mapped || opts.length === 0}
                            aria-label={`Ukážka hodnôt: ${label}`}
                          >
                            <option value="">
                              {mapped && opts.length === 0
                                ? "Žiadne unikátne hodnoty"
                                : !mapped
                                  ? "Najprv mapuj vyššie"
                                  : `Ukážka (${opts.length} hodnôt)`}
                            </option>
                            {opts.slice(0, 150).map((option) => (
                              <option key={option} value={option}>
                                {option}
                              </option>
                            ))}
                          </select>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </Card>
              </div>
            </section>
          )}

          {activeView === "ponuky" && (
            <OffersPanel
              apiBase={API_BASE}
              apiFetch={apiFetch}
              apiToken={apiToken}
              authReady={authSessionReady}
              companyConfigured={companyConfigured}
            />
          )}

          {activeView === "admin" && (
            <section className="space-y-4">
              <CompanySettingsAdmin
                apiBase={API_BASE}
                apiFetch={apiFetch}
                apiToken={apiToken}
                assetUrl={publicApiAssetUrl}
              />
              <Card className="overflow-hidden border-violet-200/80 p-0 shadow-sm ring-1 ring-violet-100/60">
                <div className="border-b border-violet-200/60 bg-gradient-to-r from-violet-50 via-white to-slate-50 px-5 py-4">
                  <h2 className="text-base font-semibold text-slate-900">
                    Správa účtov (pobočky)
                  </h2>
                  <p className="mt-1 text-sm text-slate-600">
                    Centrálna šablóna dodávateľov (URL, JSON, mapovanie) je spoločná. Každý účet
                    dostane kópie prihlasovacích údajov z tejto šablóny a v sekcii{" "}
                    <strong className="font-medium text-slate-800">Dodávatelia</strong> si môže
                    zadať vlastné meno a heslo do B2B e-shopov.
                  </p>
                </div>
                <div className="space-y-5 p-5">
                  {adminUsersError ? (
                    <p
                      className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900"
                      role="alert"
                    >
                      {adminUsersError}
                    </p>
                  ) : null}
                  <div className="rounded-lg border border-slate-200/90 bg-slate-50/80 px-4 py-3 text-sm text-slate-700">
                    <p className="font-medium text-slate-900">Používatelia</p>
                    {adminUsers.length === 0 ? (
                      <p className="mt-1 text-xs text-slate-600">Načítavam…</p>
                    ) : (
                      <ul className="mt-2 space-y-1.5 text-xs">
                        {adminUsers.map((u) => (
                          <li
                            key={u.id}
                            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200/80 bg-white/90 px-3 py-2"
                          >
                            <span className="font-medium text-slate-900">{u.username}</span>
                            <span className="text-slate-600">
                              {u.display_label ?? "—"}
                              {u.is_admin ? (
                                <Badge className="ml-2 border-violet-200 bg-violet-50 text-violet-900">
                                  Admin
                                </Badge>
                              ) : (
                                <Badge className="ml-2 border-slate-200 bg-slate-50 text-slate-800">
                                  Pobočka
                                </Badge>
                              )}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div className="rounded-xl border border-violet-200/70 bg-white px-4 py-4 shadow-sm ring-1 ring-violet-100/40">
                    <p className="text-sm font-semibold text-slate-900">
                      Nový účet pobočky
                    </p>
                    <p className="mt-1 text-xs text-slate-600">
                      Zdedí všetkých dodávateľov a aktuálne skopírované prihlasovacie údaje z
                      centrálnej šablóny. Pobočka si ich následne zmení sama.
                    </p>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2">
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-slate-700">
                          Prihlasovacie meno
                        </label>
                        <Input
                          value={newBranchUsername}
                          onChange={(e) => setNewBranchUsername(e.target.value)}
                          autoComplete="off"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-slate-700">Heslo</label>
                        <Input
                          type="password"
                          value={newBranchPassword}
                          onChange={(e) => setNewBranchPassword(e.target.value)}
                          autoComplete="new-password"
                        />
                      </div>
                      <div className="space-y-1.5 sm:col-span-2">
                        <label className="text-xs font-medium text-slate-700">
                          Označenie pobočky (voliteľné)
                        </label>
                        <Input
                          value={newBranchLabel}
                          onChange={(e) => setNewBranchLabel(e.target.value)}
                          placeholder="napr. Bratislava"
                        />
                      </div>
                    </div>
                    <Button
                      type="button"
                      className="mt-4 shadow-sm shadow-violet-600/20"
                      disabled={adminUserSubmitting}
                      onClick={() => void createBranchAccount()}
                    >
                      {adminUserSubmitting ? "Vytváram…" : "Vytvoriť účet"}
                    </Button>
                  </div>
                </div>
              </Card>
            </section>
          )}

          {activeView === "dev" && (
            <section className="space-y-4">
              <Card className="p-4">
                <div className="mb-4 flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <p className="text-xs font-medium text-slate-800">
                      Screenshoty krokov (Playwright)
                    </p>
                    <p className="mt-0.5 text-[11px] text-slate-600">
                      Predvolene vypnuté (rýchlejší beh). Zapnúť ich vieš tu alebo env{" "}
                      <code className="rounded bg-white px-1">SCRAPER_STEP_SCREENSHOTS=1</code>.
                    </p>
                  </div>
                  <select
                    value={devStepScreenshotMode || "env"}
                    disabled={devStepScreenshotMode === ""}
                    onChange={(e) => {
                      const v = e.target.value;
                      if (v === "on" || v === "off" || v === "env") {
                        void applyDevStepScreenshotMode(v);
                      }
                    }}
                    className="h-8 w-full max-w-xs shrink-0 rounded-md border border-slate-300 bg-white px-2 text-xs text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 disabled:opacity-60"
                  >
                    <option value="off">Vypnuté</option>
                    <option value="on">Zapnuté</option>
                    <option value="env">Podľa env (SCRAPER_STEP_SCREENSHOTS)</option>
                  </select>
                </div>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-slate-900">
                      Dev — log automatizácie (Playwright)
                    </p>
                    <p className="mt-1 text-xs text-slate-600">
                      Záznamy z košíka (<code className="rounded bg-slate-100 px-1">cart</code>) a
                      scrapu ceny/skladu (<code className="rounded bg-slate-100 px-1">scrape</code>
                      ). Otvor túto záložku počas behu alebo klikni Obnoviť. Filter „dodávateľ“ musí
                      sedieť s riadkom (inak zvoľ „Všetci“). Po reštarte API je buffer prázdny; kópia
                      záznamov je v{" "}
                      <code className="rounded bg-slate-100 px-1">backend/data/dev_automation.ndjson</code>
                      .
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <select
                      value={devSupplierFilter}
                      onChange={(event) => setDevSupplierFilter(event.target.value)}
                      className="h-8 rounded-md border border-slate-300 bg-white px-2 text-xs text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
                    >
                      <option value="all">Všetci dodávatelia</option>
                      {devSupplierOptions.map((supplier) => (
                        <option key={supplier} value={supplier}>
                          {supplier}
                        </option>
                      ))}
                    </select>
                    <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-700">
                      <input
                        type="checkbox"
                        checked={devLogPaused}
                        onChange={(e) => setDevLogPaused(e.target.checked)}
                        className="rounded border-slate-300"
                      />
                      Pozastaviť obnovovanie
                    </label>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => void refreshDevLogs()}
                    >
                      Obnoviť
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => void clearDevLogs()}
                    >
                      Vymazať log
                    </Button>
                  </div>
                </div>
                {devLogError ? (
                  <p className="mb-2 text-sm text-rose-600">{devLogError}</p>
                ) : null}
                <div
                  ref={devLogScrollRef}
                  onScroll={handleDevLogScroll}
                  className="max-h-[calc(100vh-220px)] overflow-y-auto rounded-lg border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-200"
                >
                  {visibleDevLogs.length === 0 ? (
                    <p className="text-slate-500">
                      Zatiaľ prázdne pre tento filter. Spusti scrape (rozbaľ produkt) alebo{" "}
                      <span className="text-slate-400">🛒</span> — záznamy sa objavia tu.
                    </p>
                  ) : (
                    visibleDevLogs.map((line, index) => (
                      <div
                        key={`${line.ts}-${index}-${line.source}-${line.run_id ?? ""}`}
                        className="border-b border-slate-800/90 py-1.5 last:border-b-0"
                      >
                        <div className="flex flex-wrap gap-x-2 gap-y-0.5">
                          <span className="shrink-0 text-slate-500">{line.ts}</span>
                          <span
                            className={cn(
                              "shrink-0 font-semibold uppercase",
                              line.level === "error" && "text-red-400",
                              line.level === "warn" && "text-amber-400",
                              line.level === "trace" && "text-slate-500",
                              line.level === "info" && "text-emerald-500",
                            )}
                          >
                            {line.level}
                          </span>
                          <span className="text-sky-400">{line.source}</span>
                          {line.supplier ? (
                            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">
                              {line.supplier}
                            </span>
                          ) : null}
                          {line.run_id ? (
                            <span className="text-[10px] text-slate-500">
                              run: {line.run_id}
                            </span>
                          ) : null}
                        </div>
                        <div className="mt-0.5 whitespace-pre-wrap break-words text-slate-200">
                          {line.message}
                        </div>
                        {line.screenshot_url ? (
                          <div className="mt-1.5 space-y-1">
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img
                              src={`${API_BASE}${line.screenshot_url}`}
                              alt="Playwright screenshot"
                              className="max-h-44 rounded border border-slate-700"
                            />
                            <a
                              href={`${API_BASE}${line.screenshot_url}`}
                              target="_blank"
                              rel="noreferrer"
                              className="text-[10px] text-sky-400 underline"
                            >
                              Otvoriť screenshot
                            </a>
                          </div>
                        ) : null}
                      </div>
                    ))
                  )}
                </div>
              </Card>
            </section>
          )}
          {listPicker ? (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4"
              role="dialog"
              aria-modal="true"
              aria-label="Výber zoznamu pre produkt"
              onClick={() => setListPicker(null)}
            >
              <div
                className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-4 shadow-2xl"
                onClick={(event) => event.stopPropagation()}
              >
                <h3 className="text-sm font-semibold text-slate-900">
                  Pridať produkt do zoznamu
                </h3>
                <p className="mt-1 text-xs text-slate-600">
                  Produkt: <span className="font-medium">{listPicker.internalCode}</span>
                </p>
                {productLists.length > 0 ? (
                  <select
                    className="mt-3 h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm"
                    value={listPicker.listId ?? ""}
                    onChange={(e) =>
                      setListPicker((prev) =>
                        prev
                          ? {
                              ...prev,
                              listId: e.target.value ? Number(e.target.value) : null,
                            }
                          : prev,
                      )
                    }
                  >
                    <option value="">— Vyber zoznam —</option>
                    {productLists.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    Najprv si vytvor zoznam v sekcii Zoznamy.
                  </p>
                )}
                <div className="mt-4 flex justify-end gap-2">
                  <Button type="button" variant="outline" onClick={() => setListPicker(null)}>
                    Zrušiť
                  </Button>
                  <Button
                    type="button"
                    disabled={listPicker.listId == null}
                    onClick={() => {
                      if (listPicker.listId == null) return;
                      void addProductToList(listPicker.listId, listPicker.internalCode);
                      setListPicker(null);
                    }}
                  >
                    Pridať
                  </Button>
                </div>
              </div>
            </div>
          ) : null}
          {imagePreview ? (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/75 p-4"
              role="dialog"
              aria-modal="true"
              aria-label={`Obrázok produktu ${imagePreview.code}`}
              onClick={() => setImagePreview(null)}
            >
              <div
                className="w-full max-w-4xl rounded-xl border border-slate-200 bg-white shadow-2xl"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-2.5">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-slate-900">
                      {imagePreview.code}
                    </p>
                    <p
                      className="truncate text-xs text-slate-500"
                      title={imagePreview.filename}
                    >
                      {imagePreview.filename}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-700"
                    aria-label="Zavrieť náhľad obrázka"
                    onClick={() => setImagePreview(null)}
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <div className="max-h-[78vh] overflow-auto p-3">
                  {/* Obrázok sa načíta až po otvorení popupu (lazy by-click). */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={imagePreview.url}
                    alt={`Produkt ${imagePreview.code}`}
                    loading="lazy"
                    className="mx-auto h-auto max-w-full rounded border border-slate-200"
                  />
                </div>
              </div>
            </div>
          ) : null}
        </main>
      </div>
      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200/90 bg-white/95 px-2 py-1.5 backdrop-blur md:hidden">
        <div className="mx-auto grid max-w-3xl grid-cols-6 gap-0.5 sm:gap-1">
          {[
            { id: "vyhladavanie" as const, label: "Hľadať", icon: PackageSearch },
            { id: "zoznamy" as const, label: "Zoznamy", icon: List },
            { id: "kosik" as const, label: "Košík", icon: ShoppingCart },
            { id: "ponuky" as const, label: "Ponuky", icon: FileText },
            { id: "historia" as const, label: "História", icon: History },
          ].map((item) => {
            const Icon = item.icon;
            const active = activeView === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => {
                  setActiveView(item.id);
                  setMobileMenuOpen(false);
                }}
                className={cn(
                  "flex flex-col items-center justify-center gap-0.5 rounded-md px-1 py-1 text-[10px] font-medium transition-colors",
                  active ? "bg-sky-100 text-sky-700" : "text-slate-600 hover:bg-slate-100",
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </button>
            );
          })}
          <button
            type="button"
            onClick={() => setMobileMenuOpen((v) => !v)}
            className={cn(
              "flex flex-col items-center justify-center gap-0.5 rounded-md px-1 py-1 text-[10px] font-medium transition-colors",
              mobileMenuOpen ? "bg-slate-200 text-slate-800" : "text-slate-600 hover:bg-slate-100",
            )}
          >
            <Menu className="h-4 w-4" />
            Viac
          </button>
        </div>
      </div>
      {mobileMenuOpen ? (
        <div className="fixed inset-0 z-50 bg-slate-900/35 md:hidden" onClick={() => setMobileMenuOpen(false)}>
          <div
            className="absolute bottom-16 left-2 right-2 rounded-xl border border-slate-200 bg-white p-2 shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="grid grid-cols-2 gap-2">
              {[
                { id: "dodavatelia" as const, label: "Dodávatelia", icon: Truck },
                { id: "parovanie" as const, label: "Párovanie", icon: Link2 },
                ...(isAppAdmin
                  ? [{ id: "admin" as const, label: "Admin", icon: KeyRound }]
                  : []),
                { id: "dev" as const, label: "Dev / log", icon: Terminal },
              ].map((item) => {
                const Icon = item.icon;
                const active = activeView === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => {
                      setActiveView(item.id);
                      setMobileMenuOpen(false);
                    }}
                    className={cn(
                      "flex items-center gap-2 rounded-md border px-2.5 py-2 text-sm font-medium transition-colors",
                      active
                        ? "border-sky-200 bg-sky-50 text-sky-700"
                        : "border-slate-200 text-slate-700 hover:bg-slate-50",
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {item.label}
                  </button>
                );
              })}
            </div>
            <button
              type="button"
              onClick={() => setThemeMode((prev) => (prev === "dark" ? "light" : "dark"))}
              className="mt-2 flex w-full items-center justify-center gap-2 rounded-md border border-slate-200 px-2.5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              {themeMode === "dark" ? (
                <Sun className="h-4 w-4" />
              ) : (
                <Moon className="h-4 w-4" />
              )}
              {themeMode === "dark" ? "Svetlý mód" : "Tmavý mód"}
            </button>
            <button
              type="button"
              onClick={() => {
                setMobileMenuOpen(false);
                void fetch("/api/auth/logout", { method: "POST" }).finally(() => {
                  window.location.href = "/login";
                });
              }}
              className="mt-2 flex w-full items-center justify-center gap-2 rounded-md border border-slate-200 px-2.5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              <LogOut className="h-4 w-4" />
              Odhlásiť
            </button>
          </div>
        </div>
      ) : null}
      <AddToOfferDialog
        open={addToOfferOpen}
        payload={addToOfferPayload}
        apiBase={API_BASE}
        apiFetch={apiFetch}
        onClose={() => {
          setAddToOfferOpen(false);
          setAddToOfferPayload(null);
        }}
        onAdded={() => {
          setAddToOfferFeedback("Položka bola pridaná do vybranej ponuky.");
          window.setTimeout(() => setAddToOfferFeedback(null), 5000);
        }}
      />
    </div>
  );
}

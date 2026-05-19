"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ImageIcon, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export type ProductImageCascadeFilters = {
  code: string | null;
  norma: string | null;
  surface: string | null;
  diameter: string | null;
  length: string | null;
  v_class: string | null;
  y_money_name: string | null;
};

type ImageFilterOption = {
  filename: string;
  count: number;
};

type ProductImageFilterProps = {
  value: string;
  onChange: (filename: string) => void;
  cascadeFilters: ProductImageCascadeFilters;
  apiFetch: (url: string, init?: RequestInit) => Promise<Response>;
  apiBase: string;
  imageUrl: (filename: string) => string | null;
  className?: string;
};

function cascadeCacheKey(filters: ProductImageCascadeFilters): string {
  return JSON.stringify(filters);
}

export function ProductImageFilter({
  value,
  onChange,
  cascadeFilters,
  apiFetch,
  apiBase,
  imageUrl,
  className,
}: ProductImageFilterProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const cacheRef = useRef<Map<string, ImageFilterOption[]>>(new Map());
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState<ImageFilterOption[]>([]);

  const loadOptions = useCallback(async () => {
    const key = cascadeCacheKey(cascadeFilters);
    const cached = cacheRef.current.get(key);
    if (cached) {
      setOptions(cached);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`${apiBase}/api/products/filter-options/images`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...cascadeFilters,
          image_filename: null,
          prefetch_live_prices: false,
        }),
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = (await res.json()) as ImageFilterOption[];
      const list = Array.isArray(data) ? data : [];
      cacheRef.current.set(key, list);
      setOptions(list);
    } catch {
      setError("Nepodarilo sa načítať obrázky.");
      setOptions([]);
    } finally {
      setLoading(false);
    }
  }, [apiBase, apiFetch, cascadeFilters]);

  useEffect(() => {
    if (!open) {
      return;
    }
    void loadOptions();
  }, [open, loadOptions]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onDoc = (event: MouseEvent) => {
      const el = rootRef.current;
      if (!el || !(event.target instanceof Node) || !el.contains(event.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const active = Boolean(value.trim());

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={
          active
            ? `Filter obrázkom: ${value}. Otvoriť výber.`
            : "Filter podľa obrázku produktu"
        }
        title={active ? `Obrázok: ${value}` : "Filter podľa obrázku"}
        onClick={() => setOpen((prev) => !prev)}
        className={cn(
          "flex h-10 w-full items-center justify-center rounded-lg border bg-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500",
          active
            ? "border-sky-500 bg-sky-50 text-sky-800"
            : "border-slate-300 text-slate-600 hover:border-slate-400 hover:bg-slate-50",
        )}
      >
        {active && imageUrl(value) ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={imageUrl(value)!}
            alt=""
            className="h-8 w-8 object-contain"
          />
        ) : (
          <ImageIcon className="h-5 w-5" aria-hidden />
        )}
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label="Výber obrázka produktu"
          className="absolute left-0 z-50 mt-1 w-[min(100vw-2rem,22rem)] rounded-lg border border-slate-200 bg-white p-2.5 shadow-lg sm:w-[26rem]"
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-slate-700">Obrázok</span>
            {active ? (
              <Button
                type="button"
                variant="default"
                size="sm"
                className="h-7 px-2 text-[11px]"
                onClick={() => {
                  onChange("");
                  setOpen(false);
                }}
              >
                Zrušiť
              </Button>
            ) : null}
          </div>
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-xs text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              Načítavam…
            </div>
          ) : error ? (
            <p className="py-4 text-center text-xs text-red-600">{error}</p>
          ) : options.length === 0 ? (
            <p className="py-4 text-center text-xs text-slate-500">
              Žiadne obrázky pre aktuálne filtre.
            </p>
          ) : (
            <div className="grid max-h-80 grid-cols-3 gap-2.5 overflow-y-auto sm:max-h-96 sm:grid-cols-4 sm:gap-3">
              {options.map((opt) => {
                const url = imageUrl(opt.filename);
                const selected = value === opt.filename;
                return (
                  <button
                    key={opt.filename}
                    type="button"
                    title={`${opt.filename} (${opt.count})`}
                    aria-pressed={selected}
                    onClick={() => {
                      onChange(opt.filename);
                      setOpen(false);
                    }}
                    className={cn(
                      "relative aspect-square min-h-[4.75rem] overflow-hidden rounded-md border bg-white transition-colors hover:border-sky-400 hover:ring-1 hover:ring-sky-200 sm:min-h-[5.75rem]",
                      selected
                        ? "border-sky-500 ring-2 ring-sky-400/60"
                        : "border-slate-200",
                    )}
                  >
                    {url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={url}
                        alt={opt.filename}
                        loading="lazy"
                        decoding="async"
                        className="h-full w-full object-contain"
                      />
                    ) : (
                      <span className="flex h-full w-full items-center justify-center bg-slate-50">
                        <ImageIcon
                          className="h-7 w-7 text-slate-400 sm:h-8 sm:w-8"
                          aria-hidden
                        />
                      </span>
                    )}
                    {selected ? (
                      <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-sky-600 text-white">
                        <Check className="h-2.5 w-2.5" strokeWidth={3} />
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

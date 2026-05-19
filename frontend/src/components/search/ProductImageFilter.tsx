"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
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
  const panelRef = useRef<HTMLDivElement>(null);
  const cacheRef = useRef<Map<string, ImageFilterOption[]>>(new Map());
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState<ImageFilterOption[]>([]);
  const [panelPos, setPanelPos] = useState<{
    top: number;
    left: number;
    width: number;
  } | null>(null);

  const updatePanelPos = useCallback(() => {
    const anchor = rootRef.current;
    if (!anchor) {
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const margin = 8;
    const isDesktop = window.innerWidth >= 1024;
    const maxPanel = isDesktop ? 560 : 480;
    const width = Math.min(maxPanel, window.innerWidth - margin * 2);
    let left = rect.left;
    if (left + width > window.innerWidth - margin) {
      left = window.innerWidth - width - margin;
    }
    if (left < margin) {
      left = margin;
    }
    setPanelPos({
      top: rect.bottom + 4,
      left,
      width,
    });
  }, []);

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

  useLayoutEffect(() => {
    if (!open) {
      setPanelPos(null);
      return;
    }
    updatePanelPos();
    const onLayout = () => updatePanelPos();
    window.addEventListener("resize", onLayout);
    window.addEventListener("scroll", onLayout, true);
    return () => {
      window.removeEventListener("resize", onLayout);
      window.removeEventListener("scroll", onLayout, true);
    };
  }, [open, updatePanelPos]);

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
      const anchor = rootRef.current;
      const panel = panelRef.current;
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (
        (anchor && anchor.contains(target)) ||
        (panel && panel.contains(target))
      ) {
        return;
      }
      setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const active = Boolean(value.trim());

  return (
    <div ref={rootRef} className={cn("relative min-w-0", className)}>
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
        onClick={() => {
          if (open) {
            setOpen(false);
            return;
          }
          updatePanelPos();
          setOpen(true);
        }}
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
      {open && panelPos ? (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="Výber obrázka produktu"
          style={{
            position: "fixed",
            top: panelPos.top,
            left: panelPos.left,
            width: panelPos.width,
          }}
          className="z-50 overflow-hidden rounded-lg border border-slate-200 bg-white p-2.5 shadow-lg sm:p-3"
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
            <div className="grid max-h-[min(52vh,20rem)] w-full min-w-0 grid-cols-3 gap-2 overflow-x-hidden overflow-y-auto sm:max-h-[min(62vh,28rem)] sm:grid-cols-4 sm:gap-2.5 lg:max-h-[min(72vh,40rem)] lg:grid-cols-3 lg:gap-3">
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
                      "relative aspect-square w-full min-w-0 overflow-hidden rounded-md border bg-white transition-colors hover:border-sky-400 hover:ring-1 hover:ring-sky-200",
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

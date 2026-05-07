"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";

type SearchableSelectProps = {
  id?: string;
  value: string;
  onChange: (next: string) => void;
  options: string[];
  /** Zobrazenie pri prázdnej hodnote (predvolene „Všetky“). */
  emptyLabel?: string;
  placeholder?: string;
  className?: string;
};

export function SearchableSelect({
  id: idProp,
  value,
  onChange,
  options,
  emptyLabel = "Všetky",
  placeholder = "Hľadať v zozname…",
  className,
}: SearchableSelectProps) {
  const genId = useId();
  const baseId = idProp ?? genId.replace(/:/g, "");
  const listId = `${baseId}-list`;
  const searchId = `${baseId}-search`;

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const normLoose = (s: string): string =>
    s
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/\s+/g, " ")
      .trim();

  const normCompact = (s: string): string =>
    normLoose(s).replace(/[^a-z0-9]+/g, "");

  const filtered = useMemo(() => {
    const q = query.trim();
    if (!q) {
      return options;
    }
    const qLoose = normLoose(q);
    const qCompact = normCompact(q);
    return options.filter((opt) => {
      const oLoose = normLoose(opt);
      if (oLoose.includes(qLoose)) {
        return true;
      }
      const oCompact = normCompact(opt);
      return qCompact.length > 0 && oCompact.includes(qCompact);
    });
  }, [options, query]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const t = window.setTimeout(() => searchInputRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onDoc = (event: MouseEvent) => {
      const el = rootRef.current;
      if (!el || !(event.target instanceof Node) || !el.contains(event.target)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const displayLabel = value ? value : emptyLabel;

  return (
    <div ref={rootRef} className={cn("relative w-full", className)}>
      <button
        type="button"
        id={baseId}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => {
          setOpen((prev) => !prev);
          if (open) {
            setQuery("");
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setOpen(false);
            setQuery("");
          }
        }}
        className={cn(
          "flex h-10 w-full items-center justify-between gap-2 rounded-lg border border-slate-300 bg-white px-3 text-left text-sm text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500",
          !value && "text-slate-500",
        )}
      >
        <span className="min-w-0 flex-1 truncate">{displayLabel}</span>
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 text-slate-500 transition-transform",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </button>
      {open ? (
        <div
          id={listId}
          role="listbox"
          className="absolute z-50 mt-1 w-full min-w-[12rem] rounded-lg border border-slate-200 bg-white py-1 shadow-lg"
        >
          <div className="border-b border-slate-100 px-2 pb-2 pt-1">
            <Input
              ref={searchInputRef}
              id={searchId}
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={placeholder}
              autoComplete="off"
              className="h-9 text-sm"
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.stopPropagation();
                  setOpen(false);
                  setQuery("");
                }
              }}
            />
          </div>
          <ul className="max-h-48 overflow-y-auto py-1">
            <li role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={value === ""}
                className="w-full px-3 py-2 text-left text-sm hover:bg-slate-100"
                onClick={() => {
                  onChange("");
                  setOpen(false);
                  setQuery("");
                }}
              >
                {emptyLabel}
              </button>
            </li>
            {filtered.length === 0 ? (
              <li className="px-3 py-2 text-xs text-slate-500">Žiadna zhoda.</li>
            ) : (
              filtered.map((opt) => (
                <li key={opt} role="presentation">
                  <button
                    type="button"
                    role="option"
                    aria-selected={value === opt}
                    className={cn(
                      "w-full px-3 py-2 text-left text-sm hover:bg-slate-100",
                      value === opt && "bg-sky-50 font-medium text-sky-900",
                    )}
                    onClick={() => {
                      onChange(opt);
                      setOpen(false);
                      setQuery("");
                    }}
                  >
                    {opt}
                  </button>
                </li>
              ))
            )}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

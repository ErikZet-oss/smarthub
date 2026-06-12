"use client";

import { ClipboardList, Loader2, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  InquiryEditorTable,
  type InquiryRowsChangeHandler,
} from "@/components/inquiries/InquiryEditorTable";
import { InquiryResultsTable } from "@/components/inquiries/InquiryResultsTable";
import {
  InquirySupplierPicker,
  inquirySuppliersReady,
} from "@/components/inquiries/InquirySupplierPicker";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { readApiJsonOrText } from "@/lib/api-errors";
import { cn } from "@/lib/utils";
import {
  INQUIRY_DRAFT_STORAGE_KEY,
  MAX_INQUIRY_ROWS,
  MAX_INQUIRY_UPLOAD_MB,
  type InquiryDraft,
  type InquiryLineParsed,
  formatInquiryParseCompleteMessage,
  inquiryParseSummary,
  inquiryRowIsValid,
  normalizeInquiryRowFromApi,
  normalizeInquiryRunResult,
  type InquiryRunTaskResult,
} from "@/types/inquiry";

type Props = {
  apiBase: string;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  apiToken: string | null;
  authReady: boolean;
  userId: number | null;
};

function draftStorageKey(userId: number | null): string {
  return `${INQUIRY_DRAFT_STORAGE_KEY}::${userId ?? "anon"}`;
}

function loadDraft(userId: number | null): InquiryDraft | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(draftStorageKey(userId));
    if (!raw) return null;
    return JSON.parse(raw) as InquiryDraft;
  } catch {
    return null;
  }
}

function saveDraft(userId: number | null, draft: InquiryDraft) {
  if (typeof window === "undefined") return;
  localStorage.setItem(draftStorageKey(userId), JSON.stringify(draft));
}

function clearDraft(userId: number | null) {
  if (typeof window === "undefined") return;
  localStorage.removeItem(draftStorageKey(userId));
}

/** Výška spodného mobilného menu v page.tsx — panel „Spustiť dopyt“ musí sedieť nad ním. */
const MOBILE_TAB_BAR_OFFSET = "3.25rem";

export function InquiriesPanel({ apiBase, apiFetch, apiToken, authReady, userId }: Props) {
  const [status, setStatus] = useState<string | null>(null);
  const [parsing, setParsing] = useState(false);
  const [progressPct, setProgressPct] = useState<number | null>(null);
  const [rows, setRows] = useState<InquiryLineParsed[]>([]);
  const [sourceFileName, setSourceFileName] = useState("");
  const [draftPrompt, setDraftPrompt] = useState<InquiryDraft | null>(null);
  const [selectedSupplierIds, setSelectedSupplierIds] = useState<number[]>([]);
  const [running, setRunning] = useState(false);
  const [runProgressPct, setRunProgressPct] = useState<number | null>(null);
  const [runResult, setRunResult] = useState<InquiryRunTaskResult | null>(null);
  const [ignoreErrors, setIgnoreErrors] = useState(false);
  const [showOkOnly, setShowOkOnly] = useState(false);
  const [showErrorsOnly, setShowErrorsOnly] = useState(false);

  useEffect(() => {
    if (!authReady) return;
    const existing = loadDraft(userId);
    if (existing?.rows?.length) {
      setDraftPrompt(existing);
    }
    if (existing?.selectedSupplierIds?.length) {
      setSelectedSupplierIds(existing.selectedSupplierIds);
    }
  }, [authReady, userId]);

  const allValid = useMemo(
    () => rows.length > 0 && rows.every(inquiryRowIsValid),
    [rows],
  );

  const canRun = useMemo(
    () =>
      rows.length > 0 &&
      inquirySuppliersReady(selectedSupplierIds) &&
      (allValid || ignoreErrors),
    [rows.length, selectedSupplierIds, allValid, ignoreErrors],
  );

  const invalidRowCount = useMemo(
    () => rows.filter((r) => !inquiryRowIsValid(r)).length,
    [rows],
  );

  const parseSummary = useMemo(() => inquiryParseSummary(rows), [rows]);

  const filteredRows = useMemo(() => {
    if (showOkOnly) return rows.filter(inquiryRowIsValid);
    if (showErrorsOnly) return rows.filter((r) => !inquiryRowIsValid(r));
    return rows;
  }, [rows, showOkOnly, showErrorsOnly]);

  const enrichAttemptKeyRef = useRef("");

  const mergeEnrichedRows = useCallback(
    (current: InquiryLineParsed[], enriched: InquiryLineParsed[]): InquiryLineParsed[] => {
      const byIndex = new Map(enriched.map((r) => [r.row_index, r]));
      return current.map((row) => {
        const patch = byIndex.get(row.row_index);
        if (!patch) return row;
        return {
          ...row,
          ...patch,
          raw_text: row.raw_text,
          row_index: row.row_index,
        };
      });
    },
    [],
  );

  const enrichRowsWithSmartCodes = useCallback(
    async (nextRows: InquiryLineParsed[]): Promise<InquiryLineParsed[]> => {
      if (!nextRows.some((r) => !r.internal_code?.trim())) return nextRows;
      try {
        const res = await apiFetch(`${apiBase}/api/inquiries/rows/enrich-codes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rows: nextRows }),
        });
        if (!res.ok) return nextRows;
        const parsed = await readApiJsonOrText(res);
        if (!parsed.ok) return nextRows;
        const data = parsed.data as { rows?: Record<string, unknown>[] };
        if (!Array.isArray(data.rows)) return nextRows;
        return mergeEnrichedRows(
          nextRows,
          data.rows.map(normalizeInquiryRowFromApi),
        );
      } catch {
        return nextRows;
      }
    },
    [apiBase, apiFetch, mergeEnrichedRows],
  );

  const persistDraft = useCallback(
    (nextRows: InquiryLineParsed[], fileName?: string, supplierIds?: number[]) => {
      const ids = supplierIds ?? selectedSupplierIds;
      setRows(nextRows);
      if (!nextRows.length && !ids.length) return;
      saveDraft(userId, {
        savedAt: new Date().toISOString(),
        sourceFileName: fileName ?? sourceFileName,
        rows: nextRows,
        selectedSupplierIds: ids,
      });
    },
    [selectedSupplierIds, sourceFileName, userId],
  );

  const applyRowsChange: InquiryRowsChangeHandler = useCallback(
    (updater) => {
      setRows((prev) => {
        const next = typeof updater === "function" ? updater(prev) : updater;
        if (next.length || selectedSupplierIds.length) {
          saveDraft(userId, {
            savedAt: new Date().toISOString(),
            sourceFileName,
            rows: next,
            selectedSupplierIds,
          });
        }
        return next;
      });
    },
    [selectedSupplierIds, sourceFileName, userId],
  );

  useEffect(() => {
    if (!authReady || !apiToken || rows.length === 0) return;
    const missing = rows.filter((r) => !r.internal_code?.trim());
    if (missing.length === 0) return;
    const attemptKey = missing
      .map((r) => `${r.row_index}:${r.norma}:${r.diameter}:${r.length}:${r.surface}`)
      .join("|");
    if (enrichAttemptKeyRef.current === attemptKey) return;
    let cancelled = false;
    void enrichRowsWithSmartCodes(rows).then((enriched) => {
      if (cancelled) return;
      enrichAttemptKeyRef.current = attemptKey;
      const changed = enriched.some(
        (r, i) => r.internal_code !== rows[i]?.internal_code || r.norma !== rows[i]?.norma,
      );
      if (changed) persistDraft(enriched);
    });
    return () => {
      cancelled = true;
    };
  }, [authReady, apiToken, rows, enrichRowsWithSmartCodes, persistDraft]);

  const handleEditorChange: InquiryRowsChangeHandler = useCallback(
    (updater) => {
      applyRowsChange(updater);
    },
    [applyRowsChange],
  );

  const handleSupplierChange = useCallback(
    (ids: number[]) => {
      setSelectedSupplierIds(ids);
      if (rows.length > 0 || ids.length > 0) {
        saveDraft(userId, {
          savedAt: new Date().toISOString(),
          sourceFileName,
          rows,
          selectedSupplierIds: ids,
        });
      }
    },
    [rows, sourceFileName, userId],
  );

  const restoreDraft = async () => {
    if (!draftPrompt) return;
    enrichAttemptKeyRef.current = "";
    const enriched = await enrichRowsWithSmartCodes(draftPrompt.rows);
    setRows(enriched);
    setSourceFileName(draftPrompt.sourceFileName);
    if (draftPrompt.selectedSupplierIds?.length) {
      setSelectedSupplierIds(draftPrompt.selectedSupplierIds);
    }
    saveDraft(userId, {
      ...draftPrompt,
      rows: enriched,
      savedAt: new Date().toISOString(),
    });
    setDraftPrompt(null);
    setStatus(`Obnovený rozpracovaný dopyt (${enriched.length} riadkov).`);
  };

  const discardDraft = () => {
    clearDraft(userId);
    setDraftPrompt(null);
  };

  const onFileSelected = async (file: File | null) => {
    if (!file || !apiToken) return;
    enrichAttemptKeyRef.current = "";
    setParsing(true);
    setProgressPct(0);
    setStatus(`Nahrávam ${file.name}…`);
    try {
      const form = new FormData();
      form.append("file", file);
      const startRes = await apiFetch(`${apiBase}/api/inquiries/parse/upload`, {
        method: "POST",
        body: form,
      });
      const startParsed = await readApiJsonOrText(startRes);
      if (!startParsed.ok) throw new Error(startParsed.detail);
      const startData = startParsed.data as { task_id?: string };
      if (!startRes.ok || !startData.task_id) {
        throw new Error("Nepodarilo sa spustiť parsovanie.");
      }

      const taskId = startData.task_id;
      setSourceFileName(file.name);

      while (true) {
        await new Promise((r) => setTimeout(r, 1200));
        const stRes = await apiFetch(`${apiBase}/api/inquiries/parse/${taskId}`);
        const stParsed = await readApiJsonOrText(stRes);
        if (!stParsed.ok) throw new Error(stParsed.detail);
        const st = stParsed.data as {
          state?: string;
          phase?: string;
          progress_pct?: number;
          error?: string;
          result?: { rows?: Record<string, unknown>[]; source_filename?: string };
        };
        setProgressPct(typeof st.progress_pct === "number" ? st.progress_pct : null);
        if (st.state === "done" && st.result?.rows) {
          const parsed = st.result.rows.map(normalizeInquiryRowFromApi);
          const enriched = await enrichRowsWithSmartCodes(parsed);
          persistDraft(enriched, st.result.source_filename ?? file.name);
          setShowOkOnly(false);
          setShowErrorsOnly(false);
          setStatus(formatInquiryParseCompleteMessage(enriched));
          break;
        }
        if (st.state === "error") {
          throw new Error(st.error || "Parsovanie zlyhalo.");
        }
        if (st.phase === "catalog_snap") {
          setStatus(`Zosúladzujem s katalógom… ${st.progress_pct ?? 0} %`);
        } else {
          setStatus(`AI parsuje riadky… ${st.progress_pct ?? 0} %`);
        }
      }
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "Chyba pri parsovaní.");
      setRows([]);
    } finally {
      setParsing(false);
      setProgressPct(null);
    }
  };

  const onRunInquiry = async () => {
    if (!apiToken || !canRun) return;
    setRunning(true);
    setRunProgressPct(0);
    setRunResult(null);
    setStatus("Spúšťam vyhľadávanie u dodávateľov…");
    try {
      const startRes = await apiFetch(`${apiBase}/api/inquiries/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rows,
          supplier_ids: selectedSupplierIds,
          source_filename: sourceFileName,
          ignore_errors: ignoreErrors,
        }),
      });
      const startParsed = await readApiJsonOrText(startRes);
      if (!startParsed.ok) throw new Error(startParsed.detail);
      const startData = startParsed.data as { task_id?: string };
      if (!startRes.ok || !startData.task_id) {
        throw new Error("Nepodarilo sa spustiť dopyt.");
      }

      const taskId = startData.task_id;
      while (true) {
        await new Promise((r) => setTimeout(r, 1500));
        const stRes = await apiFetch(`${apiBase}/api/inquiries/run/${taskId}`);
        const stParsed = await readApiJsonOrText(stRes);
        if (!stParsed.ok) throw new Error(stParsed.detail);
        const st = stParsed.data as {
          state?: string;
          phase?: string;
          progress_pct?: number;
          error?: string;
          result?: Record<string, unknown>;
        };
        setRunProgressPct(typeof st.progress_pct === "number" ? st.progress_pct : null);
        if (st.state === "done" && st.result) {
          const result = normalizeInquiryRunResult(st.result);
          setRunResult(result);
          setStatus(
            `Dopyt hotový — ${result.rows_with_offer}/${result.total_rows} s cenou` +
              (result.rows_failed > 0 ? `, ${result.rows_failed} preskočených/chybných` : "") +
              ".",
          );
          break;
        }
        if (st.state === "error") {
          throw new Error(st.error || "Dopyt zlyhal.");
        }
        setStatus(
          st.phase === "catalog_snap"
            ? `Pripravujem katalóg… ${st.progress_pct ?? 0} %`
            : `Hľadám ceny… ${st.progress_pct ?? 0} %`,
        );
      }
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "Chyba pri spúšťaní dopytu.");
    } finally {
      setRunning(false);
      setRunProgressPct(null);
    }
  };

  if (!authReady) {
    return <p className="text-sm text-slate-500">Načítavam prihlásenie…</p>;
  }
  if (!apiToken) {
    return <p className="text-sm text-slate-500">Pre dopyty sa prihlás.</p>;
  }

  return (
    <div
      className={cn(
        "space-y-2 md:space-y-4",
        rows.length > 0 && "pb-[calc(7rem+env(safe-area-inset-bottom))] md:pb-0",
      )}
    >
      <div className="flex items-center gap-1.5 md:gap-2">
        <ClipboardList className="h-4 w-4 shrink-0 text-sky-600 md:h-5 md:w-5" />
        <div className="min-w-0">
          <h1 className="text-base font-semibold text-slate-900 md:text-lg">Import dopytov</h1>
          <p className="hidden text-xs text-slate-500 sm:block md:hidden">
            Nahraj Excel, skontroluj riadky a spusti vyhľadávanie.
          </p>
        </div>
      </div>

      <InquirySupplierPicker
        apiBase={apiBase}
        apiFetch={apiFetch}
        userId={userId}
        selectedIds={selectedSupplierIds}
        onChange={handleSupplierChange}
      />

      {draftPrompt ? (
        <Card className="border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-900 md:p-4 md:text-sm">
          <p>
            Rozpracovaný dopyt ({draftPrompt.rows.length} riadkov,{" "}
            {new Date(draftPrompt.savedAt).toLocaleString("sk-SK", {
              day: "2-digit",
              month: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })}
            ).
          </p>
          <div className="mt-2 flex gap-2">
            <Button
              type="button"
              size="sm"
              className="h-7 flex-1 px-2 text-xs md:h-8 md:flex-none md:text-sm"
              onClick={restoreDraft}
            >
              Obnoviť
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 flex-1 bg-white px-2 text-xs md:h-8 md:flex-none md:text-sm"
              onClick={discardDraft}
            >
              Zahodiť
            </Button>
          </div>
        </Card>
      ) : null}

      <Card className="space-y-2 p-2.5 md:space-y-3 md:p-4">
        <p className="hidden text-sm leading-relaxed text-slate-600 md:block">
          Nahraj XLSX alebo CSV s textom položiek (jeden popis na riadok, max.{" "}
          {MAX_INQUIRY_ROWS.toLocaleString("sk-SK")} riadkov, {MAX_INQUIRY_UPLOAD_MB} MB). AI
          rozloží parametre; chýbajúce polia označíme červeno — doplň ich ručne.
        </p>
        <p className="text-[11px] leading-snug text-slate-500 md:hidden">
          XLSX/CSV — jeden popis na riadok (max. {MAX_INQUIRY_ROWS.toLocaleString("sk-SK")}{" "}
          riadkov). Chýbajúce polia doplníš ručne.
        </p>
        <label className="flex w-full cursor-pointer md:inline-flex md:w-auto">
          <input
            type="file"
            accept=".xlsx,.xlsm,.csv"
            className="hidden"
            disabled={parsing}
            onChange={(e) => void onFileSelected(e.target.files?.[0] ?? null)}
          />
          <span
            className={cn(
              "inline-flex h-8 w-full items-center justify-center rounded-md border border-slate-200 bg-white px-3 text-xs font-medium md:h-9 md:w-auto md:rounded-lg md:px-4 md:text-sm",
              parsing ? "cursor-not-allowed opacity-60" : "hover:bg-slate-50 active:bg-slate-100",
            )}
          >
            {parsing ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin md:mr-2 md:h-4 md:w-4" />
            ) : (
              <Upload className="mr-1.5 h-3.5 w-3.5 md:mr-2 md:h-4 md:w-4" />
            )}
            {parsing ? "Parsujem…" : "Nahrať a parsovať"}
          </span>
        </label>
        {progressPct != null && parsing ? (
          <div className="h-1 w-full overflow-hidden rounded bg-slate-100 md:h-2">
            <div
              className="h-full bg-sky-500 transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        ) : null}
        {status ? (
          <p className="text-[10px] leading-snug text-slate-500 md:text-xs">{status}</p>
        ) : null}
      </Card>

      {rows.length > 0 ? (
        <>
          <div className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50/80 px-2.5 py-2 md:flex-row md:flex-wrap md:items-center md:gap-4 md:px-3">
            <p className="text-xs text-slate-700 md:text-sm">
              <span className="font-medium text-emerald-700">{parseSummary.ok} OK</span>
              {" · "}
              <span className="font-medium text-amber-800">{parseSummary.error} s chybou</span>
              {" · "}
              <span className="text-slate-600">{parseSummary.total} celkom</span>
              {showOkOnly || showErrorsOnly ? (
                <span className="text-slate-500">
                  {" "}
                  (zobrazených {filteredRows.length})
                </span>
              ) : null}
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-slate-700 md:text-sm">
                <input
                  type="checkbox"
                  checked={showOkOnly}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setShowOkOnly(checked);
                    if (checked) setShowErrorsOnly(false);
                  }}
                  className="h-3.5 w-3.5 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500 md:h-4 md:w-4"
                />
                Len OK
              </label>
              <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-slate-700 md:text-sm">
                <input
                  type="checkbox"
                  checked={showErrorsOnly}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setShowErrorsOnly(checked);
                    if (checked) setShowOkOnly(false);
                  }}
                  className="h-3.5 w-3.5 rounded border-slate-300 text-amber-600 focus:ring-amber-500 md:h-4 md:w-4"
                />
                Len chybné
              </label>
            </div>
          </div>

          <InquiryEditorTable
            rows={filteredRows}
            apiBase={apiBase}
            apiFetch={apiFetch}
            onChange={handleEditorChange}
          />

          {/* Desktop: akcie pod tabuľkou */}
          <div className="hidden flex-wrap items-center gap-3 md:flex">
            <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={ignoreErrors}
                onChange={(e) => setIgnoreErrors(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-sky-600 focus:ring-sky-500"
              />
              Ignorovať chyby
            </label>
            <Button
              type="button"
              disabled={!canRun || running || parsing}
              onClick={() => void onRunInquiry()}
              title={
                canRun
                  ? ignoreErrors && invalidRowCount > 0
                    ? `Spustí aj ${invalidRowCount} riadkov s chybami`
                    : ""
                  : !inquirySuppliersReady(selectedSupplierIds)
                    ? "Vyber aspoň jedného dodávateľa"
                    : "Doplň riadky alebo zapni Ignorovať chyby"
              }
            >
              {running ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Hľadám ceny…
                </>
              ) : (
                "Spustiť dopyt"
              )}
            </Button>
            {!canRun ? (
              <span className="text-xs text-slate-500">
                {!inquirySuppliersReady(selectedSupplierIds)
                  ? "Vyber aspoň jedného dodávateľa hore."
                  : "Doplň riadky alebo zapni „Ignorovať chyby“."}
              </span>
            ) : allValid ? (
              <span className="text-xs text-emerald-700">
                Pripravené — {selectedSupplierIds.length} dodávateľov, {rows.length} riadkov.
              </span>
            ) : (
              <span className="text-xs text-amber-800">
                Spustí sa aj {invalidRowCount} riadkov s chybami — vo výsledku uvidíš dôvod.
              </span>
            )}
            {running && runProgressPct != null ? (
              <div className="h-2 w-full min-w-[12rem] flex-1 overflow-hidden rounded bg-slate-100">
                <div
                  className="h-full bg-emerald-500 transition-all"
                  style={{ width: `${runProgressPct}%` }}
                />
              </div>
            ) : null}
          </div>

          {/* Mobil: kompaktný panel nad spodným menu */}
          <div
            className="fixed inset-x-0 z-30 border-t border-slate-200 bg-white/95 px-2 py-1.5 shadow-[0_-2px_12px_rgba(15,23,42,0.06)] backdrop-blur-md md:hidden"
            style={{ bottom: `calc(${MOBILE_TAB_BAR_OFFSET} + env(safe-area-inset-bottom, 0px))` }}
          >
            <div className="mx-auto max-w-lg space-y-1">
              {running && runProgressPct != null ? (
                <div className="h-1 w-full overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full bg-emerald-500 transition-all"
                    style={{ width: `${runProgressPct}%` }}
                  />
                </div>
              ) : null}
              <div className="flex items-center gap-1.5">
                <label className="inline-flex shrink-0 cursor-pointer items-center gap-1 rounded border border-slate-200 bg-slate-50 px-1.5 py-1 text-[10px] text-slate-600">
                  <input
                    type="checkbox"
                    checked={ignoreErrors}
                    onChange={(e) => setIgnoreErrors(e.target.checked)}
                    className="h-3 w-3 rounded border-slate-300 text-sky-600 focus:ring-sky-500"
                  />
                  Ignor. chyby
                </label>
                <Button
                  type="button"
                  className="h-8 flex-1 px-2 text-xs"
                  disabled={!canRun || running || parsing}
                  onClick={() => void onRunInquiry()}
                  title={
                    !canRun
                      ? !inquirySuppliersReady(selectedSupplierIds)
                        ? "Vyber dodávateľa"
                        : "Doplň riadky"
                      : `${selectedSupplierIds.length} dodáv. · ${rows.length} riadkov`
                  }
                >
                  {running ? (
                    <>
                      <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                      Hľadám…
                    </>
                  ) : (
                    "Spustiť dopyt"
                  )}
                </Button>
              </div>
            </div>
          </div>

          {runResult ? <InquiryResultsTable apiBase={apiBase} result={runResult} /> : null}
        </>
      ) : null}
    </div>
  );
}

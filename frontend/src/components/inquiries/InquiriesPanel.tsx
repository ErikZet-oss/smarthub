"use client";

import { ClipboardList, Loader2, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { InquiryEditorTable } from "@/components/inquiries/InquiryEditorTable";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { readApiJsonOrText } from "@/lib/api-errors";
import { cn } from "@/lib/utils";
import {
  INQUIRY_DRAFT_STORAGE_KEY,
  type InquiryDraft,
  type InquiryLineParsed,
  inquiryRowIsValid,
  normalizeInquiryRowFromApi,
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

export function InquiriesPanel({ apiBase, apiFetch, apiToken, authReady, userId }: Props) {
  const [status, setStatus] = useState<string | null>(null);
  const [parsing, setParsing] = useState(false);
  const [progressPct, setProgressPct] = useState<number | null>(null);
  const [rows, setRows] = useState<InquiryLineParsed[]>([]);
  const [sourceFileName, setSourceFileName] = useState("");
  const [draftPrompt, setDraftPrompt] = useState<InquiryDraft | null>(null);

  useEffect(() => {
    if (!authReady) return;
    const existing = loadDraft(userId);
    if (existing?.rows?.length) {
      setDraftPrompt(existing);
    }
  }, [authReady, userId]);

  const allValid = useMemo(
    () => rows.length > 0 && rows.every(inquiryRowIsValid),
    [rows],
  );

  const persistRows = useCallback(
    (nextRows: InquiryLineParsed[], fileName?: string) => {
      setRows(nextRows);
      if (!nextRows.length) return;
      saveDraft(userId, {
        savedAt: new Date().toISOString(),
        sourceFileName: fileName ?? sourceFileName,
        rows: nextRows,
      });
    },
    [sourceFileName, userId],
  );

  const restoreDraft = () => {
    if (!draftPrompt) return;
    setRows(draftPrompt.rows);
    setSourceFileName(draftPrompt.sourceFileName);
    setDraftPrompt(null);
    setStatus(`Obnovený rozpracovaný dopyt (${draftPrompt.rows.length} riadkov).`);
  };

  const discardDraft = () => {
    clearDraft(userId);
    setDraftPrompt(null);
  };

  const onFileSelected = async (file: File | null) => {
    if (!file || !apiToken) return;
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
          progress_pct?: number;
          error?: string;
          result?: { rows?: Record<string, unknown>[]; source_filename?: string };
        };
        setProgressPct(typeof st.progress_pct === "number" ? st.progress_pct : null);
        if (st.state === "done" && st.result?.rows) {
          const parsed = st.result.rows.map(normalizeInquiryRowFromApi);
          persistRows(parsed, st.result.source_filename ?? file.name);
          setStatus(`Parsovanie hotové — ${parsed.length} riadkov. Skontroluj červené bunky.`);
          break;
        }
        if (st.state === "error") {
          throw new Error(st.error || "Parsovanie zlyhalo.");
        }
        setStatus(`AI parsuje riadky… ${st.progress_pct ?? 0} %`);
      }
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "Chyba pri parsovaní.");
      setRows([]);
    } finally {
      setParsing(false);
      setProgressPct(null);
    }
  };

  if (!authReady) {
    return <p className="text-sm text-slate-500">Načítavam prihlásenie…</p>;
  }
  if (!apiToken) {
    return <p className="text-sm text-slate-500">Pre dopyty sa prihlás.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <ClipboardList className="h-5 w-5 text-sky-600" />
        <h1 className="text-lg font-semibold text-slate-900">Import dopytov</h1>
      </div>

      {draftPrompt ? (
        <Card className="border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p>
            Máte rozpracovaný dopyt ({draftPrompt.rows.length} riadkov,{" "}
            {new Date(draftPrompt.savedAt).toLocaleString("sk-SK")}).
          </p>
          <div className="mt-2 flex gap-2">
            <Button type="button" size="sm" onClick={restoreDraft}>
              Obnoviť
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={discardDraft}>
              Zahodiť
            </Button>
          </div>
        </Card>
      ) : null}

      <Card className="space-y-3 p-4">
        <p className="text-sm text-slate-600">
          Nahraj XLSX alebo CSV s textom položiek (jeden popis na riadok). AI rozloží parametre;
          chýbajúce polia označíme červeno — doplň ich ručne.
        </p>
        <label className="inline-flex cursor-pointer items-center gap-2">
          <input
            type="file"
            accept=".xlsx,.xlsm,.csv"
            className="hidden"
            disabled={parsing}
            onChange={(e) => void onFileSelected(e.target.files?.[0] ?? null)}
          />
          <span
            className={cn(
              "inline-flex h-9 items-center justify-center rounded-md border border-slate-200 bg-white px-4 text-sm font-medium",
              parsing ? "cursor-not-allowed opacity-60" : "hover:bg-slate-50",
            )}
          >
            {parsing ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Upload className="mr-2 h-4 w-4" />
            )}
            {parsing ? "Parsujem…" : "Nahrať a parsovať"}
          </span>
        </label>
        {progressPct != null && parsing ? (
          <div className="h-2 w-full overflow-hidden rounded bg-slate-100">
            <div
              className="h-full bg-sky-500 transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        ) : null}
        {status ? <p className="text-xs text-slate-500">{status}</p> : null}
      </Card>

      {rows.length > 0 ? (
        <>
          <InquiryEditorTable
            rows={rows}
            apiBase={apiBase}
            apiFetch={apiFetch}
            onChange={persistRows}
          />
          <div className="flex items-center gap-3">
            <Button type="button" disabled={!allValid} title={allValid ? "" : "Doplň všetky červené bunky"}>
              Spustiť dopyt
            </Button>
            {!allValid ? (
              <span className="text-xs text-slate-500">
                Tlačidlo bude aktívne, keď budú všetky riadky bez chýbajúcich polí.
              </span>
            ) : (
              <span className="text-xs text-emerald-700">
                Všetky riadky sú pripravené (pipeline dodávateľov bude v ďalšom kroku).
              </span>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}

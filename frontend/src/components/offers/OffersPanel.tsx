"use client";

import {
  ChevronLeft,
  Download,
  FileSpreadsheet,
  FileText,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import type { OfferDetail, OfferListItem } from "@/components/offers/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Props = {
  apiBase: string;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  apiToken: string | null;
  authReady: boolean;
  companyConfigured: boolean | null;
};

const STATUS_LABELS: Record<string, string> = {
  draft: "Koncept",
  sent: "Odoslaná",
  accepted: "Akceptovaná",
  rejected: "Zamietnutá",
};

function fmtEur(n: number) {
  return new Intl.NumberFormat("sk-SK", {
    style: "currency",
    currency: "EUR",
  }).format(n);
}

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("sk-SK");
  } catch {
    return iso;
  }
}

async function downloadBlob(res: Response, fallbackName: string) {
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/i.exec(cd);
  const name = match?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

const EMPTY_DETAIL: OfferDetail = {
  id: 0,
  offer_number: "—",
  title: "",
  status: "draft",
  valid_until: null,
  client_name: "",
  client_street: null,
  client_city: null,
  client_zip: null,
  client_country: "Slovensko",
  client_ico: null,
  client_dic: null,
  client_ic_dph: null,
  client_contact: null,
  client_email: null,
  client_phone: null,
  notes_client: null,
  notes_internal: null,
  default_margin_percent: 0,
  created_at: null,
  updated_at: null,
  lines: [],
  subtotal_eur: 0,
  vat_eur: 0,
  total_eur: 0,
};

export function OffersPanel({
  apiBase,
  apiFetch,
  apiToken,
  authReady,
  companyConfigured,
}: Props) {
  const [offers, setOffers] = useState<OfferListItem[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [selectedId, setSelectedId] = useState<number | "new" | null>(null);
  const [detail, setDetail] = useState<OfferDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<"pdf" | "csv" | null>(null);
  const [newLine, setNewLine] = useState({
    description: "",
    quantity: "1",
    unit: "ks",
    unit_price_eur: "0",
    discount_percent: "0",
  });

  const loadList = useCallback(async () => {
    if (!apiToken) return;
    setLoadingList(true);
    setError(null);
    try {
      const res = await apiFetch(`${apiBase}/api/offers`);
      if (!res.ok) throw new Error("Nepodarilo sa načítať ponuky.");
      setOffers((await res.json()) as OfferListItem[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba.");
    } finally {
      setLoadingList(false);
    }
  }, [apiBase, apiFetch, apiToken]);

  const loadDetail = useCallback(
    async (id: number) => {
      setLoadingDetail(true);
      setError(null);
      try {
        const res = await apiFetch(`${apiBase}/api/offers/${id}`);
        if (!res.ok) throw new Error("Ponuka sa nenašla.");
        setDetail((await res.json()) as OfferDetail);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Chyba.");
        setDetail(null);
      } finally {
        setLoadingDetail(false);
      }
    },
    [apiBase, apiFetch],
  );

  useEffect(() => {
    if (!authReady || !apiToken) return;
    void loadList();
  }, [authReady, apiToken, loadList]);

  useEffect(() => {
    if (selectedId === null || selectedId === "new") return;
    void loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  const startNew = () => {
    setSelectedId("new");
    setDetail({ ...EMPTY_DETAIL });
  };

  const createOffer = async () => {
    if (!detail) return;
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(`${apiBase}/api/offers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: detail.title,
          client_name: detail.client_name,
          client_street: detail.client_street,
          client_city: detail.client_city,
          client_zip: detail.client_zip,
          client_country: detail.client_country,
          client_ico: detail.client_ico,
          client_dic: detail.client_dic,
          client_ic_dph: detail.client_ic_dph,
          client_contact: detail.client_contact,
          client_email: detail.client_email,
          client_phone: detail.client_phone,
          notes_client: detail.notes_client,
          notes_internal: detail.notes_internal,
          valid_until: detail.valid_until,
          default_margin_percent: detail.default_margin_percent,
        }),
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(d.detail ?? "Vytvorenie zlyhalo.");
      }
      const created = (await res.json()) as OfferDetail;
      await loadList();
      setSelectedId(created.id);
      setDetail(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba.");
    } finally {
      setSaving(false);
    }
  };

  const saveOffer = async () => {
    if (!detail?.id) return;
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(`${apiBase}/api/offers/${detail.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: detail.title,
          status: detail.status,
          client_name: detail.client_name,
          client_street: detail.client_street,
          client_city: detail.client_city,
          client_zip: detail.client_zip,
          client_country: detail.client_country,
          client_ico: detail.client_ico,
          client_dic: detail.client_dic,
          client_ic_dph: detail.client_ic_dph,
          client_contact: detail.client_contact,
          client_email: detail.client_email,
          client_phone: detail.client_phone,
          notes_client: detail.notes_client,
          notes_internal: detail.notes_internal,
          valid_until: detail.valid_until,
          default_margin_percent: detail.default_margin_percent,
        }),
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(d.detail ?? "Uloženie zlyhalo.");
      }
      const updated = (await res.json()) as OfferDetail;
      setDetail(updated);
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba.");
    } finally {
      setSaving(false);
    }
  };

  const applyBulkMargin = async () => {
    if (!detail?.id) return;
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(`${apiBase}/api/offers/${detail.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          default_margin_percent: detail.default_margin_percent,
          apply_margin_to_all_lines: true,
        }),
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(d.detail ?? "Aplikovanie marže zlyhalo.");
      }
      setDetail((await res.json()) as OfferDetail);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba.");
    } finally {
      setSaving(false);
    }
  };

  const updateLineMargin = async (lineId: number, marginPercent: number) => {
    if (!detail?.id) return;
    try {
      const res = await apiFetch(
        `${apiBase}/api/offers/${detail.id}/lines/${lineId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ margin_percent: marginPercent }),
        },
      );
      if (!res.ok) throw new Error("Úprava marže zlyhala.");
      await loadDetail(detail.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba.");
    }
  };

  const deleteOffer = async () => {
    if (!detail?.id) return;
    if (!window.confirm(`Zmazať ponuku ${detail.offer_number}?`)) return;
    setSaving(true);
    try {
      const res = await apiFetch(`${apiBase}/api/offers/${detail.id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error("Zmazanie zlyhalo.");
      setSelectedId(null);
      setDetail(null);
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba.");
    } finally {
      setSaving(false);
    }
  };

  const addLine = async () => {
    if (!detail?.id) return;
    const desc = newLine.description.trim();
    if (!desc) {
      setError("Vyplň popis položky.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(`${apiBase}/api/offers/${detail.id}/lines`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description: desc,
          quantity: parseFloat(newLine.quantity) || 1,
          unit: newLine.unit || "ks",
          unit_price_eur: parseFloat(newLine.unit_price_eur) || 0,
          discount_percent: parseFloat(newLine.discount_percent) || 0,
        }),
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(d.detail ?? "Pridanie položky zlyhalo.");
      }
      setNewLine({
        description: "",
        quantity: "1",
        unit: "ks",
        unit_price_eur: "0",
        discount_percent: "0",
      });
      await loadDetail(detail.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba.");
    } finally {
      setSaving(false);
    }
  };

  const deleteLine = async (lineId: number) => {
    if (!detail?.id) return;
    setSaving(true);
    try {
      const res = await apiFetch(
        `${apiBase}/api/offers/${detail.id}/lines/${lineId}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error("Zmazanie položky zlyhalo.");
      await loadDetail(detail.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba.");
    } finally {
      setSaving(false);
    }
  };

  const exportFile = async (kind: "pdf" | "csv") => {
    if (!detail?.id) return;
    setExporting(kind);
    setError(null);
    try {
      const res = await apiFetch(
        `${apiBase}/api/offers/${detail.id}/export/${kind}`,
      );
      if (!res.ok) throw new Error(`Export ${kind.toUpperCase()} zlyhal.`);
      await downloadBlob(res, `ponuka.${kind}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba exportu.");
    } finally {
      setExporting(null);
    }
  };

  const patchDetail = (patch: Partial<OfferDetail>) => {
    setDetail((d) => (d ? { ...d, ...patch } : d));
  };

  const showEditor = selectedId !== null;

  return (
    <section className="space-y-4">
      <Card className="overflow-hidden border-sky-200/80 p-0 shadow-sm ring-1 ring-sky-100/60">
        <OffersPanelHeader companyConfigured={companyConfigured} />

        <OffersLayout
          loadingList={loadingList}
          offers={offers}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onNew={startNew}
          error={error}
          showEditor={showEditor}
          loadingDetail={loadingDetail}
          detail={detail}
          isNew={selectedId === "new"}
          saving={saving}
          exporting={exporting}
          newLine={newLine}
          onNewLineChange={setNewLine}
          onPatch={patchDetail}
          onBack={() => {
            setSelectedId(null);
            setDetail(null);
          }}
          onCreate={() => void createOffer()}
          onSave={() => void saveOffer()}
          onDelete={() => void deleteOffer()}
          onAddLine={() => void addLine()}
          onDeleteLine={(id) => void deleteLine(id)}
          onExportPdf={() => void exportFile("pdf")}
          onExportCsv={() => void exportFile("csv")}
          onApplyBulkMargin={() => void applyBulkMargin()}
          onUpdateLineMargin={(lineId, margin) => void updateLineMargin(lineId, margin)}
        />
      </Card>
    </section>
  );
}

function OffersPanelHeader({
  companyConfigured,
}: {
  companyConfigured: boolean | null;
}) {
  return (
    <OffersPanelHeaderInner companyConfigured={companyConfigured} />
  );
}

function OffersPanelHeaderInner({
  companyConfigured,
}: {
  companyConfigured: boolean | null;
}) {
  return (
    <div className="border-b border-sky-200/60 bg-gradient-to-r from-sky-50 via-white to-indigo-50/40 px-5 py-4">
      <h2 className="flex items-center gap-2 text-base font-semibold text-slate-900">
        <FileText className="h-5 w-5 text-sky-600" aria-hidden />
        Ponuky
      </h2>
      <p className="mt-1 text-sm text-slate-600">
        Vytvárajte cenové ponuky pre firmy a exportujte ich do PDF alebo CSV.
      </p>
      {companyConfigured === false ? (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
          Admin ešte nedoplnil firemné údaje — v sekcii Admin doplňte údaje a logo pre
          správnu hlavičku PDF.
        </p>
      ) : null}
    </div>
  );
}

function OffersLayout(props: {
  loadingList: boolean;
  offers: OfferListItem[];
  selectedId: number | "new" | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  error: string | null;
  showEditor: boolean;
  loadingDetail: boolean;
  detail: OfferDetail | null;
  isNew: boolean;
  saving: boolean;
  exporting: "pdf" | "csv" | null;
  newLine: {
    description: string;
    quantity: string;
    unit: string;
    unit_price_eur: string;
    discount_percent: string;
  };
  onNewLineChange: (v: typeof props.newLine) => void;
  onPatch: (p: Partial<OfferDetail>) => void;
  onBack: () => void;
  onCreate: () => void;
  onSave: () => void;
  onDelete: () => void;
  onAddLine: () => void;
  onDeleteLine: (id: number) => void;
  onExportPdf: () => void;
  onExportCsv: () => void;
  onApplyBulkMargin: () => void;
  onUpdateLineMargin: (lineId: number, marginPercent: number) => void;
}) {
  const {
    loadingList,
    offers,
    selectedId,
    onSelect,
    onNew,
    error,
    showEditor,
    loadingDetail,
    detail,
    isNew,
    saving,
    exporting,
    newLine,
    onNewLineChange,
    onPatch,
    onBack,
    onCreate,
    onSave,
    onDelete,
    onAddLine,
    onDeleteLine,
    onExportPdf,
    onExportCsv,
    onApplyBulkMargin,
    onUpdateLineMargin,
  } = props;

  return (
    <div className="grid gap-0 lg:grid-cols-[minmax(240px,280px)_1fr]">
      <aside className="border-b border-slate-200/80 bg-slate-50/50 p-4 lg:border-b-0 lg:border-r">
        <div className="mb-3 flex items-center justify-between gap-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Zoznam ponúk
          </p>
          <Button type="button" size="sm" onClick={onNew}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            Nová
          </Button>
        </div>
        {loadingList ? (
          <p className="flex items-center gap-2 text-sm text-slate-600">
            <Loader2 className="h-4 w-4 animate-spin" /> Načítavam…
          </p>
        ) : offers.length === 0 ? (
          <p className="text-sm text-slate-600">Zatiaľ žiadne ponuky.</p>
        ) : (
          <ul className="max-h-[min(60vh,520px)] space-y-1 overflow-y-auto pr-1">
            {offers.map((o) => (
              <li key={o.id}>
                <button
                  type="button"
                  onClick={() => onSelect(o.id)}
                  className={cn(
                    "w-full rounded-lg border px-3 py-2.5 text-left text-sm transition-colors",
                    selectedId === o.id
                      ? "border-sky-300 bg-white shadow-sm ring-1 ring-sky-200"
                      : "border-transparent bg-white/60 hover:border-slate-200 hover:bg-white",
                  )}
                >
                  <span className="font-medium text-slate-900">{o.offer_number}</span>
                  <span className="mt-0.5 block truncate text-xs text-slate-600">
                    {o.client_name}
                  </span>
                  <Badge className="mt-1.5 border-slate-200 bg-slate-50 text-[10px] text-slate-700">
                    {STATUS_LABELS[o.status] ?? o.status}
                  </Badge>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <div className="p-4 sm:p-5">
        {error ? (
          <p
            className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900"
            role="alert"
          >
            {error}
          </p>
        ) : null}

        {!showEditor ? (
          <p className="py-12 text-center text-sm text-slate-500">
            Vyberte ponuku zo zoznamu alebo vytvorte novú.
          </p>
        ) : loadingDetail && !isNew ? (
          <p className="flex items-center gap-2 py-12 text-sm text-slate-600">
            <Loader2 className="h-4 w-4 animate-spin" /> Načítavam ponuku…
          </p>
        ) : detail ? (
          <OfferEditor
            detail={detail}
            isNew={isNew}
            saving={saving}
            exporting={exporting}
            newLine={newLine}
            onNewLineChange={onNewLineChange}
            onPatch={onPatch}
            onBack={onBack}
            onCreate={onCreate}
            onSave={onSave}
            onDelete={onDelete}
            onAddLine={onAddLine}
            onDeleteLine={onDeleteLine}
            onExportPdf={onExportPdf}
            onExportCsv={onExportCsv}
            onApplyBulkMargin={onApplyBulkMargin}
            onUpdateLineMargin={onUpdateLineMargin}
          />
        ) : null}
      </div>
    </div>
  );
}

function OfferEditor(props: {
  detail: OfferDetail;
  isNew: boolean;
  saving: boolean;
  exporting: "pdf" | "csv" | null;
  newLine: {
    description: string;
    quantity: string;
    unit: string;
    unit_price_eur: string;
    discount_percent: string;
  };
  onNewLineChange: (v: typeof props.newLine) => void;
  onPatch: (p: Partial<OfferDetail>) => void;
  onBack: () => void;
  onCreate: () => void;
  onSave: () => void;
  onDelete: () => void;
  onAddLine: () => void;
  onDeleteLine: (id: number) => void;
  onExportPdf: () => void;
  onExportCsv: () => void;
  onApplyBulkMargin: () => void;
  onUpdateLineMargin: (lineId: number, marginPercent: number) => void;
}) {
  const {
    detail,
    isNew,
    saving,
    exporting,
    newLine,
    onNewLineChange,
    onPatch,
    onBack,
    onCreate,
    onSave,
    onDelete,
    onAddLine,
    onDeleteLine,
    onExportPdf,
    onExportCsv,
    onApplyBulkMargin,
    onUpdateLineMargin,
  } = props;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <EditorHeader
          isNew={isNew}
          detail={detail}
          onBack={onBack}
        />
        {!isNew ? (
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!!exporting}
              onClick={onExportPdf}
            >
              {exporting === "pdf" ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Download className="mr-1 h-3.5 w-3.5" />
              )}
              PDF
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!!exporting}
              onClick={onExportCsv}
            >
              {exporting === "csv" ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <FileSpreadsheet className="mr-1 h-3.5 w-3.5" />
              )}
              CSV
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="text-rose-700 hover:bg-rose-50"
              disabled={saving}
              onClick={onDelete}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        ) : null}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Predmet ponuky" className="sm:col-span-2">
          <Input
            value={detail.title ?? ""}
            onChange={(e) => onPatch({ title: e.target.value || null })}
            placeholder="napr. Spojovací materiál"
          />
        </Field>
        {!isNew ? (
          <Field label="Stav">
            <select
              className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm"
              value={detail.status}
              onChange={(e) => onPatch({ status: e.target.value })}
            >
              {Object.entries(STATUS_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field label="Platnosť do">
          <Input
            type="date"
            value={detail.valid_until?.slice(0, 10) ?? ""}
            onChange={(e) =>
              onPatch({
                valid_until: e.target.value
                  ? new Date(e.target.value).toISOString()
                  : null,
              })
            }
          />
        </Field>
      </div>

      <div className="rounded-xl border border-slate-200/90 bg-slate-50/40 p-4">
        <p className="mb-3 text-sm font-semibold text-slate-900">Odberateľ (klient)</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Názov firmy *" className="sm:col-span-2">
            <Input
              value={detail.client_name}
              onChange={(e) => onPatch({ client_name: e.target.value })}
            />
          </Field>
          <Field label="Ulica">
            <Input
              value={detail.client_street ?? ""}
              onChange={(e) => onPatch({ client_street: e.target.value || null })}
            />
          </Field>
          <Field label="PSČ">
            <Input
              value={detail.client_zip ?? ""}
              onChange={(e) => onPatch({ client_zip: e.target.value || null })}
            />
          </Field>
          <Field label="Mesto">
            <Input
              value={detail.client_city ?? ""}
              onChange={(e) => onPatch({ client_city: e.target.value || null })}
            />
          </Field>
          <Field label="Krajina">
            <Input
              value={detail.client_country ?? ""}
              onChange={(e) => onPatch({ client_country: e.target.value || null })}
            />
          </Field>
          <Field label="IČO">
            <Input
              value={detail.client_ico ?? ""}
              onChange={(e) => onPatch({ client_ico: e.target.value || null })}
            />
          </Field>
          <Field label="DIČ">
            <Input
              value={detail.client_dic ?? ""}
              onChange={(e) => onPatch({ client_dic: e.target.value || null })}
            />
          </Field>
          <Field label="IČ DPH">
            <Input
              value={detail.client_ic_dph ?? ""}
              onChange={(e) => onPatch({ client_ic_dph: e.target.value || null })}
            />
          </Field>
          <Field label="Kontaktná osoba">
            <Input
              value={detail.client_contact ?? ""}
              onChange={(e) => onPatch({ client_contact: e.target.value || null })}
            />
          </Field>
          <Field label="E-mail">
            <Input
              value={detail.client_email ?? ""}
              onChange={(e) => onPatch({ client_email: e.target.value || null })}
            />
          </Field>
          <Field label="Telefón">
            <Input
              value={detail.client_phone ?? ""}
              onChange={(e) => onPatch({ client_phone: e.target.value || null })}
            />
          </Field>
        </div>
      </div>

      {!isNew ? (
        <>
          <div className="rounded-xl border border-sky-200/80 bg-sky-50/40 p-4">
            <p className="text-sm font-semibold text-slate-900">Marža ponuky</p>
            <p className="mt-1 text-xs text-slate-600">
              Nastavte predvolenú maržu a aplikujte ju na všetky položky s nákupnou cenou.
            </p>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <Field label="Hromadná marža (%)">
                <Input
                  type="number"
                  step="0.1"
                  min={0}
                  className="w-28"
                  value={detail.default_margin_percent}
                  onChange={(e) =>
                    onPatch({
                      default_margin_percent: parseFloat(e.target.value) || 0,
                    })
                  }
                />
              </Field>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={saving}
                onClick={onApplyBulkMargin}
              >
                Aplikovať na všetky položky
              </Button>
            </div>
          </div>

          <div className="rounded-xl border border-slate-200/90 p-4">
            <p className="mb-3 text-sm font-semibold text-slate-900">Položky ponuky</p>
            <p className="mb-3 text-xs text-slate-500">
              Pridajte položky z vyhľadávania (ikona + pri dodávateľovi) alebo manuálne nižšie.
            </p>
            {detail.lines.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-xs text-slate-500">
                      <th className="py-2 pr-2">#</th>
                      <th className="py-2 pr-2">Popis</th>
                      <th className="py-2 pr-2">Dodávateľ</th>
                      <th className="py-2 pr-2 text-right">Množ.</th>
                      <th className="py-2 pr-2 text-right">Nákup</th>
                      <th className="py-2 pr-2 text-right">Marža %</th>
                      <th className="py-2 pr-2 text-right">Predaj</th>
                      <th className="py-2 pr-2 text-right">Spolu</th>
                      <th className="py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {detail.lines.map((ln) => (
                      <tr key={ln.id} className="border-b border-slate-100">
                        <td className="py-2 pr-2 text-slate-500">{ln.position}</td>
                        <td className="max-w-[200px] py-2 pr-2 text-xs">{ln.description}</td>
                        <td className="py-2 pr-2 text-xs text-slate-600">
                          {ln.supplier_name ?? "—"}
                        </td>
                        <td className="py-2 pr-2 text-right">{ln.quantity}</td>
                        <td className="py-2 pr-2 text-right text-slate-600">
                          {ln.purchase_unit_price_eur != null
                            ? fmtEur(ln.purchase_unit_price_eur)
                            : "—"}
                        </td>
                        <td className="py-2 pr-2 text-right">
                          {ln.purchase_unit_price_eur != null ? (
                            <Input
                              key={`m-${ln.id}-${ln.margin_percent}`}
                              type="number"
                              step="0.1"
                              min={0}
                              className="ml-auto h-7 w-16 text-right text-xs"
                              defaultValue={ln.margin_percent}
                              onBlur={(e) => {
                                const v = parseFloat(e.target.value);
                                if (Number.isFinite(v)) {
                                  onUpdateLineMargin(ln.id, v);
                                }
                              }}
                            />
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="py-2 pr-2 text-right font-medium text-sky-800">
                          {fmtEur(ln.unit_price_eur)}
                        </td>
                        <td className="py-2 pr-2 text-right font-medium">
                          {fmtEur(ln.line_total_eur)}
                        </td>
                        <td className="py-2">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-8 w-8 p-0 text-rose-600"
                            disabled={saving}
                            onClick={() => onDeleteLine(ln.id)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-slate-500">Žiadne položky.</p>
            )}

            <div className="mt-4 grid gap-2 rounded-lg border border-dashed border-sky-200 bg-sky-50/30 p-3 sm:grid-cols-12">
              <AddLineFields
                newLine={newLine}
                saving={saving}
                onNewLineChange={onNewLineChange}
                onAddLine={onAddLine}
              />
            </div>

            <div className="mt-4 flex flex-wrap justify-end gap-4 border-t border-slate-200 pt-4 text-sm">
              <span className="text-slate-600">
                Bez DPH: <strong>{fmtEur(detail.subtotal_eur)}</strong>
              </span>
              <span className="text-slate-600">
                DPH 21 %: <strong>{fmtEur(detail.vat_eur)}</strong>
              </span>
              <span className="text-slate-900">
                Celkom: <strong className="text-sky-700">{fmtEur(detail.total_eur)}</strong>
              </span>
            </div>
          </div>

          <Field label="Poznámka pre klienta (zobrazí sa v PDF)">
            <textarea
              className="min-h-[72px] w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm"
              value={detail.notes_client ?? ""}
              onChange={(e) => onPatch({ notes_client: e.target.value || null })}
            />
          </Field>
          <Field label="Interná poznámka">
            <textarea
              className="min-h-[56px] w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600"
              value={detail.notes_internal ?? ""}
              onChange={(e) => onPatch({ notes_internal: e.target.value || null })}
            />
          </Field>
        </>
      ) : null}

      <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 pt-4">
        {isNew ? (
          <Button type="button" disabled={saving} onClick={onCreate}>
            {saving ? "Vytváram…" : "Vytvoriť ponuku"}
          </Button>
        ) : (
          <Button type="button" disabled={saving} onClick={onSave}>
            {saving ? "Ukladám…" : "Uložiť zmeny"}
          </Button>
        )}
        {!isNew && detail.updated_at ? (
          <span className="text-xs text-slate-500">
            Upravené {fmtDate(detail.updated_at)}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function EditorHeader(props: {
  isNew: boolean;
  detail: OfferDetail;
  onBack: () => void;
}) {
  const { isNew, detail, onBack } = props;
  return (
    <div className="flex items-center gap-2">
      <Button type="button" variant="outline" size="sm" onClick={onBack}>
        <ChevronLeft className="h-4 w-4" />
      </Button>
      <EditorHeaderInner isNew={isNew} detail={detail} />
    </div>
  );
}

function EditorHeaderInner(props: { isNew: boolean; detail: OfferDetail }) {
  const { isNew, detail } = props;
  return (
    <div>
      <h3 className="text-lg font-semibold text-slate-900">
        {isNew ? "Nová ponuka" : detail.offer_number}
      </h3>
      {!isNew && detail.title ? (
        <p className="text-sm text-slate-600">{detail.title}</p>
      ) : null}
    </div>
  );
}

function AddLineFields(props: {
  newLine: {
    description: string;
    quantity: string;
    unit: string;
    unit_price_eur: string;
    discount_percent: string;
  };
  saving: boolean;
  onNewLineChange: (v: typeof props.newLine) => void;
  onAddLine: () => void;
}) {
  const { newLine, saving, onNewLineChange, onAddLine } = props;
  return (
    <>
      <div className="sm:col-span-5">
        <Input
          placeholder="Popis položky"
          value={newLine.description}
          onChange={(e) => onNewLineChange({ ...newLine, description: e.target.value })}
        />
      </div>
      <div className="sm:col-span-2">
        <Input
          placeholder="Množ."
          value={newLine.quantity}
          onChange={(e) => onNewLineChange({ ...newLine, quantity: e.target.value })}
        />
      </div>
      <div className="sm:col-span-1">
        <Input
          placeholder="MJ"
          value={newLine.unit}
          onChange={(e) => onNewLineChange({ ...newLine, unit: e.target.value })}
        />
      </div>
      <div className="sm:col-span-2">
        <Input
          placeholder="Cena €"
          value={newLine.unit_price_eur}
          onChange={(e) =>
            onNewLineChange({ ...newLine, unit_price_eur: e.target.value })
          }
        />
      </div>
      <div className="sm:col-span-2">
        <Button type="button" size="sm" className="w-full" disabled={saving} onClick={onAddLine}>
          Pridať
        </Button>
      </div>
    </>
  );
}

function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <FormField label={label} className={className}>
      {children}
    </FormField>
  );
}

function FormField({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <label className="text-xs font-medium text-slate-700">{label}</label>
      {children}
    </div>
  );
}

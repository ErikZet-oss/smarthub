"use client";

import { Building2, ImageIcon, Loader2, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { CompanySettings } from "@/components/offers/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Props = {
  apiBase: string;
  apiFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  apiToken: string | null;
  assetUrl: (path: string | null | undefined) => string | null;
};

const EMPTY: CompanySettings = {
  company_name: "",
  street: null,
  city: null,
  zip_code: null,
  country: "Slovensko",
  ico: null,
  dic: null,
  ic_dph: null,
  email: null,
  phone: null,
  web: null,
  iban: null,
  bank_name: null,
  logo_url: null,
  pdf_accent_color: "#0284c7",
  offer_footer_note: null,
};

export function CompanySettingsAdmin({
  apiBase,
  apiFetch,
  apiToken,
  assetUrl,
}: Props) {
  const [form, setForm] = useState<CompanySettings>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [logoUploading, setLogoUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!apiToken) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`${apiBase}/api/company-settings`);
      if (!res.ok) throw new Error("Nepodarilo sa načítať firemné údaje.");
      const data = (await res.json()) as CompanySettings;
      setForm({ ...EMPTY, ...data });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba načítania.");
    } finally {
      setLoading(false);
    }
  }, [apiBase, apiFetch, apiToken]);

  useEffect(() => {
    void load();
  }, [load]);

  const set = (key: keyof CompanySettings, value: string) => {
    setForm((f) => ({
      ...f,
      [key]: key === "company_name" || key === "pdf_accent_color" ? value : value || null,
    }));
    setSaved(false);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const res = await apiFetch(`${apiBase}/api/admin/company-settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(d.detail ?? "Uloženie zlyhalo.");
      }
      const data = (await res.json()) as CompanySettings;
      setForm({ ...EMPTY, ...data });
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba uloženia.");
    } finally {
      setSaving(false);
    }
  };

  const uploadLogo = async (file: File) => {
    setLogoUploading(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const res = await apiFetch(`${apiBase}/api/admin/company-settings/logo`, {
        method: "POST",
        body,
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(d.detail ?? "Nahratie loga zlyhalo.");
      }
      const d = (await res.json()) as { logo_url?: string | null };
      setForm((f) => ({ ...f, logo_url: d.logo_url ?? null }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba nahratia.");
    } finally {
      setLogoUploading(false);
    }
  };

  const removeLogo = async () => {
    setLogoUploading(true);
    setError(null);
    try {
      const res = await apiFetch(`${apiBase}/api/admin/company-settings/logo`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error("Zmazanie loga zlyhalo.");
      setForm((f) => ({ ...f, logo_url: null }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chyba.");
    } finally {
      setLogoUploading(false);
    }
  };

  const logoSrc = assetUrl(form.logo_url);

  return (
    <Card className="overflow-hidden border-sky-200/70 p-0 shadow-md shadow-sky-100/40 ring-1 ring-sky-100/50">
      <SettingsHeader />
      <SettingsBody
        loading={loading}
        error={error}
        saved={saved}
        form={form}
        logoSrc={logoSrc}
        logoUploading={logoUploading}
        saving={saving}
        fileRef={fileRef}
        onSet={set}
        onSave={() => void save()}
        onUpload={(f) => void uploadLogo(f)}
        onRemoveLogo={() => void removeLogo()}
      />
    </Card>
  );
}

function SettingsHeader() {
  return (
    <div className="border-b border-sky-200/50 bg-gradient-to-r from-sky-50/90 via-white to-cyan-50/30 px-5 py-4">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-sky-100 text-sky-700 ring-1 ring-sky-200/60">
          <Building2 className="h-4 w-4" aria-hidden />
        </div>
        <div>
          <h2 className="text-base font-semibold text-slate-900">Firemné údaje na ponukách</h2>
          <p className="mt-1 text-sm leading-relaxed text-slate-600">
            Tieto údaje a logo sa zobrazia v hlavičke PDF ponuky. Každý používateľ môže vytvárať
            vlastné ponuky pre klientov.
          </p>
        </div>
      </div>
    </div>
  );
}

function SettingsBody(props: {
  loading: boolean;
  error: string | null;
  saved: boolean;
  form: CompanySettings;
  logoSrc: string | null;
  logoUploading: boolean;
  saving: boolean;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onSet: (key: keyof CompanySettings, value: string) => void;
  onSave: () => void;
  onUpload: (file: File) => void;
  onRemoveLogo: () => void;
}) {
  const {
    loading,
    error,
    saved,
    form,
    logoSrc,
    logoUploading,
    saving,
    fileRef,
    onSet,
    onSave,
    onUpload,
    onRemoveLogo,
  } = props;

  return (
    <div className="space-y-6 bg-gradient-to-b from-white to-sky-50/20 p-5">
      {loading ? (
        <p className="flex items-center justify-center gap-2 rounded-xl border border-dashed border-slate-200 bg-slate-50/60 py-10 text-sm text-slate-600">
          <Loader2 className="h-4 w-4 animate-spin" /> Načítavam firemné údaje…
        </p>
      ) : (
        <>
          {error ? (
            <p
              className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900"
              role="alert"
            >
              {error}
            </p>
          ) : null}
          {saved ? (
            <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
              Uložené.
            </p>
          ) : null}

          <div className="rounded-xl border border-sky-100/80 bg-white p-4 shadow-sm">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-sky-800/80">
              Logo firmy
            </p>
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
              <div
                className={cn(
                  "flex h-24 w-40 shrink-0 items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50/50",
                  logoSrc ? "border-solid border-sky-200 bg-white" : "",
                )}
              >
                {logoSrc ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={logoSrc}
                    alt="Logo firmy"
                    className="max-h-20 max-w-[9rem] object-contain p-2"
                  />
                ) : (
                  <ImageIcon className="h-8 w-8 text-slate-300" aria-hidden />
                )}
              </div>
              <LogoActions
                logoUploading={logoUploading}
                fileRef={fileRef}
                logoSrc={logoSrc}
                onUpload={onUpload}
                onRemoveLogo={onRemoveLogo}
              />
            </div>
          </div>

          <FormSection title="Identifikácia firmy">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Názov firmy" className="sm:col-span-2">
                <Input
                  value={form.company_name}
                  onChange={(e) => onSet("company_name", e.target.value)}
                  className="bg-white"
                />
              </Field>
              <Field label="IČO">
                <Input
                  value={form.ico ?? ""}
                  onChange={(e) => onSet("ico", e.target.value)}
                  className="bg-white"
                />
              </Field>
              <Field label="DIČ">
                <Input
                  value={form.dic ?? ""}
                  onChange={(e) => onSet("dic", e.target.value)}
                  className="bg-white"
                />
              </Field>
              <Field label="IČ DPH" className="sm:col-span-2">
                <Input
                  value={form.ic_dph ?? ""}
                  onChange={(e) => onSet("ic_dph", e.target.value)}
                  className="bg-white"
                />
              </Field>
            </div>
          </FormSection>

          <FormSection title="Adresa">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Ulica" className="sm:col-span-2">
                <Input
                  value={form.street ?? ""}
                  onChange={(e) => onSet("street", e.target.value)}
                  className="bg-white"
                />
              </Field>
              <Field label="PSČ">
                <Input
                  value={form.zip_code ?? ""}
                  onChange={(e) => onSet("zip_code", e.target.value)}
                  className="bg-white"
                />
              </Field>
              <Field label="Mesto">
                <Input
                  value={form.city ?? ""}
                  onChange={(e) => onSet("city", e.target.value)}
                  className="bg-white"
                />
              </Field>
              <Field label="Krajina" className="sm:col-span-2">
                <Input
                  value={form.country ?? ""}
                  onChange={(e) => onSet("country", e.target.value)}
                  className="bg-white"
                />
              </Field>
            </div>
          </FormSection>

          <FormSection title="Kontakt">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="E-mail">
                <Input
                  value={form.email ?? ""}
                  onChange={(e) => onSet("email", e.target.value)}
                  className="bg-white"
                />
              </Field>
              <Field label="Telefón">
                <Input
                  value={form.phone ?? ""}
                  onChange={(e) => onSet("phone", e.target.value)}
                  className="bg-white"
                />
              </Field>
              <Field label="Web" className="sm:col-span-2">
                <Input
                  value={form.web ?? ""}
                  onChange={(e) => onSet("web", e.target.value)}
                  className="bg-white"
                />
              </Field>
            </div>
          </FormSection>

          <FormSection title="Platobné údaje">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="IBAN">
                <Input
                  value={form.iban ?? ""}
                  onChange={(e) => onSet("iban", e.target.value)}
                  className="bg-white"
                />
              </Field>
              <Field label="Banka">
                <Input
                  value={form.bank_name ?? ""}
                  onChange={(e) => onSet("bank_name", e.target.value)}
                  className="bg-white"
                />
              </Field>
            </div>
          </FormSection>

          <FormSection title="Vzhľad PDF ponuky">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Farba PDF šablóny">
                <div className="flex items-center gap-3">
                  <Input
                    type="color"
                    value={form.pdf_accent_color || "#0284c7"}
                    onChange={(e) => onSet("pdf_accent_color", e.target.value)}
                    className="h-10 w-14 cursor-pointer rounded-lg p-1"
                  />
                  <span className="font-mono text-xs text-slate-500">
                    {form.pdf_accent_color || "#0284c7"}
                  </span>
                </div>
              </Field>
              <Field label="Poznámka v pätičke PDF" className="sm:col-span-2">
                <textarea
                  className="min-h-[72px] w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
                  value={form.offer_footer_note ?? ""}
                  onChange={(e) => onSet("offer_footer_note", e.target.value)}
                  placeholder="napr. platobné podmienky, dodacia lehota…"
                />
              </Field>
            </div>
          </FormSection>

          <div className="border-t border-sky-100/80 pt-2">
            <Button
              type="button"
              className="shadow-sm shadow-sky-600/20"
              disabled={saving}
              onClick={onSave}
            >
              {saving ? "Ukladám…" : "Uložiť firemné údaje"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function LogoActions(props: {
  logoUploading: boolean;
  fileRef: React.RefObject<HTMLInputElement | null>;
  logoSrc: string | null;
  onUpload: (file: File) => void;
  onRemoveLogo: () => void;
}) {
  const { logoUploading, fileRef, logoSrc, onUpload, onRemoveLogo } = props;
  return (
    <LogoActionsInner
      logoUploading={logoUploading}
      fileRef={fileRef}
      logoSrc={logoSrc}
      onUpload={onUpload}
      onRemoveLogo={onRemoveLogo}
    />
  );
}

function LogoActionsInner(props: {
  logoUploading: boolean;
  fileRef: React.RefObject<HTMLInputElement | null>;
  logoSrc: string | null;
  onUpload: (file: File) => void;
  onRemoveLogo: () => void;
}) {
  const { logoUploading, fileRef, logoSrc, onUpload, onRemoveLogo } = props;
  return (
    <div className="flex flex-wrap gap-2">
      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onUpload(f);
          e.target.value = "";
        }}
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={logoUploading}
        onClick={() => fileRef.current?.click()}
      >
        {logoUploading ? "Nahrávam…" : "Nahrať logo"}
      </Button>
      {logoSrc ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={logoUploading}
          onClick={onRemoveLogo}
        >
          <Trash2 className="mr-1 h-3.5 w-3.5" />
          Odstrániť
        </Button>
      ) : null}
      <p className="w-full text-xs text-slate-500">PNG, JPEG, WebP alebo GIF, max. 2 MB</p>
    </div>
  );
}

function FormSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-slate-200/80 bg-white/80 p-4 shadow-sm">
      <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </p>
      {children}
    </div>
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
    <div className={cn("space-y-1.5", className)}>
      <label className="text-xs font-medium text-slate-700">{label}</label>
      {children}
    </div>
  );
}

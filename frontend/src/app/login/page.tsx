"use client";

import { Lock, User } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState, useSyncExternalStore } from "react";

import { HubMark } from "@/components/HubMark";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const CAPABILITIES = [
  "Vyhľadávanie produktov podľa mapovania z Excelu (kód, norma, filtre).",
  "Porovnanie ponúk dodávateľov s načítaním cien a skladu (Playwright / HTTP API).",
  "Košík a história pridaní, správa dodávateľov a párovanie stĺpcov.",
  "Interný nástroj — nové pobočkové účty zakladá administrátor v sekcii Admin.",
];

const subscribeNothing = () => () => {};
const snapshotTrue = () => true;
const snapshotFalse = () => false;

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const isClient = useSyncExternalStore(
    subscribeNothing,
    snapshotTrue,
    snapshotFalse,
  );
  const urlError = useMemo(() => {
    if (!isClient) {
      return null;
    }
    const q = new URLSearchParams(window.location.search).get("error");
    if (q === "config") {
      return "Chýba konfigurácia prihlásenia (SMARTHUB_AUTH_SECRET a prihlasovacie údaje v .env.local).";
    }
    if (q === "session") {
      return "Relácia expirovala — prihlás sa znova.";
    }
    return null;
  }, [isClient]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const displayError = submitError ?? urlError;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = (await res.json().catch(() => ({}))) as { error?: string };
      if (!res.ok) {
        setSubmitError(
          typeof data.error === "string"
            ? data.error
            : "Prihlásenie zlyhalo.",
        );
        return;
      }
      router.replace("/");
      router.refresh();
    } catch {
      setSubmitError("Sieťová chyba — skús znova.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-slate-50">
      <div
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_120%_80%_at_50%_-20%,rgba(14,165,233,0.18),transparent),radial-gradient(ellipse_80%_50%_at_100%_50%,rgba(51,65,85,0.08),transparent)]"
        aria-hidden
      />
      <div className="relative mx-auto flex min-h-screen max-w-6xl flex-col gap-10 px-4 py-10 sm:flex-row sm:items-center sm:justify-center sm:gap-16 sm:px-8 lg:gap-24">
        <div className="max-w-xl flex-1 space-y-6 text-slate-800">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-slate-900 shadow-lg shadow-slate-900/25">
              <HubMark size={28} title="Smarthub" />
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
                Smarthub
              </h1>
              <p className="text-sm text-slate-600">
                Interný porovnávač cien a skladov
              </p>
            </div>
          </div>
          <p className="text-base leading-relaxed text-slate-700">
            Nástroj pre rýchlu orientáciu v ponukách B2B dodávateľov a prácu s
            mapovaním produktov z tvojich dát.
          </p>
          <ul className="space-y-3 text-sm leading-snug text-slate-700">
            {CAPABILITIES.map((line) => (
              <li key={line} className="flex gap-3">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-sky-500" />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>

        <Card className="w-full max-w-md border border-slate-200/90 bg-white/95 p-6 shadow-xl shadow-slate-900/10 ring-1 ring-slate-100/80 sm:p-8">
          <h2 className="text-lg font-semibold text-slate-900">Prihlásenie</h2>
          <p className="mt-1 text-sm text-slate-600">
            Zadaj prihlasovacie údaje, ktoré ti sprístupní správca.
          </p>
          <form className="mt-6 space-y-4" onSubmit={(e) => void handleSubmit(e)}>
            <div className="space-y-2">
              <label
                htmlFor="login-username"
                className="text-xs font-medium text-slate-700"
              >
                Meno
              </label>
              <div className="relative">
                <User
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
                  aria-hidden
                />
                <Input
                  id="login-username"
                  name="username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className={cn("pl-9")}
                  placeholder="Používateľské meno"
                  required
                />
              </div>
            </div>
            <div className="space-y-2">
              <label
                htmlFor="login-password"
                className="text-xs font-medium text-slate-700"
              >
                Heslo
              </label>
              <div className="relative">
                <Lock
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
                  aria-hidden
                />
                <Input
                  id="login-password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={cn("pl-9")}
                  placeholder="Heslo"
                  required
                />
              </div>
            </div>
            {displayError ? (
              <p
                className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900"
                role="alert"
              >
                {displayError}
              </p>
            ) : null}
            <Button
              type="submit"
              className="w-full shadow-md shadow-sky-600/20"
              disabled={submitting}
            >
              {submitting ? "Prihlasujem…" : "Prihlásiť sa"}
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}

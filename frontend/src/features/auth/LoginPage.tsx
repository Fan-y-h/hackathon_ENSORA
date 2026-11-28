import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FlaskConical, LoaderCircle, LogIn, ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";

import { ApiError } from "@/api/client";
import { useJob } from "@/api/hooks";
import { useLogin, useSession } from "@/api/hooksWorkspace";
import type { Job } from "@/api/types";
import { USE_MOCKS } from "@/app/config";
import { setPageTitle } from "@/app/usePageAnnouncer";
import { EnsaiLogo } from "@/components/EnsaiLogo";
import { EnsoraLogo } from "@/components/EnsoraLogo";
import { Button, Card, ErrorPanel, LoadingState, TextField } from "@/components/ui";
import { formatPercent } from "@/lib/format";

/** Only same-site paths are accepted as a return address (no open redirect). */
function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/login") ? value : "/";
}

const IMPORT_STEPS = [
  { label: "Pamplemousse", detail: "Emploi du temps, groupe de TD, matières et coefficients" },
  { label: "Moodle", detail: "Cours, TD, TP, devoirs et échéances" },
  { label: "Drive Ensora", detail: "Annales et corrigés de votre promotion" },
];

/** Shown right after signing in, while the student's ENSAI data is imported. */
function ImportProgress({ job, onDone }: { job: Job; onDone: () => void }) {
  const queryClient = useQueryClient();
  const current = useJob(job.id);
  const progress = current.data?.progress ?? 0;
  const status = current.data?.status ?? job.status;

  useEffect(() => {
    if (status === "succeeded") {
      void queryClient.invalidateQueries();
      const timer = window.setTimeout(onDone, 600);
      return () => window.clearTimeout(timer);
    }
  }, [status, onDone, queryClient]);

  return (
    <Card>
      <h1 className="text-xl font-semibold">Récupération de vos données</h1>
      <p className="mt-1 mb-5 text-sm text-muted">Ensora+ lit vos informations ENSAI. Cela ne prend qu'un instant.</p>
      <ol className="flex flex-col gap-3" aria-live="polite">
        {IMPORT_STEPS.map((step, index) => {
          const done = status === "succeeded" || progress >= (index + 1) / IMPORT_STEPS.length;
          const active = !done && progress >= index / IMPORT_STEPS.length;
          return (
            <li key={step.label} className="flex items-start gap-3">
              {done ? (
                <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-success" aria-hidden="true" />
              ) : (
                <LoaderCircle className={active ? "mt-0.5 size-5 shrink-0 animate-spin text-accent" : "mt-0.5 size-5 shrink-0 text-muted"} aria-hidden="true" />
              )}
              <div>
                <p className="font-medium">
                  {step.label}
                  <span className="sr-only">{done ? " : terminé" : active ? " : en cours" : " : en attente"}</span>
                </p>
                <p className="text-xs text-muted">{step.detail}</p>
              </div>
            </li>
          );
        })}
      </ol>
      <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
        <div className="h-full bg-accent transition-[width]" style={{ width: `${(status === "succeeded" ? 1 : progress) * 100}%` }} />
      </div>
      <p className="mt-2 text-xs text-muted">{status === "succeeded" ? "Terminé" : formatPercent(progress)}</p>
      {status === "failed" && (
        <div className="mt-4">
          <ErrorPanel message={current.data?.error ?? "L'import a échoué. Vous pourrez relancer la synchronisation depuis Paramètres."} />
          <Button className="mt-3" onClick={onDone}>
            Continuer quand même
          </Button>
        </div>
      )}
    </Card>
  );
}

/**
 * The student signs in with the ENSAI username and password. The team backend uses them to
 * reach Moodle, Pamplemousse and the Ensora Drive and imports everything about the student:
 * nothing else has to be typed. The password is sent once to the backend, never stored here.
 */
export function LoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const session = useSession();
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [importJob, setImportJob] = useState<Job | null>(null);
  // True from the click until the import screen takes over: the session becomes "signed in"
  // during that time, and we must not jump to the dashboard before showing the import.
  const [signingIn, setSigningIn] = useState(false);
  const field = login.error instanceof ApiError ? (login.error.details as { field?: string } | undefined)?.field : undefined;
  const demoPending = login.isPending && login.variables !== undefined && "demo" in login.variables;

  useEffect(() => {
    setPageTitle(importJob ? "Récupération de vos données" : "Connexion");
  }, [importJob]);

  if (importJob) {
    return (
      <Shell>
        <ImportProgress job={importJob} onDone={() => void navigate(next, { replace: true })} />
      </Shell>
    );
  }
  if (session.isPending) return <LoadingState />;
  if (session.data?.authenticated && !signingIn) return <Navigate to={next} replace />;

  function signedIn(result: { sync_job: Job | null }) {
    if (result.sync_job) setImportJob(result.sync_job);
    else void navigate(next, { replace: true });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setSigningIn(true);
    login.mutate({ username: username.trim(), password }, { onSuccess: signedIn, onError: () => setSigningIn(false), onSettled: () => setPassword("") });
  }

  return (
    <Shell>
      <Card>
        <h1 className="mb-1 text-xl font-semibold">Connexion</h1>
        <p className="mb-5 text-sm text-muted">Avec votre compte ENSAI : vos cours, votre emploi du temps et vos matières sont récupérés automatiquement.</p>
        <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
          <TextField
            label="Identifiant ENSAI"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            hint="Le même que pour Moodle et Pamplemousse."
            error={field === "username" ? login.error?.message : undefined}
          />
          <TextField label="Mot de passe" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} error={field === "password" ? login.error?.message : undefined} />
          {login.error && !field && <p className="text-sm text-danger">{login.error.message}</p>}
          <Button type="submit" loading={login.isPending && !demoPending}>
            <LogIn className="size-4" aria-hidden="true" />
            Se connecter
          </Button>
        </form>
        <p className="mt-4 flex items-start gap-2 text-xs text-muted">
          <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          Votre mot de passe sert uniquement à vous connecter aux services de l'ENSAI. Il n'est ni affiché, ni conservé dans votre navigateur.
        </p>
        {USE_MOCKS && (
          <div className="mt-5 border-t border-line pt-4">
            <Button variant="secondary" className="w-full" loading={demoPending} onClick={() => {
                setSigningIn(true);
                login.mutate({ demo: true }, { onSuccess: signedIn, onError: () => setSigningIn(false) });
              }}>
              <FlaskConical className="size-4" aria-hidden="true" />
              Utiliser le compte de démonstration
            </Button>
            <p className="mt-2 text-xs text-muted">Données fictives, aucun compte réel n'est contacté.</p>
          </div>
        )}
      </Card>
    </Shell>
  );
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <main className="grid min-h-dvh place-items-center bg-canvas px-4 py-10">
      <div className="flex w-full max-w-sm flex-col gap-6">
        <div className="flex items-center justify-between gap-4">
          <EnsoraLogo className="w-36 ring-1 ring-line" />
          <EnsaiLogo className="w-24 ring-1 ring-line" />
        </div>
        {children}
      </div>
    </main>
  );
}

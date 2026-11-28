import { Download, FlaskConical, Monitor, Moon, PlugZap, RefreshCw, RotateCcw, Settings as SettingsIcon, Sun, Unplug } from "lucide-react";
import { Link } from "react-router";
import { useState } from "react";

import { useConnections, useJob, useStartSync } from "@/api/hooks";
import { useConnect, useDeleteAccount, useDisconnect, useExportAccount, useResetDemo, useSettings, useUpdateSettings } from "@/api/hooksWorkspace";
import { USE_MOCKS } from "@/app/config";
import type { SourceKind } from "@/api/types";
import { ENSAI_SOURCES } from "@/api/typesWorkspace";
import { setThemePreference, useThemePreference, type ThemePreference } from "@/app/theme";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, Card, CardHeader, ConfirmDialog, ErrorPanel, JobStatus, PageHeader, SourceStatus, buttonClasses } from "@/components/ui";
import { cn } from "@/lib/cn";
import { SOURCE_LABELS } from "@/lib/labels";

const ALL_SOURCES: SourceKind[] = ["pamplemousse", "moodle", "ensora_drive", "google_calendar", "personal_drive"];

function SourcesCard() {
  const connections = useConnections();
  const connect = useConnect();
  const disconnect = useDisconnect();
  const sync = useStartSync();
  return (
    <Card>
      <CardHeader title="Sources" description="Moodle, Pamplemousse et le Drive Ensora sont lus avec votre compte ENSAI ; rien n'y est jamais modifié." />
      <QueryStatus query={connections} />
      <ul className="flex flex-col divide-y divide-line">
        {ALL_SOURCES.map((kind) => {
          const connection = connections.data?.find((c) => c.source_kind === kind);
          const connected = connection && connection.status !== "disconnected";
          return (
            <li key={kind} className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2">
                <SourceStatus source={SOURCE_LABELS[kind]} state={connection?.status ?? "disconnected"} lastSyncAt={connection?.last_success_at ?? undefined} />
                {connection?.is_fixture && connected && (
                  <span className="inline-flex items-center gap-1 text-xs text-muted">
                    <FlaskConical className="size-3" aria-hidden="true" />
                    démonstration
                  </span>
                )}
              </div>
              {(ENSAI_SOURCES as readonly string[]).includes(kind) ? (
                <Button size="sm" variant="ghost" loading={sync.isPending && sync.variables?.connection_id === connection?.id} disabled={!connection} onClick={() => connection && sync.mutate({ connection_id: connection.id })}>
                  <RefreshCw className="size-4" aria-hidden="true" />
                  Resynchroniser
                </Button>
              ) : connected ? (
                <Button size="sm" variant="ghost" loading={disconnect.isPending && disconnect.variables === connection.id} onClick={() => disconnect.mutate(connection.id)}>
                  <Unplug className="size-4" aria-hidden="true" />
                  Déconnecter
                </Button>
              ) : (
                <Button size="sm" variant="secondary" loading={connect.isPending && connect.variables === kind} onClick={() => connect.mutate(kind)}>
                  <PlugZap className="size-4" aria-hidden="true" />
                  Connecter
                </Button>
              )}
            </li>
          );
        })}
      </ul>
      {(connect.error ?? disconnect.error) && <ErrorPanel message={(connect.error ?? disconnect.error)?.message ?? ""} />}
    </Card>
  );
}

/** Privacy of course material. Model and provider names are developer settings, not shown here. */
function PrivacyCard() {
  const settings = useSettings();
  const update = useUpdateSettings();
  if (!settings.data) return <QueryStatus query={settings} />;
  const policy = settings.data.provider_policy;

  return (
    <Card>
      <CardHeader title="Confidentialité de mes documents" description="Choisissez où vos cours peuvent être analysés pour le tuteur et les supports." />
      <fieldset className="flex flex-col gap-2 text-sm">
        <legend className="sr-only">Analyse des documents</legend>
        {(
          [
            ["local_only", "Uniquement sur les serveurs d'Ensora+", "Vos documents ne sont jamais transmis à un service extérieur."],
            ["allow_external", "Autoriser un service extérieur", "Seuls les passages nécessaires à une réponse peuvent être transmis."],
          ] as const
        ).map(([value, label, hint]) => (
          <label key={value} className={cn("flex cursor-pointer gap-2.5 rounded-lg border p-3 has-focus-visible:outline-2 has-focus-visible:outline-accent", policy === value ? "border-accent bg-accent-soft/40" : "border-line")}>
            <input type="radio" name="provider-policy" checked={policy === value} onChange={() => update.mutate({ provider_policy: value, expected_revision: settings.data.revision })} className="mt-1 accent-(--accent)" />
            <span>
              <span className="font-medium">{label}</span>
              <span className="block text-muted">{hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {update.error && <p className="mt-2 text-sm text-danger">{update.error.message}</p>}
    </Card>
  );
}

function DisplayCard() {
  const theme = useThemePreference();
  const settings = useSettings();
  const update = useUpdateSettings();
  const options: Array<[ThemePreference, string, typeof Sun]> = [
    ["system", "Automatique", Monitor],
    ["light", "Clair", Sun],
    ["dark", "Sombre", Moon],
  ];
  return (
    <Card>
      <CardHeader title="Affichage et notifications" />
      <fieldset className="mb-4">
        <legend className="mb-2 text-sm font-medium">Thème</legend>
        <div className="flex flex-wrap gap-2">
          {options.map(([value, label, Icon]) => (
            <label key={value} className={cn("flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm has-focus-visible:outline-2 has-focus-visible:outline-accent", theme === value ? "border-accent bg-accent-soft/40 font-medium" : "border-line")}>
              <input type="radio" name="theme" className="sr-only" checked={theme === value} onChange={() => setThemePreference(value)} />
              <Icon className="size-4" aria-hidden="true" />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      {settings.data && (
        <fieldset className="flex flex-col gap-2 text-sm">
          <legend className="mb-1 font-medium">Recevoir les rappels</legend>
          {(
            [
              ["in_app", "Dans l'application"],
              ["email", "Par e-mail (bientôt)"],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="flex items-center gap-2">
              <input
                type="checkbox"
                className="focus-ring size-4 accent-(--accent)"
                disabled={key === "email"}
                checked={settings.data.notification_channels[key]}
                onChange={(e) => update.mutate({ notification_channels: { ...settings.data.notification_channels, [key]: e.target.checked }, expected_revision: settings.data.revision })}
              />
              {label}
            </label>
          ))}
        </fieldset>
      )}
    </Card>
  );
}

function DataCard() {
  const exportAccount = useExportAccount();
  const resetDemo = useResetDemo();
  const deleteAccount = useDeleteAccount();
  const [jobId, setJobId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const job = useJob(jobId);

  return (
    <Card>
      <CardHeader title="Mes données" description="Vos notes, résultats et copies restent privés par défaut." />
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" loading={exportAccount.isPending} onClick={() => exportAccount.mutate(undefined, { onSuccess: (j) => setJobId(j.id) })}>
          <Download className="size-4" aria-hidden="true" />
          Exporter mes données
        </Button>
        <Button variant="danger" onClick={() => setConfirming(true)}>
          Supprimer mon compte
        </Button>
        {USE_MOCKS && (
          <Button variant="ghost" loading={resetDemo.isPending} onClick={() => resetDemo.mutate()}>
            Réinitialiser la démonstration
          </Button>
        )}
      </div>
      {resetDemo.isSuccess && (
        <p role="status" className="mt-3 text-sm text-success">
          Données de démonstration remises à zéro.
        </p>
      )}
      {job.data && (
        <div className="mt-3">
          <JobStatus label="Préparation de l'export" state={job.data.status} progress={job.data.progress} error={job.data.error ?? undefined} />
        </div>
      )}
      {deleteAccount.error && (
        <div className="mt-3">
          <ErrorPanel message={deleteAccount.error.message} />
        </div>
      )}
      <ConfirmDialog
        open={confirming}
        tone="danger"
        title="Supprimer définitivement votre compte ?"
        description="Toutes vos données Ensora+ seront effacées. Vos plateformes (Moodle, Pamplemousse…) ne sont pas touchées."
        confirmLabel="Supprimer"
        loading={deleteAccount.isPending}
        onConfirm={() => deleteAccount.mutate(undefined, { onSettled: () => setConfirming(false) })}
        onCancel={() => setConfirming(false)}
      />
    </Card>
  );
}

export function SettingsPage() {
  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Paramètres"
        icon={<SettingsIcon className="size-5" aria-hidden="true" />}
        actions={
          <Link to="/onboarding" className={buttonClasses("secondary")}>
            <RotateCcw className="size-4" aria-hidden="true" />
            Refaire la configuration
          </Link>
        }
      />
      <div className="grid items-start gap-5 xl:grid-cols-2">
        <SourcesCard />
        <PrivacyCard />
        <DisplayCard />
        <DataCard />
      </div>
    </section>
  );
}

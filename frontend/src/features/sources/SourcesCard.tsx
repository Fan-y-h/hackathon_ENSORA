import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { errorMessage } from "../../api/client";
import {
  invalidateSyncedModels,
  SOURCE_LABEL,
  sourcesUnavailable,
  TERMINAL,
  useCancelJob,
  useConnectionAction,
  useConnections,
  useConsent,
  useDisconnectConnection,
  useImportFromSources,
  useJob,
  usePipelineStatus,
  useResetSyncSchedule,
  useResults,
  useResumeJob,
  useSourcesSettings,
  useStartSync,
  useSyncRuns,
  useSyncSchedule,
  useUpdateSyncSchedule,
  type ConnectionStatus,
  type SourceConnection,
  type SourceKind,
} from "../../api/sources";
import { Icon } from "../../components/Icon";
import { Card, ConfirmDialog, EmptyState, ErrorPanel, Loading } from "../../components/ui";
import { dayKey, formatClock, formatLongDate } from "../../lib/time";
import { useSubjects } from "../../app/subjects";
import "../library/library.css";

const REASON: Record<string, string> = {
  adapter_error: "Le module de connexion a échoué : consultez le journal du serveur.",
  adapter_missing: "Cette source n'est pas disponible sur ce poste.",
  consent_required: "Autorisez d'abord la lecture avant de connecter cette source.",
  configuration_missing: "Configuration manquante (identifiants OAuth ou variables d'environnement).",
  profile_busy: "Une autre connexion ENSAI est déjà en cours.",
  credential_expired: "Session expirée : reconnectez-vous.",
  cancelled_by_user: "Connexion annulée par l'utilisateur.",
  service_verification_required: "Connexion faite, mais l'accès au service n'est pas confirmé : cliquez sur « Vérifier ».",
  transient_failure: "Service momentanément injoignable, réessayez.",
  cas_login_required: "Cliquez sur Reconnecter, terminez la connexion ENSAI CAS et le MFA dans la fenêtre ouverte, puis cliquez sur Vérifier.",
  calendar_window_unverified: "La fenêtre de dates demandée n'a pas pu être vérifiée.",
  academic_year_unknown: "L'année universitaire des données doit être confirmée.",
};

const JOB_STATUS_LABEL: Record<string, { label: string; tone: "good" | "warning" | "critical" | "neutral" }> = {
  queued: { label: "En attente", tone: "neutral" },
  running: { label: "Synchronisation en cours", tone: "neutral" },
  succeeded: { label: "Réussie", tone: "good" },
  partial: { label: "Synchronisation partielle", tone: "warning" },
  failed: { label: "Erreur", tone: "critical" },
  cancelled: { label: "Annulée", tone: "warning" },
  interrupted: { label: "Interrompue", tone: "warning" },
};

function intervalLabel(minutes: number): string {
  if (minutes < 60) return `Toutes les ${minutes} min`;
  if (minutes % 1440 === 0) {
    const days = minutes / 1440;
    return days === 1 ? "Tous les jours" : `Tous les ${days} jours`;
  }
  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return hours === 1 ? "Toutes les heures" : `Toutes les ${hours} h`;
  }
  return `Toutes les ${minutes} min`;
}

interface SourceBadgeState {
  label: string;
  tone: "good" | "warning" | "critical" | "neutral";
  detail?: string;
}

/**
 * Honest state presentation: NEVER present CONNECTED as proof that data was retrieved.
 * Separates consent, authentication status, active sync, partial/error state, and data availability.
 */
function describeSource(
  conn: SourceConnection,
  syncing: boolean,
  lastReportStatus?: string,
): SourceBadgeState {
  if (syncing) return { label: "Synchronisation en cours", tone: "neutral" };
  if (!conn.consent.read) return { label: "Lecture non autorisée", tone: "neutral" };
  if (conn.validation_required) return { label: "Connexion à vérifier", tone: "warning" };
  if (conn.source === "local") {
    return conn.last_success_at
      ? { label: "Données disponibles", tone: "good" }
      : { label: "Import autorisé (aucun fichier)", tone: "neutral" };
  }
  const statusMap: Record<ConnectionStatus, SourceBadgeState> = {
    CONNECTED:
      lastReportStatus === "partial"
        ? { label: "Synchronisation partielle", tone: "warning" }
        : lastReportStatus === "failed"
          ? { label: "Connecté · dernière synchro en erreur", tone: "critical" }
          : conn.last_success_at
            ? { label: "Connecté · données disponibles", tone: "good" }
            : { label: "Connecté (pas encore synchronisé)", tone: "warning" },
    NOT_CONFIGURED: { label: "Non connecté", tone: "neutral" },
    REAUTH_REQUIRED: { label: "Reconnexion requise", tone: "warning" },
    ACCESS_DENIED: { label: "Accès refusé", tone: "critical" },
    UNAVAILABLE: { label: "Service indisponible", tone: "warning" },
    ERROR: { label: "Erreur de connexion", tone: "critical" },
  };
  return statusMap[conn.status] ?? { label: "État inconnu", tone: "warning" };
}

/** Read-only connections to Moodle, Pamplemousse, Drive Ensora, Google Agenda and local files. */
export function SourcesCard() {
  const qc = useQueryClient();
  const { timezone } = useSubjects();
  const connections = useConnections();
  const results = useResults();
  const programmeSubjects = results.data?.semesters.flatMap((semester) =>
    semester.units.filter((unit) => !unit.unassigned).flatMap((unit) => unit.subjects),
  ) ?? [];
  const [subjectId, setSubjectId] = useState("");
  const schedule = useSyncSchedule();
  const sourcesSettings = useSourcesSettings();
  const runs = useSyncRuns(8);
  const pipeline = usePipelineStatus();

  const consent = useConsent();
  const action = useConnectionAction();
  const disconnect = useDisconnectConnection();
  const startSync = useStartSync();
  const cancelJob = useCancelJob();
  const resumeJob = useResumeJob();
  const importer = useImportFromSources();
  const updateSchedule = useUpdateSyncSchedule();
  const resetSchedule = useResetSyncSchedule();

  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeSource, setActiveSource] = useState<SourceKind | "all" | null>(null);
  const [rowFeedback, setRowFeedback] = useState<Record<string, { tone: "good" | "warning" | "critical"; text: string }>>({});
  const [revokeTarget, setRevokeTarget] = useState<SourceConnection | null>(null);
  const handledJob = useRef<string | null>(null);

  const job = useJob(activeJobId);
  const jobStatus = job.data?.status;
  const jobRunning = activeJobId !== null && (!jobStatus || !TERMINAL.includes(jobStatus));

  useEffect(() => {
    if (!activeJobId || !jobStatus || !TERMINAL.includes(jobStatus) || handledJob.current === activeJobId) return;
    handledJob.current = activeJobId;
    if (jobStatus === "succeeded" || jobStatus === "partial") {
      importer.mutate(undefined, { onSettled: () => invalidateSyncedModels(qc) });
    } else {
      invalidateSyncedModels(qc);
    }
  }, [activeJobId, jobStatus, importer, qc]);

  const busyId = action.isPending ? action.variables?.id : consent.isPending ? consent.variables?.id : undefined;

  const triggerSync = (source?: SourceKind) => {
    handledJob.current = null;
    setActiveSource(source ?? "all");
    startSync.mutate(source === "moodle" && subjectId ? { source, subject_id: subjectId } : source, {
      onSuccess: (res) => setActiveJobId(res.id),
      onError: () => setActiveSource(null),
    });
  };

  const latestRun = runs.data?.items[0];
  const latestReport = job.data?.report ?? latestRun?.report ?? {};

  const scheduleConfig = sourcesSettings.data?.preferences?.sync_schedule;

  return (
    <Card
      title="Sources & Synchronisation"
      subtitle="Lecture seule. Aucune donnée n'est écrite sur Moodle, Pamplemousse, Drive ou Google Agenda."
      aside={
        pipeline.data && (
          <span className="xsmall muted">
            {pipeline.data.active_jobs > 0 ? `${pipeline.data.active_jobs} synchro active(s)` : "Prêt"}
          </span>
        )
      }
    >
      {connections.isPending ? (
        <Loading lines={4} />
      ) : connections.error ? (
        sourcesUnavailable(connections.error) ? (
          <EmptyState title="Sources indisponibles">
            Le module de synchronisation P1 n'est pas démarré sur ce serveur.
          </EmptyState>
        ) : (
          <ErrorPanel error={connections.error} onRetry={() => void connections.refetch()} />
        )
      ) : (
        <div className="stack">
          {connections.data!.items
            .filter((c) => c.source !== "academic_import")
            .map((c) => {
              const isLocal = c.source === "local";
              const connected = c.status === "CONNECTED";
              const isThisSyncing = jobRunning && (activeSource === "all" || activeSource === c.source);
              const reportForSource = latestReport[c.source];
              const badge = describeSource(c, isThisSyncing, reportForSource?.status);
              const fb = rowFeedback[c.id];
              const canToggleAuto = c.consent.automatic_sync || (connected && c.consent.read);

              return (
                <div key={c.id} className="stack" style={{ padding: "12px 0", borderTop: "1px solid var(--grid)", gap: 8 }}>
                  <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div className="stack" style={{ gap: 2 }}>
                      <strong>{SOURCE_LABEL[c.source] ?? c.source}</strong>
                      <span className="xsmall muted">
                        {c.last_success_at
                          ? `Dernière synchro réussie : ${formatLongDate(dayKey(c.last_success_at, timezone)).toLowerCase()} à ${formatClock(c.last_success_at, timezone)}`
                          : "Aucune synchronisation réussie enregistrée"}
                      </span>
                    </div>
                    <span className={`pill${badge.tone === "neutral" ? "" : ` status--${badge.tone}`}`}>
                      {badge.label}
                    </span>
                  </div>

                  <div className="row" style={{ gap: 14 }}>
                    <label className="checkbox" style={{ alignItems: "center" }}>
                      <input
                        type="checkbox"
                        checked={c.consent.read}
                        disabled={busyId === c.id}
                        onChange={(e) => {
                          setRowFeedback((prev) => {
                            const next = { ...prev };
                            delete next[c.id];
                            return next;
                          });
                          consent.mutate({ id: c.id, purpose: "read", granted: e.target.checked });
                        }}
                      />
                      Autoriser la lecture
                    </label>
                    {!isLocal && (
                      <label className="checkbox" style={{ alignItems: "center" }}>
                        <input
                          type="checkbox"
                          checked={c.consent.automatic_sync}
                          disabled={busyId === c.id || !canToggleAuto}
                          title={canToggleAuto ? undefined : "Connectez et vérifiez la source avant d'activer la synchronisation automatique"}
                          onChange={(e) =>
                            consent.mutate({ id: c.id, purpose: "automatic_sync", granted: e.target.checked })
                          }
                        />
                        Synchronisation automatique
                      </label>
                    )}
                  </div>

                  {c.source === "moodle" && programmeSubjects.length > 0 && (
                    <label className="field"><span>Matière Moodle à synchroniser</span><select className="select" value={subjectId} onChange={(event) => setSubjectId(event.target.value)}>
                      <option value="">Toutes les matières pertinentes</option>
                      {programmeSubjects.map((item) => <option key={item.key} value={item.key}>{item.title}</option>)}
                    </select></label>
                  )}
                  {!isLocal && (
                    <div className="row">
                      {!connected ? (
                        <button
                          type="button"
                          className="btn btn--small btn--primary"
                          disabled={!c.consent.read || busyId === c.id}
                          title={c.consent.read ? undefined : "Autorisez d'abord la lecture"}
                          onClick={() =>
                            action.mutate(
                              { id: c.id, action: c.status === "REAUTH_REQUIRED" ? "reauthorize" : "authorize" },
                              {
                                onSuccess: (res) => {
                                  if (res.status === "CONNECTED") {
                                    setRowFeedback((p) => ({
                                      ...p,
                                      [c.id]: { tone: "good", text: "Connexion autorisée. Vous pouvez maintenant synchroniser." },
                                    }));
                                  } else {
                                    setRowFeedback((p) => ({
                                      ...p,
                                      [c.id]: {
                                        tone: "warning",
                                        text: REASON[res.reason ?? ""] ?? `Connexion impossible (${res.reason ?? res.status}).`,
                                      },
                                    }));
                                  }
                                },
                              },
                            )
                          }
                        >
                          {busyId === c.id ? "Connexion…" : c.status === "REAUTH_REQUIRED" ? "Reconnecter" : "Autoriser la connexion"}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn btn--small btn--primary"
                          disabled={!c.consent.read || jobRunning || startSync.isPending}
                          onClick={() => triggerSync(c.source)}
                        >
                          <Icon name="refresh" size={14} />
                          {isThisSyncing ? "Synchronisation…" : "Synchroniser"}
                        </button>
                      )}

                      <button
                        type="button"
                        className="btn btn--small"
                        disabled={!c.consent.read || busyId === c.id}
                        title={c.consent.read ? "Vérifier que la session et l'accès au service fonctionnent" : "Autorisez d'abord la lecture"}
                        onClick={() =>
                          action.mutate(
                            { id: c.id, action: "validate" },
                            {
                              onSuccess: (res) => {
                                setRowFeedback((p) => ({
                                  ...p,
                                  [c.id]:
                                    res.status === "CONNECTED"
                                      ? { tone: "good", text: "Accès vérifié (session active)." }
                                      : {
                                          tone: "warning",
                                          text: REASON[res.reason ?? ""] ?? `Vérification échouée (${res.reason ?? res.status}).`,
                                        },
                                }));
                              },
                            },
                          )
                        }
                      >
                        {busyId === c.id && action.variables?.action === "validate" ? "Vérification…" : "Vérifier"}
                      </button>

                      {(connected || c.status === "REAUTH_REQUIRED" || c.status === "ERROR") && (
                        <button
                          type="button"
                          className="btn btn--small btn--ghost btn--danger"
                          disabled={disconnect.isPending}
                          onClick={() => setRevokeTarget(c)}
                        >
                          Révoquer
                        </button>
                      )}
                    </div>
                  )}

                  {!isLocal && c.consent.read && (c.validation_required || c.status === "REAUTH_REQUIRED") && (
                    <p className="small muted">{c.validation_required
                      ? "Cliquez sur Vérifier pour tester la session enregistrée. Si elle a expiré, renouez la connexion."
                      : "Cliquez sur Reconnecter et terminez la connexion dans la fenêtre ouverte sur ce poste, y compris le MFA demandé. Revenez ensuite ici et cliquez sur Vérifier."}</p>
                  )}
                  {fb && (
                    <div className={`status status--${fb.tone} small`} role="status">
                      {fb.text}
                    </div>
                  )}
                </div>
              );
            })}

          {(consent.error || action.error || startSync.error || disconnect.error) && (
            <div className="status status--critical small" role="alert">
              {errorMessage(consent.error ?? action.error ?? startSync.error ?? disconnect.error)}
            </div>
          )}

          {action.isPending && (
            <p className="xsmall muted">Si une fenêtre ENSAI CAS ou Google s’ouvre sur ce poste, terminez la connexion et le MFA dans cette fenêtre.</p>
          )}

          {job.data && (
            <div
              className={`status status--${JOB_STATUS_LABEL[job.data.status]?.tone === "neutral" ? "good" : JOB_STATUS_LABEL[job.data.status]?.tone ?? "warning"} small`}
              role="status"
            >
              <div className="stack" style={{ gap: 4, flex: 1 }}>
                <strong>
                  Tâche de synchronisation : {JOB_STATUS_LABEL[job.data.status]?.label ?? job.data.status}
                </strong>
                {Object.entries(job.data.report ?? {}).length > 0 && (
                  <span className="xsmall">
                    {Object.entries(job.data.report)
                      .map(([src, r]) => {
                        const label = SOURCE_LABEL[src as SourceKind] ?? src;
                        const counts = [
                          r.documents != null ? `${r.documents} doc(s)` : null,
                          r.records != null ? `${r.records} élément(s)` : null,
                        ]
                          .filter(Boolean)
                          .join(", ");
                        return `${label}: ${r.status ?? "ok"}${counts ? ` (${counts})` : ""}${r.reason ? ` — ${REASON[r.reason] ?? r.reason}` : ""}`;
                      })
                      .join(" · ")}
                  </span>
                )}
              </div>
              {jobRunning && (
                <button
                  type="button"
                  className="btn btn--small"
                  disabled={cancelJob.isPending}
                  onClick={() => cancelJob.mutate(job.data!.id)}
                >
                  Annuler
                </button>
              )}
              {(job.data.status === "partial" || job.data.status === "interrupted" || job.data.status === "failed") && (
                <button
                  type="button"
                  className="btn btn--small"
                  disabled={resumeJob.isPending}
                  onClick={() =>
                    resumeJob.mutate(
                      { jobId: job.data!.id },
                      { onSuccess: (r) => { handledJob.current = null; setActiveJobId(r.id); } },
                    )
                  }
                >
                  Reprendre
                </button>
              )}
            </div>
          )}

          {/* Planification automatique */}
          {schedule.data && scheduleConfig && (
            <details className="disclosure" style={{ borderTop: "1px solid var(--grid)", paddingTop: 12 }}>
              <summary className="strong small">Planification automatique</summary>
              <div className="stack" style={{ marginTop: 10 }}>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={scheduleConfig.enabled}
                    disabled={updateSchedule.isPending}
                    onChange={(e) => updateSchedule.mutate({ ...scheduleConfig, enabled: e.target.checked })}
                  />
                  Activer la synchronisation planifiée en arrière-plan
                </label>
                {schedule.data.sources
                  .filter((row) => ["moodle", "ensora_drive", "google_calendar", "pamplemousse"].includes(row.source))
                  .map((row) => {
                    const key = row.scope_key ? `${row.source}.${row.scope_key}` : row.source;
                    return (
                      <div key={key} className="row" style={{ justifyContent: "space-between" }}>
                        <span className="small">
                          {SOURCE_LABEL[row.source] ?? row.source}
                          {row.scope_key ? ` (${row.scope_key})` : ""}
                        </span>
                        <select
                          className="select"
                          style={{ width: "auto", minHeight: 36 }}
                          aria-label={`Fréquence pour ${SOURCE_LABEL[row.source] ?? row.source}`}
                          value={row.interval_minutes ?? ""}
                          disabled={updateSchedule.isPending}
                          onChange={(e) => {
                            const val = e.target.value;
                            updateSchedule.mutate({
                              ...scheduleConfig,
                              intervals_minutes: {
                                ...scheduleConfig.intervals_minutes,
                                [key]: val ? Number(val) : null,
                              },
                            });
                          }}
                        >
                          <option value="">Manuelle</option>
                          {schedule.data!.supported_intervals_minutes.map((m) => (
                            <option key={m} value={m}>
                              {intervalLabel(m)}
                            </option>
                          ))}
                        </select>
                      </div>
                    );
                  })}
                <div className="row">
                  <button
                    type="button"
                    className="btn btn--small"
                    disabled={resetSchedule.isPending}
                    onClick={() => resetSchedule.mutate()}
                  >
                    Réinitialiser les fréquences
                  </button>
                </div>
                {updateSchedule.error && (
                  <div className="status status--critical small">{errorMessage(updateSchedule.error)}</div>
                )}
              </div>
            </details>
          )}

          {/* Historique des synchronisations */}
          {runs.data && runs.data.items.length > 0 && (
            <details className="disclosure" style={{ borderTop: "1px solid var(--grid)", paddingTop: 12 }}>
              <summary className="strong small">Historique récent ({runs.data.items.length})</summary>
              <ul className="list" style={{ marginTop: 8 }}>
                {runs.data.items.slice(0, 5).map((run) => {
                  const st = JOB_STATUS_LABEL[run.status] ?? { label: run.status, tone: "neutral" as const };
                  return (
                    <li key={run.id} style={{ padding: "6px 0", justifyContent: "space-between" }}>
                      <span className="xsmall muted">
                        {formatLongDate(dayKey(run.started_at, timezone))} · {formatClock(run.started_at, timezone)}
                      </span>
                      <span className={`pill${st.tone === "neutral" ? "" : ` status--${st.tone}`}`}>{st.label}</span>
                    </li>
                  );
                })}
              </ul>
            </details>
          )}
        </div>
      )}

      {revokeTarget && (
        <ConfirmDialog
          title={`Révoquer ${SOURCE_LABEL[revokeTarget.source] ?? revokeTarget.source} ?`}
          message="La session locale et le consentement de lecture seront retirés pour cette source. Les données sur la plateforme d'origine ne sont jamais modifiées."
          confirmLabel="Révoquer l'accès"
          danger
          pending={disconnect.isPending}
          error={disconnect.error}
          onClose={() => setRevokeTarget(null)}
          onConfirm={() =>
            disconnect.mutate(revokeTarget.id, {
              onSuccess: () => setRevokeTarget(null),
            })
          }
        />
      )}
    </Card>
  );
}

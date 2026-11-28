import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { errorMessage } from "../../api/client";
import {
  invalidateSyncedModels,
  sourcesUnavailable,
  TERMINAL,
  useImportFromSources,
  useJob,
  useStartSync,
  type SourceKind,
} from "../../api/sources";
import { Icon } from "../../components/Icon";

/**
 * One click: sync every consented source (or a single source) -> poll job -> copy calendar
 * events into the agenda and invalidate all read models (agenda, results, documents, planner).
 */
export function SyncButton({ source, label = "Synchroniser" }: { source?: SourceKind; label?: string }) {
  const qc = useQueryClient();
  const start = useStartSync();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const importer = useImportFromSources();
  const handled = useRef<string | null>(null);

  const status = job.data?.status;
  const running = start.isPending || (jobId !== null && (!status || !TERMINAL.includes(status))) || importer.isPending;

  useEffect(() => {
    if (!jobId || !status || !TERMINAL.includes(status) || handled.current === jobId) return;
    handled.current = jobId;
    if (status === "succeeded" || status === "partial") {
      importer.mutate(undefined, {
        onSettled: () => invalidateSyncedModels(qc),
      });
    } else {
      invalidateSyncedModels(qc);
    }
  }, [jobId, status, importer, qc]);

  const created = importer.data?.sources.reduce((sum, s) => sum + s.result.created + s.result.updated, 0);
  const failedSources = Object.entries(job.data?.report ?? {})
    .filter(([, entry]) => entry && entry.status && !["succeeded", "ok", "skipped"].includes(entry.status))
    .map(([src]) => src);
  const error = start.error ?? job.error ?? importer.error;

  let note: string | null = null;
  let tone: "good" | "warning" | "critical" | "neutral" = "neutral";

  if (error) {
    note = sourcesUnavailable(error) ? "Sources indisponibles" : errorMessage(error);
    tone = "critical";
  } else if (running) {
    note = importer.isPending ? "Mise à jour de l'agenda…" : "Synchronisation en cours…";
  } else if (status === "failed") {
    note = failedSources.length ? `Échec (${failedSources.join(", ")})` : "Échec de la synchronisation";
    tone = "critical";
  } else if (status === "cancelled" || status === "interrupted") {
    note = status === "cancelled" ? "Synchronisation annulée" : "Synchronisation interrompue";
    tone = "warning";
  } else if (status === "partial") {
    const suffix = created ? ` · ${created} événement(s) mis à jour` : "";
    note = failedSources.length
      ? `Synchronisation partielle (${failedSources.join(", ")} en erreur)${suffix}`
      : `Synchronisation partielle${suffix}`;
    tone = "warning";
  } else if (importer.isSuccess) {
    note = created ? `${created} événement(s) mis à jour` : "Déjà à jour";
    tone = "good";
  }

  return (
    <span className="row" style={{ gap: 8 }}>
      {note && (
        <span
          className={`small${tone === "critical" ? " status status--critical" : tone === "warning" ? " status status--warning" : " muted"}`}
          role="status"
          aria-live="polite"
          style={tone === "critical" || tone === "warning" ? { padding: "4px 8px" } : undefined}
        >
          {note}
        </span>
      )}
      <button
        type="button"
        className="btn"
        disabled={running}
        onClick={() => {
          handled.current = null;
          importer.reset();
          start.mutate(source, { onSuccess: (r) => setJobId(r.id) });
        }}
      >
        <Icon name="refresh" />
        {running ? "Synchronisation…" : label}
      </button>
    </span>
  );
}

import { useEffect, useRef, useState } from "react";

import { errorMessage } from "../../api/client";
import { sourcesUnavailable, TERMINAL, useImportFromSources, useJob, useStartSync } from "../../api/sources";
import { Icon } from "../../components/Icon";

/**
 * One click: sync every consented source -> wait for the job -> copy calendar events into
 * the agenda (exams become assessments the Smart Planner revises for).
 */
export function SyncButton() {
  const start = useStartSync();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const importer = useImportFromSources();
  const imported = useRef<string | null>(null);

  const status = job.data?.status;
  const running = start.isPending || (jobId !== null && (!status || !TERMINAL.includes(status))) || importer.isPending;

  useEffect(() => {
    if (!jobId || !status || imported.current === jobId) return;
    if (status === "succeeded" || status === "partial") {
      imported.current = jobId;
      importer.mutate();
    }
  }, [jobId, status, importer]);

  const created = importer.data?.sources.reduce((sum, s) => sum + s.result.created + s.result.updated, 0);
  const error = start.error ?? job.error ?? importer.error;

  let note: string | null = null;
  if (error) note = sourcesUnavailable(error) ? "Sources indisponibles" : errorMessage(error);
  else if (status === "failed") note = "Échec de la synchronisation";
  else if (importer.isSuccess) note = created ? `${created} événement(s) mis à jour` : "Déjà à jour";
  else if (status === "partial") note = "Synchronisation partielle";

  return (
    <span className="row" style={{ gap: 8 }}>
      {note && (
        <span className="small muted" role="status">
          {note}
        </span>
      )}
      <button
        type="button"
        className="btn"
        disabled={running}
        onClick={() => {
          imported.current = null;
          importer.reset();
          start.mutate(undefined, { onSuccess: (r) => setJobId(r.id) });
        }}
      >
        <Icon name="refresh" />
        {running ? "Synchronisation…" : "Synchroniser"}
      </button>
    </span>
  );
}

// Developer-only: shows real hook results (mock or team backend) with their
// loading/error states, and a sync job that progresses. Part of /dev/ui.
import { RefreshCw } from "lucide-react";
import { useState } from "react";

import { useCancelJob, useConnections, useJob, useStartSync, useSubjects, useTasks } from "@/api/hooks";
import { Button, Card, CardHeader, ErrorPanel, JobStatus, LoadingState, SourceStatus } from "@/components/ui";
import { SOURCE_LABELS, TASK_STATUS_LABELS } from "@/lib/labels";

export function ApiPlayground() {
  const connections = useConnections();
  const subjects = useSubjects();
  const tasks = useTasks();
  const startSync = useStartSync();
  const cancelJob = useCancelJob();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);

  return (
    <Card>
      <CardHeader
        title="Couche API"
        description="Résultats réels des hooks : faux backend en mode démo, serveur de l'équipe sinon."
        actions={
          <Button size="sm" variant="secondary" loading={startSync.isPending} onClick={() => startSync.mutate({}, { onSuccess: (j) => setJobId(j.id) })}>
            <RefreshCw className="size-3.5" aria-hidden="true" />
            Synchroniser
          </Button>
        }
      />

      <div className="flex flex-col gap-5 text-sm">
        {job.data && (
          <JobStatus
            label="Synchronisation des sources"
            state={job.data.status}
            progress={job.data.progress}
            error={job.data.error ?? undefined}
            onCancel={() => cancelJob.mutate(job.data.id)}
          />
        )}
        {startSync.error && <ErrorPanel message={startSync.error.message} />}

        <div>
          <h3 className="mb-2 font-semibold">GET /connections</h3>
          {connections.isPending && <LoadingState />}
          {connections.error && <ErrorPanel message={connections.error.message} onRetry={() => connections.refetch()} />}
          <div className="flex flex-col gap-2">
            {connections.data?.map((c) => (
              <SourceStatus key={c.id} source={SOURCE_LABELS[c.source_kind]} state={c.status} lastSyncAt={c.last_success_at ?? undefined} />
            ))}
          </div>
        </div>

        <div>
          <h3 className="mb-2 font-semibold">GET /subjects</h3>
          {subjects.isPending && <LoadingState />}
          {subjects.error && <ErrorPanel message={subjects.error.message} onRetry={() => subjects.refetch()} />}
          <ul className="list-inside list-disc">
            {subjects.data?.map((s) => (
              <li key={s.id}>
                {s.title} <span className="text-muted">({s.canonical_code})</span>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h3 className="mb-2 font-semibold">GET /tasks</h3>
          {tasks.isPending && <LoadingState />}
          {tasks.error && <ErrorPanel message={tasks.error.message} onRetry={() => tasks.refetch()} />}
          <ul className="list-inside list-disc">
            {tasks.data?.map((t) => (
              <li key={t.id}>
                {t.title} <span className="text-muted">— {TASK_STATUS_LABELS[t.status]}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}

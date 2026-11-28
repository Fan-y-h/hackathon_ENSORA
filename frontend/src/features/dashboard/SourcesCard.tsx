import { useQueryClient } from "@tanstack/react-query";
import { FlaskConical, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { useCancelJob, useConnections, useJob, useStartSync } from "@/api/hooks";
import { queryKeys } from "@/api/queryKeys";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, Card, CardHeader, ErrorPanel, JobStatus, SourceStatus } from "@/components/ui";
import { SOURCE_LABELS } from "@/lib/labels";

/** State of each connected source, with a button to synchronize them all. */
export function SourcesCard() {
  const queryClient = useQueryClient();
  const connections = useConnections();
  const startSync = useStartSync();
  const cancelJob = useCancelJob();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const status = job.data?.status;
  const running = status === "queued" || status === "running";

  // When the sync ends, new events/tasks/statuses may exist: reload them.
  useEffect(() => {
    if (status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: queryKeys.connections });
      void queryClient.invalidateQueries({ queryKey: queryKeys.allEvents });
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
    }
  }, [status, queryClient]);

  return (
    <Card>
      <CardHeader
        title="Sources"
        actions={
          <Button
            size="sm"
            variant="secondary"
            loading={startSync.isPending || running}
            onClick={() => startSync.mutate({}, { onSuccess: (newJob) => setJobId(newJob.id) })}
          >
            {!startSync.isPending && !running && <RefreshCw className="size-3.5" aria-hidden="true" />}
            Synchroniser
          </Button>
        }
      />
      <div className="flex flex-col gap-3">
        <QueryStatus query={connections} />
        {connections.data?.map((connection) => (
          <div key={connection.id} className="flex flex-wrap items-center gap-2">
            <SourceStatus
              source={SOURCE_LABELS[connection.source_kind]}
              state={connection.status}
              lastSyncAt={connection.last_success_at ?? undefined}
            />
            {connection.is_fixture && (
              <span className="inline-flex items-center gap-1 text-xs text-muted">
                <FlaskConical className="size-3" aria-hidden="true" />
                données de démonstration
              </span>
            )}
          </div>
        ))}
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
      </div>
    </Card>
  );
}

import { ListChecks } from "lucide-react";
import { Link } from "react-router";

import { ApiError } from "@/api/client";
import { useTasks, useUpdateTask } from "@/api/hooks";
import type { Task } from "@/api/types";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Card, CardHeader, EmptyState, ErrorPanel, buttonClasses } from "@/components/ui";
import { formatMinutes, formatShortDay } from "@/lib/format";

const MAX_TASKS = 5;

/** Open tasks sorted by priority (the API already sorts them). A checkbox marks one as done. */
export function TasksCard() {
  const tasks = useTasks();
  const updateTask = useUpdateTask();
  const open = tasks.data?.filter((t) => t.status === "todo" || t.status === "in_progress").slice(0, MAX_TASKS);

  function complete(task: Task) {
    updateTask.mutate({ id: task.id, status: "done", remaining_min: 0, expected_revision: task.revision });
  }

  return (
    <Card>
      <CardHeader
        title="Tâches prioritaires"
        actions={
          <Link to="/tasks" className={buttonClasses("ghost", "sm")}>
            Toutes les tâches
          </Link>
        }
      />
      <QueryStatus query={tasks} />
      {updateTask.error && (
        <div className="mb-3">
          <ErrorPanel
            title={updateTask.error instanceof ApiError && updateTask.error.isConflict ? "Tâche modifiée entre-temps" : undefined}
            message={updateTask.error.message}
            onRetry={() => void tasks.refetch()}
          />
        </div>
      )}
      {open?.length === 0 && (
        <EmptyState icon={<ListChecks className="size-5" aria-hidden="true" />} title="Aucune tâche en cours" description="Tout est à jour." />
      )}
      {open && open.length > 0 && (
        <ul className="flex flex-col divide-y divide-line">
          {open.map((task) => {
            const saving = updateTask.isPending && updateTask.variables?.id === task.id;
            return (
              <li key={task.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                <input
                  type="checkbox"
                  id={`task-${task.id}`}
                  disabled={saving}
                  onChange={() => complete(task)}
                  className="focus-ring mt-0.5 size-4 shrink-0 accent-(--accent)"
                />
                <div className="min-w-0 flex-1">
                  <label htmlFor={`task-${task.id}`} className="font-medium">
                    {task.title}
                    <span className="sr-only"> — marquer comme terminée</span>
                  </label>
                  <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                    {task.status === "in_progress" && <Badge tone="accent">En cours</Badge>}
                    {task.remaining_min !== null && task.remaining_min > 0 && <span>Reste {formatMinutes(task.remaining_min)}</span>}
                    {task.deadline_at && <span>Pour le {formatShortDay(task.deadline_at)}</span>}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

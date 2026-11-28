import { ArrowLeft, ArrowRight, CalendarDays, Columns3, List, ListChecks, Plus, Scissors, Trash2 } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router";

import { useDeleteTask, useSubjects, useTasks, useUpdateTask } from "@/api/hooks";
import type { Task, TaskStatus } from "@/api/types";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Button, Card, EmptyState, ErrorPanel, PageHeader } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatMinutes, formatRelative, formatShortDay } from "@/lib/format";
import { TASK_STATUS_LABELS } from "@/lib/labels";

import { TaskCalendar } from "./TaskCalendar";
import { CreateTaskDialog, DecomposeDialog } from "./TaskDialogs";

const COLUMNS: TaskStatus[] = ["todo", "in_progress", "done"];
const PRIORITY = { 1: { text: "Haute", tone: "danger" }, 2: { text: "Normale", tone: "neutral" }, 3: { text: "Basse", tone: "neutral" } } as const;

function TaskMeta({ task }: { task: Task }) {
  const subjects = useSubjects();
  const subject = subjects.data?.find((s) => s.id === task.subject_id);
  const late = task.deadline_at && task.status !== "done" && new Date(task.deadline_at).getTime() < Date.now();
  return (
    <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
      {subject && <span>{subject.title}</span>}
      {task.deadline_at && <span className={late ? "font-medium text-danger" : ""}>{late ? `En retard (${formatRelative(task.deadline_at)})` : `Pour le ${formatShortDay(task.deadline_at)}`}</span>}
      {task.remaining_min !== null && task.status !== "done" && <span>Reste {formatMinutes(task.remaining_min)}</span>}
    </p>
  );
}

function RemainingInput({ task }: { task: Task }) {
  const update = useUpdateTask();
  const [value, setValue] = useState(String(task.remaining_min ?? ""));
  return (
    <input
      aria-label={`Temps restant pour « ${task.title} » (minutes)`}
      type="number"
      min={0}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => {
        const minutes = value === "" ? null : Number(value);
        if (minutes !== task.remaining_min) update.mutate({ id: task.id, remaining_min: minutes, expected_revision: task.revision });
      }}
      className="focus-ring h-8 w-20 rounded-md border border-line bg-surface px-2 text-right text-sm"
    />
  );
}

/** Tasks as a list or a Kanban board. Moving a card uses buttons, so it works with the keyboard. */
export function TasksPage() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("view");
  const view = requested === "kanban" || requested === "calendar" ? requested : "list";
  const tasks = useTasks();
  const update = useUpdateTask();
  const remove = useDeleteTask();
  const [creating, setCreating] = useState(false);
  const [decomposing, setDecomposing] = useState(false);
  const [showDone, setShowDone] = useState(false);

  const move = (task: Task, status: TaskStatus) => update.mutate({ id: task.id, status, ...(status === "done" ? { remaining_min: 0 } : {}), expected_revision: task.revision });
  const visible = tasks.data?.filter((t) => t.status !== "cancelled" && (showDone || t.status !== "done"));

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Mes tâches"
        icon={<ListChecks className="size-5" aria-hidden="true" />}
        description="Le temps restant de chaque tâche est pris en compte par le planning."
        actions={
          <>
            <Button variant="secondary" onClick={() => setDecomposing(true)}>
              <Scissors className="size-4" aria-hidden="true" />
              Découper un projet
            </Button>
            <Button onClick={() => setCreating(true)}>
              <Plus className="size-4" aria-hidden="true" />
              Nouvelle tâche
            </Button>
          </>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label="Affichage" className="inline-flex rounded-lg border border-line p-0.5">
          {(
            [
              ["list", "Liste", List],
              ["kanban", "Kanban", Columns3],
              ["calendar", "Calendrier", CalendarDays],
            ] as const
          ).map(([id, label, Icon]) => (
            <button key={id} type="button" aria-pressed={view === id} onClick={() => setParams(id === "list" ? {} : { view: id }, { replace: true })} className={cn("focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm", view === id ? "bg-accent text-accent-ink" : "text-muted hover:text-ink")}>
              <Icon className="size-4" aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>
        {view === "list" && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} className="focus-ring size-4 accent-(--accent)" />
            Afficher les tâches terminées
          </label>
        )}
      </div>

      {update.error && <ErrorPanel message={update.error.message} onRetry={() => void tasks.refetch()} />}
      <QueryStatus query={tasks} />
      {tasks.data?.length === 0 && <EmptyState title="Aucune tâche" description="Ajoutez une tâche ou découpez un projet en étapes." />}

      {view === "list" && visible && visible.length > 0 && (
        <Card>
          <ul className="flex flex-col divide-y divide-line">
            {visible.map((task) => (
              <li key={task.id} className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
                <input type="checkbox" aria-label={`Marquer « ${task.title} » comme terminée`} checked={task.status === "done"} onChange={(e) => move(task, e.target.checked ? "done" : "todo")} className="focus-ring size-4 accent-(--accent)" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className={cn("font-medium", task.status === "done" && "text-muted line-through")}>{task.title}</p>
                    <Badge tone={PRIORITY[task.priority as 1 | 2 | 3]?.tone ?? "neutral"}>{PRIORITY[task.priority as 1 | 2 | 3]?.text ?? `Priorité ${task.priority}`}</Badge>
                    {task.status === "in_progress" && <Badge tone="accent">En cours</Badge>}
                  </div>
                  <TaskMeta task={task} />
                </div>
                {task.status !== "done" && <RemainingInput key={task.revision} task={task} />}
                <button type="button" onClick={() => remove.mutate(task.id)} className="focus-ring rounded p-1.5 text-muted hover:text-danger" aria-label={`Supprimer « ${task.title} »`}>
                  <Trash2 className="size-4" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {view === "kanban" && tasks.data && (
        <div className="grid gap-4 md:grid-cols-3">
          {COLUMNS.map((column, columnIndex) => {
            const items = tasks.data.filter((t) => t.status === column);
            return (
              <section key={column} aria-label={TASK_STATUS_LABELS[column]} className="flex flex-col gap-2 rounded-xl bg-surface-2/60 p-3">
                <h2 className="text-sm font-semibold">
                  {TASK_STATUS_LABELS[column]} <span className="font-normal text-muted">({items.length})</span>
                </h2>
                {items.map((task) => (
                  <article key={task.id} className="rounded-lg border border-line bg-surface p-3">
                    <p className="text-sm font-medium">{task.title}</p>
                    <TaskMeta task={task} />
                    <div className="mt-2 flex justify-between">
                      {columnIndex > 0 ? (
                        <Button size="sm" variant="ghost" onClick={() => move(task, COLUMNS[columnIndex - 1])} aria-label={`Déplacer « ${task.title} » vers ${TASK_STATUS_LABELS[COLUMNS[columnIndex - 1]]}`}>
                          <ArrowLeft className="size-4" aria-hidden="true" />
                        </Button>
                      ) : (
                        <span />
                      )}
                      {columnIndex < COLUMNS.length - 1 && (
                        <Button size="sm" variant="ghost" onClick={() => move(task, COLUMNS[columnIndex + 1])} aria-label={`Déplacer « ${task.title} » vers ${TASK_STATUS_LABELS[COLUMNS[columnIndex + 1]]}`}>
                          <ArrowRight className="size-4" aria-hidden="true" />
                        </Button>
                      )}
                    </div>
                  </article>
                ))}
              </section>
            );
          })}
        </div>
      )}

      {view === "calendar" && tasks.data && <TaskCalendar tasks={tasks.data} />}

      <CreateTaskDialog open={creating} onClose={() => setCreating(false)} />
      <DecomposeDialog open={decomposing} onClose={() => setDecomposing(false)} />
    </section>
  );
}

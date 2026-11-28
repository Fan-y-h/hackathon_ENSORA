import { useState } from "react";

import { errorMessage } from "../../api/client";
import { useCreateTask, useDeleteTask, useTasks, useUpdateTask } from "../../api/queries";
import type { Task } from "../../api/types";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Card, EmptyState, PageHeader, QueryState, SubjectBadge } from "../../components/ui";
import { countdownLabel, formatDuration, formatLongDate, dayKey, formatTimeInput, localDateTimeToIso } from "../../lib/time";
import "./tasks.css";

const COLUMNS: { status: Task["status"]; label: string }[] = [
  { status: "todo", label: "À faire" },
  { status: "in_progress", label: "En cours" },
  { status: "done", label: "Terminé" },
];

export function TasksPage() {
  const tasks = useTasks();
  const [view, setView] = useState<"list" | "kanban">("kanban");

  return (
    <div className="page">
      <PageHeader
        title="Mes tâches"
        actions={
          <div className="segmented" role="group" aria-label="Affichage">
            <button type="button" aria-pressed={view === "kanban"} onClick={() => setView("kanban")}>
              Kanban
            </button>
            <button type="button" aria-pressed={view === "list"} onClick={() => setView("list")}>
              Liste
            </button>
          </div>
        }
      />
      <div className="split">
        <div className="split__main">
          <QueryState query={tasks} lines={6}>
            {(page) => {
              const items = page.items.filter((t) => t.status !== "cancelled" && !t.parent_task_id);
              if (items.length === 0) return <Card><EmptyState title="Aucune tâche" /></Card>;
              return view === "kanban" ? (
                <div className="kanban">
                  {COLUMNS.map((col) => (
                    <section key={col.status} className="kanban__col" aria-label={col.label}>
                      <h2 className="card__title">
                        {col.label} <span className="muted small">({items.filter((t) => t.status === col.status).length})</span>
                      </h2>
                      {items
                        .filter((t) => t.status === col.status)
                        .map((t) => (
                          <TaskCard key={t.id} task={t} />
                        ))}
                    </section>
                  ))}
                </div>
              ) : (
                <Card>
                  <div className="stack">
                    {items
                      .sort((a, b) => (a.deadline_at ?? "9") < (b.deadline_at ?? "9") ? -1 : 1)
                      .map((t) => (
                        <TaskCard key={t.id} task={t} />
                      ))}
                  </div>
                </Card>
              );
            }}
          </QueryState>
        </div>
        <aside className="split__side">
          <TaskForm />
        </aside>
      </div>
    </div>
  );
}

function TaskCard({ task }: { task: Task }) {
  const subjects = useSubjects();
  const update = useUpdateTask();
  const remove = useDeleteTask();
  const [editing, setEditing] = useState(false);
  const next = task.status === "todo" ? "in_progress" : task.status === "in_progress" ? "done" : "todo";
  const nextLabel = task.status === "todo" ? "Commencer" : task.status === "in_progress" ? "Terminer" : "Rouvrir";
  const error = update.error ?? remove.error;

  return (
    <article className={`task${task.overdue ? " task--overdue" : ""}`} style={{ borderLeftColor: subjects.color(task.subject_id) }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <strong className="small" style={{ flex: 1 }}>
          <Icon name="checkSquare" size={14} /> {task.title}
        </strong>
        <SubjectBadge acronym={subjects.acronym(task.subject_id)} color={subjects.color(task.subject_id)} />
      </div>
      <span className="xsmall muted">
        {task.deadline_at ? `${countdownLabel(task.deadline_at, new Date(), subjects.timezone)} · ${formatLongDate(dayKey(task.deadline_at, subjects.timezone))}` : "Sans échéance"} · reste{" "}
        {formatDuration(task.remaining_min)} · priorité {task.priority}/5
      </span>
      {task.description && <p className="small">{task.description}</p>}
      {task.overdue && (
        <span className="xsmall" style={{ color: "var(--critical-text)" }}>
          <Icon name="alert" size={12} /> En retard
        </span>
      )}
      {error && <span className="xsmall" style={{ color: "var(--critical-text)" }}>{errorMessage(error)}</span>}
      <div className="row">
        <button
          type="button"
          className="btn btn--small"
          disabled={update.isPending}
          onClick={() => update.mutate({ id: task.id, patch: { status: next, expected_revision: task.revision, ...(next === "done" ? { remaining_min: 0 } : {}) } })}
        >
          {nextLabel}
        </button>
        <button className="btn btn--small" type="button" onClick={() => setEditing((value) => !value)}>{editing ? "Fermer l’édition" : "Modifier"}</button>
        <button
          type="button"
          className="btn btn--small btn--ghost btn--danger"
          aria-label={`Supprimer ${task.title}`}
          disabled={remove.isPending}
          onClick={() => window.confirm(`Supprimer « ${task.title} » ?`) && remove.mutate({ id: task.id, revision: task.revision })}
        >
          <Icon name="trash" size={14} />
        </button>
      </div>
      {editing && <TaskForm task={task} close={() => setEditing(false)} />}
    </article>
  );
}

function TaskForm({ task, close }: { task?: Task; close?: () => void }) {
  const create = useCreateTask();
  const subjects = useSubjects();
  const update = useUpdateTask();
  const [title, setTitle] = useState(task?.title ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [subject, setSubject] = useState(task?.subject_id ?? "");
  const [deadline, setDeadline] = useState(task?.deadline_at ? `${dayKey(task.deadline_at, subjects.timezone)}T${formatTimeInput(task.deadline_at, subjects.timezone)}` : "");
  const [estimate, setEstimate] = useState(task?.estimated_min ?? 60);
  const [priority, setPriority] = useState(task?.priority ?? 3);
  const error = task ? update.error : create.error;

  return (
    <Card title={task ? "Modifier la tâche" : "Nouvelle tâche"}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          const [day, time] = deadline.split("T");
          const details = {
            title: title.trim(),
            description: description.trim() || null,
            subject_id: subject || null,
            deadline_at: deadline ? localDateTimeToIso(day, time, subjects.timezone) : null,
            estimated_min: estimate,
            priority,
          };
          if (task) {
            const completed = Math.max(0, (task.estimated_min ?? task.remaining_min ?? 0) - (task.remaining_min ?? 0));
            update.mutate({ id: task.id, patch: {
              ...details,
              expected_revision: task.revision,
              ...(estimate !== task.estimated_min && task.status !== "done" ? { remaining_min: Math.max(0, estimate - completed) } : {}),
            } }, { onSuccess: close });
          } else {
            create.mutate({ ...details, remaining_min: estimate, locked: false, splittable: true }, {
              onSuccess: () => { setTitle(""); setDescription(""); },
            });
          }
        }}
      >
        <label className="field">
          <span>Intitulé</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300} />
        </label>
        <label className="field">
          <span>Description</span>
          <textarea className="input" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={2000} />
        </label>
        <label className="field">
          <span>Matière</span>
          <select className="select" value={subject} onChange={(e) => setSubject(e.target.value)}>
            <option value="">—</option>
            {subjects.ids.map((id) => (
              <option key={id} value={id}>
                {subjects.name(id)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Échéance ({subjects.timezone})</span>
          <input className="input" type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
        </label>
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label className="field">
            <span>Durée estimée (min)</span>
            <input className="input" type="number" min={5} max={6000} step={5} value={estimate} onChange={(e) => setEstimate(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Priorité (1–5)</span>
            <input className="input" type="number" min={1} max={5} value={priority} onChange={(e) => setPriority(Number(e.target.value))} />
          </label>
        </div>
        {error && <div className="status status--critical" role="alert">{errorMessage(error)}</div>}
        <button type="submit" className="btn btn--primary" disabled={create.isPending || update.isPending || !title.trim()}>
          {task ? "Enregistrer" : <><Icon name="plus" /> Ajouter</>}
        </button>
      </form>
    </Card>
  );
}

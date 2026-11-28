import { useState } from "react";

import { errorMessage } from "../../api/client";
import { useCreateTask, useDeleteTask, useTasks, useUpdateTask } from "../../api/queries";
import type { Task } from "../../api/types";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Card, EmptyState, PageHeader, QueryState, SubjectBadge } from "../../components/ui";
import { countdownLabel, formatDuration, formatLongDate, dayKey } from "../../lib/time";
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
        eyebrow="Mes tâches"
        title="Devoirs, projets et tâches personnelles"
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
              if (items.length === 0) return <Card><EmptyState title="Aucune tâche">Ajoutez votre première tâche à droite.</EmptyState></Card>;
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
          <NewTask />
        </aside>
      </div>
    </div>
  );
}

function TaskCard({ task }: { task: Task }) {
  const subjects = useSubjects();
  const update = useUpdateTask();
  const remove = useDeleteTask();
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
    </article>
  );
}

function NewTask() {
  const create = useCreateTask();
  const subjects = useSubjects();
  const [title, setTitle] = useState("");
  const [subject, setSubject] = useState("");
  const [deadline, setDeadline] = useState("");
  const [estimate, setEstimate] = useState(60);
  const [priority, setPriority] = useState(3);

  return (
    <Card title="Nouvelle tâche">
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(
            {
              title: title.trim(),
              subject_id: subject || null,
              deadline_at: deadline ? new Date(deadline).toISOString() : null,
              estimated_min: estimate,
              remaining_min: estimate,
              priority,
              locked: false,
              splittable: true,
            },
            { onSuccess: () => setTitle("") },
          );
        }}
      >
        <label className="field">
          <span>Intitulé</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300} />
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
          <span>Échéance</span>
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
        {create.error && <div className="status status--critical">{errorMessage(create.error)}</div>}
        <button type="submit" className="btn btn--primary" disabled={create.isPending || !title.trim()}>
          <Icon name="plus" /> Ajouter
        </button>
        <span className="xsmall muted">La tâche sera répartie dans vos créneaux libres au prochain calcul du planning.</span>
      </form>
    </Card>
  );
}

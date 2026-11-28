import { useEffect, useState } from "react";

import { ApiError, errorMessage } from "../../api/client";
import { useCreateNotebook, useErrors, useNotebooks, useNotes, useSaveNote } from "../../api/queries";
import type { Note } from "../../api/types";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Card, EmptyState, PageHeader, QueryState, SubjectBadge } from "../../components/ui";
import "./notes.css";

/**
 * Smart Notes (lite): notebooks and Markdown notes with server-side version history.
 * Notes are shown as plain text: no HTML from user or model content is ever injected.
 */
export function NotesPage() {
  const notebooks = useNotebooks();
  const createNotebook = useCreateNotebook();
  const subjects = useSubjects();
  const [notebookId, setNotebookId] = useState<string>("");
  const [editing, setEditing] = useState<Note | "new" | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const current = notebookId || notebooks.data?.[0]?.id || "";
  const notes = useNotes(current || undefined);
  const errors = useErrors();

  return (
    <div className="page">
      <PageHeader eyebrow="Mes notes" title="Cahiers et carnet d'erreurs" />
      <div className="notes-layout">
        <Card title="Cahiers">
          <QueryState query={notebooks}>
            {(list) => (
              <nav aria-label="Cahiers" className="stack" style={{ gap: 4 }}>
                {list.length === 0 && <p className="small muted">Aucun cahier.</p>}
                {list.map((nb) => (
                  <button
                    key={nb.id}
                    type="button"
                    className={`notes-nav${nb.id === current ? " notes-nav--on" : ""}`}
                    aria-current={nb.id === current ? "true" : undefined}
                    onClick={() => (setNotebookId(nb.id), setEditing(null))}
                  >
                    <SubjectBadge acronym={subjects.acronym(nb.subject_id)} color={subjects.color(nb.subject_id)} />
                    {nb.title}
                  </button>
                ))}
              </nav>
            )}
          </QueryState>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              if (newTitle.trim()) createNotebook.mutate({ title: newTitle.trim() }, { onSuccess: (nb) => (setNewTitle(""), setNotebookId(nb.id)) });
            }}
          >
            <input className="input" style={{ flex: 1 }} placeholder="Nouveau cahier" aria-label="Titre du nouveau cahier" value={newTitle} onChange={(e) => setNewTitle(e.target.value)} />
            <button type="submit" className="btn" aria-label="Créer le cahier">
              <Icon name="plus" />
            </button>
          </form>
        </Card>

        <div className="stack">
          {editing ? (
            <NoteEditor key={editing === "new" ? "new" : editing.id} notebookId={current} note={editing === "new" ? null : editing} onDone={() => setEditing(null)} />
          ) : (
            <Card title="Notes" aside={current && <button type="button" className="btn btn--small" onClick={() => setEditing("new")}><Icon name="plus" /> Nouvelle note</button>}>
              {!current ? (
                <EmptyState title="Créez un cahier pour commencer" />
              ) : (
                <QueryState query={notes}>
                  {(list) =>
                    list.length === 0 ? (
                      <EmptyState title="Cahier vide" />
                    ) : (
                      <ul className="list">
                        {list.map((n) => (
                          <li key={n.id}>
                            <button type="button" className="notes-item" onClick={() => setEditing(n)}>
                              <strong>{n.title}</strong>
                              <span className="xsmall muted">{n.markdown.slice(0, 140) || "(vide)"}</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )
                  }
                </QueryState>
              )}
            </Card>
          )}
        </div>

        <Card title="Carnet d'erreurs" subtitle="Les erreurs récurrentes deviennent des séances de consolidation.">
          <QueryState query={errors}>
            {(list) =>
              list.length === 0 ? (
                <p className="small muted">Aucune erreur enregistrée. Les erreurs des quiz en mode examen s'ajoutent ici.</p>
              ) : (
                <ul className="list">
                  {list.slice(0, 12).map((e) => (
                    <li key={e.id} style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                      <strong className="small">{e.concept_id ?? "Notion non précisée"}</strong>
                      <span className="xsmall muted">
                        {e.category} · {e.occurrences ?? 1} fois{e.resolved_at ? " · résolue" : ""}{e.recurring ? " · récurrente" : ""}
                      </span>
                      {e.prompt_excerpt && <span className="xsmall">{e.prompt_excerpt}</span>}
                    </li>
                  ))}
                </ul>
              )
            }
          </QueryState>
        </Card>
      </div>
    </div>
  );
}

function NoteEditor({ notebookId, note, onDone }: { notebookId: string; note: Note | null; onDone: () => void }) {
  const save = useSaveNote();
  const [title, setTitle] = useState(note?.title ?? "");
  const [markdown, setMarkdown] = useState(note?.markdown ?? "");
  const [saved, setSaved] = useState<Note | null>(note);
  const conflict = save.error instanceof ApiError && save.error.isConflict;

  useEffect(() => setSaved(note), [note]);

  return (
    <Card title={note ? "Modifier la note" : "Nouvelle note"} aside={saved && `version ${saved.revision}`}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(
            { id: saved?.id, notebookId, title: title.trim() || "Sans titre", markdown, revision: saved?.revision },
            { onSuccess: (n) => setSaved(n) },
          );
        }}
      >
        <label className="field">
          <span>Titre</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
        </label>
        <label className="field">
          <span>Contenu (Markdown, formules LaTeX entre $…$)</span>
          <textarea className="textarea notes-editor" value={markdown} onChange={(e) => setMarkdown(e.target.value)} spellCheck />
        </label>
        {save.error && (
          <div className="status status--critical">
            {conflict ? "Cette note a été modifiée ailleurs. Rechargez-la avant d'enregistrer." : errorMessage(save.error)}
          </div>
        )}
        <div className="row">
          <button type="submit" className="btn btn--primary" disabled={save.isPending}>
            {save.isPending ? "Enregistrement…" : "Enregistrer"}
          </button>
          <button type="button" className="btn" onClick={onDone}>
            Fermer
          </button>
          {save.isSuccess && <span className="small" style={{ color: "var(--good-text)" }}>Enregistré · historique conservé</span>}
        </div>
      </form>
    </Card>
  );
}

import { CheckCircle2, NotebookPen, Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { ApiError } from "@/api/client";
import { useConcepts, useSubjects } from "@/api/hooks";
import { useCreateNote, useDeleteNote, useErrors, useNotes, useResolveError, useUpdateNote } from "@/api/hooksWorkspace";
import type { ErrorEntry, Note } from "@/api/typesWorkspace";
import { CitationList } from "@/components/Citations";
import { Markdown } from "@/components/Markdown";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorPanel, PageHeader, SelectField, TabPanel, Tabs, buttonClasses, type TabItem } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";

import { NoteToolbar, RestructurePanel } from "./NoteTools";

function NoteEditor({ note }: { note: Note }) {
  const subjects = useSubjects();
  const update = useUpdateNote();
  const remove = useDeleteNote();
  const [title, setTitle] = useState(note.title);
  const [markdown, setMarkdown] = useState(note.markdown);
  const [subjectId, setSubjectId] = useState(note.subject_id ?? "");
  const [preview, setPreview] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const dirty = title !== note.title || markdown !== note.markdown || subjectId !== (note.subject_id ?? "");
  const conflict = update.error instanceof ApiError && update.error.isConflict;
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  return (
    <Card className="flex flex-col gap-3">
      <label htmlFor="note-title" className="sr-only">
        Titre de la note
      </label>
      <input id="note-title" value={title} onChange={(e) => setTitle(e.target.value)} className="focus-ring rounded-md border border-transparent bg-transparent px-1 text-lg font-semibold hover:border-line" />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="w-56">
          <SelectField label="Matière" value={subjectId} onChange={(e) => setSubjectId(e.target.value)} options={[{ value: "", label: "Aucune" }, ...(subjects.data ?? []).map((s) => ({ value: s.id, label: s.title }))]} />
        </div>
        <div role="group" aria-label="Mode d'édition" className="inline-flex rounded-lg border border-line p-0.5">
          {(
            [
              [false, "Écrire"],
              [true, "Aperçu"],
            ] as const
          ).map(([value, label]) => (
            <button key={label} type="button" aria-pressed={preview === value} onClick={() => setPreview(value)} className={cn("focus-ring rounded-md px-3 py-1 text-sm", preview === value ? "bg-accent text-accent-ink" : "text-muted")}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {preview ? (
        <div className="min-h-64 rounded-lg border border-line p-4">
          <Markdown>{markdown || "_Note vide._"}</Markdown>
        </div>
      ) : (
        <>
          <label htmlFor="note-body" className="sr-only">
            Contenu (Markdown et LaTeX)
          </label>
          <NoteToolbar textareaRef={bodyRef} value={markdown} onChange={setMarkdown} />
          <textarea ref={bodyRef} id="note-body" value={markdown} onChange={(e) => setMarkdown(e.target.value)} rows={16} className="focus-ring w-full rounded-lg border border-line bg-surface p-3 font-mono text-sm" placeholder="Markdown : ## Titre, **gras**, - liste ; formules : $\alpha$, $$\bar X_n$$" />
        </>
      )}
      <CitationList citations={note.citations} />
      <RestructurePanel
        noteId={note.id}
        dirty={dirty}
        onApply={(proposal) => {
          setMarkdown(proposal);
          setPreview(true);
        }}
      />
      {update.error && <ErrorPanel title={conflict ? "Note modifiée ailleurs" : undefined} message={update.error.message} />}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted" aria-live="polite">
          {update.isPending ? "Enregistrement…" : dirty ? "Modifications non enregistrées" : `Enregistrée ${formatRelative(note.updated_at)}`}
        </span>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => setConfirming(true)} aria-label="Supprimer la note">
            <Trash2 className="size-4" aria-hidden="true" />
          </Button>
          <Button size="sm" disabled={!dirty} loading={update.isPending} onClick={() => update.mutate({ id: note.id, title, markdown, subject_id: subjectId || null, expected_revision: note.revision })}>
            Enregistrer
          </Button>
        </div>
      </div>
      <ConfirmDialog open={confirming} tone="danger" title="Supprimer cette note ?" confirmLabel="Supprimer" loading={remove.isPending} onConfirm={() => remove.mutate(note.id, { onSuccess: () => setConfirming(false) })} onCancel={() => setConfirming(false)} />
    </Card>
  );
}

const CATEGORY: Record<ErrorEntry["category"], string> = { calculation: "Calcul", reasoning: "Raisonnement", definition: "Définition", method: "Méthode", code: "Code", other: "Autre" };

function ErrorNotebook() {
  const errors = useErrors();
  const resolve = useResolveError();
  const subjects = useSubjects();
  const [showResolved, setShowResolved] = useState(false);
  const list = errors.data?.filter((e) => showResolved || !e.resolved_at);

  return (
    <div className="flex flex-col gap-3">
      <label className="flex items-center gap-2 self-end text-sm">
        <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} className="focus-ring size-4 accent-(--accent)" />
        Afficher les erreurs corrigées
      </label>
      <QueryStatus query={errors} />
      {list?.length === 0 && <EmptyState icon={<CheckCircle2 className="size-5" aria-hidden="true" />} title="Aucune erreur en attente" description="Les erreurs de vos quiz et examens blancs s'ajoutent ici automatiquement." />}
      {list?.map((entry) => (
        <ErrorItem key={entry.id} entry={entry} subjectTitle={subjects.data?.find((s) => s.id === entry.subject_id)?.title} onToggle={() => resolve.mutate({ id: entry.id, resolved: !entry.resolved_at })} />
      ))}
    </div>
  );
}

function ErrorItem({ entry, subjectTitle, onToggle }: { entry: ErrorEntry; subjectTitle?: string; onToggle: () => void }) {
  const concepts = useConcepts(entry.subject_id ?? "");
  const concept = concepts.data?.find((c) => c.id === entry.concept_id);
  return (
    <Card className={entry.resolved_at ? "opacity-70" : ""}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="warning">{CATEGORY[entry.category]}</Badge>
        {subjectTitle && <span className="text-xs text-muted">{subjectTitle}</span>}
        {concept && <Badge>{concept.label}</Badge>}
        <span className="ml-auto text-xs text-muted">{formatRelative(entry.occurred_at)}</span>
      </div>
      <p className="mt-2 text-sm">{entry.description}</p>
      {entry.remediation && <p className="mt-1 text-sm text-muted">À retenir : {entry.remediation}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant={entry.resolved_at ? "ghost" : "secondary"} onClick={onToggle}>
          {entry.resolved_at ? "Rouvrir" : "Marquer comme corrigée"}
        </Button>
        {!entry.resolved_at && (
          <Link to="/quiz" className={buttonClasses("ghost", "sm")}>
            S'entraîner
          </Link>
        )}
      </div>
    </Card>
  );
}

type TabId = "notes" | "errors";
const TABS: TabItem<TabId>[] = [
  { id: "notes", label: "Mes notes" },
  { id: "errors", label: "Carnet d'erreurs" },
];

export function NotesPage() {
  const [params, setParams] = useSearchParams();
  const tab: TabId = params.get("tab") === "errors" ? "errors" : "notes";
  const noteId = params.get("note");
  const notes = useNotes();
  const create = useCreateNote();
  const selected = notes.data?.find((n) => n.id === noteId) ?? notes.data?.[0];

  useEffect(() => {
    if (tab === "notes" && !noteId && notes.data?.[0]) setParams({ note: notes.data[0].id }, { replace: true });
  }, [tab, noteId, notes.data, setParams]);

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader title="Mes notes" icon={<NotebookPen className="size-5" aria-hidden="true" />} description="Vos notes en Markdown et LaTeX, les réponses du tuteur enregistrées et votre carnet d'erreurs." />
      <Tabs tabs={TABS} active={tab} label="Sections des notes" idPrefix="notes" onChange={(id) => setParams(id === "notes" ? {} : { tab: id }, { replace: true })} />
      <TabPanel idPrefix="notes" tabId={tab}>
        {tab === "errors" ? (
          <ErrorNotebook />
        ) : (
          <div className="grid items-start gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
            <div className="flex flex-col gap-2">
              <Button variant="secondary" size="sm" loading={create.isPending} onClick={() => create.mutate({ title: "Nouvelle note", markdown: "" }, { onSuccess: (n) => setParams({ note: n.id }, { replace: true }) })}>
                <Plus className="size-4" aria-hidden="true" />
                Nouvelle note
              </Button>
              <QueryStatus query={notes} />
              <ul className="flex flex-col gap-1">
                {notes.data?.map((note) => (
                  <li key={note.id}>
                    <button type="button" onClick={() => setParams({ note: note.id }, { replace: true })} aria-current={note.id === selected?.id || undefined} className={cn("focus-ring w-full rounded-md px-3 py-2 text-left text-sm", note.id === selected?.id ? "bg-accent-soft" : "hover:bg-surface-2")}>
                      <span className="block truncate font-medium">{note.title}</span>
                      <span className="text-xs text-muted">{formatRelative(note.updated_at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
            {selected ? <NoteEditor key={`${selected.id}:${selected.revision}`} note={selected} /> : notes.data && <EmptyState title="Aucune note" description="Créez une note ou enregistrez une réponse du tuteur." />}
          </div>
        )}
      </TabPanel>
    </section>
  );
}

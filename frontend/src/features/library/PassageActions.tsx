import { Download, MessageSquare, NotebookPen, StickyNote, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import { useAnnotations, useCreateAnnotation, useDeleteAnnotation } from "@/api/hooks";
import { useCreateNote } from "@/api/hooksWorkspace";
import type { Citation, DocumentSummary } from "@/api/types";
import { Button, buttonClasses } from "@/components/ui";
import { locatorLabel } from "@/lib/citations";
import { formatRelative } from "@/lib/format";
import { saveFile } from "@/lib/localFolder";

interface PassageActionsProps {
  document: DocumentSummary;
  citation?: Citation;
  content?: Blob;
}

/** What the student can do with a document or a passage: tutor, notes, annotations, save locally. */
export function PassageActions({ document, citation, content }: PassageActionsProps) {
  const createNote = useCreateNote();
  const annotations = useAnnotations(document.id);
  const createAnnotation = useCreateAnnotation();
  const deleteAnnotation = useDeleteAnnotation(document.id);
  const [annotating, setAnnotating] = useState(false);
  const [comment, setComment] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const tutorLink = `/tutor?document=${document.id}${citation ? `&cite=${citation.citation_id}` : ""}`;

  function submitAnnotation(event: FormEvent) {
    event.preventDefault();
    createAnnotation.mutate(
      {
        document_id: document.id,
        document_version_id: citation?.version_id ?? document.active_version_id,
        locator: citation?.locator ?? { kind: "page", page: 1 },
        excerpt: citation?.excerpt ?? "",
        comment,
      },
      {
        onSuccess: () => {
          setComment("");
          setAnnotating(false);
        },
      },
    );
  }

  async function saveLocally() {
    if (!content) return;
    const extension = content.type === "application/pdf" ? "pdf" : content.type.startsWith("text/") ? "txt" : "bin";
    const where = await saveFile(content, `${document.title.replace(/[\\/:*?"<>|]/g, "-")}.${extension}`);
    setSaved(where === "folder" ? "Enregistré dans votre dossier Ensora+." : "Téléchargé dans votre dossier Téléchargements.");
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Link to={tutorLink} className={buttonClasses("primary", "sm")}>
          <MessageSquare className="size-4" aria-hidden="true" />
          {citation ? "Demander au tuteur sur ce passage" : "Demander au tuteur"}
        </Link>
        {citation && (
          <Button
            size="sm"
            variant="secondary"
            loading={createNote.isPending}
            disabled={createNote.isSuccess}
            onClick={() =>
              createNote.mutate({ title: `${document.title} — ${locatorLabel(citation.locator)}`, subject_id: document.subject_id, markdown: `> ${citation.excerpt}\n\n`, citations: [citation] })
            }
          >
            <NotebookPen className="size-4" aria-hidden="true" />
            {createNote.isSuccess ? "Ajouté à mes notes" : "Ajouter à mes notes"}
          </Button>
        )}
        <Button size="sm" variant="secondary" onClick={() => setAnnotating((v) => !v)} aria-expanded={annotating}>
          <StickyNote className="size-4" aria-hidden="true" />
          Annoter
        </Button>
        {content && (
          <Button size="sm" variant="ghost" onClick={() => void saveLocally()}>
            <Download className="size-4" aria-hidden="true" />
            Enregistrer sur mon ordinateur
          </Button>
        )}
      </div>
      {createNote.isSuccess && (
        <p role="status" className="text-sm">
          Passage ajouté avec sa source.{" "}
          <Link to="/notes" className="text-accent hover:underline">
            Ouvrir Mes notes
          </Link>
        </p>
      )}
      {saved && (
        <p role="status" className="text-sm text-muted">
          {saved}
        </p>
      )}

      {annotating && (
        <form onSubmit={submitAnnotation} className="flex flex-col gap-2 rounded-lg border border-line p-3">
          <label htmlFor="annotation" className="text-sm font-medium">
            Annotation {citation ? `sur ${locatorLabel(citation.locator)}` : "sur le document"}
          </label>
          <textarea id="annotation" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} className="focus-ring rounded-lg border border-line bg-surface p-2 text-sm" placeholder="Votre remarque, une question pour le TD…" />
          {createAnnotation.error && <p className="text-xs text-danger">{createAnnotation.error.message}</p>}
          <Button type="submit" size="sm" className="self-end" loading={createAnnotation.isPending}>
            Enregistrer l'annotation
          </Button>
        </form>
      )}

      {annotations.data && annotations.data.length > 0 && (
        <section aria-label="Mes annotations sur ce document" className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">Mes annotations ({annotations.data.length})</h3>
          <ul className="flex flex-col gap-2">
            {annotations.data.map((a) => (
              <li key={a.id} className="flex items-start gap-2 rounded-lg bg-warning-soft/50 p-2.5 text-sm">
                <div className="min-w-0 flex-1">
                  <p>{a.comment}</p>
                  <p className="mt-0.5 text-xs text-muted">
                    {locatorLabel(a.locator)} · {formatRelative(a.created_at)}
                  </p>
                </div>
                <button type="button" onClick={() => deleteAnnotation.mutate(a.id)} className="focus-ring rounded p-1 text-muted hover:text-danger" aria-label="Supprimer l'annotation">
                  <Trash2 className="size-4" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

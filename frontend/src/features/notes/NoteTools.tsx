import { Code, Heading2, Image, List, Sigma, Sparkles, Table, Bold } from "lucide-react";
import { useState, type RefObject } from "react";

import { useJob } from "@/api/hooks";
import { useNoteProposal, useRestructureNote } from "@/api/hooksWorkspace";
import { Markdown } from "@/components/Markdown";
import { Button, Card, ErrorPanel, JobStatus } from "@/components/ui";

const SNIPPETS = [
  { label: "Titre", icon: Heading2, before: "\n## ", after: "\n", placeholder: "Titre" },
  { label: "Gras", icon: Bold, before: "**", after: "**", placeholder: "texte" },
  { label: "Liste", icon: List, before: "\n- ", after: "", placeholder: "élément" },
  { label: "Formule", icon: Sigma, before: "$$", after: "$$", placeholder: "\\bar X_n = \\frac{1}{n}\\sum_i X_i" },
  { label: "Code", icon: Code, before: "\n```python\n", after: "\n```\n", placeholder: "print('hello')" },
  { label: "Tableau", icon: Table, before: "\n| Colonne 1 | Colonne 2 |\n|---|---|\n| ", after: " | |\n", placeholder: "valeur" },
  { label: "Image (lien)", icon: Image, before: "![", after: "](https://)", placeholder: "description de l'image" },
] as const;

/** Inserts Markdown around the selection of a textarea and keeps the cursor on the placeholder. */
function insert(textarea: HTMLTextAreaElement, value: string, onChange: (v: string) => void, before: string, after: string, placeholder: string) {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = value.slice(start, end) || placeholder;
  const next = value.slice(0, start) + before + selected + after + value.slice(end);
  onChange(next);
  requestAnimationFrame(() => {
    textarea.focus();
    textarea.setSelectionRange(start + before.length, start + before.length + selected.length);
  });
}

export function NoteToolbar({ textareaRef, value, onChange }: { textareaRef: RefObject<HTMLTextAreaElement | null>; value: string; onChange: (v: string) => void }) {
  return (
    <div role="toolbar" aria-label="Mise en forme" className="flex flex-wrap gap-1 border-b border-line pb-2">
      {SNIPPETS.map((s) => (
        <button
          key={s.label}
          type="button"
          title={s.label}
          aria-label={`Insérer : ${s.label}`}
          onClick={() => textareaRef.current && insert(textareaRef.current, value, onChange, s.before, s.after, s.placeholder)}
          className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted hover:bg-surface-2 hover:text-ink"
        >
          <s.icon className="size-4" aria-hidden="true" />
          <span className="hidden sm:inline">{s.label}</span>
        </button>
      ))}
    </div>
  );
}

/** Asks for a restructured version; the student reviews it and decides to apply it (then saves). */
export function RestructurePanel({ noteId, dirty, onApply }: { noteId: string; dirty: boolean; onApply: (markdown: string) => void }) {
  const restructure = useRestructureNote();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const proposal = useNoteProposal(job.data?.status === "succeeded" ? job.data.result_id : null);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" disabled={dirty} loading={restructure.isPending} onClick={() => restructure.mutate(noteId, { onSuccess: (j) => setJobId(j.id) })}>
          <Sparkles className="size-4" aria-hidden="true" />
          Restructurer ma note
        </Button>
        {dirty && <span className="text-xs text-muted">Enregistrez d'abord vos modifications.</span>}
      </div>
      {restructure.error && <ErrorPanel message={restructure.error.message} />}
      {job.data && job.data.status !== "succeeded" && <JobStatus label="Restructuration" state={job.data.status} progress={job.data.progress} error={job.data.error ?? undefined} />}
      {proposal.data && (
        <Card className="border-accent">
          <p className="mb-2 text-sm font-medium">Proposition (rien n'est modifié tant que vous ne l'appliquez pas)</p>
          <div className="max-h-80 overflow-y-auto rounded-lg border border-line p-3">
            <Markdown>{proposal.data.markdown}</Markdown>
          </div>
          <div className="mt-3 flex gap-2">
            <Button
              size="sm"
              onClick={() => {
                onApply(proposal.data.markdown);
                setJobId(null);
              }}
            >
              Appliquer à ma note
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setJobId(null)}>
              Ignorer
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}

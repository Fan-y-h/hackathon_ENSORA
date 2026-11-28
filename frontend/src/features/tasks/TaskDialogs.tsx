import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError, apiFetch } from "@/api/client";
import { useCreateTask, useJob, useSubjects } from "@/api/hooks";
import { useDecomposeTask, useTaskProposal } from "@/api/hooksPlanning";
import { queryKeys } from "@/api/queryKeys";
import type { Task } from "@/api/types";
import { Button, Dialog, JobStatus, SelectField, TextField } from "@/components/ui";

function subjectOptions(subjects: ReturnType<typeof useSubjects>["data"]) {
  return [{ value: "", label: "Aucune" }, ...(subjects ?? []).map((s) => ({ value: s.id, label: s.title }))];
}

/** Date input (local day) -> ISO end of that day. */
function endOfDay(date: string): string | null {
  return date ? new Date(`${date}T23:59`).toISOString() : null;
}

export function CreateTaskDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const subjects = useSubjects();
  const create = useCreateTask();
  const [title, setTitle] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [deadline, setDeadline] = useState("");
  const [estimate, setEstimate] = useState("60");
  const [priority, setPriority] = useState("2");
  const field = create.error instanceof ApiError ? (create.error.details as { field?: string } | undefined)?.field : undefined;

  function close() {
    create.reset();
    setTitle("");
    onClose();
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate({ title, subject_id: subjectId || null, deadline_at: endOfDay(deadline), estimated_min: Number(estimate) || null, priority: Number(priority) }, { onSuccess: close });
  }

  return (
    <Dialog open={open} title="Nouvelle tâche" onClose={close}>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <TextField label="Intitulé" required value={title} onChange={(e) => setTitle(e.target.value)} error={field === "title" ? create.error?.message : undefined} />
        <SelectField label="Matière" value={subjectId} onChange={(e) => setSubjectId(e.target.value)} options={subjectOptions(subjects.data)} />
        <div className="grid grid-cols-2 gap-4">
          <TextField label="Échéance" type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
          <TextField label="Durée estimée (min)" type="number" min={5} value={estimate} onChange={(e) => setEstimate(e.target.value)} />
        </div>
        <SelectField
          label="Priorité"
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
          options={[
            { value: "1", label: "Haute" },
            { value: "2", label: "Normale" },
            { value: "3", label: "Basse" },
          ]}
        />
        {create.error && !field && <p className="text-sm text-danger">{create.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={close}>
            Annuler
          </Button>
          <Button type="submit" loading={create.isPending}>
            Créer
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Paste project instructions -> editable list of steps -> tasks. Nothing is created before the student validates. */
export function DecomposeDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const subjects = useSubjects();
  const decompose = useDecomposeTask();
  const [instructions, setInstructions] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [deadline, setDeadline] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const proposal = useTaskProposal(job.data?.status === "succeeded" ? job.data.result_id : null);
  const [steps, setSteps] = useState<Array<{ title: string; minutes: string; keep: boolean }>>([]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    if (proposal.data) setSteps(proposal.data.steps.map((s) => ({ title: s.title, minutes: String(s.estimated_min), keep: true })));
  }, [proposal.data]);

  function close() {
    setJobId(null);
    setSteps([]);
    setInstructions("");
    setCreateError(null);
    decompose.reset();
    onClose();
  }

  async function createAll() {
    setCreating(true);
    setCreateError(null);
    try {
      for (const [index, step] of steps.filter((s) => s.keep && s.title.trim()).entries()) {
        await apiFetch<Task>("/tasks", {
          method: "POST",
          body: { title: step.title.trim(), subject_id: subjectId || null, estimated_min: Number(step.minutes) || null, deadline_at: endOfDay(deadline), priority: Math.min(3, 1 + Math.floor(index / 3)) },
        });
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
      close();
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : "Création impossible.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <Dialog open={open} title="Découper un projet en étapes" onClose={close}>
      {steps.length === 0 ? (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="instructions" className="text-sm font-medium">
              Consignes du projet
            </label>
            <textarea id="instructions" rows={7} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="Collez ici l'énoncé ou la liste des livrables…" className="focus-ring rounded-lg border border-line bg-surface p-3 text-sm" />
            {decompose.error && <p className="text-xs font-medium text-danger">{decompose.error.message}</p>}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <SelectField label="Matière" value={subjectId} onChange={(e) => setSubjectId(e.target.value)} options={subjectOptions(subjects.data)} />
            <TextField label="Date de rendu" type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
          </div>
          {job.data && job.data.status !== "succeeded" && <JobStatus label="Analyse des consignes" state={job.data.status} progress={job.data.progress} error={job.data.error ?? undefined} />}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={close}>
              Annuler
            </Button>
            <Button loading={decompose.isPending || job.data?.status === "running" || job.data?.status === "queued"} onClick={() => decompose.mutate({ instructions, subject_id: subjectId || null }, { onSuccess: (j) => setJobId(j.id) })}>
              Proposer des étapes
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted">Vérifiez et ajustez les étapes : rien n'est créé avant votre validation.</p>
          <ul className="flex max-h-80 flex-col gap-2 overflow-y-auto">
            {steps.map((step, index) => (
              <li key={index} className="flex items-center gap-2">
                <input type="checkbox" aria-label={`Garder l'étape ${index + 1}`} checked={step.keep} onChange={(e) => setSteps((s) => s.map((x, i) => (i === index ? { ...x, keep: e.target.checked } : x)))} className="focus-ring size-4 accent-(--accent)" />
                <input aria-label={`Intitulé de l'étape ${index + 1}`} value={step.title} onChange={(e) => setSteps((s) => s.map((x, i) => (i === index ? { ...x, title: e.target.value } : x)))} className="focus-ring h-9 flex-1 rounded-md border border-line bg-surface px-2 text-sm" />
                <input aria-label={`Minutes pour l'étape ${index + 1}`} type="number" min={5} value={step.minutes} onChange={(e) => setSteps((s) => s.map((x, i) => (i === index ? { ...x, minutes: e.target.value } : x)))} className="focus-ring h-9 w-20 rounded-md border border-line bg-surface px-2 text-right text-sm" />
              </li>
            ))}
          </ul>
          {createError && <p className="text-sm text-danger">{createError}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={close}>
              Annuler
            </Button>
            <Button loading={creating} onClick={() => void createAll()}>
              Créer {steps.filter((s) => s.keep).length} tâches
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError } from "@/api/client";
import { useImportDocument, useJob } from "@/api/hooks";
import { queryKeys } from "@/api/queryKeys";
import type { Subject } from "@/api/types";
import { Button, Dialog, JobStatus, SelectField } from "@/components/ui";

interface ImportDialogProps {
  open: boolean;
  subjects: Subject[];
  onClose: () => void;
  /** Called with the new (or already existing) document id once the import succeeded. */
  onImported: (documentId: string) => void;
}

/** Imports a local PDF/TXT file. Re-importing the same file does not create a duplicate. */
export function ImportDialog({ open, subjects, onClose, onImported }: ImportDialogProps) {
  const queryClient = useQueryClient();
  const importDocument = useImportDocument();
  const [file, setFile] = useState<File | null>(null);
  const [subjectId, setSubjectId] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);

  const fileError = importDocument.error instanceof ApiError ? importDocument.error.message : importDocument.error?.message;

  useEffect(() => {
    if (job.data?.status === "succeeded") {
      void queryClient.invalidateQueries({ queryKey: queryKeys.allDocuments });
    }
  }, [job.data?.status, queryClient]);

  function close() {
    importDocument.reset();
    setFile(null);
    setJobId(null);
    onClose();
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    importDocument.mutate({ file, subjectId: subjectId || undefined }, { onSuccess: (newJob) => setJobId(newJob.id) });
  }

  const done = job.data?.status === "succeeded" ? job.data : null;

  return (
    <Dialog open={open} title="Importer un document" onClose={close}>
      {!jobId ? (
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="import-file" className="text-sm font-medium">
              Fichier <span className="text-danger" aria-hidden="true">*</span>
            </label>
            <input
              id="import-file"
              type="file"
              accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              aria-describedby="import-file-hint"
              className="focus-ring text-sm file:mr-3 file:rounded-md file:border file:border-line file:bg-surface-2 file:px-3 file:py-1.5 file:text-ink"
            />
            <p id="import-file-hint" className={fileError ? "text-xs font-medium text-danger" : "text-xs text-muted"}>
              {fileError ?? "PDF, TXT ou Markdown. Le fichier reste privé, dans votre espace Ensora+."}
            </p>
          </div>
          <SelectField
            label="Matière"
            value={subjectId}
            onChange={(e) => setSubjectId(e.target.value)}
            hint="Facultatif : sinon la matière sera proposée automatiquement."
            options={[{ value: "", label: "Choisir plus tard" }, ...subjects.map((s) => ({ value: s.id, label: s.title }))]}
          />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={close}>
              Annuler
            </Button>
            <Button type="submit" disabled={!file} loading={importDocument.isPending}>
              Importer
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-col gap-4">
          {job.data && (
            <JobStatus label={`Import de « ${file?.name} »`} state={job.data.status} progress={job.data.progress} error={job.data.error ?? undefined} />
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={close}>
              Fermer
            </Button>
            {done?.result_id && (
              <Button
                onClick={() => {
                  onImported(done.result_id as string);
                  close();
                }}
              >
                Voir le document
              </Button>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}

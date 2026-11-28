import { useQueryClient } from "@tanstack/react-query";
import { FileText, Layers, ListChecks, WandSparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { useJob, useSubjects } from "@/api/hooks";
import { learningKeys, useArtifacts, useGenerateArtifact } from "@/api/hooksLearning";
import type { ArtifactType, GenerationSpec } from "@/api/typesLearning";
import { QueryStatus } from "@/components/QueryStatus";
import { Card, CardHeader, EmptyState, ErrorPanel, JobStatus, PageHeader } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";

import { ArtifactViewer } from "./ArtifactViewer";
import { GenerateForm } from "./GenerateForm";

const TYPE_ICON: Partial<Record<ArtifactType, typeof FileText>> = { revision_sheet: FileText, flashcards: Layers, quiz: ListChecks };

/** Generate revision sheets, flashcards and quizzes from the student's sources, then edit and reuse them. */
export function StudioPage() {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const artifactId = params.get("a");
  const subjects = useSubjects();
  const artifacts = useArtifacts();
  const generate = useGenerateArtifact();
  const [jobId, setJobId] = useState<string | null>(null);
  const [lastSpec, setLastSpec] = useState<GenerationSpec | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const job = useJob(jobId);

  function open(id: string | null) {
    setParams(id ? { a: id } : {}, { replace: true });
  }

  function run(spec: GenerationSpec) {
    setLastSpec(spec);
    setNotice(null);
    generate.mutate(spec, {
      onSuccess: (outcome) => {
        if (outcome.kind === "cached") {
          setNotice("Ce support existait déjà avec ces réglages : il est réutilisé, sans nouvelle génération.");
          open(outcome.artifact.id);
        } else setJobId(outcome.job.id);
      },
    });
  }

  useEffect(() => {
    if (job.data?.status === "succeeded" && job.data.result_id) {
      void queryClient.invalidateQueries({ queryKey: learningKeys.artifacts });
      void queryClient.invalidateQueries({ queryKey: ["decks"] });
      void queryClient.invalidateQueries({ queryKey: ["quizzes"] });
      open(job.data.result_id);
      setJobId(null);
    }
    // open() only changes the URL; safe to leave out of the dependencies.
  }, [job.data?.status, job.data?.result_id, queryClient]);

  const subjectTitle = (id: string) => subjects.data?.find((s) => s.id === id)?.title ?? "";

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Learning Studio"
        icon={<WandSparkles className="size-5" aria-hidden="true" />}
        description="Fiches, flashcards et quiz construits à partir de vos cours, avec leurs sources. Modifiables et réutilisables."
      />

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="flex flex-col gap-5">
          <GenerateForm busy={generate.isPending || job.data?.status === "queued" || job.data?.status === "running"} onGenerate={run} />
          {job.data && job.data.status !== "succeeded" && <JobStatus label="Génération du support" state={job.data.status} progress={job.data.progress} error={job.data.error ?? undefined} />}
          {generate.error && <ErrorPanel message={generate.error.message} />}

          <Card>
            <CardHeader title="Mes supports" />
            <QueryStatus query={artifacts} />
            {artifacts.data?.length === 0 && <EmptyState title="Aucun support pour l'instant" description="Les supports générés sont conservés ici et réutilisables." />}
            <ul className="flex flex-col gap-1.5">
              {artifacts.data?.map((artifact) => {
                const Icon = TYPE_ICON[artifact.type] ?? FileText;
                return (
                  <li key={artifact.id}>
                    <button
                      type="button"
                      onClick={() => open(artifact.id)}
                      aria-current={artifact.id === artifactId || undefined}
                      className={cn("focus-ring flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm", artifact.id === artifactId ? "bg-accent-soft" : "hover:bg-surface-2")}
                    >
                      <Icon className="size-4 shrink-0 text-muted" aria-hidden="true" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{artifact.title}</span>
                        <span className="text-xs text-muted">
                          {subjectTitle(artifact.subject_id)} · {formatRelative(artifact.updated_at)}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </Card>
        </div>

        <div className="flex flex-col gap-3">
          {notice && (
            <p role="status" className="rounded-lg bg-accent-soft px-3 py-2 text-sm">
              {notice}
            </p>
          )}
          {artifactId ? (
            <ArtifactViewer
              key={artifactId}
              artifactId={artifactId}
              onClose={() => open(null)}
              regenerating={generate.isPending}
              onRegenerate={() => lastSpec && run({ ...lastSpec, force_regenerate: true })}
            />
          ) : (
            <EmptyState icon={<WandSparkles className="size-5" aria-hidden="true" />} title="Choisissez ou générez un support" description="Le support s'affichera ici, avec ses sources." />
          )}
        </div>
      </div>
    </section>
  );
}

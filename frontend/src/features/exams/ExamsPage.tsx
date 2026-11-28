import { FileText, ShieldAlert, Timer } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { useDocuments, useSubjects } from "@/api/hooks";
import { useCreateMockExam, useQuizzes, useStartAttempt } from "@/api/hooksLearning";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorPanel, PageHeader, SelectField, TextField } from "@/components/ui";
import { AttemptRunner } from "@/features/quiz/AttemptRunner";

function PastPapers() {
  const exams = useDocuments({ resource_type: "exam" });
  const solutions = useDocuments({ resource_type: "solution" });
  const subjects = useSubjects();

  return (
    <Card>
      <CardHeader title="Annales" description="Sujets des années précédentes et corrigés associés." />
      <QueryStatus query={exams} />
      {exams.data?.length === 0 && <EmptyState title="Aucune annale" description="Les annales apparaissent après synchronisation du Drive Ensora." />}
      <ul className="flex flex-col divide-y divide-line">
        {exams.data?.map((exam) => {
          const solution = solutions.data?.find((s) => s.subject_id === exam.subject_id && s.year === exam.year);
          return (
            <li key={exam.id} className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
              <FileText className="size-4 text-muted" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{exam.title}</p>
                <p className="text-xs text-muted">{subjects.data?.find((s) => s.id === exam.subject_id)?.title}</p>
              </div>
              {exam.year !== null && <Badge>{exam.year}</Badge>}
              <Link to={`/library?doc=${exam.id}`} className="text-sm text-accent hover:underline">
                Sujet
              </Link>
              {solution ? (
                <Link to={`/library?doc=${solution.id}`} className="text-sm text-accent hover:underline">
                  Corrigé
                </Link>
              ) : (
                <span className="text-xs text-muted">Pas de corrigé</span>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/** Past papers and timed mock exams in an isolated mode (no tutor, no answers until submission). */
export function ExamsPage() {
  const [params, setParams] = useSearchParams();
  const attemptId = params.get("attempt");
  const subjects = useSubjects();
  const quizzes = useQuizzes();
  const createExam = useCreateMockExam();
  const start = useStartAttempt();
  const [subjectId, setSubjectId] = useState("");
  const [duration, setDuration] = useState("30");

  function launch(quizId: string) {
    start.mutate(quizId, { onSuccess: (attempt) => setParams({ attempt: attempt.id }, { replace: true }) });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    createExam.mutate({ subject_id: subjectId, duration_min: Number(duration) }, { onSuccess: (quiz) => launch(quiz.id) });
  }

  if (attemptId) {
    // Isolated exam mode: covers the whole app (no menu, no tutor).
    return (
      <div role="dialog" aria-modal="true" aria-label="Examen blanc en cours" className="fixed inset-0 z-50 overflow-y-auto bg-canvas px-4 py-6">
        <p className="mx-auto mb-4 flex max-w-3xl items-center gap-2 rounded-lg bg-warning-soft px-4 py-2.5 text-sm">
          <ShieldAlert className="size-4 shrink-0 text-warning" aria-hidden="true" />
          Mode examen : le tuteur et les corrigés sont indisponibles jusqu'à la remise de la copie.
        </p>
        <AttemptRunner attemptId={attemptId} examMode onExit={() => setParams({}, { replace: true })} />
      </div>
    );
  }

  const mocks = quizzes.data?.filter((q) => q.type === "mock_exam");

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader title="Exam Trainer" icon={<Timer className="size-5" aria-hidden="true" />} description="Entraînez-vous dans les conditions de l'examen." />
      <div className="grid items-start gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader title="Examen blanc" description="Questions tirées de vos quiz de la matière, temps limité, correction à la fin." />
          <form onSubmit={submit} className="grid gap-4 sm:grid-cols-[2fr_1fr_auto] sm:items-end">
            <SelectField label="Matière" required value={subjectId} onChange={(e) => setSubjectId(e.target.value)} options={[{ value: "", label: "Choisir" }, ...(subjects.data ?? []).map((s) => ({ value: s.id, label: s.title }))]} />
            <TextField label="Durée (min)" type="number" min={5} max={240} value={duration} onChange={(e) => setDuration(e.target.value)} />
            <Button type="submit" disabled={!subjectId} loading={createExam.isPending || start.isPending}>
              Commencer
            </Button>
          </form>
          {(createExam.error ?? start.error) && (
            <div className="mt-3">
              <ErrorPanel message={(createExam.error ?? start.error)?.message ?? ""} />
            </div>
          )}
          {mocks && mocks.length > 0 && (
            <>
              <h3 className="mt-5 mb-2 text-sm font-medium">Examens blancs déjà créés</h3>
              <ul className="flex flex-col gap-2">
                {mocks.map((quiz) => (
                  <li key={quiz.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <span>
                      {quiz.title} <span className="text-muted">· {quiz.duration_min} min</span>
                    </span>
                    <Button size="sm" variant="secondary" onClick={() => launch(quiz.id)}>
                      Refaire
                    </Button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
        <PastPapers />
      </div>
    </section>
  );
}

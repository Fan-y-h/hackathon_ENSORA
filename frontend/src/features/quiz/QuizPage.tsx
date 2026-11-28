import { CircleHelp, ListChecks } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router";

import { useSubjects } from "@/api/hooks";
import { useQuizzes, useStartAttempt } from "@/api/hooksLearning";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Button, Card, EmptyState, ErrorPanel, PageHeader, buttonClasses } from "@/components/ui";

import { AttemptRunner } from "./AttemptRunner";

const TYPE_LABEL = { practice: "Entraînement", diagnostic: "Diagnostic", mock_exam: "Examen blanc" } as const;

/** Practice and diagnostic quizzes. Results feed mastery and the error notebook. */
export function QuizPage() {
  const [params, setParams] = useSearchParams();
  const attemptId = params.get("attempt");
  const autoQuiz = params.get("quiz");
  const quizzes = useQuizzes();
  const subjects = useSubjects();
  const start = useStartAttempt();

  function begin(quizId: string) {
    start.mutate(quizId, { onSuccess: (attempt) => setParams({ attempt: attempt.id }, { replace: true }) });
  }

  // Link from Learning Studio (?quiz=...): start that quiz directly, once
  // (React runs effects twice in development, which would create two attempts).
  const autoStarted = useRef<string | null>(null);
  useEffect(() => {
    if (autoQuiz && !attemptId && autoStarted.current !== autoQuiz) {
      autoStarted.current = autoQuiz;
      begin(autoQuiz);
    }
  }, [autoQuiz, attemptId]);

  if (attemptId) {
    return (
      <section aria-label="Quiz en cours">
        <AttemptRunner attemptId={attemptId} onExit={() => setParams({}, { replace: true })} />
      </section>
    );
  }

  const list = quizzes.data?.filter((q) => q.type !== "mock_exam");
  const subjectTitle = (id: string) => subjects.data?.find((s) => s.id === id)?.title ?? "";

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Quiz"
        icon={<CircleHelp className="size-5" aria-hidden="true" />}
        description="Testez-vous : chaque réponse met à jour votre niveau par notion, et les erreurs rejoignent votre carnet."
        actions={
          <Link to="/studio" className={buttonClasses("secondary")}>
            Générer un quiz
          </Link>
        }
      />
      {start.error && <ErrorPanel message={start.error.message} />}
      <QueryStatus query={quizzes} />
      {list?.length === 0 && <EmptyState icon={<ListChecks className="size-5" aria-hidden="true" />} title="Aucun quiz" description="Générez un quiz depuis Learning Studio." />}
      <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {list?.map((quiz) => (
          <li key={quiz.id}>
            <Card className="flex h-full flex-col">
              <p className="text-xs text-muted">{subjectTitle(quiz.subject_id)}</p>
              <h2 className="mt-1 font-semibold">{quiz.title}</h2>
              <div className="mt-3 flex flex-wrap gap-2">
                <Badge tone="accent">{TYPE_LABEL[quiz.type]}</Badge>
                <Badge>{quiz.question_count} questions</Badge>
              </div>
              <Button className="mt-4 self-start" size="sm" loading={start.isPending && start.variables === quiz.id} onClick={() => begin(quiz.id)}>
                Commencer
              </Button>
            </Card>
          </li>
        ))}
      </ul>
    </section>
  );
}

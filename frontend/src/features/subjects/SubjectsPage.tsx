import { BookOpen, CalendarClock, ChevronRight, FileText } from "lucide-react";
import { Link } from "react-router";

import { useDocuments, useEvents, useSubjects } from "@/api/hooks";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, EmptyState, PageHeader } from "@/components/ui";
import { dayRange, formatRelative, formatShortDay } from "@/lib/format";

import { nextAssessment } from "./nextAssessment";

/** Grid of the student's subjects, each with its next assessment and resource count. */
export function SubjectsPage() {
  const subjects = useSubjects();
  const documents = useDocuments();
  const events = useEvents(dayRange(0, 120));

  return (
    <section aria-labelledby="page-title">
      <PageHeader
        title="Mes matières"
        icon={<BookOpen className="size-5" aria-hidden="true" />}
        description="Chaque matière regroupe son programme, ses ressources et votre niveau sur chaque notion."
      />

      <QueryStatus query={subjects} />
      {subjects.data?.length === 0 && (
        <EmptyState
          title="Aucune matière pour l'instant"
          description="Connectez Moodle ou importez vos cours pour voir vos matières apparaître."
          action={
            <Link to="/settings" className="focus-ring text-sm font-medium text-accent hover:underline">
              Gérer les sources
            </Link>
          }
        />
      )}

      {subjects.data && subjects.data.length > 0 && (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {subjects.data.map((subject) => {
            const exam = nextAssessment(events.data, subject.id);
            const count = documents.data?.filter((d) => d.subject_id === subject.id).length;
            return (
              <li key={subject.id}>
                <Link
                  to={`/subjects/${subject.id}`}
                  className="focus-ring group flex h-full flex-col rounded-xl border border-line bg-surface p-5 transition hover:border-accent"
                >
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <div className="flex flex-wrap gap-1.5">
                      {subject.canonical_code && <Badge>{subject.canonical_code}</Badge>}
                      <Badge tone="accent">{subject.cohort}</Badge>
                    </div>
                    <ChevronRight className="size-4 text-muted transition group-hover:translate-x-0.5 group-hover:text-accent" aria-hidden="true" />
                  </div>
                  <h2 className="text-base font-semibold">{subject.title}</h2>

                  <dl className="mt-auto flex flex-col gap-1.5 pt-4 text-sm text-muted">
                    <div className="flex items-center gap-2">
                      <dt>
                        <CalendarClock className="size-4" aria-hidden="true" />
                        <span className="sr-only">Prochaine évaluation</span>
                      </dt>
                      <dd>
                        {exam ? (
                          <>
                            <span className="text-ink">{exam.title}</span> · {formatShortDay(exam.start_at)} ({formatRelative(exam.start_at)})
                            {exam.confirmation_required && <span className="text-warning"> · à confirmer</span>}
                          </>
                        ) : events.isPending ? (
                          "…"
                        ) : (
                          "Aucune évaluation prévue"
                        )}
                      </dd>
                    </div>
                    <div className="flex items-center gap-2">
                      <dt>
                        <FileText className="size-4" aria-hidden="true" />
                        <span className="sr-only">Ressources</span>
                      </dt>
                      <dd>{count === undefined ? "…" : count === 1 ? "1 ressource" : `${count} ressources`}</dd>
                    </div>
                  </dl>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

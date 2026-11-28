import { ArrowLeft, BookOpen, CalendarClock, CircleAlert } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router";

import { ApiError } from "@/api/client";
import { useEvents, useSubject } from "@/api/hooks";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, EmptyState, ErrorPanel, PageHeader, TabPanel, Tabs, buttonClasses, type TabItem } from "@/components/ui";
import { dayRange, formatRelative, formatShortDay, formatTime } from "@/lib/format";

import { nextAssessment } from "./nextAssessment";
import { ProgramTab } from "./ProgramTab";
import { ResourcesTab } from "./ResourcesTab";

type TabId = "program" | "resources" | "notes" | "artifacts" | "results";

const TABS: TabItem<TabId>[] = [
  { id: "program", label: "Programme et niveau" },
  { id: "resources", label: "Ressources" },
  { id: "notes", label: "Notes" },
  { id: "artifacts", label: "Supports générés" },
  { id: "results", label: "Résultats" },
];

/** Tabs whose screen is built later: say so and point to where it will live. */
const LATER: Record<Exclude<TabId, "program" | "resources">, { title: string; to: string; link: string }> = {
  notes: { title: "Vos notes sur cette matière apparaîtront ici", to: "/notes", link: "Ouvrir Mes notes" },
  artifacts: { title: "Fiches, quiz et flashcards générés pour cette matière apparaîtront ici", to: "/studio", link: "Ouvrir Learning Studio" },
  results: { title: "Vos notes d'évaluation pour cette matière apparaîtront ici", to: "/grades", link: "Ouvrir Résultats et objectifs" },
};

function isTab(value: string | null): value is TabId {
  return TABS.some((tab) => tab.id === value);
}

export function SubjectDetailPage() {
  const { subjectId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("tab");
  const active: TabId = isTab(requested) ? requested : "program";

  const subject = useSubject(subjectId);
  const events = useEvents(dayRange(0, 120));
  const exam = nextAssessment(events.data, subjectId);

  const backLink = (
    <Link to="/subjects" className={buttonClasses("ghost", "sm", "-ml-3 mb-2")}>
      <ArrowLeft className="size-4" aria-hidden="true" />
      Mes matières
    </Link>
  );

  if (subject.error instanceof ApiError && subject.error.status === 404) {
    return (
      <section>
        {backLink}
        <ErrorPanel title="Matière introuvable" message="Cette matière n'existe pas ou n'est plus associée à votre compte." />
      </section>
    );
  }
  if (!subject.data) return <QueryStatus query={subject} />;

  return (
    <section aria-labelledby="page-title">
      {backLink}
      <PageHeader
        title={subject.data.title}
        icon={<BookOpen className="size-5" aria-hidden="true" />}
        description={
          <span className="inline-flex flex-wrap gap-1.5">
            {subject.data.canonical_code && <Badge>{subject.data.canonical_code}</Badge>}
            <Badge tone="accent">{subject.data.cohort}</Badge>
            <Badge>{subject.data.academic_year}</Badge>
          </span>
        }
      />

      {exam && (
        <p className="mb-5 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface px-4 py-3 text-sm">
          <CalendarClock className="size-4 text-danger" aria-hidden="true" />
          <span className="font-medium">{exam.title}</span>
          <span className="text-muted">
            {formatShortDay(exam.start_at)} à {formatTime(exam.start_at)} · {formatRelative(exam.start_at)}
          </span>
          {exam.confirmation_required && (
            <Link to="/agenda" className="focus-ring inline-flex items-center gap-1 rounded text-warning hover:underline">
              <CircleAlert className="size-4" aria-hidden="true" />à confirmer
            </Link>
          )}
        </p>
      )}

      <Tabs
        tabs={TABS}
        active={active}
        label="Sections de la matière"
        idPrefix="subject"
        // The tab lives in the URL (?tab=resources): reload and sharing keep it.
        onChange={(id) => setSearchParams(id === "program" ? {} : { tab: id }, { replace: true })}
      />
      <TabPanel idPrefix="subject" tabId={active}>
        {active === "program" && <ProgramTab subjectId={subjectId} />}
        {active === "resources" && <ResourcesTab subjectId={subjectId} />}
        {active !== "program" && active !== "resources" && (
          <EmptyState
            title={LATER[active].title}
            description="Cette section sera disponible prochainement."
            action={
              <Link to={LATER[active].to} className={buttonClasses("secondary", "sm")}>
                {LATER[active].link}
              </Link>
            }
          />
        )}
      </TabPanel>
    </section>
  );
}

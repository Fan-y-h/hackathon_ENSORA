import { ListTree } from "lucide-react";

import { useChapters, useConcepts, useMastery } from "@/api/hooks";
import type { Mastery, Uuid } from "@/api/types";
import { QueryStatus } from "@/components/QueryStatus";
import { Card, EmptyState, MasteryIndicator } from "@/components/ui";
import { formatRelative } from "@/lib/format";
import { DIMENSION_LABELS } from "@/lib/labels";

/** Below this confidence the estimate rests on too few answers (docs/ADAPTIVE_LEARNING.md). */
const LOW_CONFIDENCE = 0.4;

function NextReview({ records }: { records: Mastery[] }) {
  // Earliest due review among the concept's dimensions.
  const due = records
    .map((r) => r.next_due_at)
    .filter((d): d is string => d !== null)
    .sort()[0];
  if (!due) return null;
  const delayMs = Date.now() - new Date(due).getTime();
  // Due less than 12 h ago: it is simply today's review, not really "late".
  const text = delayMs > 12 * 3_600_000 ? `Révision en retard : ${formatRelative(due)}` : delayMs > 0 ? "Révision à faire aujourd'hui" : `Prochaine révision : ${formatRelative(due)}`;
  return <p className={delayMs > 0 ? "text-xs font-medium text-warning" : "text-xs text-muted"}>{text}</p>;
}

/** Chapters and concepts of the subject, with the student's mastery on each concept. */
export function ProgramTab({ subjectId }: { subjectId: Uuid }) {
  const chapters = useChapters(subjectId);
  const concepts = useConcepts(subjectId);
  const mastery = useMastery({ subject_id: subjectId });

  // Show the first error or loading state among the three requests.
  const notReady = [chapters, concepts, mastery].find((q) => q.error || q.isPending);
  if (notReady) return <QueryStatus query={notReady} loadingLabel="Chargement du programme…" />;

  const chapterList = chapters.data ?? [];
  const conceptList = concepts.data ?? [];
  const masteryList = mastery.data ?? [];

  if (chapterList.length === 0) {
    return (
      <EmptyState
        icon={<ListTree className="size-5" aria-hidden="true" />}
        title="Programme pas encore disponible"
        description="Le programme apparaît dès que les cours de la matière ont été importés et analysés."
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">
        Le niveau est estimé à partir de vos réponses aux quiz et aux flashcards. « Non évalué » signifie qu'aucune réponse n'a encore été enregistrée, pas que le niveau est nul.
      </p>
      {chapterList.map((chapter) => {
        const chapterConcepts = conceptList.filter((c) => c.chapter_id === chapter.id && c.active);
        return (
          <Card key={chapter.id}>
            <h2 className="mb-3 font-semibold">
              <span className="text-muted">Chapitre {chapter.ordinal} · </span>
              {chapter.title}
            </h2>
            <ul className="flex flex-col divide-y divide-line">
              {chapterConcepts.map((concept) => {
                const records = masteryList.filter((m) => m.concept_id === concept.id);
                return (
                  <li key={concept.id} className="grid gap-2 py-3 first:pt-0 last:pb-0 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] sm:gap-6">
                    <div>
                      <p className="text-sm font-medium">{concept.label}</p>
                      <NextReview records={records} />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      {records.length === 0 && <MasteryIndicator value={null} label={concept.label} />}
                      {records.map((record) => (
                        <div key={record.dimension}>
                          <p className="text-xs text-muted">{DIMENSION_LABELS[record.dimension]}</p>
                          <MasteryIndicator
                            value={record.score}
                            label={`${concept.label}, ${DIMENSION_LABELS[record.dimension].toLowerCase()}`}
                            lowEvidence={record.confidence < LOW_CONFIDENCE}
                          />
                        </div>
                      ))}
                    </div>
                  </li>
                );
              })}
            </ul>
          </Card>
        );
      })}
    </div>
  );
}

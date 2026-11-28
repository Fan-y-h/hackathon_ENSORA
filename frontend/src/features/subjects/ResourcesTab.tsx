import { FileText, FolderOpen } from "lucide-react";

import { useChapters, useDocuments } from "@/api/hooks";
import type { Uuid } from "@/api/types";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Card, EmptyState } from "@/components/ui";
import { RESOURCE_TYPE_LABELS, RESOURCE_TYPE_ORDER, SOURCE_LABELS } from "@/lib/labels";

/** Subject documents grouped by type (course, TD, past exams...), with their origin. */
export function ResourcesTab({ subjectId }: { subjectId: Uuid }) {
  const documents = useDocuments({ subject_id: subjectId });
  const chapters = useChapters(subjectId);

  if (documents.error || documents.isPending) return <QueryStatus query={documents} loadingLabel="Chargement des ressources…" />;

  if (documents.data.length === 0) {
    return (
      <EmptyState
        icon={<FolderOpen className="size-5" aria-hidden="true" />}
        title="Aucune ressource"
        description="Les documents apparaissent ici après la synchronisation de Moodle ou du Drive Ensora, ou après un import."
      />
    );
  }

  const chapterTitle = (id: Uuid | null) => chapters.data?.find((c) => c.id === id)?.title;
  const groups = RESOURCE_TYPE_ORDER.map((type) => ({ type, items: documents.data.filter((d) => d.resource_type === type) })).filter(
    (group) => group.items.length > 0,
  );

  return (
    <div className="flex flex-col gap-4">
      {groups.map(({ type, items }) => (
        <Card key={type}>
          <h2 className="mb-3 font-semibold">
            {RESOURCE_TYPE_LABELS[type]} <span className="font-normal text-muted">({items.length})</span>
          </h2>
          <ul className="flex flex-col divide-y divide-line">
            {items.map((document) => {
              const chapter = chapterTitle(document.chapter_id);
              return (
                <li key={document.id} className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
                  <FileText className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{document.title}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                      {chapter && <span>{chapter}</span>}
                      {/* Provenance is required: the student must know where a file comes from. */}
                      <span>Source : {SOURCE_LABELS[document.source]}</span>
                    </p>
                  </div>
                  {document.year !== null && <Badge>{document.year}</Badge>}
                </li>
              );
            })}
          </ul>
        </Card>
      ))}
    </div>
  );
}

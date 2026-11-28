import { FileText, SearchX } from "lucide-react";

import type { DocumentSummary, SearchHit, Subject } from "@/api/types";
import { Highlight } from "@/components/Highlight";
import { Badge, EmptyState } from "@/components/ui";
import { locatorLabel } from "@/lib/citations";
import { cn } from "@/lib/cn";
import { RESOURCE_TYPE_LABELS, SOURCE_LABELS } from "@/lib/labels";

const ITEM = "focus-ring w-full rounded-lg border p-3 text-left transition";
const itemState = (selected: boolean) => (selected ? "border-accent bg-accent-soft/40" : "border-line bg-surface hover:border-accent/60");

interface HitListProps {
  hits: SearchHit[];
  terms: string[];
  documentsById: Map<string, DocumentSummary>;
  selectedCitationId?: string;
  onSelect: (hit: SearchHit) => void;
}

/** Passages found by the search, each with its exact location in the source. */
export function HitList({ hits, terms, documentsById, selectedCitationId, onSelect }: HitListProps) {
  if (hits.length === 0) {
    return (
      <EmptyState
        icon={<SearchX className="size-5" aria-hidden="true" />}
        title="Aucun passage trouvé"
        description="Essayez d'autres mots, un autre type de recherche, ou retirez des filtres."
      />
    );
  }
  return (
    <ul className="flex flex-col gap-2" aria-label="Passages trouvés">
      {hits.map((hit) => {
        const document = documentsById.get(hit.citation.document_id);
        return (
          <li key={hit.citation.citation_id}>
            <button
              type="button"
              onClick={() => onSelect(hit)}
              aria-current={hit.citation.citation_id === selectedCitationId || undefined}
              className={cn(ITEM, itemState(hit.citation.citation_id === selectedCitationId))}
            >
              <span className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                <span className="font-medium text-ink">{hit.citation.title}</span>
                <Badge tone="accent">{locatorLabel(hit.citation.locator)}</Badge>
                {document && <span>{RESOURCE_TYPE_LABELS[document.resource_type]}</span>}
              </span>
              <span className="block text-sm leading-relaxed">
                « <Highlight text={hit.citation.excerpt} terms={terms} /> »
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

interface DocumentListProps {
  documents: DocumentSummary[];
  subjects: Subject[];
  selectedId?: string;
  onSelect: (document: DocumentSummary) => void;
}

/** All documents matching the filters (shown when there is no search). */
export function DocumentList({ documents, subjects, selectedId, onSelect }: DocumentListProps) {
  if (documents.length === 0) {
    return <EmptyState title="Aucun document" description="Aucun document ne correspond à ces filtres." />;
  }
  const subjectTitle = (id: string | null) => subjects.find((s) => s.id === id)?.title;
  return (
    <ul className="flex flex-col gap-2" aria-label="Documents">
      {documents.map((document) => (
        <li key={document.id}>
          <button
            type="button"
            onClick={() => onSelect(document)}
            aria-current={document.id === selectedId || undefined}
            className={cn(ITEM, "flex items-start gap-3", itemState(document.id === selectedId))}
          >
            <FileText className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium">{document.title}</span>
              <span className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted">
                <span>{RESOURCE_TYPE_LABELS[document.resource_type]}</span>
                {subjectTitle(document.subject_id) && <span>{subjectTitle(document.subject_id)}</span>}
                <span>{SOURCE_LABELS[document.source]}</span>
              </span>
            </span>
            {document.year !== null && <Badge>{document.year}</Badge>}
          </button>
        </li>
      ))}
    </ul>
  );
}

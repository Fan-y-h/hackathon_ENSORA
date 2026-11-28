import { BookOpenCheck, CircleAlert, CircleHelp, FlaskConical, Quote } from "lucide-react";
import { Link } from "react-router";

import type { Citation } from "@/api/types";
import type { EvidenceStatus } from "@/api/typesLearning";
import { Badge } from "@/components/ui";
import { locatorLabel } from "@/lib/citations";

/** Link that opens the exact cited passage in the library preview. */
export function citationHref(citation: Citation): string {
  return `/library?doc=${citation.document_id}&cite=${citation.citation_id}`;
}

/** Numbered sources under an answer or a generated sheet. */
export function CitationList({ citations, title = "Sources" }: { citations: Citation[]; title?: string }) {
  if (citations.length === 0) return null;
  return (
    <div className="mt-3 rounded-lg border border-line bg-surface-2/50 p-3">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-muted">
        <Quote className="size-3.5" aria-hidden="true" />
        {title}
      </p>
      <ol className="flex flex-col gap-1.5 text-sm">
        {citations.map((citation, index) => (
          <li key={citation.citation_id} className="flex gap-2">
            <span className="font-semibold text-accent tabular-nums">[{index + 1}]</span>
            <Link to={citationHref(citation)} className="focus-ring rounded hover:underline">
              {citation.title} <span className="text-muted">· {locatorLabel(citation.locator)}</span>
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}

const EVIDENCE = {
  grounded: { text: "Appuyée sur vos sources", tone: "success", Icon: BookOpenCheck },
  partial: { text: "Sources partielles", tone: "warning", Icon: CircleHelp },
  insufficient: { text: "Sources insuffisantes", tone: "danger", Icon: CircleAlert },
} as const;

export function EvidenceBadge({ status }: { status: EvidenceStatus }) {
  const { text, tone, Icon } = EVIDENCE[status];
  return (
    <Badge tone={tone} icon={<Icon className="size-3" aria-hidden="true" />}>
      {text}
    </Badge>
  );
}

/**
 * Labels demo output ("Démonstration, sans IA"). Model and provider names are developer
 * information and are never shown to students.
 */
export function DemoLabel({ provider }: { provider: string | null }) {
  if (provider !== "mock") return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted">
      <FlaskConical className="size-3" aria-hidden="true" />
      Démonstration, sans IA
    </span>
  );
}

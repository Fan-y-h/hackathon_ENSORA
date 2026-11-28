import { ExternalLink, X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";

import { useDocument, useDocumentContent } from "@/api/hooks";
import type { Citation, Subject } from "@/api/types";
import { Highlight } from "@/components/Highlight";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, buttonClasses } from "@/components/ui";
import { locatorLabel } from "@/lib/citations";
import { formatDay } from "@/lib/format";

import { PassageActions } from "./PassageActions";
import { RESOURCE_TYPE_LABELS, SOURCE_LABELS } from "@/lib/labels";

interface DocumentPreviewProps {
  documentId: string;
  /** Passage to highlight (opened from a search result). */
  citation?: Citation;
  subjects: Subject[];
  onClose: () => void;
}

/** Text document: shows it with the cited passage highlighted and scrolled into view. */
function TextContent({ blob, versionId, citation }: { blob: Blob; versionId: string; citation?: Citation }) {
  // Keyed by document version: two different files of the same size must not share a cache entry.
  const text = useQuery({ queryKey: ["document-text", versionId], queryFn: () => blob.text(), staleTime: Infinity });
  const markRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    markRef.current?.querySelector("mark")?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [text.data, citation?.citation_id]);

  if (!text.data) return null;
  return (
    <div ref={markRef} className="max-h-[60vh] overflow-y-auto rounded-lg bg-surface-2 p-4 text-sm leading-relaxed whitespace-pre-wrap">
      {/* The whole excerpt is highlighted: it is the exact source span of the citation. */}
      <Highlight text={text.data} terms={citation ? [citation.excerpt] : []} />
    </div>
  );
}

/** Original file next to the results. Never shows a server file path, only the content. */
export function DocumentPreview({ documentId, citation, subjects, onClose }: DocumentPreviewProps) {
  const document = useDocument(documentId);
  const content = useDocumentContent(document.data);
  const objectUrl = useMemo(() => (content.data ? URL.createObjectURL(content.data) : undefined), [content.data]);
  useEffect(() => () => (objectUrl ? URL.revokeObjectURL(objectUrl) : undefined), [objectUrl]);

  const subject = subjects.find((s) => s.id === document.data?.subject_id);
  const isPdf = content.data?.type === "application/pdf";
  const pageAnchor = citation?.locator.page ? `#page=${citation.locator.page}` : "";

  return (
    <aside aria-labelledby="preview-title" className="flex flex-col gap-4 rounded-xl border border-line bg-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <h2 id="preview-title" className="font-semibold">
          {document.data?.title ?? "Aperçu"}
        </h2>
        <button type="button" onClick={onClose} className="focus-ring -m-1 rounded-md p-1 text-muted hover:bg-surface-2" aria-label="Fermer l'aperçu">
          <X className="size-5" aria-hidden="true" />
        </button>
      </div>

      <QueryStatus query={document} />
      {document.data && (
        <>
          <dl className="flex flex-wrap gap-2 text-xs">
            <Badge>{RESOURCE_TYPE_LABELS[document.data.resource_type]}</Badge>
            {subject && <Badge tone="accent">{subject.title}</Badge>}
            {document.data.year !== null && <Badge>{document.data.year}</Badge>}
            <span className="text-muted">
              Source : {SOURCE_LABELS[document.data.source]}
              {document.data.remote_modified_at && ` · version du ${formatDay(document.data.remote_modified_at)}`}
            </span>
          </dl>

          {citation && (
            <p className="rounded-lg border-l-4 border-accent bg-accent-soft/40 px-3 py-2 text-sm">
              <span className="font-medium">Passage cité ({locatorLabel(citation.locator)}) :</span> « {citation.excerpt} »
            </p>
          )}

          <PassageActions document={document.data} citation={citation} content={content.data} />
          {objectUrl && (
            <a href={`${objectUrl}${isPdf ? pageAnchor : ""}`} target="_blank" rel="noreferrer" className={buttonClasses("ghost", "sm", "self-start")}>
              <ExternalLink className="size-4" aria-hidden="true" />
              Ouvrir l'original dans un onglet
            </a>
          )}

          <QueryStatus query={content} loadingLabel="Chargement du document…" />
          {content.data && isPdf && objectUrl && (
            <iframe title={`Aperçu de ${document.data.title}`} src={`${objectUrl}${pageAnchor}`} className="h-[60vh] w-full rounded-lg border border-line" />
          )}
          {content.data?.type.startsWith("text/") && <TextContent blob={content.data} versionId={document.data.active_version_id} citation={citation} />}
          {content.data && !isPdf && !content.data.type.startsWith("text/") && (
            <p className="text-sm text-muted">Aperçu indisponible pour ce format. Utilisez « Ouvrir l'original ».</p>
          )}
        </>
      )}
    </aside>
  );
}

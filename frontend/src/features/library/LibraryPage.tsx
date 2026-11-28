import { Library, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useCitation, useDocuments, useSearch, useSubjects } from "@/api/hooks";
import type { SearchHit } from "@/api/types";
import { searchTerms } from "@/components/Highlight";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, PageHeader } from "@/components/ui";

import { DocumentPreview } from "./DocumentPreview";
import { Filters } from "./Filters";
import { ImportDialog } from "./ImportDialog";
import { DocumentList, HitList } from "./ResultLists";
import { SearchForm } from "./SearchForm";
import { useLibraryParams } from "./useLibraryParams";

/** Search across every course document, preview the original and jump to the cited passage. */
export function LibraryPage() {
  const params = useLibraryParams();
  const [importing, setImporting] = useState(false);

  const subjects = useSubjects();
  const allDocuments = useDocuments();
  const filtered = useDocuments({ subject_id: params.subjectId, resource_type: params.type, year: params.year });
  const search = useSearch(params.q ? { q: params.q, mode: params.mode, subject_id: params.subjectId } : null);

  const documentsById = useMemo(() => new Map((allDocuments.data ?? []).map((d) => [d.id, d])), [allDocuments.data]);

  // /search only filters by subject; type and year are applied here on the documents of each hit.
  const hits = search.data?.filter((hit) => {
    const document = documentsById.get(hit.citation.document_id);
    return (!params.type || document?.resource_type === params.type) && (!params.year || document?.year === params.year);
  });
  const selectedHit = hits?.find((h) => h.citation.citation_id === params.citationId);
  // A link (from the tutor or a sheet) can point at a passage that is not in the current results.
  const linkedCitation = useCitation(selectedHit ? null : params.citationId);

  function selectHit(hit: SearchHit) {
    params.update({ doc: hit.citation.document_id, cite: hit.citation.citation_id });
  }

  const resultsQuery = params.q ? search : filtered;

  // On narrow screens the preview sits below the list: bring it into view when it changes.
  const previewRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (params.docId && window.matchMedia("(max-width: 1023px)").matches) {
      previewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [params.docId, params.citationId]);

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Bibliothèque"
        icon={<Library className="size-5" aria-hidden="true" />}
        description="Tous vos cours, TD, annales et corrigés au même endroit, avec la source de chaque passage."
        actions={
          <Button variant="secondary" onClick={() => setImporting(true)}>
            <Upload className="size-4" aria-hidden="true" />
            Importer un document
          </Button>
        }
      />

      <SearchForm query={params.q} mode={params.mode} onSearch={(q, mode) => params.update({ q, mode: mode === "hybrid" ? undefined : mode, cite: undefined })} />
      <Filters
        subjects={subjects.data ?? []}
        documents={allDocuments.data ?? []}
        subjectId={params.subjectId}
        type={params.type}
        year={params.year}
        onChange={(changes) => params.update(changes)}
      />

      <div className={params.docId ? "grid items-start gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]" : ""}>
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted" aria-live="polite">
            {params.q
              ? hits && `${hits.length} passage${hits.length > 1 ? "s" : ""} pour « ${params.q} »`
              : filtered.data && `${filtered.data.length} document${filtered.data.length > 1 ? "s" : ""}`}
          </p>
          <QueryStatus query={resultsQuery} />
          {params.q && hits && (
            <HitList hits={hits} terms={searchTerms(params.q)} documentsById={documentsById} selectedCitationId={params.citationId} onSelect={selectHit} />
          )}
          {!params.q && filtered.data && (
            <DocumentList documents={filtered.data} subjects={subjects.data ?? []} selectedId={params.docId} onSelect={(d) => params.update({ doc: d.id, cite: undefined })} />
          )}
        </div>

        {params.docId && (
          <div ref={previewRef} className="scroll-mt-20 lg:sticky lg:top-20">
            <DocumentPreview
              key={params.docId}
              documentId={params.docId}
              citation={selectedHit?.citation ?? linkedCitation.data}
              subjects={subjects.data ?? []}
              onClose={() => params.update({ doc: undefined, cite: undefined })}
            />
          </div>
        )}
      </div>

      <ImportDialog
        open={importing}
        subjects={subjects.data ?? []}
        onClose={() => setImporting(false)}
        onImported={(id) => params.update({ q: undefined, doc: id, cite: undefined })}
      />
    </section>
  );
}

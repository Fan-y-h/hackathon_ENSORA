import { useMemo, useRef, useState } from "react";

import { errorMessage } from "../../api/client";
import {
  documentContentUrl,
  RESOURCE_TYPE_LABEL,
  SOURCE_LABEL,
  sourcesUnavailable,
  useDocumentChunks,
  useSourceDocuments,
  useUploadDocument,
  type SourceDocument,
} from "../../api/sources";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Card, EmptyState, ErrorPanel, Loading, PageHeader, SubjectBadge } from "../../components/ui";
import { SyncButton } from "../sources/SyncButton";
import "./library.css";

function typeOf(doc: SourceDocument): string {
  return doc.metadata_json.classification?.resource_type ?? "other";
}

function subjectOf(doc: SourceDocument): string | null {
  return doc.metadata_json.match?.subject_id ?? doc.metadata_json.subject_id ?? null;
}

export function LibraryPage() {
  const documents = useSourceDocuments();
  const upload = useUploadDocument();
  const subjects = useSubjects();
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  const items = useMemo(() => documents.data?.items ?? [], [documents.data]);
  const types = useMemo(() => [...new Set(items.map(typeOf))].sort(), [items]);
  const shown = items.filter(
    (d) => (!type || typeOf(d) === type) && d.metadata_json.title.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const open = items.find((d) => d.id === openId) ?? null;

  return (
    <div className="page">
      <PageHeader
        title="Bibliothèque"
        actions={
          <>
            <input
              ref={input}
              type="file"
              className="sr-only"
              aria-label="Importer un document"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) upload.mutate(file);
                e.target.value = "";
              }}
            />
            <button type="button" className="btn" onClick={() => input.current?.click()} disabled={upload.isPending}>
              <Icon name="plus" /> {upload.isPending ? "Import…" : "Importer"}
            </button>
            <SyncButton />
          </>
        }
      />

      {upload.error && <div className="status status--critical">{errorMessage(upload.error)}</div>}

      {documents.isPending ? (
        <Card>
          <Loading lines={6} />
        </Card>
      ) : documents.error ? (
        sourcesUnavailable(documents.error) ? (
          <Card>
            <EmptyState title="Sources indisponibles">Le module de synchronisation n'est pas démarré.</EmptyState>
          </Card>
        ) : (
          <ErrorPanel error={documents.error} onRetry={() => void documents.refetch()} />
        )
      ) : (
        <div className="split">
          <Card className="split__main">
            <div className="row">
              <input
                className="input"
                style={{ flex: 1, minWidth: 200 }}
                type="search"
                placeholder="Rechercher un document"
                aria-label="Rechercher un document"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <select className="select" style={{ width: "auto" }} aria-label="Type" value={type} onChange={(e) => setType(e.target.value)}>
                <option value="">Tous les types</option>
                {types.map((t) => (
                  <option key={t} value={t}>
                    {RESOURCE_TYPE_LABEL[t] ?? t}
                  </option>
                ))}
              </select>
            </div>
            {shown.length === 0 ? (
              <EmptyState title={items.length ? "Aucun résultat" : "Bibliothèque vide"} />
            ) : (
              <div className="table-wrap">
                <table className="table library">
                  <thead>
                    <tr>
                      <th scope="col">Document</th>
                      <th scope="col">Type</th>
                      <th scope="col">Matière</th>
                      <th scope="col">Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((d) => {
                      const subject = subjectOf(d);
                      return (
                        <tr key={d.id} aria-selected={d.id === openId}>
                          <td>
                            <button type="button" className="library__open" onClick={() => setOpenId(d.id === openId ? null : d.id)}>
                              {d.metadata_json.title}
                            </button>
                          </td>
                          <td>{RESOURCE_TYPE_LABEL[typeOf(d)] ?? typeOf(d)}</td>
                          <td>{subject ? <SubjectBadge acronym={subjects.acronym(subject)} color={subjects.color(subject)} /> : <span className="muted">—</span>}</td>
                          <td className="muted">{SOURCE_LABEL[d.metadata_json.provenance.source] ?? d.metadata_json.provenance.source}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <aside className="split__side">{open ? <DocumentPanel doc={open} /> : null}</aside>
        </div>
      )}
    </div>
  );
}

function DocumentPanel({ doc }: { doc: SourceDocument }) {
  const chunks = useDocumentChunks(doc.id, doc.active_version_id);
  const classification = doc.metadata_json.classification;
  return (
    <Card title={doc.metadata_json.title}>
      <span className="small muted">
        {RESOURCE_TYPE_LABEL[classification?.resource_type ?? "other"]}
        {classification && ` · confiance ${Math.round(classification.confidence * 100)} %`}
      </span>
      {doc.active_version_id && (
        <a className="btn btn--small" href={documentContentUrl(doc.id, doc.active_version_id)} target="_blank" rel="noreferrer">
          Ouvrir le fichier
        </a>
      )}
      {chunks.isPending ? (
        <Loading lines={3} />
      ) : chunks.error ? (
        <ErrorPanel error={chunks.error} />
      ) : (
        <ol className="library__chunks">
          {(chunks.data?.items ?? []).slice(0, 5).map((c) => (
            <li key={c.id}>{String(c.payload.text ?? "").slice(0, 400)}</li>
          ))}
        </ol>
      )}
    </Card>
  );
}

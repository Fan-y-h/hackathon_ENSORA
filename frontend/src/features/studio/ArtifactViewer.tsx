import { History, Pencil, RefreshCw, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { ApiError } from "@/api/client";
import { useArtifact, useArtifactVersions, useUpdateArtifact } from "@/api/hooksLearning";
import { CitationList, DemoLabel } from "@/components/Citations";
import { Markdown } from "@/components/Markdown";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Button, Card, ErrorPanel, buttonClasses } from "@/components/ui";
import { formatRelative } from "@/lib/format";

interface ArtifactViewerProps {
  artifactId: string;
  onClose: () => void;
  onRegenerate: () => void;
  regenerating: boolean;
}

/** Shows a generated support, lets the student edit it (new version) and browse versions. */
export function ArtifactViewer({ artifactId, onClose, onRegenerate, regenerating }: ArtifactViewerProps) {
  const detail = useArtifact(artifactId);
  const versions = useArtifactVersions(artifactId);
  const update = useUpdateArtifact();
  const [draft, setDraft] = useState<string | null>(null);
  const [showVersions, setShowVersions] = useState(false);

  if (!detail.data) return <QueryStatus query={detail} />;
  const { artifact, version } = detail.data;

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">{artifact.title}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <DemoLabel provider={version.provider} />
            {version.edited_by_user && <Badge tone="accent">Modifiée par vous</Badge>}
            {artifact.stale && <Badge tone="warning">Sources modifiées depuis</Badge>}
            <span className="text-xs text-muted">Version du {formatRelative(version.created_at)}</span>
          </div>
        </div>
        <button type="button" onClick={onClose} className="focus-ring rounded-md p-1 text-muted hover:bg-surface-2" aria-label="Fermer le support">
          <X className="size-5" aria-hidden="true" />
        </button>
      </div>

      {artifact.type === "flashcards" && artifact.linked_id && (
        <p className="text-sm">
          Le paquet a été créé.{" "}
          <Link to={`/flashcards?deck=${artifact.linked_id}`} className={buttonClasses("primary", "sm", "ml-2")}>
            Réviser ces flashcards
          </Link>
        </p>
      )}
      {artifact.type === "quiz" && artifact.linked_id && (
        <p className="text-sm">
          Le quiz est prêt.{" "}
          <Link to={`/quiz?quiz=${artifact.linked_id}`} className={buttonClasses("primary", "sm", "ml-2")}>
            Faire ce quiz
          </Link>
        </p>
      )}

      {version.content_markdown !== null && draft === null && (
        <>
          <Markdown>{version.content_markdown}</Markdown>
          <CitationList citations={version.citations} />
        </>
      )}

      {draft !== null && (
        <div className="flex flex-col gap-2">
          <label htmlFor="artifact-draft" className="text-sm font-medium">
            Modifier la fiche (Markdown et LaTeX)
          </label>
          <textarea id="artifact-draft" value={draft} onChange={(e) => setDraft(e.target.value)} rows={18} className="focus-ring w-full rounded-lg border border-line bg-surface p-3 font-mono text-sm" />
          <p className="text-xs text-muted">Enregistrer crée une nouvelle version : la version générée reste disponible dans l'historique.</p>
        </div>
      )}

      {update.error && (
        <ErrorPanel
          title={update.error instanceof ApiError && update.error.isConflict ? "Support modifié entre-temps" : undefined}
          message={update.error.message}
          onRetry={() => void detail.refetch()}
        />
      )}

      <div className="flex flex-wrap gap-2">
        {version.content_markdown !== null &&
          (draft === null ? (
            <Button size="sm" variant="secondary" onClick={() => setDraft(version.content_markdown ?? "")}>
              <Pencil className="size-4" aria-hidden="true" />
              Modifier
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                loading={update.isPending}
                onClick={() => update.mutate({ id: artifact.id, expected_revision: artifact.revision, content_markdown: draft }, { onSuccess: () => setDraft(null) })}
              >
                Enregistrer
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
                Annuler
              </Button>
            </>
          ))}
        <Button size="sm" variant="secondary" onClick={onRegenerate} loading={regenerating}>
          <RefreshCw className="size-4" aria-hidden="true" />
          Nouvelle version générée
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setShowVersions((v) => !v)} aria-expanded={showVersions}>
          <History className="size-4" aria-hidden="true" />
          Historique ({versions.data?.length ?? "…"})
        </Button>
      </div>

      {showVersions && versions.data && (
        <ul className="flex flex-col divide-y divide-line rounded-lg border border-line text-sm">
          {versions.data.map((v) => (
            <li key={v.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span>
                {v.edited_by_user ? "Modifiée par vous" : "Générée"} · {formatRelative(v.created_at)}
                {v.id === artifact.active_version_id && <Badge tone="accent" className="ml-2">Affichée</Badge>}
              </span>
              {v.id !== artifact.active_version_id && (
                <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: artifact.id, expected_revision: artifact.revision, active_version_id: v.id })}>
                  Revenir à cette version
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

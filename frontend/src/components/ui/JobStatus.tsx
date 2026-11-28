import { CircleCheck, CircleSlash, CircleX, Hourglass, LoaderCircle } from "lucide-react";

import { Badge, type Tone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { formatPercent } from "@/lib/format";

/** Same enum as Job.status in docs/API_CONTRACTS.md. */
export type JobState = "queued" | "running" | "succeeded" | "failed" | "cancelled";

const STATES: Record<JobState, { text: string; tone: Tone; Icon: typeof CircleCheck }> = {
  queued: { text: "En attente", tone: "neutral", Icon: Hourglass },
  running: { text: "En cours", tone: "accent", Icon: LoaderCircle },
  succeeded: { text: "Terminé", tone: "success", Icon: CircleCheck },
  failed: { text: "Échec", tone: "danger", Icon: CircleX },
  cancelled: { text: "Annulé", tone: "neutral", Icon: CircleSlash },
};

interface JobStatusProps {
  /** What the job does, e.g. "Génération de la fiche". */
  label: string;
  state: JobState;
  /** Job.progress between 0 and 1, when the backend reports it. */
  progress?: number;
  /** Job.error message when state is "failed". */
  error?: string;
  onCancel?: () => void;
  onRetry?: () => void;
}

/** Long-running backend task (generation, import, sync) with cancel and retry. */
export function JobStatus({ label, state, progress, error, onCancel, onRetry }: JobStatusProps) {
  const { text, tone, Icon } = STATES[state];
  const active = state === "queued" || state === "running";

  return (
    <div className="rounded-lg border border-line bg-surface p-3 text-sm" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 font-medium">{label}</span>
        <Badge
          tone={tone}
          icon={<Icon className={state === "running" ? "size-3 animate-spin" : "size-3"} aria-hidden="true" />}
        >
          {text}
          {state === "running" && progress !== undefined && ` · ${formatPercent(progress)}`}
        </Badge>
        {active && onCancel && (
          <Button variant="ghost" size="sm" onClick={onCancel}>
            Annuler
          </Button>
        )}
        {state === "failed" && onRetry && (
          <Button variant="secondary" size="sm" onClick={onRetry}>
            Réessayer
          </Button>
        )}
      </div>
      {state === "running" && progress !== undefined && (
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
          <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${progress * 100}%` }} />
        </div>
      )}
      {state === "failed" && error && <p className="mt-2 text-danger">{error}</p>}
    </div>
  );
}

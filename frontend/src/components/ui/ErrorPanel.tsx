import { CircleAlert, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/Button";

interface ErrorPanelProps {
  title?: string;
  /** Human-readable message (error.message from the API). */
  message: string;
  /** error.request_id from the API, useful when reporting a bug to the team. */
  requestId?: string;
  onRetry?: () => void;
}

export function ErrorPanel({ title = "Une erreur est survenue", message, requestId, onRetry }: ErrorPanelProps) {
  return (
    <div role="alert" className="rounded-xl border border-danger/30 bg-danger-soft p-4 text-sm">
      <div className="flex flex-wrap items-start gap-2.5">
        <CircleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-danger">{title}</p>
          <p className="mt-0.5 text-ink">{message}</p>
          {requestId && <p className="mt-1 font-mono text-xs text-muted">Référence : {requestId}</p>}
        </div>
        {onRetry && (
          <Button variant="secondary" size="sm" onClick={onRetry}>
            <RotateCcw className="size-3.5" aria-hidden="true" />
            Réessayer
          </Button>
        )}
      </div>
    </div>
  );
}

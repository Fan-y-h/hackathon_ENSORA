import { CircleCheck, CircleOff, CircleX, Clock, RefreshCw } from "lucide-react";

import { Badge, type Tone } from "@/components/ui/Badge";
import { formatRelative } from "@/lib/format";

export type SourceState = "connected" | "syncing" | "stale" | "error" | "disconnected";

const STATES: Record<SourceState, { text: string; tone: Tone; Icon: typeof CircleCheck }> = {
  connected: { text: "À jour", tone: "success", Icon: CircleCheck },
  syncing: { text: "Synchronisation…", tone: "accent", Icon: RefreshCw },
  stale: { text: "Pas à jour", tone: "warning", Icon: Clock },
  error: { text: "Erreur", tone: "danger", Icon: CircleX },
  disconnected: { text: "Non connecté", tone: "neutral", Icon: CircleOff },
};

interface SourceStatusProps {
  /** Source name shown to the student, e.g. "Pamplemousse" or "Moodle". */
  source: string;
  state: SourceState;
  /** Last successful sync (ISO UTC from the API). Omitted = never synced: show nothing invented. */
  lastSyncAt?: string;
}

/** State of an external source. The date always comes from the API, never from the browser clock alone. */
export function SourceStatus({ source, state, lastSyncAt }: SourceStatusProps) {
  const { text, tone, Icon } = STATES[state];
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      <span className="font-medium">{source}</span>
      <Badge
        tone={tone}
        icon={<Icon className={state === "syncing" ? "size-3 animate-spin" : "size-3"} aria-hidden="true" />}
      >
        {text}
      </Badge>
      {lastSyncAt && state !== "disconnected" && (
        <span className="text-xs text-muted">
          Dernière synchro <time dateTime={lastSyncAt}>{formatRelative(lastSyncAt)}</time>
        </span>
      )}
    </div>
  );
}

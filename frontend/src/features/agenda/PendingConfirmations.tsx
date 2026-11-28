import { CircleAlert } from "lucide-react";

import { useConfirmEvent, useEvents } from "@/api/hooks";
import type { CalendarEvent } from "@/api/types";
import { Button, ErrorPanel } from "@/components/ui";
import { dayRange, formatShortDay, formatTime } from "@/lib/format";
import { EVENT_TYPE_LABELS, eventSourceLabel } from "@/lib/labels";

/** Buttons to confirm or reject one ambiguous assessment. Reused in the event details. */
export function ConfirmationButtons({ event, onDone }: { event: CalendarEvent; onDone?: () => void }) {
  const confirm = useConfirmEvent();
  const busy = confirm.isPending ? confirm.variables?.decision : undefined;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" loading={busy === "confirm"} disabled={confirm.isPending} onClick={() => confirm.mutate({ id: event.id, decision: "confirm" }, { onSuccess: onDone })}>
          Oui, c'est un {EVENT_TYPE_LABELS[event.type].toLowerCase()}
        </Button>
        <Button size="sm" variant="secondary" loading={busy === "reject"} disabled={confirm.isPending} onClick={() => confirm.mutate({ id: event.id, decision: "reject" }, { onSuccess: onDone })}>
          Non, ce n'en est pas un
        </Button>
      </div>
      {confirm.error && <ErrorPanel message={confirm.error.message} />}
    </div>
  );
}

/**
 * Assessments detected automatically but not certain (e.g. a red Pamplemousse slot).
 * The planner only counts them once the student has confirmed.
 */
export function PendingConfirmations() {
  // Look far enough ahead to catch every upcoming exam.
  const events = useEvents(dayRange(0, 90));
  const pending = events.data?.filter((e) => e.confirmation_required) ?? [];

  if (pending.length === 0) return null;

  return (
    <section aria-labelledby="pending-title" className="rounded-xl border border-warning/40 bg-warning-soft p-4">
      <h2 id="pending-title" className="flex items-center gap-2 font-semibold text-warning">
        <CircleAlert className="size-4" aria-hidden="true" />
        {pending.length === 1 ? "1 évaluation à confirmer" : `${pending.length} évaluations à confirmer`}
      </h2>
      <p className="mt-1 text-sm">Ces créneaux ressemblent à des évaluations. Confirmez-les pour que le planning de révision en tienne compte.</p>
      <ul className="mt-3 flex flex-col gap-3">
        {pending.map((event) => (
          <li key={event.id} className="rounded-lg border border-line bg-surface p-3">
            <p className="font-medium">{event.title}</p>
            <p className="mb-3 text-sm text-muted">
              {formatShortDay(event.start_at)} à {formatTime(event.start_at)}
              {event.room && ` · ${event.room}`} · {eventSourceLabel(event.source)}
            </p>
            <ConfirmationButtons event={event} />
          </li>
        ))}
      </ul>
    </section>
  );
}

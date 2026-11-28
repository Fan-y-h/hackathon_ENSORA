import { CircleAlert, Flag } from "lucide-react";
import { Link } from "react-router";

import { useEvents } from "@/api/hooks";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Card, CardHeader, EmptyState } from "@/components/ui";
import { dayRange, formatRelative, formatShortDay, formatTime } from "@/lib/format";
import { EVENT_TYPE_LABELS } from "@/lib/labels";

import { EVENT_TONES } from "@/lib/eventTones";

const HORIZON_DAYS = 14;

/** Exams, presentations and deadlines of the next two weeks. */
export function DeadlinesCard() {
  const events = useEvents(dayRange(0, HORIZON_DAYS));
  const deadlines = events.data?.filter((e) => e.type === "exam" || e.type === "presentation" || e.type === "deadline");

  return (
    <Card>
      <CardHeader title="Prochaines échéances" description={`Sur les ${HORIZON_DAYS} prochains jours`} />
      <QueryStatus query={events} />
      {deadlines?.length === 0 && (
        <EmptyState icon={<Flag className="size-5" aria-hidden="true" />} title="Aucune échéance à venir" />
      )}
      {deadlines && deadlines.length > 0 && (
        <ul className="flex flex-col gap-3">
          {deadlines.map((event) => (
            <li key={event.id} className="rounded-lg border border-line p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={EVENT_TONES[event.type]}>{EVENT_TYPE_LABELS[event.type]}</Badge>
                <p className="min-w-0 flex-1 font-medium">{event.title}</p>
              </div>
              <p className="mt-1 text-sm text-muted">
                <time dateTime={event.start_at}>
                  {formatShortDay(event.start_at)} à {formatTime(event.start_at)}
                </time>{" "}
                · {formatRelative(event.start_at)}
              </p>
              {/* Ambiguous assessment: only the student can confirm it is a real exam. */}
              {event.confirmation_required && (
                <Link
                  to="/agenda"
                  className="focus-ring mt-2 inline-flex items-center gap-1.5 rounded-md text-sm font-medium text-warning hover:underline"
                >
                  <CircleAlert className="size-4" aria-hidden="true" />
                  À confirmer dans l'agenda
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

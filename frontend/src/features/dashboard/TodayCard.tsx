import { CalendarDays, MapPin, Users } from "lucide-react";
import { Link } from "react-router";

import { useEvents } from "@/api/hooks";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Card, CardHeader, EmptyState, buttonClasses } from "@/components/ui";
import { dayRange, formatTime } from "@/lib/format";
import { EVENT_TYPE_LABELS } from "@/lib/labels";

import { EVENT_TONES } from "@/lib/eventTones";

/** Today's classes and events, in time order. */
export function TodayCard() {
  const events = useEvents(dayRange(0));

  return (
    <Card>
      <CardHeader
        title="Aujourd'hui"
        actions={
          <Link to="/agenda" className={buttonClasses("ghost", "sm")}>
            Agenda
          </Link>
        }
      />
      <QueryStatus query={events} />
      {events.data?.length === 0 && (
        <EmptyState
          icon={<CalendarDays className="size-5" aria-hidden="true" />}
          title="Rien de prévu aujourd'hui"
          description="Profitez-en pour avancer vos tâches prioritaires."
        />
      )}
      {events.data && events.data.length > 0 && (
        <ol className="flex flex-col divide-y divide-line">
          {events.data.map((event) => (
            <li key={event.id} className="flex gap-4 py-3 first:pt-0 last:pb-0">
              <p className="w-12 shrink-0 text-sm font-semibold tabular-nums">
                <time dateTime={event.start_at}>{formatTime(event.start_at)}</time>
                <span className="block text-xs font-normal text-muted">
                  <time dateTime={event.end_at}>{formatTime(event.end_at)}</time>
                </span>
              </p>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">{event.title}</p>
                  {event.source === "planner" ? (
                    <Link to="/planner" className="focus-ring rounded-full">
                      <Badge tone="accent">Révision planifiée</Badge>
                    </Link>
                  ) : (
                    <Badge tone={EVENT_TONES[event.type]}>{EVENT_TYPE_LABELS[event.type]}</Badge>
                  )}
                </div>
                <p className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                  {event.room && (
                    <span className="inline-flex items-center gap-1">
                      <MapPin className="size-3" aria-hidden="true" />
                      {event.room}
                    </span>
                  )}
                  {/* The group only comes from Pamplemousse; never ask the student for it. */}
                  {(event.type === "td" || event.type === "tp") && (
                    <span className="inline-flex items-center gap-1">
                      <Users className="size-3" aria-hidden="true" />
                      {event.group ?? "Groupe non disponible"}
                    </span>
                  )}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

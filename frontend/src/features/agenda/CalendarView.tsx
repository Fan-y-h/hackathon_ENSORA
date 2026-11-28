import type { DatesSetArg, EventContentArg, EventInput } from "@fullcalendar/core";
import frLocale from "@fullcalendar/core/locales/fr";
import dayGridPlugin from "@fullcalendar/daygrid";
import listPlugin from "@fullcalendar/list";
import FullCalendar from "@fullcalendar/react";
import timeGridPlugin from "@fullcalendar/timegrid";
import { CircleAlert, Lock } from "lucide-react";
import { useState } from "react";

import { useEvents } from "@/api/hooks";
import type { CalendarEvent } from "@/api/types";
import { QueryStatus } from "@/components/QueryStatus";
import { EVENT_TYPE_LABELS } from "@/lib/labels";

interface CalendarViewProps {
  onSelectEvent: (event: CalendarEvent) => void;
}

/** Imported events (Pamplemousse, Moodle...) are read-only copies of the source. */
export function isImported(event: CalendarEvent): boolean {
  return event.source !== "manual" && event.source !== "planner";
}

function toCalendarInput(event: CalendarEvent): EventInput {
  // A deadline is an instant (often 23:59): show it in the "all day" row instead of a 0-minute block.
  const isDeadline = event.type === "deadline";
  return {
    id: event.id,
    title: event.title,
    start: isDeadline ? event.start_at.slice(0, 10) : event.start_at,
    end: isDeadline ? undefined : event.end_at,
    allDay: isDeadline,
    classNames: [event.source === "planner" ? "ev-planner" : `ev-${event.type}`, event.confirmation_required ? "ev-pending" : ""],
    extendedProps: { event },
  };
}

function EventContent({ event: fcEvent, timeText }: EventContentArg) {
  const event = fcEvent.extendedProps.event as CalendarEvent;
  return (
    <div className="flex h-full flex-col overflow-hidden px-1 py-0.5 text-xs leading-tight">
      <span className="flex items-center gap-1 font-semibold">
        {event.confirmation_required && <CircleAlert className="size-3 shrink-0" aria-label="À confirmer" />}
        {isImported(event) && <Lock className="size-3 shrink-0 opacity-70" aria-label="Importé, lecture seule" />}
        <span className="truncate">{event.title}</span>
      </span>
      {timeText && <span className="opacity-80">{timeText}</span>}
      {event.room && <span className="truncate opacity-80">{event.room}</span>}
      <span className="sr-only">{EVENT_TYPE_LABELS[event.type]}</span>
    </div>
  );
}

const isSmallScreen = () => typeof window !== "undefined" && window.matchMedia("(max-width: 640px)").matches;

/** Day / week / month / list calendar. Only fetches the dates currently visible. */
export function CalendarView({ onSelectEvent }: CalendarViewProps) {
  const [range, setRange] = useState<{ from: string; to: string } | null>(null);
  const events = useEvents(range);

  function handleDatesSet(arg: DatesSetArg) {
    const next = { from: arg.start.toISOString(), to: arg.end.toISOString() };
    // Only update when the visible dates really change, to avoid useless re-renders.
    setRange((current) => (current?.from === next.from && current.to === next.to ? current : next));
  }

  return (
    <div className="flex flex-col gap-3">
      {events.error && <QueryStatus query={events} />}
      <div className="ensora-calendar relative rounded-xl border border-line bg-surface p-3 sm:p-4" aria-busy={events.isFetching}>
        <FullCalendar
          plugins={[timeGridPlugin, dayGridPlugin, listPlugin]}
          locale={frLocale}
          initialView={isSmallScreen() ? "listWeek" : "timeGridWeek"}
          headerToolbar={{ left: "prev,next today", center: "title", right: "timeGridDay,timeGridWeek,dayGridMonth,listWeek" }}
          buttonText={{ prev: "‹ Précédent", next: "Suivant ›", today: "Aujourd'hui", day: "Jour", week: "Semaine", month: "Mois", list: "Liste" }}
          // Text buttons instead of icon fonts: readable by screen readers.
          buttonIcons={false}
          firstDay={1}
          slotMinTime="07:30:00"
          slotMaxTime="21:00:00"
          allDayText="Échéances"
          nowIndicator
          height="auto"
          expandRows
          events={events.data?.map(toCalendarInput) ?? []}
          eventContent={(arg) => <EventContent {...arg} />}
          eventClick={(arg) => {
            arg.jsEvent.preventDefault();
            onSelectEvent(arg.event.extendedProps.event as CalendarEvent);
          }}
          datesSet={handleDatesSet}
          noEventsText="Aucun événement sur cette période"
        />
        {events.isFetching && (
          <p role="status" className="absolute top-3 right-4 text-xs text-muted sm:top-4">
            Chargement…
          </p>
        )}
      </div>
    </div>
  );
}

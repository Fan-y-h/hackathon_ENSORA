import { CalendarDays, Lock, Plus } from "lucide-react";
import { useState } from "react";

import type { CalendarEvent, EventType } from "@/api/types";
import { Button, PageHeader } from "@/components/ui";
import { EVENT_TYPE_LABELS } from "@/lib/labels";

import { AddEventDialog } from "./AddEventDialog";
import { CalendarView } from "./CalendarView";
import { EventDetailsDialog } from "./EventDetailsDialog";
import { PendingConfirmations } from "./PendingConfirmations";

const LEGEND: EventType[] = ["class", "td", "exam", "deadline", "personal"];

/** Calendar of classes, assessments and personal events. Imported events are read-only. */
export function AgendaPage() {
  const [selected, setSelected] = useState<CalendarEvent | null>(null);
  const [adding, setAdding] = useState(false);

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Agenda"
        icon={<CalendarDays className="size-5" aria-hidden="true" />}
        description="Cours et évaluations importés, et vos propres indisponibilités."
        actions={
          <Button onClick={() => setAdding(true)}>
            <Plus className="size-4" aria-hidden="true" />
            Ajouter une indisponibilité
          </Button>
        }
      />

      <PendingConfirmations />

      <ul className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted" aria-label="Légende">
        {LEGEND.map((type) => (
          <li key={type} className="inline-flex items-center gap-1.5">
            <span className={`ev-swatch ev-${type} size-3 rounded-sm`} aria-hidden="true" />
            {EVENT_TYPE_LABELS[type]}
          </li>
        ))}
        <li className="inline-flex items-center gap-1.5">
          <span className="ev-swatch ev-planner size-3 rounded-sm border-dashed" aria-hidden="true" />
          Révision planifiée
        </li>
        <li className="inline-flex items-center gap-1.5">
          <Lock className="size-3" aria-hidden="true" />
          Importé (lecture seule)
        </li>
      </ul>

      <CalendarView onSelectEvent={setSelected} />

      <EventDetailsDialog event={selected} onClose={() => setSelected(null)} />
      <AddEventDialog open={adding} onClose={() => setAdding(false)} />
    </section>
  );
}

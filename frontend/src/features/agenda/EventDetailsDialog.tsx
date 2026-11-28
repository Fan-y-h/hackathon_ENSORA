import { Clock, Lock, MapPin, Trash2, UserRound, Users } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { useDeleteEvent } from "@/api/hooks";
import type { CalendarEvent } from "@/api/types";
import { Badge, Button, Dialog, ErrorPanel, buttonClasses } from "@/components/ui";
import { EVENT_TONES } from "@/lib/eventTones";
import { formatDay, formatTime } from "@/lib/format";
import { EVENT_TYPE_LABELS, eventSourceLabel } from "@/lib/labels";

import { isImported } from "./CalendarView";
import { ConfirmationButtons } from "./PendingConfirmations";

function Row({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <li className="flex items-start gap-2.5">
      <span className="mt-0.5 text-muted">{icon}</span>
      <span>{children}</span>
    </li>
  );
}

interface EventDetailsDialogProps {
  event: CalendarEvent | null;
  onClose: () => void;
}

export function EventDetailsDialog({ event, onClose }: EventDetailsDialogProps) {
  const deleteEvent = useDeleteEvent();
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  function close() {
    setConfirmingDelete(false);
    deleteEvent.reset();
    onClose();
  }

  if (!event) return <Dialog open={false} title="" onClose={close}>{null}</Dialog>;

  const imported = isImported(event);
  const planned = event.source === "planner";
  const isDeadline = event.type === "deadline";

  return (
    <Dialog
      open
      title={event.title}
      onClose={close}
      footer={
        planned ? (
          <Link to="/planner" className={buttonClasses("secondary")}>
            Gérer dans le planning
          </Link>
        ) : (
          !imported && (
          <Button
            variant={confirmingDelete ? "danger" : "secondary"}
            loading={deleteEvent.isPending}
            onClick={() => (confirmingDelete ? deleteEvent.mutate(event.id, { onSuccess: close }) : setConfirmingDelete(true))}
          >
            <Trash2 className="size-4" aria-hidden="true" />
            {confirmingDelete ? "Confirmer la suppression" : "Supprimer"}
          </Button>
          )
        )
      }
    >
      <div className="mb-4 flex flex-wrap gap-2">
        <Badge tone={planned ? "accent" : EVENT_TONES[event.type]}>{planned ? "Révision planifiée" : EVENT_TYPE_LABELS[event.type]}</Badge>
        {event.confirmation_required && <Badge tone="warning">À confirmer</Badge>}
      </div>

      <ul className="flex flex-col gap-2.5 text-sm">
        <Row icon={<Clock className="size-4" aria-hidden="true" />}>
          {formatDay(event.start_at)}
          {isDeadline ? ` à ${formatTime(event.start_at)}` : `, ${formatTime(event.start_at)} – ${formatTime(event.end_at)}`}
        </Row>
        {event.room && <Row icon={<MapPin className="size-4" aria-hidden="true" />}>{event.room}</Row>}
        {event.teacher && <Row icon={<UserRound className="size-4" aria-hidden="true" />}>{event.teacher}</Row>}
        {(event.type === "td" || event.type === "tp") && (
          <Row icon={<Users className="size-4" aria-hidden="true" />}>
            {event.group ?? "Groupe non disponible (Pamplemousse non connecté)"}
          </Row>
        )}
        <Row icon={<Lock className="size-4" aria-hidden="true" />}>
          {eventSourceLabel(event.source)}
          {imported && <span className="block text-xs text-muted">Lecture seule : les modifications se font dans la source.</span>}
        </Row>
      </ul>

      {event.confirmation_required && (
        <div className="mt-5 rounded-lg bg-warning-soft p-3">
          <p className="mb-2 text-sm font-medium">Ce créneau est-il bien une évaluation ?</p>
          <ConfirmationButtons event={event} onDone={close} />
        </div>
      )}

      {deleteEvent.error && (
        <div className="mt-4">
          <ErrorPanel message={deleteEvent.error.message} />
        </div>
      )}
    </Dialog>
  );
}

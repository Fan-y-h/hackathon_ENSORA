import { useState } from "react";

import { errorMessage } from "../../api/client";
import { useCreateEvent, useUpdateEvent } from "../../api/queries";
import type { CalendarEvent } from "../../api/types";
import { useSubjects } from "../../app/subjects";
import { Dialog } from "../../components/ui";
import { dayKey, formatTimeInput, localDateTimeToIso } from "../../lib/time";

const EVENT_TYPES: { value: CalendarEvent["type"]; label: string }[] = [
  { value: "personal", label: "Personnel" },
  { value: "class", label: "Cours magistral (CM)" },
  { value: "td", label: "Travaux dirigés (TD)" },
  { value: "tp", label: "Travaux pratiques (TP)" },
  { value: "exam", label: "Partiel / Examen" },
  { value: "presentation", label: "Soutenance / Oral" },
  { value: "deadline", label: "Rendu / Échéance" },
  { value: "other", label: "Autre" },
];

export function EventFormDialog({
  initialDay,
  event,
  onClose,
}: {
  initialDay: string;
  event?: CalendarEvent;
  onClose: () => void;
}) {
  const subjects = useSubjects();
  const tz = subjects.timezone;
  const create = useCreateEvent();
  const update = useUpdateEvent();

  const [type, setType] = useState<CalendarEvent["type"]>(event?.type ?? "personal");
  const [title, setTitle] = useState(event?.title ?? "");
  const [subjectId, setSubjectId] = useState(event?.subject_id ?? "");
  const [allDay, setAllDay] = useState(Boolean(event?.all_day_date && !event?.start_at));
  const [day, setDay] = useState(event?.all_day_date ?? (event?.start_at ? dayKey(event.start_at, tz) : initialDay));
  const [startTime, setStartTime] = useState(event?.start_at ? formatTimeInput(event.start_at, tz) : "14:00");
  const [endTime, setEndTime] = useState(event?.end_at ? formatTimeInput(event.end_at, tz) : "15:30");
  const [room, setRoom] = useState(event?.room ?? "");
  const [busy, setBusy] = useState(event?.busy ?? type !== "deadline");
  const [notes, setNotes] = useState(event?.notes ?? "");
  const [validationError, setValidationError] = useState<string | null>(null);

  const error = create.error ?? update.error;
  const pending = create.isPending || update.isPending;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError(null);
    if (!title.trim()) {
      setValidationError("Indiquez un intitulé.");
      return;
    }

    let start_at: string | null = null;
    let end_at: string | null = null;
    let all_day_date: string | null = null;

    if (allDay) {
      all_day_date = day;
    } else {
      start_at = localDateTimeToIso(day, startTime, tz);
      // Deadlines require end_at > start_at on the backend schema; give them +10 min if equal.
      const effectiveEnd = endTime <= startTime && type === "deadline" ? addMinutes(startTime, 10) : endTime;
      end_at = localDateTimeToIso(day, effectiveEnd, tz);
      if (new Date(end_at) <= new Date(start_at)) {
        setValidationError("L'heure de fin doit être postérieure à l'heure de début.");
        return;
      }
    }

    if (event) {
      update.mutate(
        {
          id: event.id,
          patch: {
            expected_revision: event.revision,
            type,
            title: title.trim(),
            subject_id: subjectId || null,
            start_at,
            end_at,
            all_day_date,
            room: room.trim() || null,
            busy,
            notes: notes.trim() || null,
          },
        },
        { onSuccess: onClose },
      );
    } else {
      create.mutate(
        {
          type,
          title: title.trim(),
          subject_id: subjectId || null,
          start_at,
          end_at,
          all_day_date,
          room: room.trim() || null,
          busy,
          status: "confirmed",
          notes: notes.trim() || null,
        },
        { onSuccess: onClose },
      );
    }
  };

  return (
    <Dialog title={event ? "Modifier l'événement" : "Nouvel événement"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label className="field">
            <span>Catégorie</span>
            <select
              className="select"
              value={type}
              onChange={(e) => {
                const next = e.target.value as CalendarEvent["type"];
                setType(next);
                if (!event) setBusy(next !== "deadline");
              }}
            >
              {EVENT_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Matière</span>
            <select className="select" value={subjectId} onChange={(e) => setSubjectId(e.target.value)}>
              <option value="">Aucune</option>
              {subjects.ids.map((id) => (
                <option key={id} value={id}>
                  {subjects.name(id)}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="field">
          <span>Intitulé</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} />
        </label>

        <label className="checkbox">
          <input type="checkbox" checked={allDay} onChange={(e) => setAllDay(e.target.checked)} />
          Événement sur toute la journée
        </label>

        <div className="form-grid" style={{ gridTemplateColumns: allDay ? "1fr" : "1.2fr 1fr 1fr" }}>
          <label className="field">
            <span>Date</span>
            <input className="input" type="date" value={day} onChange={(e) => setDay(e.target.value)} required />
          </label>
          {!allDay && (
            <>
              <label className="field">
                <span>Début</span>
                <input className="input" type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} required />
              </label>
              <label className="field">
                <span>Fin</span>
                <input className="input" type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} required />
              </label>
            </>
          )}
        </div>

        <div className="form-grid" style={{ gridTemplateColumns: "1fr auto" }}>
          <label className="field">
            <span>Salle / Lieu</span>
            <input className="input" value={room} onChange={(e) => setRoom(e.target.value)} maxLength={120} />
          </label>
          <label className="checkbox" style={{ alignSelf: "end", paddingBottom: 8 }}>
            <input type="checkbox" checked={busy} onChange={(e) => setBusy(e.target.checked)} />
            Bloque le Smart Planner
          </label>
        </div>

        <label className="field">
          <span>Notes</span>
          <textarea className="textarea" style={{ minHeight: 72 }} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={5000} />
        </label>

        {(validationError || error) && (
          <div className="status status--critical small" role="alert">
            {validationError ?? errorMessage(error)}
          </div>
        )}

        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose} disabled={pending}>
            Annuler
          </button>
          <button type="submit" className="btn btn--primary" disabled={pending}>
            {pending ? "Enregistrement…" : event ? "Enregistrer" : "Ajouter"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function addMinutes(hhmm: string, delta: number): string {
  const [h, m] = hhmm.split(":").map(Number);
  const total = Math.min(23 * 60 + 59, (h || 0) * 60 + (m || 0) + delta);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

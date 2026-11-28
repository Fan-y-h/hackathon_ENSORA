import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, errorMessage } from "../../api/client";
import {
  useAssessments,
  useConfirmEvent,
  useDeleteEvent,
  useFinishSession,
  usePatchAssessment,
  useUpdateSession,
} from "../../api/queries";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { ConfirmDialog, Dialog, StatusLine, SubjectBadge } from "../../components/ui";
import { dayKey, formatClock, formatDuration, formatLongDate, formatTimeInput, localDateTimeToIso } from "../../lib/time";
import { KIND_LABEL, ORIGIN_LABEL, type Block } from "./calendar";
import { EventFormDialog } from "./EventFormDialog";
import { reasonLabels } from "./reasons";

export function BlockDialog({ block, onClose }: { block: Block; onClose: () => void }) {
  const subjects = useSubjects();
  const navigate = useNavigate();
  const updateSession = useUpdateSession();
  const finishSession = useFinishSession();
  const confirmEvent = useConfirmEvent();
  const deleteEvent = useDeleteEvent();
  const assessments = useAssessments();
  const patchAssessment = usePatchAssessment();

  const tz = subjects.timezone;
  const session = block.session;
  const ev = block.event;

  const [actual, setActual] = useState(Math.max(5, block.endMin - block.startMin));
  const [moveDay, setMoveDay] = useState(block.day);
  const [moveStart, setMoveStart] = useState(session ? formatTimeInput(session.start_at, tz) : "09:00");
  const [moveEnd, setMoveEnd] = useState(session ? formatTimeInput(session.end_at, tz) : "10:00");
  const [moveError, setMoveError] = useState<string | null>(null);
  const [editingEvent, setEditingEvent] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const linkedAssessment = ev?.assessment_id
    ? assessments.data?.items.find((a) => a.id === ev.assessment_id)
    : undefined;

  if (editingEvent && ev && !ev.read_only) {
    return <EventFormDialog initialDay={block.day} event={ev} onClose={onClose} />;
  }

  const error =
    updateSession.error ?? finishSession.error ?? confirmEvent.error ?? deleteEvent.error ?? patchAssessment.error;

  const formatConstraintError = (err: unknown): string => {
    if (err instanceof ApiError && err.status === 422 && Array.isArray(err.details)) {
      const codes = err.details
        .map((d) => (typeof d === "object" && d && "code" in d ? String((d as { code: unknown }).code) : String(d)))
        .join(", ");
      return codes ? `${err.message} (${codes})` : err.message;
    }
    return errorMessage(err);
  };

  const handleMoveSession = (e: React.FormEvent) => {
    e.preventDefault();
    if (!session) return;
    setMoveError(null);
    const start_at = localDateTimeToIso(moveDay, moveStart, tz);
    const end_at = localDateTimeToIso(moveDay, moveEnd, tz);
    if (new Date(end_at) <= new Date(start_at)) {
      setMoveError("L'heure de fin doit être postérieure à l'heure de début.");
      return;
    }
    updateSession.mutate(
      { id: session.id, expected_revision: session.revision, start_at, end_at, locked: true },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog title={block.title} onClose={onClose}>
      <div className="row">
        <span className="pill">{KIND_LABEL[block.kind]}</span>
        <span className="pill">{ORIGIN_LABEL[block.sourceOrigin]}</span>
        <SubjectBadge acronym={subjects.acronym(block.subjectId)} color={subjects.color(block.subjectId)} />
        {block.subjectId && <span className="small muted">{subjects.name(block.subjectId)}</span>}
      </div>

      <p>
        <strong>{formatLongDate(block.day)}</strong>
        {block.allDay
          ? " · Toute la journée"
          : `, ${formatClock(block.startAt, tz)} – ${formatClock(block.endAt, tz)} · ${formatDuration(block.endMin - block.startMin)}`}
      </p>

      {block.conflict && (
        <StatusLine tone="critical" label="Conflit horaire détecté">
          Chevauche : {block.conflictWith.join(", ")}
        </StatusLine>
      )}

      {ev && (
        <div className="stack small">
          {ev.room && <span>Salle : {ev.room}</span>}
          {ev.teacher && <span>Enseignant·e : {ev.teacher}</span>}
          {ev.notes && <span>Notes : {ev.notes}</span>}
          {ev.read_only && (
            <span className="muted">
              Importé ({ev.source}) : lecture seule, la source d'origine n'est jamais modifiée.
            </span>
          )}

          {ev.confirmation_required && (
            <div className="card card--warning stack" style={{ padding: 12 }}>
              <StatusLine tone="warning" label="Créneau ambigu à confirmer">
                Confirmez ce créneau pour que le Smart Planner le prenne en compte.
              </StatusLine>
              <div className="row">
                <button
                  type="button"
                  className="btn btn--small btn--primary"
                  disabled={confirmEvent.isPending}
                  onClick={() =>
                    confirmEvent.mutate(
                      { id: ev.id, body: { decision: "confirm", expected_revision: ev.revision } },
                      { onSuccess: onClose },
                    )
                  }
                >
                  <Icon name="check" size={14} /> Confirmer ce créneau
                </button>
                <button
                  type="button"
                  className="btn btn--small btn--danger"
                  disabled={confirmEvent.isPending}
                  onClick={() =>
                    confirmEvent.mutate(
                      { id: ev.id, body: { decision: "reject", expected_revision: ev.revision } },
                      { onSuccess: onClose },
                    )
                  }
                >
                  Rejeter (annuler)
                </button>
              </div>
            </div>
          )}

          {linkedAssessment && linkedAssessment.scope_status !== "confirmed" && !ev.confirmation_required && (
            <div className="card card--warning stack" style={{ padding: 12 }}>
              <StatusLine tone="warning" label="Programme d'évaluation non confirmé">
                Confirmez le périmètre pour activer la planification automatique des révisions.
              </StatusLine>
              <button
                type="button"
                className="btn btn--small btn--primary"
                style={{ alignSelf: "flex-start" }}
                disabled={patchAssessment.isPending}
                onClick={() =>
                  patchAssessment.mutate(
                    {
                      id: linkedAssessment.id,
                      patch: { expected_revision: linkedAssessment.revision, scope_status: "confirmed" },
                    },
                    { onSuccess: onClose },
                  )
                }
              >
                <Icon name="check" size={14} /> Confirmer le programme
              </button>
            </div>
          )}

          {!ev.read_only && (
            <div className="row">
              <button type="button" className="btn btn--small" onClick={() => setEditingEvent(true)}>
                Modifier l'événement
              </button>
              <button
                type="button"
                className="btn btn--small btn--ghost btn--danger"
                onClick={() => setConfirmingDelete(true)}
              >
                <Icon name="trash" size={14} /> Supprimer
              </button>
            </div>
          )}
        </div>
      )}

      {session && (
        <div className="stack small">
          {session.objective && (
            <div>
              <strong>Objectif : </strong>
              {session.objective}
            </div>
          )}
          {session.success_criteria && (
            <div>
              <strong>Critère de réussite : </strong>
              {session.success_criteria}
            </div>
          )}
          {session.reason && (
            <div>
              <strong>Explication : </strong>
              {reasonLabels(session.reason).join(" · ")}
            </div>
          )}
          {session.generation_spec && (
            <div className="muted">
              Support prévu : {String((session.generation_spec as { artifact_type?: string }).artifact_type ?? "—")}, généré
              uniquement au démarrage de la séance (aucun appel IA anticipé).
            </div>
          )}
          <div className="muted">
            Statut : <strong>{session.status}</strong> · {session.locked ? "Verrouillée" : "Déplaçable"}
          </div>
        </div>
      )}

      {(moveError || error) && (
        <div className="status status--critical small" role="alert">
          {moveError ?? formatConstraintError(error)}
        </div>
      )}

      {session && (session.status === "planned" || session.status === "in_progress") && (
        <div className="stack">
          <div className="row">
            <button type="button" className="btn btn--primary" onClick={() => navigate(`/focus?session=${session.id}`)}>
              <Icon name="play" size={14} /> Lancer la Focus Room
            </button>
            <button
              type="button"
              className="btn"
              disabled={updateSession.isPending}
              onClick={() =>
                updateSession.mutate(
                  { id: session.id, expected_revision: session.revision, locked: !session.locked },
                  { onSuccess: onClose },
                )
              }
            >
              <Icon name="lock" size={14} />
              {session.locked ? "Déverrouiller" : "Verrouiller"}
            </button>
          </div>

          <details className="disclosure" style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12 }}>
            <summary className="small strong">Déplacer ce créneau</summary>
            <form className="stack" style={{ marginTop: 10 }} onSubmit={handleMoveSession}>
              <div className="form-grid" style={{ gridTemplateColumns: "1.2fr 1fr 1fr" }}>
                <label className="field">
                  <span>Jour</span>
                  <input className="input" type="date" value={moveDay} onChange={(e) => setMoveDay(e.target.value)} required />
                </label>
                <label className="field">
                  <span>Début</span>
                  <input className="input" type="time" value={moveStart} onChange={(e) => setMoveStart(e.target.value)} required />
                </label>
                <label className="field">
                  <span>Fin</span>
                  <input className="input" type="time" value={moveEnd} onChange={(e) => setMoveEnd(e.target.value)} required />
                </label>
              </div>
              <span className="xsmall muted">
                Le déplacement vérifie les contraintes dures (créneau libre, plafonds, échéance) et verrouille la séance.
              </span>
              <button type="submit" className="btn btn--small" disabled={updateSession.isPending}>
                {updateSession.isPending ? "Déplacement…" : "Appliquer le nouveau créneau"}
              </button>
            </form>
          </details>

          <fieldset className="stack" style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12 }}>
            <legend className="small strong">Clôturer sans Focus Room</legend>
            <label className="field">
              <span>Temps réellement travaillé (min)</span>
              <input
                className="input"
                type="number"
                min={0}
                max={600}
                value={actual}
                onChange={(e) => setActual(Number(e.target.value))}
              />
            </label>
            <div className="row">
              <button
                type="button"
                className="btn btn--small"
                disabled={finishSession.isPending}
                onClick={() =>
                  finishSession.mutate(
                    { id: session.id, status: session.status, body: { outcome: "completed", actual_min: actual } },
                    { onSuccess: onClose },
                  )
                }
              >
                Terminée
              </button>
              <button
                type="button"
                className="btn btn--small"
                disabled={finishSession.isPending}
                onClick={() =>
                  finishSession.mutate(
                    { id: session.id, status: session.status, body: { outcome: "partial", actual_min: actual } },
                    { onSuccess: onClose },
                  )
                }
              >
                Partielle
              </button>
              <button
                type="button"
                className="btn btn--small btn--danger"
                disabled={finishSession.isPending}
                onClick={() =>
                  finishSession.mutate(
                    { id: session.id, status: session.status, body: { outcome: "skipped", actual_min: 0 } },
                    { onSuccess: onClose },
                  )
                }
              >
                Non faite
              </button>
            </div>
          </fieldset>
        </div>
      )}

      {confirmingDelete && ev && (
        <ConfirmDialog
          title={`Supprimer « ${ev.title} » ?`}
          message={`Cet événement du ${formatLongDate(dayKey(ev.start_at ?? block.day, tz))} sera retiré de votre agenda.`}
          confirmLabel="Supprimer"
          danger
          pending={deleteEvent.isPending}
          error={deleteEvent.error}
          onClose={() => setConfirmingDelete(false)}
          onConfirm={() =>
            deleteEvent.mutate({ id: ev.id, revision: ev.revision }, { onSuccess: onClose })
          }
        />
      )}
    </Dialog>
  );
}

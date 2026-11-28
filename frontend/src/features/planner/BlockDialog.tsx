import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { errorMessage } from "../../api/client";
import { useFinishSession, useUpdateSession } from "../../api/queries";
import { useSubjects } from "../../app/subjects";
import { Dialog, StatusLine, SubjectBadge } from "../../components/ui";
import { formatClock, formatDuration, formatLongDate } from "../../lib/time";
import { KIND_LABEL, type Block } from "./calendar";
import { reasonLabels } from "./reasons";

export function BlockDialog({ block, onClose }: { block: Block; onClose: () => void }) {
  const subjects = useSubjects();
  const navigate = useNavigate();
  const update = useUpdateSession();
  const finish = useFinishSession();
  const [actual, setActual] = useState(block.endMin - block.startMin);
  const session = block.session;
  const tz = subjects.timezone;
  const error = update.error ?? finish.error;

  return (
    <Dialog title={block.title} onClose={onClose}>
      <div className="row">
        <span className="pill">{KIND_LABEL[block.kind]}</span>
        <SubjectBadge acronym={subjects.acronym(block.subjectId)} color={subjects.color(block.subjectId)} />
        {block.subjectId && <span className="small muted">{subjects.name(block.subjectId)}</span>}
      </div>
      <p>
        <strong>{formatLongDate(block.day)}</strong>, {formatClock(block.startAt, tz)} – {formatClock(block.endAt, tz)} ·{" "}
        {formatDuration(block.endMin - block.startMin)}
      </p>

      {block.event && (
        <div className="stack small">
          {block.event.room && <span>Salle : {block.event.room}</span>}
          {block.event.teacher && <span>Enseignant·e : {block.event.teacher}</span>}
          {block.event.read_only && (
            <span className="muted">Importé ({block.event.source}) : lecture seule, la source d'origine n'est jamais modifiée.</span>
          )}
          {block.event.confirmation_required && (
            <StatusLine tone="warning" label="Créneau à confirmer">
              confirmez-le dans l'agenda pour que le planner s'appuie dessus
            </StatusLine>
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
              <strong>Pourquoi : </strong>
              {reasonLabels(session.reason).join(" · ")}
            </div>
          )}
          {session.generation_spec && (
            <div className="muted">
              Support prévu : {String((session.generation_spec as { artifact_type?: string }).artifact_type ?? "—")}, généré
              uniquement au démarrage de la séance.
            </div>
          )}
          <div className="muted">Statut : {session.status}</div>
        </div>
      )}

      {error && <div className="status status--critical">{errorMessage(error)}</div>}

      {session && (session.status === "planned" || session.status === "in_progress") && (
        <div className="stack">
          <div className="row">
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => navigate(`/focus?session=${session.id}`)}
            >
              Lancer la Focus Room
            </button>
            <button
              type="button"
              className="btn"
              disabled={update.isPending}
              onClick={() =>
                update.mutate(
                  { id: session.id, expected_revision: session.revision, locked: !session.locked },
                  { onSuccess: onClose },
                )
              }
            >
              {session.locked ? "Déverrouiller" : "Verrouiller ce créneau"}
            </button>
          </div>
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
                disabled={finish.isPending}
                onClick={() =>
                  finish.mutate({ id: session.id, body: { outcome: "completed", actual_min: actual } }, { onSuccess: onClose })
                }
              >
                Terminée
              </button>
              <button
                type="button"
                className="btn btn--small"
                disabled={finish.isPending}
                onClick={() =>
                  finish.mutate({ id: session.id, body: { outcome: "partial", actual_min: actual } }, { onSuccess: onClose })
                }
              >
                Partielle
              </button>
              <button
                type="button"
                className="btn btn--small btn--danger"
                disabled={finish.isPending}
                onClick={() => finish.mutate({ id: session.id, body: { outcome: "skipped", actual_min: 0 } }, { onSuccess: onClose })}
              >
                Non faite
              </button>
            </div>
            <span className="xsmall muted">Le travail restant est replanifié au prochain calcul du planning.</span>
          </fieldset>
        </div>
      )}
    </Dialog>
  );
}

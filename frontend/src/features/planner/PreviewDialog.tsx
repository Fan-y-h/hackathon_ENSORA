import { ApiError, errorMessage } from "../../api/client";
import { useAcceptPlan } from "../../api/queries";
import type { PlanPreview } from "../../api/types";
import { Dialog, StatusLine } from "../../components/ui";
import { formatDuration, formatShortDate } from "../../lib/time";
import { unscheduledReason, warningLabel } from "./calendar";

/**
 * Plan preview: nothing is committed until "Accepter" (docs/SMART_PLANNER.md).
 * A 409 means the inputs changed after the preview was computed.
 */
export function PreviewDialog({
  preview,
  onClose,
  onRecompute,
}: {
  preview: PlanPreview;
  onClose: () => void;
  onRecompute: () => void;
}) {
  const accept = useAcceptPlan();
  const stale = accept.error instanceof ApiError && accept.error.isConflict;
  const { summary, diff } = preview;
  const warnings = [...new Set(preview.warnings.map(warningLabel))];

  return (
    <Dialog title="Proposition de planning" onClose={onClose}>
      <p className="small muted">
        Calculée sans IA (algorithme {preview.algorithm_version}) à partir de votre agenda, de vos tâches, de vos
        révisions dues et de vos plafonds. Rien n'est modifié tant que vous n'acceptez pas.
      </p>

      <div className="row" style={{ gap: 16 }}>
        <div className="stat">
          <span className="stat__label">Planifié</span>
          <span className="stat__value" style={{ fontSize: 24 }}>{formatDuration(summary.scheduled_min)}</span>
        </div>
        <div className="stat">
          <span className="stat__label">Hors limites</span>
          <span className="stat__value" style={{ fontSize: 24 }}>{formatDuration(summary.unscheduled_min)}</span>
        </div>
        <div className="stat">
          <span className="stat__label">Changements</span>
          <span className="stat__value" style={{ fontSize: 24 }}>
            +{diff.added.length} · ↔{diff.moved.length} · −{diff.removed.length}
          </span>
        </div>
      </div>

      <div className="stack" style={{ gap: 6 }}>
        <strong className="small">Charge par jour</strong>
        {summary.per_day.map((d) => {
          const over = d.planned_min > d.daily_max_min;
          return (
            <div key={d.date} className={`load-row${over ? " load-row--over" : ""}`}>
              <span className="muted">{formatShortDate(d.date)}</span>
              <div className="meter" role="img" aria-label={`${formatDuration(d.planned_min)} sur ${formatDuration(d.daily_max_min)}`}>
                <div style={{ width: `${Math.min(100, (d.planned_min / Math.max(1, d.daily_max_min)) * 100)}%` }} />
              </div>
              <span className="tabular strong" style={{ textAlign: "right" }}>
                {formatDuration(d.planned_min)}
              </span>
            </div>
          );
        })}
      </div>

      {preview.unscheduled.length > 0 && (
        <div className="card card--warning" style={{ padding: 14 }}>
          <StatusLine tone="warning" label="Non planifiable dans l'horizon" />
          <ul className="diff-list">
            {preview.unscheduled.map((u) => (
              <li key={u.need_id}>
                <span>{formatDuration(u.remaining_min)}</span>
                <span className="muted">
                  {u.title} — {unscheduledReason(u.reason)}
                </span>
              </li>
            ))}
          </ul>
          <span className="xsmall">Vos plafonds de travail ne sont jamais dépassés automatiquement.</span>
        </div>
      )}

      {warnings.length > 0 && (
        <ul className="diff-list small">
          {warnings.map((w) => (
            <li key={w}>
              <span aria-hidden>•</span>
              {w}
            </li>
          ))}
        </ul>
      )}

      {accept.error && (
        <div className="status status--critical" role="alert">
          {stale ? "Vos données ont changé depuis ce calcul : recalculez la proposition." : errorMessage(accept.error)}
        </div>
      )}

      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button type="button" className="btn" onClick={stale ? onRecompute : onClose}>
          {stale ? "Recalculer" : "Annuler"}
        </button>
        <button type="button" className="btn btn--primary" disabled={accept.isPending || stale} onClick={() => accept.mutate(preview, { onSuccess: onClose })}>
          {accept.isPending ? "Enregistrement…" : "Accepter ce planning"}
        </button>
      </div>
    </Dialog>
  );
}

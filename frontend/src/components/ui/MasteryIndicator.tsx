import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/cn";

interface MasteryIndicatorProps {
  /**
   * Estimated mastery between 0 and 1, from GET /mastery.
   * null means "not evaluated yet" and must never be shown as 0 %.
   */
  value: number | null;
  /** What is measured, read by screen readers (e.g. the concept name). */
  label: string;
  /** Mastery.confidence below 0.4: the estimate rests on very few answers. */
  lowEvidence?: boolean;
  className?: string;
}

// Display-only status names and thresholds from docs/ADAPTIVE_LEARNING.md.
// The backend owns the estimate itself.
function level(value: number): { text: string; bar: string; ink: string } {
  if (value < 0.5) return { text: "En apprentissage", bar: "bg-danger", ink: "text-danger" };
  if (value < 0.7) return { text: "Fragile", bar: "bg-warning", ink: "text-warning" };
  return { text: "Acquis", bar: "bg-success", ink: "text-success" };
}

export function MasteryIndicator({ value, label, lowEvidence = false, className }: MasteryIndicatorProps) {
  if (value === null) {
    return (
      <div className={cn("flex items-center gap-3", className)}>
        <div className="h-2 flex-1 rounded-full border border-dashed border-line" aria-hidden="true" />
        <span className="w-36 text-right text-xs text-muted">Non évalué</span>
        <span className="sr-only">{label} : pas encore évalué</span>
      </div>
    );
  }

  const clamped = Math.min(1, Math.max(0, value));
  const { text, bar, ink } = level(clamped);
  const evidenceText = lowEvidence ? ", peu de données" : "";

  return (
    <div className={cn("flex items-center gap-3", className)}>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(clamped * 100)}
        aria-valuetext={`${formatPercent(clamped)}, ${text.toLowerCase()}${evidenceText}`}
        className="h-2 flex-1 overflow-hidden rounded-full bg-surface-2"
      >
        <div className={cn("h-full rounded-full", bar, lowEvidence && "opacity-60")} style={{ width: `${clamped * 100}%` }} />
      </div>
      <span className="w-36 text-right text-xs tabular-nums" aria-hidden="true">
        <span className={cn("font-medium", ink)}>
          {formatPercent(clamped)} · {text}
        </span>
        {lowEvidence && <span className="block text-muted">peu de données</span>}
      </span>
    </div>
  );
}

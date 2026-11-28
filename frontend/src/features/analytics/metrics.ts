import type { HeatmapDay, MasteryCell } from "../../api/types";
import { addDays } from "../../lib/time";

export const activeMinutes = (d: Pick<HeatmapDay, "study_min" | "focus_min">) => Math.max(d.study_min, d.focus_min);

const isActive = (d: HeatmapDay | undefined) => Boolean(d && (activeMinutes(d) > 0 || d.reviews > 0 || d.evidence > 0));

/** Consecutive active days ending today (or yesterday, so the streak survives until tonight). */
export function currentStreak(days: HeatmapDay[], today: string): number {
  const byDate = new Map(days.map((d) => [d.date, d]));
  let cursor = isActive(byDate.get(today)) ? today : addDays(today, -1);
  let streak = 0;
  while (isActive(byDate.get(cursor))) {
    streak += 1;
    cursor = addDays(cursor, -1);
  }
  return streak;
}

/** Index 0..5 on the sequential ramp, or null for "no evidence" (never shown as zero). */
export function rampStep(score: number | null | undefined): number | null {
  if (score === null || score === undefined) return null;
  const s = Math.max(0, Math.min(1, score));
  return Math.min(5, Math.floor(s * 6));
}

export function minutesStep(minutes: number): number | null {
  if (minutes <= 0) return null;
  if (minutes < 30) return 0;
  if (minutes < 60) return 1;
  if (minutes < 120) return 3;
  return 5;
}

export function cellLabel(cell: MasteryCell | undefined): string {
  if (!cell || cell.score === null) return "non évalué";
  return `${Math.round(cell.score * 100)} %, ${STATUS_LABEL[cell.status]}`;
}

export const STATUS_LABEL: Record<MasteryCell["status"], string> = {
  unknown: "non évalué",
  learning: "en cours d'apprentissage",
  weak: "insuffisant",
  acquired: "acquis",
  consolidate: "à consolider",
};

export const DIMENSION_LABEL: Record<string, string> = {
  theory: "Compréhension",
  memory: "Mémorisation",
  reasoning: "Raisonnement",
  application: "Application",
  implementation: "Implémentation",
  autonomy: "Autonomie",
  retention: "Rétention",
};

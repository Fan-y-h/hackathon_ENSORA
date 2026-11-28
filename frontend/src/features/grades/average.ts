/**
 * Averages for the "Résultats" screen, same rules as the student's spreadsheet:
 * - a subject without a mark is not counted (missing is never 0);
 * - subject mark = official Pamplemousse mark, else the coefficient-weighted mean of its
 *   assessments (each normalised to /20);
 * - UE average = Σ coef × mark / Σ coef over marked subjects;
 * - semester average = UE averages weighted by their marked coefficients.
 * Simulated marks (what-if) override missing values only and make the result provisional.
 * Official averages published by Pamplemousse are shown separately, never recomputed.
 */
import type { ResultsAssessment, ResultsSemester, ResultsSubject, ResultsUnit } from "../../api/sources";

export type Simulations = Record<string, string>;

export const parseMark = (value: string | number | null | undefined): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number.parseFloat(String(value).replace(",", "."));
  return Number.isFinite(n) ? n : null;
};

export function assessmentMark(a: ResultsAssessment, sims: Simulations): { mark: number | null; simulated: boolean } {
  if (a.grade !== null) return { mark: a.grade, simulated: false };
  const sim = parseMark(sims[a.key]);
  return { mark: sim, simulated: sim !== null };
}

export function subjectMark(s: ResultsSubject, sims: Simulations): { mark: number | null; official: boolean; simulated: boolean } {
  if (s.official_grade !== null) return { mark: s.official_grade, official: true, simulated: false };
  const direct = parseMark(sims[s.key]);
  if (direct !== null) return { mark: direct, official: false, simulated: true };
  let sum = 0;
  let weight = 0;
  let simulated = false;
  for (const a of s.assessments) {
    const { mark, simulated: sim } = assessmentMark(a, sims);
    const coef = a.coefficient ?? a.weight ?? 1;
    if (mark === null || coef <= 0) continue;
    sum += (coef * mark * 20) / (a.maximum || 20);
    weight += coef;
    simulated ||= sim;
  }
  return { mark: weight > 0 ? sum / weight : null, official: false, simulated };
}

export interface Average {
  value: number | null;
  markedCoef: number;
  totalCoef: number;
  simulated: boolean;
}

export function unitAverage(u: ResultsUnit, sims: Simulations): Average {
  let sum = 0;
  let marked = 0;
  let total = 0;
  let simulated = false;
  for (const s of u.subjects) {
    const coef = s.coefficient ?? 0;
    total += coef;
    const { mark, simulated: sim } = subjectMark(s, sims);
    if (mark === null || coef <= 0) continue;
    sum += coef * mark;
    marked += coef;
    simulated ||= sim;
  }
  return { value: marked > 0 ? sum / marked : null, markedCoef: marked, totalCoef: total, simulated };
}

export function semesterAverage(sem: ResultsSemester, sims: Simulations): Average {
  let sum = 0;
  let marked = 0;
  let total = 0;
  let simulated = false;
  for (const u of sem.units) {
    const avg = unitAverage(u, sims);
    total += avg.totalCoef;
    if (avg.value === null) continue;
    sum += avg.value * avg.markedCoef;
    marked += avg.markedCoef;
    simulated ||= avg.simulated;
  }
  return { value: marked > 0 ? sum / marked : null, markedCoef: marked, totalCoef: total, simulated };
}

export const fmt = (n: number | null | undefined, digits = 2) =>
  n === null || n === undefined ? "–" : n.toFixed(digits).replace(".", ",");

/** "4" / "2,5" / "0,25": coefficients without useless zeros. */
export const fmtCoef = (n: number | null | undefined) =>
  n === null || n === undefined ? "–" : String(Math.round(n * 1000) / 1000).replace(".", ",");

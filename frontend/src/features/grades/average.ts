/**
 * Averages for the Pamplemousse "Relevé" view:
 * - a subject without a mark is not counted (missing is never 0);
 * - subject mark = official Pamplemousse mark, else the coefficient-weighted mean of its
 *   assessments (each normalised to /20);
 * - UE average = Σ coef × mark / Σ coef over marked subjects;
 * - semester average = UE averages weighted by their marked coefficients.
 * Simulated marks (what-if) and custom coefficients override missing values only and make
 * the result explicitly provisional. Unknown coefficients are tracked, never invented.
 */
import type { ResultsAssessment, ResultsSemester, ResultsSubject, ResultsUnit } from "../../api/sources";

export type Simulations = Record<string, string>;
export type CustomCoefficients = Record<string, string>;

export const parseMark = (value: string | number | null | undefined): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number.parseFloat(String(value).replace(",", "."));
  return Number.isFinite(n) ? n : null;
};

export function effectiveCoefficient(
  official: number | null | undefined,
  key: string,
  customCoefs: CustomCoefficients = {},
): { value: number | null; source: "official" | "custom" | "unknown" } {
  if (official !== null && official !== undefined && Number.isFinite(official) && official > 0) {
    return { value: official, source: "official" };
  }
  const parsed = parseMark(customCoefs[key]);
  if (parsed !== null && parsed > 0) {
    return { value: parsed, source: "custom" };
  }
  return { value: null, source: "unknown" };
}

export function assessmentMark(a: ResultsAssessment, sims: Simulations): { mark: number | null; simulated: boolean } {
  if (a.grade !== null) return { mark: a.grade, simulated: false };
  const sim = parseMark(sims[a.key]);
  if (sim === null || sim < 0 || sim > (a.maximum || 20)) return { mark: null, simulated: false };
  return { mark: sim, simulated: true };
}

export function subjectMark(
  s: ResultsSubject,
  sims: Simulations,
  customCoefs: CustomCoefficients = {},
): { mark: number | null; official: boolean; simulated: boolean; unknownAssessmentCoefs: number } {
  if (s.official_grade !== null) {
    return { mark: s.official_grade, official: true, simulated: false, unknownAssessmentCoefs: 0 };
  }
  const direct = parseMark(sims[s.key]);
  if (direct !== null && direct >= 0 && direct <= 20) {
    return { mark: direct, official: false, simulated: true, unknownAssessmentCoefs: 0 };
  }
  let sum = 0;
  let weight = 0;
  let simulated = false;
  let unknownAssessmentCoefs = 0;
  for (const a of s.assessments) {
    const { mark, simulated: sim } = assessmentMark(a, sims);
    const coefInfo = effectiveCoefficient(a.coefficient ?? a.weight, a.key, customCoefs);
    if (coefInfo.value === null) {
      unknownAssessmentCoefs += 1;
      continue;
    }
    if (mark === null) continue;
    sum += (coefInfo.value * mark * 20) / (a.maximum || 20);
    weight += coefInfo.value;
    simulated ||= sim;
  }
  return { mark: weight > 0 ? sum / weight : null, official: false, simulated, unknownAssessmentCoefs };
}

export interface Average {
  value: number | null;
  markedCoef: number;
  totalCoef: number;
  simulated: boolean;
  unknownCoefCount: number;
}

export function unitAverage(
  u: ResultsUnit,
  sims: Simulations,
  customCoefs: CustomCoefficients = {},
): Average {
  let sum = 0;
  let marked = 0;
  let total = 0;
  let simulated = false;
  let unknownCoefCount = 0;
  for (const s of u.subjects) {
    const coefInfo = effectiveCoefficient(s.coefficient, s.key, customCoefs);
    if (coefInfo.value === null) {
      unknownCoefCount += 1;
      continue;
    }
    total += coefInfo.value;
    const { mark, simulated: sim } = subjectMark(s, sims, customCoefs);
    if (mark === null) continue;
    sum += coefInfo.value * mark;
    marked += coefInfo.value;
    simulated ||= sim || coefInfo.source === "custom";
  }
  return { value: marked > 0 ? sum / marked : null, markedCoef: marked, totalCoef: total, simulated, unknownCoefCount };
}

export function semesterAverage(
  sem: ResultsSemester,
  sims: Simulations,
  customCoefs: CustomCoefficients = {},
): Average {
  let sum = 0;
  let marked = 0;
  let total = 0;
  let simulated = false;
  let unknownCoefCount = 0;
  for (const u of sem.units) {
    const avg = unitAverage(u, sims, customCoefs);
    total += avg.totalCoef;
    unknownCoefCount += avg.unknownCoefCount;
    if (avg.value === null) continue;
    sum += avg.value * avg.markedCoef;
    marked += avg.markedCoef;
    simulated ||= avg.simulated;
  }
  return { value: marked > 0 ? sum / marked : null, markedCoef: marked, totalCoef: total, simulated, unknownCoefCount };
}

export interface TargetRequirement {
  status: "achieved" | "guaranteed" | "feasible" | "impossible" | "unknown";
  requiredMark: number | null;
  maxReachable: number | null;
}

/** Computes the uniform /20 mark needed on remaining known-coef weight to reach `target`. */
export function requiredForTarget(avg: Average, target: number): TargetRequirement {
  if (avg.totalCoef <= 0 || avg.unknownCoefCount > 0) {
    return { status: "unknown", requiredMark: null, maxReachable: null };
  }
  const earned = (avg.value ?? 0) * avg.markedCoef;
  const remaining = avg.totalCoef - avg.markedCoef;
  const maxReachable = (earned + remaining * 20) / avg.totalCoef;
  const minReachable = earned / avg.totalCoef;
  if (remaining <= 1e-9) {
    return {
      status: avg.value !== null && avg.value >= target - 1e-9 ? "achieved" : "impossible",
      requiredMark: null,
      maxReachable: avg.value,
    };
  }
  if (minReachable >= target - 1e-9) {
    return { status: "guaranteed", requiredMark: 0, maxReachable };
  }
  const needed = (target * avg.totalCoef - earned) / remaining;
  if (needed > 20 + 1e-9) {
    return { status: "impossible", requiredMark: needed, maxReachable };
  }
  return { status: "feasible", requiredMark: Math.max(0, needed), maxReachable };
}

export const fmt = (n: number | null | undefined, digits = 2) =>
  n === null || n === undefined ? "–" : n.toFixed(digits).replace(".", ",");

/** Formats a decimal string coming from `/api/v1/grades/*` without binary float drift. */
export const fmtDec = (value: string | null | undefined, digits = 2): string => {
  if (value === null || value === undefined || value === "") return "–";
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits).replace(".", ",") : value;
};

/** "4" / "2,5" / "0,25": coefficients without useless zeros. */
export const fmtCoef = (n: number | string | null | undefined) => {
  if (n === null || n === undefined || n === "") return "Inconnu";
  const num = typeof n === "number" ? n : Number.parseFloat(n);
  if (!Number.isFinite(num)) return "Inconnu";
  return String(Math.round(num * 1000) / 1000).replace(".", ",");
};

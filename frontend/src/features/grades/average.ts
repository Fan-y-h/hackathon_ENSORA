/**
 * Averages for the Pamplemousse "Relevé" view:
 * - a subject without a mark is not counted (missing is never 0);
 * - subject mark = official Pamplemousse mark, else the coefficient-weighted mean of its
 *   assessments (each normalised to /20);
 * - UE average = Σ coef × mark / Σ coef over marked subjects;
 * - semester average uses published UE weights and excludes unassigned courses.
 * Simulated marks (what-if) and custom coefficients override missing values only and make
 * the result explicitly provisional. Unknown coefficients are tracked, never invented.
 */
import type { ResultsAssessment, ResultsSemester, ResultsSubject, ResultsUnit } from "../../api/sources";

export type Simulations = Record<string, string>;
export type CustomCoefficients = Record<string, string>;

export const parseMark = (value: string | number | null | undefined): number | null => {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string" && !/^\s*[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)\s*$/.test(value)) return null;
  const n = typeof value === "number" ? value : Number(value.replace(",", "."));
  return Number.isFinite(n) ? n : null;
};

export function effectiveCoefficient(
  official: number | null | undefined,
  key: string,
  customCoefs: CustomCoefficients = {},
): { value: number | null; source: "official" | "custom" | "unknown" } {
  if (official !== null && official !== undefined && Number.isFinite(official) && official >= 0) {
    return { value: official, source: "official" };
  }
  const parsed = parseMark(customCoefs[key]);
  if (parsed !== null && parsed >= 0) {
    return { value: parsed, source: "custom" };
  }
  return { value: null, source: "unknown" };
}

export function assessmentMark(a: ResultsAssessment, sims: Simulations): { mark: number | null; simulated: boolean } {
  if (a.grade !== null) return { mark: a.grade, simulated: false };
  const sim = parseMark(sims[a.key]);
  if (a.maximum === null || sim === null || sim < (a.minimum ?? 0) || sim > a.maximum) return { mark: null, simulated: false };
  return { mark: sim, simulated: true };
}

function toTwenty(mark: number, maximum: number | null | undefined, minimum: number | null = 0): number | null {
  const floor = minimum ?? 0;
  return maximum != null && maximum > floor ? 20 * (mark - floor) / (maximum - floor) : null;
}

export function subjectMark(
  s: ResultsSubject,
  sims: Simulations,
  customCoefs: CustomCoefficients = {},
): { mark: number | null; official: boolean; simulated: boolean; unknownAssessmentCoefs: number } {
  if (s.official_grade !== null) {
    return { mark: toTwenty(s.official_grade, s.official_grade_maximum, s.official_grade_minimum), official: true, simulated: false, unknownAssessmentCoefs: 0 };
  }
  const direct = parseMark(sims[s.key]);
  if (direct !== null && direct >= 0 && direct <= 20) {
    return { mark: direct, official: false, simulated: true, unknownAssessmentCoefs: 0 };
  }
  const average = assessmentAverage(s.assessments, sims, customCoefs);
  return { mark: average.value, official: false, simulated: average.simulated, unknownAssessmentCoefs: average.unknownCoefCount };
}

function assessmentAverage(
  assessments: ResultsAssessment[],
  sims: Simulations,
  customCoefs: CustomCoefficients,
): Average {
  let sum = 0;
  let marked = 0;
  let total = 0;
  let simulated = false;
  let unknownCoefCount = 0;
  for (const assessment of assessments) {
    const coefficient = effectiveCoefficient(assessment.coefficient ?? assessment.weight, assessment.key, customCoefs);
    if (coefficient.value === null) {
      unknownCoefCount += 1;
      continue;
    }
    if (coefficient.value === 0) continue;
    total += coefficient.value;
    const { mark, simulated: simulatedLeaf } = assessmentMark(assessment, sims);
    const normalized = mark === null ? null : toTwenty(mark, assessment.maximum, assessment.minimum);
    if (normalized !== null) {
      sum += coefficient.value * normalized;
      marked += coefficient.value;
      simulated ||= simulatedLeaf || coefficient.source === "custom";
    } else if (assessment.children?.length) {
      const nested = assessmentAverage(assessment.children, sims, customCoefs);
      unknownCoefCount += nested.unknownCoefCount;
      if (nested.value === null || nested.unknownCoefCount > 0 || nested.totalCoef === 0) continue;
      const coverageWeight = coefficient.value * nested.markedCoef / nested.totalCoef;
      sum += nested.value * coverageWeight;
      marked += coverageWeight;
      simulated ||= nested.simulated || coefficient.source === "custom";
    }
  }
  return { value: marked > 0 ? sum / marked : null, markedCoef: marked, totalCoef: total, simulated, unknownCoefCount };
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
    if (coefInfo.value === 0) continue;
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
    if (u.unassigned) continue;
    const coefficient = effectiveCoefficient(u.coefficient, u.key, customCoefs);
    if (coefficient.value === null) {
      unknownCoefCount += 1;
      continue;
    }
    if (coefficient.value === 0) continue;
    const avg = unitAverage(u, sims, customCoefs);
    total += coefficient.value;
    unknownCoefCount += avg.unknownCoefCount;
    if (!avg.simulated && u.official_average !== null && u.official_average_maximum) {
      sum += coefficient.value * u.official_average * 20 / u.official_average_maximum;
      marked += coefficient.value;
    } else if (avg.value !== null && avg.totalCoef > 0 && avg.unknownCoefCount === 0) {
      const coverageWeight = coefficient.value * avg.markedCoef / avg.totalCoef;
      sum += avg.value * coverageWeight;
      marked += coverageWeight;
    }
    simulated ||= avg.simulated || coefficient.source === "custom";
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

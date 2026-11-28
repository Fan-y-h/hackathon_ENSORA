// Grade Tracker & Academic Optimizer (docs/GRADE_TRACKER.md, academic API extension).
// Grades and weights are decimal strings; the frontend never computes official results.
import type { DecimalString, IsoDateTime, Uuid, Versioned } from "./types";

export interface AcademicProgram extends Versioned {
  academic_year: string;
  cohort: string;
  specialization: string | null;
  title: string;
  /** ASSUMPTION to confirm with backend: one program per semester. */
  semester: 1 | 2;
}

export type NodeKind = "year" | "semester" | "unit" | "subject" | "assessment_group" | "assessment";

export interface AcademicNode {
  id: Uuid;
  parent_id: Uuid | null;
  kind: NodeKind;
  title: string;
  weight: DecimalString;
  weight_mode: "coefficient" | "fraction";
  subject_id: Uuid | null;
  event_id: Uuid | null;
}

export interface Curriculum {
  program_id: Uuid;
  revision: number;
  nodes: AcademicNode[];
}

export interface GradeRecord extends Versioned {
  node_id: Uuid;
  kind: "actual" | "estimated";
  value: DecimalString;
  provenance: "manual" | "import";
}

export interface NodeSummary {
  node_id: Uuid;
  /** Mean of the marks already known, weighted; null = no mark yet. */
  provisional: DecimalString | null;
  /** Share (0..1) of the weight already graded. */
  actual_coverage: DecimalString;
  forecast: DecimalString | null;
  remaining_weight: DecimalString;
  /** Never "validated" unless the official rules were verified. */
  official_status: "unknown" | "validated" | "not_validated";
}

export interface GradeSummary {
  program_id: Uuid;
  revision: number;
  nodes: NodeSummary[];
}

export interface Objective extends Versioned {
  node_id: Uuid;
  target_kind: "numeric" | "pass";
  target_value: DecimalString | null;
  active: boolean;
}

export type Feasibility = "feasible" | "guaranteed" | "impossible" | "unknown";

export interface RequiredResult {
  node_id: Uuid;
  required_mean: DecimalString | null;
  feasibility: Feasibility;
  assumptions: string[];
  reason_codes: string[];
}

export interface SimulationResult {
  /** Projected values per node, separate from the real summary (nothing is saved). */
  nodes: Array<{ node_id: Uuid; baseline: DecimalString | null; projected: DecimalString | null }>;
  warnings: string[];
}

export interface GradeRecordInput {
  node_id: Uuid;
  value: DecimalString;
  observed_at?: IsoDateTime;
}

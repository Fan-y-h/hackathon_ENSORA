// Smart Planner, sessions, focus and analytics (docs/SMART_PLANNER.md, docs/FEATURES.md).
import type { IsoDateTime, Uuid, Versioned } from "./types";

export interface Assessment extends Versioned {
  event_id: Uuid | null;
  subject_id: Uuid;
  title: string;
  deadline_at: IsoDateTime;
  format: "written" | "oral" | "project" | "quiz";
  duration_min: number | null;
  /** proposed = detected, must be confirmed before the planner relies on it. */
  scope_status: "proposed" | "confirmed";
  concept_ids: Uuid[];
}

export type ActivityType = "learn" | "active_recall" | "practice" | "review" | "task" | "mock_exam";

export type SessionStatus = "planned" | "in_progress" | "completed" | "partial" | "skipped" | "cancelled";

export interface StudySession {
  id: Uuid;
  subject_id: Uuid | null;
  task_id: Uuid | null;
  assessment_id: Uuid | null;
  concept_ids: Uuid[];
  activity_type: ActivityType;
  title: string;
  start_at: IsoDateTime;
  end_at: IsoDateTime;
  estimated_min: number;
  actual_min: number | null;
  remaining_min: number | null;
  objective: string;
  success_criteria: string;
  locked: boolean;
  status: SessionStatus;
  /** Short explanation of why the planner placed it (priority reasons). */
  reason: string;
}

export interface Plan {
  id: Uuid;
  revision: number;
  accepted_at: IsoDateTime;
  horizon_start: IsoDateTime;
  horizon_end: IsoDateTime;
  sessions: StudySession[];
}

export interface UnscheduledNeed {
  need_id: Uuid;
  label: string;
  subject_id: Uuid | null;
  remaining_min: number;
  reason: "CAPACITY" | "DEADLINE_PASSED" | "UNCONFIRMED_SCOPE";
}

export interface PlanPreview {
  preview_id: Uuid;
  input_revision: string;
  sessions: StudySession[];
  unscheduled: UnscheduledNeed[];
  warnings: string[];
  summary: { planned_min: number; unscheduled_min: number; days: Array<{ date: string; minutes: number }> };
  /** ASSUMPTION: replan previews list what changes compared with the current plan. */
  diff: { added: Uuid[]; moved: Uuid[]; removed: Uuid[] } | null;
}

export interface SessionFinish {
  status: "completed" | "partial" | "skipped";
  actual_min: number;
  remaining_min: number;
  reflection: string;
}

// --- Focus room --------------------------------------------------------------------

export interface WorkInterval {
  kind: "work" | "break";
  started_at: IsoDateTime;
  ended_at: IsoDateTime | null;
}

export interface FocusSession {
  id: Uuid;
  study_session_id: Uuid | null;
  goal: string;
  mode: "pomodoro" | "free";
  work_min: number;
  break_min: number;
  status: "running" | "paused" | "finished";
  intervals: WorkInterval[];
}

// --- Analytics -------------------------------------------------------------------------

export interface Analytics {
  range: { from: IsoDateTime; to: IsoDateTime };
  work_by_day: Array<{ date: string; minutes: number }>;
  planned_min: number;
  completed_min: number;
  scores: Array<{ date: string; subject_id: Uuid; score: number }>;
  mastery_by_subject: Array<{ subject_id: Uuid; unknown: number; learning: number; fragile: number; acquired: number }>;
  weak_concepts: Array<{ concept_id: Uuid; label: string; subject_id: Uuid; score: number }>;
  /** Minutes worked per weekday (index 0 = Monday) for each of the last 4 weeks, oldest first. */
  heatmap: Array<{ week_start: string; minutes: number[] }>;
  /** Readiness of each upcoming confirmed assessment: acquired concepts out of the confirmed scope. */
  readiness: Array<{ assessment_id: Uuid; title: string; deadline_at: IsoDateTime; acquired: number; total: number; planned_min: number }>;
}

// --- Task decomposition ----------------------------------------------------------------

/** ASSUMPTION: GET /tasks/proposals/{id}, the result of POST /tasks/decompose. */
export interface TaskProposal {
  id: Uuid;
  steps: Array<{ title: string; estimated_min: number }>;
}

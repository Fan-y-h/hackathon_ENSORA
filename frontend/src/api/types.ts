/**
 * DTO aliases. `schema.d.ts` is GENERATED from the backend OpenAPI (`npm run gen:api`);
 * never edit it by hand. The few endpoints the backend still returns as untyped dicts are
 * described at the bottom, shaped on the current service output - replace them with
 * generated types once the backend declares response models.
 */
import type { components } from "./schema";

type S = components["schemas"];

export type Profile = S["ProfileOut"];
export type ProfilePatch = S["ProfilePatch"];
export type StudentStatus = NonNullable<Profile["student_status"]>;
export type StudentStatusOption = S["StudentStatusOptionOut"];
export type Preferences = S["PreferencesOut"];
export type TimeWindow = S["TimeWindow"];

export type CalendarEvent = S["EventOut"];
export type EventCreate = S["EventCreate"];
export type EventPage = S["Page_EventOut_"];
export type Assessment = S["AssessmentOut"];
export type AssessmentPage = S["Page_AssessmentOut_"];
export type Task = S["TaskOut"];
export type TaskCreate = S["TaskCreate"];
export type TaskPatch = S["TaskPatch"];
export type TaskPage = S["Page_TaskOut_"];
export type Availability = S["AvailabilityOut"];

export type Plan = S["PlanOut"];
export type PlanPreview = S["PlanPreviewOut"];
export type PreviewSession = S["PreviewSessionOut"];
export type StudySession = S["SessionOut"];
export type SessionFinish = S["SessionFinish"];
export type PlannerStatus = S["PlannerStatusOut"];
export type ReplanReason = S["ReplanRequest"]["reason"];

export type Deck = S["DeckOut"];
export type Card = S["CardOut"];
export type ReviewResult = S["ReviewResult"];
export type Rating = S["ReviewRequest"]["rating"];
export type Quiz = S["QuizOut"];
export type Question = S["QuestionPublic"];
export type Attempt = S["AttemptOut"];
export type AttemptResult = S["AttemptResult"];
export type Mastery = S["MasteryOut"];
export type StudyNeed = S["StudyNeedOut"];
export type ExamMode = S["ExamModeOut"];


export type Focus = S["FocusOut"];
export type FocusStart = S["FocusStart"];
export type FocusMode = NonNullable<FocusStart["mode"]>;
export type FocusAction = S["FocusTransition"]["action"];
export type FocusFinish = S["FocusFinish"];

export type Notebook = S["NotebookOut"];
export type Note = S["NoteOut"];
export type ErrorEntry = S["ErrorOut"];
export type NotificationList = S["NotificationList"];
export type AppNotification = S["NotificationOut"];

export type GradeProgram = S["ProgramOut"];
export type GradeSummary = S["ProgramSummaryOut"];

export type MasteryStatus = Mastery["status"];

export const DIMENSIONS = [
  "theory",
  "memory",
  "reasoning",
  "application",
  "implementation",
  "autonomy",
  "retention",
] as const;
export type Dimension = (typeof DIMENSIONS)[number];

/* ---- Endpoints without a declared response model (shaped on backend output) ---- */

export interface MasteryCell {
  score: number | null;
  status: MasteryStatus;
  confidence: number;
  next_due_at: string | null;
}

export interface MasteryMap {
  dimensions: Dimension[];
  concepts: { concept_id: string; dimensions: Partial<Record<Dimension, MasteryCell>> }[];
  note: string;
}

export interface HeatmapDay {
  date: string;
  study_min: number;
  focus_min: number;
  reviews: number;
  evidence: number;
}

export interface Heatmap {
  timezone: string;
  days: HeatmapDay[];
  regularity: { active_days: number; days_in_range: number; value: number | null };
}

export interface ReadinessItem {
  assessment_id: string;
  title: string;
  deadline_at: string | null;
  days_left: number | null;
  scope_status: string;
  readiness: {
    estimate: number | null;
    coverage: number | null;
    low: number | null;
    high: number | null;
    concepts: number;
    known: number;
  };
  planned_min_remaining: number;
  studied_min: number;
  weakest_concepts: { concept_id: string; score?: number | null; dimension?: string }[];
  historical_exam_weighted: boolean;
}

export interface Readiness {
  assessments: ReadinessItem[];
  notes: string[];
}

interface Ratio {
  value: number | null;
  numerator: number;
  denominator: number;
}

/** `AnalyticsOut` declares its sections as free dicts; these are the fields the service emits. */
export interface Analytics {
  start_at: string;
  end_at: string;
  timezone: string;
  subject_id: string | null;
  study: {
    planned_min: number;
    actual_min: number;
    sessions: Record<string, number>;
    adherence: Ratio;
    time_accuracy: Ratio;
  };
  per_day: { date: string; planned_min: number; actual_min: number; sessions: number }[];
  per_subject: { subject_id: string | null; planned_min: number; actual_min: number; sessions: number }[];
  quizzes: {
    attempts: number;
    score_ratio: { value: number | null; earned_points: number; possible_points: number };
    pending_review_items: number;
  };
  flashcards: {
    reviews: number;
    ratings: Record<"again" | "hard" | "good" | "easy", number>;
    again_rate: Ratio;
  };
  mastery: {
    concept_dimensions: number;
    concepts: number;
    by_status: Record<MasteryStatus, number>;
    mean_known_score: number | null;
    known_count: number;
    reviews_overdue: number;
    weakest: { concept_id: string; dimension?: string; score?: number | null }[];
  };
  academic: { program_id: string; title: string; provenance: string; provisional: string | null; coverage: string | null; official_status: string }[];
  upcoming: {
    assessments: { assessment_id: string; title: string; deadline_at: string | null; scope_status: string; confirmation_required: boolean }[];
    tasks_open: number;
    tasks_overdue: number;
    tasks_due_7d: number;
    remaining_task_min: number;
  };
  plan: { plan_id: string | null; revision: number | null; accepted_at: string | null; inputs_changed_since_accept: boolean };
  notes: string[];
}

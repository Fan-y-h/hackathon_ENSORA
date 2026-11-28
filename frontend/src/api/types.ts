/**
 * DTO aliases. `schema.d.ts` is GENERATED from the backend OpenAPI (`npm run gen:api`);
 * never edit it by hand. Frozen contracts from `contracts/*.md` that are pending Lead
 * mount (SYNC-1) are typed cleanly at the bottom so UI code stays strictly typed.
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
export type EventPatch = S["EventPatch"];
export type EventConfirm = S["EventConfirm"];
export type EventPage = S["Page_EventOut_"];
export type Assessment = S["app__modules__planner__schemas__AssessmentOut"];
export type AssessmentPatch = S["AssessmentPatch"];
export type AssessmentPage = S["Page_AssessmentOut_"];
export type Task = S["TaskOut"];
export type TaskCreate = S["TaskCreate"];
export type TaskPatch = S["TaskPatch"];
export type TaskPage = S["Page_TaskOut_"];
export type DecomposeOut = S["DecomposeOut"];
export type SubtasksCreate = S["SubtasksCreate"];
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
export type CardPatch = S["CardPatch"];
export type ReviewResult = S["ReviewResult"];
export type Rating = S["ReviewRequest"]["rating"];
export type Quiz = S["QuizOut"];
export type QuizCreate = S["QuizCreate"];
export type MockExamRequest = S["MockExamRequest"];
export type Question = S["QuestionPublic"];
export type Attempt = S["AttemptOut"];
export type AttemptResult = S["AttemptResult"];
export type ItemGrade = S["ItemGrade"];
export type CalibrationOut = S["CalibrationOut"];
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
export type NoteVersion = S["NoteVersionOut"];
export type ErrorEntry = S["ErrorOut"];
export type ErrorCreate = S["ErrorCreate"];
export type ErrorPatch = S["ErrorPatch"];
export type NotificationList = S["NotificationList"];
export type AppNotification = S["NotificationOut"];

/* -------------------------------- Grades S4 -------------------------------- */
export type GradeProgram = S["ProgramOut"];
export type ProgramCreate = S["ProgramCreate"];
export type CurriculumOut = S["CurriculumOut"];
export type CurriculumPut = S["CurriculumPut"];
export type NodeIn = S["NodeIn"];
export type NodeOut = S["NodeOut"];
export type NodePatch = S["NodePatch"];
export type GradeRecord = S["RecordOut"];
export type GradeRecordCreate = S["RecordCreate"];
export type GradeRecordPatch = S["RecordPatch"];
export type GradeObjective = S["ObjectiveOut"];
export type GradeObjectiveCreate = S["ObjectiveCreate"];
export type GradeObjectivePatch = S["ObjectivePatch"];
export type GradeSummary = S["ProgramSummaryOut"];
export type GradeSummaryNode = S["SummaryOut"];
export type RequiredRequest = S["RequiredRequest"];
export type RequiredOut = S["RequiredOut"];
export type RequirementOut = S["RequirementOut"];
export type SimulateRequest = S["SimulateRequest"];
export type SimulateOut = S["SimulateOut"];
export type SignalsOut = S["SignalsOut"];
export type SignalOut = S["SignalOut"];
export type RulesOut = S["RulesOut"];
export type RulesPut = S["RulesPut"];
export type ValidationOut = S["ValidationOut"];
export type ScenarioOut = S["ScenarioOut"];
export type ScenarioCreate = S["ScenarioCreate"];
export type ScenarioPatch = S["ScenarioPatch"];
export type ScenarioEvaluationOut = S["ScenarioEvaluationOut"];
export type TrendsOut = S["TrendsOut"];
export type FeedbackOut = S["FeedbackOut"];
export type FeedbackCreate = S["FeedbackCreate"];

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

/* ---- Frozen coordination contracts (contracts/API.md, ACADEMIC_DATA.md, AI_RAG.md) ---- */

export interface CatalogSubjectItem {
  id: string;
  code: string | null;
  title: string;
  source: string;
  document_count: number;
  assessment_count: number;
  mastery_mean: number | null;
  next_deadline_at: string | null;
}

export type CatalogSubjectDetail = S["SubjectDetailOut"];

export interface AnnotationOut {
  id: string;
  document_id: string;
  document_version_id?: string | null;
  chunk_id?: string | null;
  locator?: Record<string, unknown> | null;
  excerpt: string;
  comment?: string | null;
  note_id?: string | null;
  created_at: string;
}

export interface AnnotationCreate {
  document_id: string;
  document_version_id?: string | null;
  chunk_id?: string | null;
  locator?: Record<string, unknown> | null;
  excerpt: string;
  comment?: string | null;
  note_id?: string | null;
}

export interface GradesImportRequest {
  source: "pamplemousse" | "manual_json";
  academic_year?: string;
  dry_run?: boolean;
  program?: Record<string, unknown> | null;
}

export interface GradesImportResult {
  program_id: string | null;
  program_title: string;
  dry_run: boolean;
  nodes_imported: number;
  grades_imported: number;
  conflicts: string[];
  revision: number;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  version_id: string;
  title: string;
  subject_id: string | null;
  resource_type: string | null;
  score: number;
  excerpt: string;
  locator: { page?: number | null; heading?: string | null; [key: string]: unknown };
  citation_id: string;
}

export interface SearchResponse {
  query: string;
  mode: "hybrid" | "lexical" | "semantic";
  indexed_chunks: number;
  hits: SearchHit[];
}

export interface CitationOut {
  citation_id: string;
  document_id: string;
  version_id: string;
  chunk_id: string;
  title: string;
  locator: { page?: number | null; heading?: string | null; [key: string]: unknown };
  excerpt: string;
  content_url?: string | null;
}

export interface ReindexOut {
  documents_indexed: number;
  chunks_indexed: number;
  embedding_model: string;
  duration_ms: number;
}

export interface AiProvider {
  provider_id: string;
  kind: string;
  local: boolean;
  configured: boolean;
  available: boolean;
  error_code?: string | null;
  models: { provider_id: string; model_id: string }[];
}

export interface AiPreferences {
  enabled: boolean;
  cloud_data_consent: boolean;
  allow_extractive_fallback: boolean;
  preferred_provider: string | null;
  preferred_model: string | null;
  daily_token_budget: number;
}

export type TutorMode = "explain" | "derivation" | "code_explain" | "correction" | "socratic" | "exam_prep" | "memorize";

export interface ConversationOut {
  id: string;
  subject_id: string | null;
  title: string;
  created_at: string;
  updated_at?: string;
}

export interface ConversationMessageOut {
  id: string;
  role: "user" | "assistant";
  content_markdown: string;
  citations?: CitationOut[];
  provider?: string | null;
  model?: string | null;
  evidence_status?: "grounded" | "partial" | "insufficient" | null;
  is_fallback?: boolean;
  fallback_reason?: string | null;
  created_at: string;
}

export interface TutorAnswerRequest {
  question: string;
  conversation_id?: string | null;
  subject_id?: string | null;
  document_ids?: string[];
  mode?: TutorMode;
  locale?: "fr" | "en";
  provider_override?: string | null;
  model_override?: string | null;
}

export interface TutorAnswerOut {
  conversation_id: string;
  message_id: string;
  answer_markdown: string;
  citations: CitationOut[];
  evidence_status: "grounded" | "partial" | "insufficient";
  provider: string;
  model: string;
  generation_mode: string;
  is_fallback: boolean;
  fallback_reason: string | null;
  usage: { tokens_in: number; tokens_out: number };
}

export type ArtifactType = "revision_sheet" | "flashcards" | "quiz" | "exercise" | "mind_map";

export interface ArtifactGenerateRequest {
  topic?: string;
  type: ArtifactType;
  subject_id?: string | null;
  document_ids?: string[];
  concept_ids?: string[];
  objective?: string;
  locale?: "fr" | "en";
  difficulty?: "beginner" | "intermediate" | "advanced";
  duration_min?: number;
  detail_level?: "concise" | "standard" | "detailed";
  user_instructions?: string | null;
  force_regenerate?: boolean;
  persist_to_learning?: boolean;
}

export interface ArtifactOut {
  id: string;
  type: ArtifactType;
  title: string;
  subject_id: string | null;
  version: number;
  cache_hit: boolean;
  stale: boolean;
  provider: string;
  model: string;
  is_fallback: boolean;
  content_markdown: string;
  structured_payload?: unknown;
  citations: CitationOut[];
  linked_deck_id: string | null;
  linked_quiz_id: string | null;
  created_at: string;
}

export interface ArtifactVersionOut {
  version: number;
  title: string;
  content_markdown: string;
  created_at: string;
  edited_by_user?: boolean;
}

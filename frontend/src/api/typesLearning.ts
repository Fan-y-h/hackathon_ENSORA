// Tutor, Learning Studio, flashcards and quizzes (docs/AI_ENGINE.md, docs/API_CONTRACTS.md).
import type { Citation, IsoDateTime, MasteryDimension, Uuid, Versioned } from "./types";

// --- AI Tutor ----------------------------------------------------------------

export type TutorMode = "explain" | "derivation" | "code" | "correction" | "socratic" | "exam_prep" | "memorize";

/** grounded = backed by the sources; partial = only partly; insufficient = sources do not answer. */
export type EvidenceStatus = "grounded" | "partial" | "insufficient";

export interface Conversation extends Versioned {
  subject_id: Uuid | null;
  title: string;
}

export interface TutorMessage {
  id: Uuid;
  conversation_id: Uuid;
  role: "user" | "assistant";
  /** Safe Markdown + LaTeX ($...$). Never raw HTML. */
  content_markdown: string;
  citations: Citation[];
  evidence_status: EvidenceStatus | null;
  /** "mock" in demo mode: the UI must say no AI was called. */
  provider: string | null;
  model: string | null;
  occurred_at: IsoDateTime;
}

export interface TutorRequest {
  question: string;
  subject_id?: Uuid | null;
  document_ids?: Uuid[];
  /** ASSUMPTION to confirm with backend: restrict the answer to selected passages. */
  citation_ids?: Uuid[];
  mode: TutorMode;
  locale: "fr" | "en";
  model: "auto" | string;
}

export interface TutorResponse {
  message_id: Uuid;
  answer_markdown: string;
  citations: Citation[];
  evidence_status: EvidenceStatus;
  provider: string;
  model: string;
  usage: { tokens_in: number; tokens_out: number } | null;
}

// --- Learning Studio -----------------------------------------------------------

export type ArtifactType =
  | "revision_sheet"
  | "flashcards"
  | "quiz"
  | "exercises"
  | "mock_exam"
  | "podcast"
  | "video"
  | "animation"
  | "infographic"
  | "mind_map";

export type StudyObjective = "discover" | "understand" | "memorize" | "deepen" | "practice";
export type Difficulty = "beginner" | "intermediate" | "advanced";
export type DetailLevel = "summary" | "standard" | "detailed";

export interface GenerationSpec {
  type: ArtifactType;
  subject_id: Uuid;
  document_ids: Uuid[];
  concept_ids: Uuid[];
  objective: StudyObjective;
  locale: "fr" | "en";
  difficulty: Difficulty;
  duration_min: number;
  detail_level: DetailLevel;
  model: "auto" | string;
  user_instructions: string;
  force_regenerate: boolean;
}

export interface Artifact extends Versioned {
  type: ArtifactType;
  subject_id: Uuid;
  title: string;
  status: "ready" | "failed";
  active_version_id: Uuid;
  /** Sources changed since generation: offer to refresh, never erase edits silently. */
  stale: boolean;
  /** ASSUMPTION: deck id (flashcards) or quiz id (quiz) created by this artifact. */
  linked_id: Uuid | null;
}

export interface ArtifactVersion {
  id: Uuid;
  artifact_id: Uuid;
  parent_version_id: Uuid | null;
  content_markdown: string | null;
  citations: Citation[];
  provider: string;
  model: string;
  edited_by_user: boolean;
  created_at: IsoDateTime;
}

/** ASSUMPTION: GET /artifacts/{id} returns the artifact and its active version. */
export interface ArtifactDetail {
  artifact: Artifact;
  version: ArtifactVersion;
}

// --- Flashcards ------------------------------------------------------------------

export type ReviewRating = "again" | "hard" | "good" | "easy";

export interface Deck extends Versioned {
  subject_id: Uuid;
  title: string;
  card_count: number;
  /** ASSUMPTION: number of cards due now, computed by the server. */
  due_count: number;
}

export interface Flashcard extends Versioned {
  deck_id: Uuid;
  concept_id: Uuid | null;
  front: string;
  back: string;
  citations: Citation[];
  due_at: IsoDateTime | null;
  interval_days: number;
}

export interface MasteryUpdate {
  concept_id: Uuid;
  dimension: MasteryDimension;
  previous_score: number | null;
  score: number | null;
}

export interface ReviewResult {
  card_id: Uuid;
  next_due_at: IsoDateTime;
  interval_days: number;
  mastery_updates: MasteryUpdate[];
}

// --- Quizzes and exams -------------------------------------------------------------

export type QuestionType = "single" | "multiple" | "true_false" | "fill" | "open";

export interface Quiz extends Versioned {
  subject_id: Uuid;
  title: string;
  type: "practice" | "diagnostic" | "mock_exam";
  question_count: number;
  /** Set for mock exams: the countdown comes from the server (expires_at). */
  duration_min: number | null;
}

/** Active question: never contains the answer key. */
export interface AttemptQuestion {
  id: Uuid;
  type: QuestionType;
  prompt: string;
  options: Array<{ id: string; label: string }> | null;
  points: number;
}

export type AttemptResponseValue = string | string[];

export interface Attempt {
  id: Uuid;
  quiz_id: Uuid;
  mode: "practice" | "exam";
  status: "active" | "submitted" | "expired" | "graded";
  started_at: IsoDateTime;
  expires_at: IsoDateTime | null;
  questions: AttemptQuestion[];
  responses: Record<Uuid, AttemptResponseValue>;
}

export interface AttemptItemResult {
  question_id: Uuid;
  prompt: string;
  response: AttemptResponseValue | null;
  /** Correct answer, shown only after submission. */
  expected: string;
  /** null = open answer that cannot be graded automatically. */
  correct: boolean | null;
  score: number | null;
  points: number;
  explanation: string;
  citations: Citation[];
  concept_ids: Uuid[];
}

export interface AttemptResult {
  attempt_id: Uuid;
  quiz_id: Uuid;
  score: number;
  max_score: number;
  items: AttemptItemResult[];
  mastery_updates: MasteryUpdate[];
}

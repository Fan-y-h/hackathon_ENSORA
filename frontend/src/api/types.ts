// TypeScript mirror of docs/API_CONTRACTS.md and docs/DATA_MODEL.md.
// Rule from the contract: no frontend-only field inventions. When the backend
// publishes its OpenAPI schema, these types should be generated from it instead.

/** UUID string. */
export type Uuid = string;
/** ISO 8601 timestamp in UTC, e.g. "2026-12-07T07:30:00Z". */
export type IsoDateTime = string;
/** Decimal sent as a string to avoid rounding, e.g. "13.50" (grades, weights). */
export type DecimalString = string;

/** Every list endpoint returns this shape (pagination with an opaque cursor). */
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

/** Every error response body. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: unknown;
    request_id?: string;
  };
}

/** Fields shared by every mutable record. */
export interface Versioned {
  id: Uuid;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  /** Optimistic locking: send it back as expected_revision when updating. */
  revision: number;
}

// --- Jobs (long-running backend tasks) -------------------------------------

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Job {
  id: Uuid;
  kind: string;
  status: JobStatus;
  /** Between 0 and 1. */
  progress: number;
  result_id: Uuid | null;
  error: string | null;
}

// --- Profile ---------------------------------------------------------------

export type Cohort = "1A" | "2A" | "3A";

export interface Profile extends Versioned {
  user_id: Uuid;
  /** ASSUMPTION to confirm with backend: users.display_name returned with GET /profile. */
  display_name: string;
  cohort: Cohort;
  /** Specialization (filière), mostly for 2A/3A. */
  track: string | null;
  /** e.g. "2026-2027" */
  academic_year: string;
  locale: "fr" | "en";
  timezone: string;
  goals: string[];
  /** ASSUMPTION to confirm with backend: current semester (1 or 2), drives the grades view. */
  semester: 1 | 2;
  /** ASSUMPTION: set when the first-run setup is finished; null = never done. */
  onboarding_completed_at: IsoDateTime | null;
}

export type ProfileUpdate = Partial<Pick<Profile, "display_name" | "cohort" | "track" | "academic_year" | "locale" | "goals" | "semester" | "onboarding_completed_at">> & {
  expected_revision: number;
};

// --- Connections (external sources) ----------------------------------------

export type SourceKind = "moodle" | "ensora_drive" | "pamplemousse" | "google_calendar" | "personal_drive" | "local";

export type ConnectionStatus = "connected" | "syncing" | "stale" | "error" | "disconnected";

export interface Connection extends Versioned {
  source_kind: SourceKind;
  status: ConnectionStatus;
  last_success_at: IsoDateTime | null;
  /**
   * ASSUMPTION to confirm with backend (name not in the contract): MVP.md requires to
   * "visibly distinguish fixture from live connector". true = demo fixture, label it.
   */
  is_fixture: boolean;
}

// --- Subjects --------------------------------------------------------------

export type ContentLanguage = "fr" | "en";

export interface Subject extends Versioned {
  canonical_code: string | null;
  title: string;
  cohort: Cohort;
  track: string | null;
  academic_year: string;
  /**
   * ASSUMPTION to confirm with backend: languages of the course material found on Moodle.
   * Two values = the teacher published both versions; the student may choose.
   */
  languages: ContentLanguage[];
  /** Language the teacher uses by default (the original one). */
  default_language: ContentLanguage;
}

export interface Chapter extends Versioned {
  subject_id: Uuid;
  title: string;
  ordinal: number;
  active: boolean;
}

export interface Concept extends Versioned {
  chapter_id: Uuid;
  label: string;
  description: string | null;
  /** ASSUMPTION to confirm with backend: difficulty and importance on a 1-3 scale. */
  difficulty: number | null;
  importance: number | null;
  active: boolean;
}

/** Dimensions from docs/ADAPTIVE_LEARNING.md. ASSUMPTION: snake_case values. */
export type MasteryDimension =
  | "theoretical_understanding"
  | "memory"
  | "mathematical_reasoning"
  | "practical_application"
  | "implementation"
  | "autonomy"
  | "long_term_retention";

/** GET /mastery item: one concept x one dimension. */
export interface Mastery {
  concept_id: Uuid;
  dimension: MasteryDimension;
  /** 0..1, null = unknown (never assessed). Never display null as 0. */
  score: number | null;
  evidence_count: number;
  /** 0..1: how much evidence backs the score. Low = show "peu de données". */
  confidence: number;
  last_review_at: IsoDateTime | null;
  next_due_at: IsoDateTime | null;
}

// --- Documents -------------------------------------------------------------

/** Classification from docs/DATA_ENGINEERING.md. */
export type ResourceType = "course" | "td" | "tp" | "notebook" | "assignment" | "exam" | "solution" | "sheet" | "note" | "other";

export interface DocumentSummary extends Versioned {
  subject_id: Uuid | null;
  chapter_id: Uuid | null;
  title: string;
  resource_type: ResourceType;
  /**
   * ASSUMPTION to confirm with backend: provenance flattened from document_origins
   * (P0 requires showing it). source = where it was imported from.
   */
  source: SourceKind;
  /** Historical year for past exams (e.g. 2024), separate from the student's year. */
  year: number | null;
  remote_modified_at: IsoDateTime | null;
  /** Version shown by default; content URL: /documents/{id}/versions/{active_version_id}/content */
  active_version_id: Uuid;
}

/** A note attached to an exact passage of a document version (docs/DATA_MODEL.md "annotations"). */
export interface Annotation {
  id: Uuid;
  document_id: Uuid;
  document_version_id: Uuid;
  locator: CitationLocator;
  excerpt: string;
  comment: string;
  created_at: IsoDateTime;
}

export interface DocumentFilters {
  subject_id?: Uuid;
  resource_type?: ResourceType;
  year?: number;
}

// --- Search and citations (docs/RAG_ARCHITECTURE.md) ------------------------

export type SearchMode = "lexical" | "semantic" | "hybrid";

/** Where the passage is in the original file. Numbers are one-based. */
export interface CitationLocator {
  kind: "page" | "slide" | "cell" | "lines" | "section";
  page?: number;
  slide?: number;
  cell?: number;
  line_start?: number;
  line_end?: number;
  section?: string;
}

export interface Citation {
  citation_id: Uuid;
  document_id: Uuid;
  version_id: Uuid;
  chunk_id: Uuid;
  /** Document title. */
  title: string;
  locator: CitationLocator;
  /** Exact text of the source passage, never generated. */
  excerpt: string;
  source_url?: string;
}

/** ASSUMPTION to confirm with backend: GET /search returns Page<SearchHit>. */
export interface SearchHit {
  citation: Citation;
  /** Relevance, higher is better. Only used for ordering, not shown as a percentage. */
  score: number;
}

export interface SearchParams {
  q: string;
  mode: SearchMode;
  subject_id?: Uuid;
}

// --- Calendar events -------------------------------------------------------

export type EventType = "class" | "td" | "tp" | "exam" | "presentation" | "deadline" | "personal" | "other";

export interface CalendarEvent extends Versioned {
  subject_id: Uuid | null;
  title: string;
  type: EventType;
  start_at: IsoDateTime;
  end_at: IsoDateTime;
  source: SourceKind | "manual" | "planner";
  room: string | null;
  teacher: string | null;
  /**
   * TD/TP group, read-only and only known from Pamplemousse.
   * null = unknown: the UI shows "non disponible" and never asks the student.
   */
  group: string | null;
  /** Ambiguous assessment the student must confirm. */
  confirmation_required: boolean;
}

/** Personal event created by the student (e.g. an unavailability). Imported events are read-only. */
export type EventCreate = Pick<CalendarEvent, "title" | "start_at" | "end_at"> & {
  type: "personal";
  subject_id?: Uuid | null;
};

/**
 * ASSUMPTION to confirm with backend: the contract mentions a "separate confirmation action"
 * for ambiguous assessments without naming it. We use POST /events/{id}/confirm.
 * confirm = it really is an assessment; reject = it is not.
 */
export interface EventConfirmation {
  decision: "confirm" | "reject";
}

// --- Tasks -----------------------------------------------------------------

export type TaskStatus = "todo" | "in_progress" | "done" | "cancelled";

export interface Task extends Versioned {
  parent_task_id: Uuid | null;
  subject_id: Uuid | null;
  title: string;
  description: string | null;
  deadline_at: IsoDateTime | null;
  estimated_min: number | null;
  remaining_min: number | null;
  /** 1 = highest. */
  priority: number;
  status: TaskStatus;
  locked: boolean;
}

export type TaskCreate = Pick<Task, "title"> & Partial<Pick<Task, "subject_id" | "description" | "deadline_at" | "estimated_min" | "priority">>;

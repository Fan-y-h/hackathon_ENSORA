// Notes, error notebook, notifications, preferences and AI providers.
import type { Citation, IsoDateTime, Uuid, Versioned } from "./types";

export interface Note extends Versioned {
  subject_id: Uuid | null;
  title: string;
  /** Markdown + LaTeX. */
  markdown: string;
  citations: Citation[];
}

export type NoteInput = Pick<Note, "title" | "markdown"> & Partial<Pick<Note, "subject_id" | "citations">>;

export interface ErrorEntry {
  id: Uuid;
  concept_id: Uuid | null;
  subject_id: Uuid | null;
  category: "calculation" | "reasoning" | "definition" | "method" | "code" | "other";
  description: string;
  remediation: string | null;
  occurred_at: IsoDateTime;
  resolved_at: IsoDateTime | null;
}

export interface AppNotification {
  id: Uuid;
  event_type: "new_document" | "timetable_change" | "deadline" | "review_due" | "plan_changed" | "overdue_task";
  title: string;
  body: string;
  /** In-app link to open, e.g. "/agenda". */
  target_path: string | null;
  created_at: IsoDateTime;
  read_at: IsoDateTime | null;
}

export interface WorkWindow {
  /** 1 = Monday ... 7 = Sunday */
  day: number;
  start: string;
  end: string;
}

export type PreferredFormat = "revision_sheet" | "flashcards" | "quiz" | "exercises" | "mind_map" | "video" | "podcast" | "animation" | "infographic";

/** Pomodoro-style work methods: they set the session and break lengths used by the planner. */
export type WorkMethod = "pomodoro_25_5" | "pomodoro_50_10" | "deep_90_15";

/** Self-assessed starting level per subject (a hint for the first plan, never "mastery"). */
export type SelfLevel = "beginner" | "fragile" | "average" | "comfortable" | "very_comfortable";

/** ASSUMPTION: returned inside GET /settings ("planner policy" + learning preferences). */
export interface Preferences {
  work_windows: WorkWindow[];
  /** The only limit the student sets; the weekly one is deduced from it. */
  daily_max_min: number;
  weekly_max_min: number;
  work_method: WorkMethod;
  /** Derived from work_method. */
  min_session_min: number;
  max_session_min: number;
  /** One list for every subject and every support type. */
  preferred_formats: PreferredFormat[];
  subject_levels: Record<string, SelfLevel>;
  /** Only for subjects whose course exists in two languages; otherwise the original language is used. */
  subject_languages: Record<string, "fr" | "en">;
  starting_difficulty: "progressive" | "diagnostic";
  /** Name of the folder chosen on the student's computer for downloaded files (the folder itself stays local). */
  download_folder: string | null;
}

/** ASSUMPTION: GET /availability/suggestions, free time deduced from the connected calendars. */
export interface AvailabilitySuggestion {
  windows: WorkWindow[];
  /** Calendars actually used for the deduction. */
  based_on: Array<"pamplemousse" | "google_calendar">;
  /** Calendars to connect for a complete deduction. */
  missing: Array<"pamplemousse" | "google_calendar">;
}

/** ASSUMPTION: GET /auth/session. */
export interface AuthSession {
  authenticated: boolean;
  demo: boolean;
}

/**
 * ASSUMPTION to confirm with backend: POST /auth/login takes the ENSAI username and password.
 * The backend signs in to the ENSAI services (Moodle, Pamplemousse, Drive Ensora) with them,
 * never stores the password in clear, and starts importing the student's data (sync_job).
 */
export interface LoginResult extends AuthSession {
  sync_job: import("./types").Job | null;
}

/** Sources reached with the ENSAI account itself; the others are personal and optional. */
export const ENSAI_SOURCES = ["moodle", "pamplemousse", "ensora_drive"] as const;

export interface Settings extends Versioned {
  preferences: Preferences;
  notification_channels: { in_app: boolean; email: boolean };
  /** "local_only" = never send course content to an external AI provider. */
  provider_policy: "local_only" | "allow_external";
  preferred_model: "auto" | string;
}

export interface ProviderInfo {
  id: string;
  label: string;
  available: boolean;
  /** Short reason when unavailable (e.g. "clé API non configurée"). */
  status_note: string | null;
  external: boolean;
}

export interface Usage {
  period: "day" | "month";
  tokens_used: number;
  token_limit: number | null;
  /** null = unknown, never shown as free. */
  estimated_cost: string | null;
  currency: string | null;
}

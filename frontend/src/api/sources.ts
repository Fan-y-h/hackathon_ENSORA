/**
 * P1 sources API (mounted under /api/v1/sources): connections, sync jobs, schedule, documents.
 * The bearer token is added by the dev/preview proxy, never by browser code.
 */
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { ApiError, api } from "./client";

export type SourceKind = "moodle" | "pamplemousse" | "ensora_drive" | "google_calendar" | "local" | "academic_import";
export type ConnectionStatus = "NOT_CONFIGURED" | "CONNECTED" | "REAUTH_REQUIRED" | "ACCESS_DENIED" | "UNAVAILABLE" | "ERROR";

export interface SourceConnection {
  id: string;
  source: SourceKind;
  status: ConnectionStatus;
  last_success_at: string | null;
  validation_required?: boolean;
  consent: { read: boolean; automatic_sync: boolean };
}

export interface AuthenticationResult {
  status: ConnectionStatus;
  reason: string | null;
  session_state: string;
  service_access: string;
}

export interface SourceDocument {
  id: string;
  connection_id: string;
  external_id: string;
  active_version_id: string | null;
  metadata_json: {
    title: string;
    mime_type: string;
    subject_id: string | null;
    course_title: string | null;
    provenance: { source: SourceKind; source_url: string | null; folder: string | null; modified_at: string | null };
    classification?: { resource_type: string; confidence: number } | null;
    match?: { subject_id: string | null; chapter_id: string | null; confidence: number; confirmed: boolean } | null;
  };
}

export interface DocumentVersion {
  id: string;
  sha256: string;
  mime_type: string;
  size_bytes: number;
  status: string;
  filename: string | null;
}

export interface DocumentChunk {
  id: string;
  ordinal: number;
  payload: { text?: string; heading?: string | null; page?: number | null; [key: string]: unknown };
}

export type JobStatus = "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled" | "interrupted";

export interface SyncSourceReport {
  status?: string;
  documents?: number;
  records?: number;
  error?: string;
  reason?: string;
  [key: string]: unknown;
}

export interface SyncJob {
  id: string;
  status: JobStatus;
  started_at: string;
  finished_at: string | null;
  report: Record<string, SyncSourceReport>;
}

export interface SyncScheduleSource {
  source: SourceKind;
  scope_key?: string | null;
  enabled: boolean;
  interval_minutes: number | null;
  next_run_at?: string | null;
  last_run_at?: string | null;
  recent_failure_code?: string | null;
}

export interface SyncScheduleStatus {
  enabled: boolean;
  supported_intervals_minutes: number[];
  sources: SyncScheduleSource[];
}

export interface SyncScheduleSettings {
  enabled: boolean;
  enabled_sources: string[];
  intervals_minutes: Record<string, number | null>;
}

export interface SourcesSettings {
  sync_on_startup: boolean;
  sync_interval_minutes: number;
  max_document_bytes: number;
  google_project_id: string | null;
  preferences?: { sync_schedule?: SyncScheduleSettings | null } | null;
}

export interface PipelineStatus {
  active_jobs: number;
  registered_sources: string[];
}

export interface AgendaImportReport {
  sources: {
    source: string;
    agenda_source: string;
    read: number;
    skipped: number;
    result: { created: number; updated: number; unchanged: number; agenda_revision: number };
  }[];
}

export interface SourceRecordItem {
  id: string;
  connection_id: string;
  kind: string;
  external_id: string;
  parent_id: string | null;
  payload: Record<string, unknown>;
  user_override?: Record<string, unknown> | null;
  effective_payload?: Record<string, unknown>;
}

interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export const SOURCE_LABEL: Record<SourceKind, string> = {
  moodle: "Moodle",
  pamplemousse: "Pamplemousse",
  ensora_drive: "Drive Ensora",
  google_calendar: "Google Agenda",
  local: "Fichiers importés",
  academic_import: "Import de cursus",
};

export const RESOURCE_TYPE_LABEL: Record<string, string> = {
  lecture: "Cours",
  tutorial: "TD",
  practical_work: "TP",
  examination: "Annale",
  examination_correction: "Corrigé",
  assignment: "Devoir",
  course_summary: "Fiche",
  notebook: "Notebook",
  source_code: "Code",
  administrative: "Administratif",
  other: "Autre",
};

export const TERMINAL: JobStatus[] = ["succeeded", "partial", "failed", "cancelled", "interrupted"];

const S = "/sources";
export const sourceKeys = {
  connections: ["sources", "connections"] as const,
  documents: ["sources", "documents"] as const,
  subjects: ["sources", "subjects"] as const,
  schedule: ["sources", "schedule"] as const,
  settings: ["sources", "settings"] as const,
  runs: ["sources", "runs"] as const,
  pipeline: ["sources", "pipeline"] as const,
  examCandidates: ["sources", "exam-candidates"] as const,
  versions: (id: string) => ["sources", "documents", id, "versions"] as const,
  chunks: (doc: string, version: string) => ["sources", "documents", doc, version, "chunks"] as const,
  job: (id: string) => ["sources", "jobs", id] as const,
};

/** 503 = sources runtime stopped, 404 = server started without the sources module. */
export function sourcesUnavailable(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 503 || error.status === 404);
}

/** Invalidate every read model affected by a source sync or consent change. */
export function invalidateSyncedModels(qc: QueryClient) {
  for (const key of [
    ["sources"],
    ["results"],
    ["events"],
    ["assessments"],
    ["planner"],
    ["plan"],
    ["availability"],
    ["analytics"],
    ["notifications"],
    ["grades"],
    ["catalog"],
  ]) {
    void qc.invalidateQueries({ queryKey: key });
  }
}

export const useConnections = () =>
  useQuery({
    queryKey: sourceKeys.connections,
    queryFn: () => api.get<Page<SourceConnection>>(`${S}/connections`),
    retry: false,
  });

export function useConnectionAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: "authorize" | "validate" | "reauthorize" }) =>
      api.post<AuthenticationResult>(`${S}/connections/${encodeURIComponent(id)}/${action}`),
    onSettled: () => void qc.invalidateQueries({ queryKey: sourceKeys.connections }),
  });
}

export function useDisconnectConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`${S}/connections/${encodeURIComponent(id)}`),
    onSettled: () => {
      qc.removeQueries({ queryKey: sourceKeys.documents });
      invalidateSyncedModels(qc);
    },
  });
}

export function useConsent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, purpose, granted }: { id: string; purpose: "read" | "automatic_sync"; granted: boolean }) =>
      api.put(`${S}/connections/${encodeURIComponent(id)}/consent`, { purpose, granted }),
    onSuccess: (_data, vars) => {
      if (vars.purpose === "read" && !vars.granted) {
        qc.removeQueries({ queryKey: sourceKeys.documents });
        qc.removeQueries({ queryKey: ["results"] });
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: sourceKeys.connections });
      void qc.invalidateQueries({ queryKey: sourceKeys.documents });
      void qc.invalidateQueries({ queryKey: sourceKeys.subjects });
      void qc.invalidateQueries({ queryKey: sourceKeys.schedule });
      void qc.invalidateQueries({ queryKey: ["results"] });
      void qc.invalidateQueries({ queryKey: ["catalog"] });
    },
  });
}

export const useSyncSchedule = () =>
  useQuery({
    queryKey: sourceKeys.schedule,
    queryFn: () => api.get<SyncScheduleStatus>(`${S}/sync/schedule`),
    retry: false,
  });

export const useSourcesSettings = () =>
  useQuery({
    queryKey: sourceKeys.settings,
    queryFn: () => api.get<SourcesSettings>(`${S}/settings`),
    retry: false,
  });

export function useUpdateSyncSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SyncScheduleSettings) => api.patch<SyncScheduleStatus>(`${S}/sync/schedule`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceKeys.schedule });
      void qc.invalidateQueries({ queryKey: sourceKeys.settings });
    },
  });
}

export function useResetSyncSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<SyncScheduleStatus>(`${S}/sync/schedule/reset`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceKeys.schedule });
      void qc.invalidateQueries({ queryKey: sourceKeys.settings });
    },
  });
}

export const useSyncRuns = (limit = 10) =>
  useQuery({
    queryKey: [...sourceKeys.runs, limit] as const,
    queryFn: () => api.get<Page<SyncJob>>(`${S}/sync/runs`, { limit }),
    retry: false,
  });

export const usePipelineStatus = () =>
  useQuery({
    queryKey: sourceKeys.pipeline,
    queryFn: () => api.get<PipelineStatus>(`${S}/pipeline/status`),
    retry: false,
  });

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => api.post<{ id: string }>(`${S}/jobs/${encodeURIComponent(jobId)}/cancel`),
    onSettled: (_d, _e, jobId) => {
      void qc.invalidateQueries({ queryKey: sourceKeys.job(jobId) });
      void qc.invalidateQueries({ queryKey: sourceKeys.runs });
      void qc.invalidateQueries({ queryKey: sourceKeys.pipeline });
    },
  });
}

export function useResumeJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, source }: { jobId: string; source?: SourceKind | null }) =>
      api.post<{ id: string }>(`${S}/jobs/${encodeURIComponent(jobId)}/resume`, source ? { source } : {}),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: sourceKeys.runs });
      void qc.invalidateQueries({ queryKey: sourceKeys.pipeline });
    },
  });
}

export interface ResultsAssessment {
  key: string;
  title: string;
  coefficient: number | null;
  weight: number | null;
  grade: number | null;
  maximum: number | null;
  minimum?: number | null;
  children?: ResultsAssessment[];
}

export interface ResultsSubject {
  key: string;
  code: string | null;
  title: string;
  coefficient: number | null;
  ects: number | null;
  official_grade: number | null;
  official_grade_maximum?: number | null;
  official_grade_minimum?: number | null;
  assessments: ResultsAssessment[];
}

export interface ResultsUnit {
  key: string;
  coefficient?: number | null;
  unassigned?: boolean;
  title: string;
  official_average: number | null;
  official_average_maximum?: number | null;
  subjects: ResultsSubject[];
}

export interface ResultsSemester {
  key: string;
  title: string;
  official_average: number | null;
  official_average_maximum?: number | null;
  units: ResultsUnit[];
}

export interface Results {
  source: string;
  academic_year: string | null;
  general_average: number | null;
  general_average_maximum?: number | null;
  academic_years?: string[];
  semesters: ResultsSemester[];
}

/** UE / subject / coefficient / mark tree synced from Pamplemousse. */
export const useResults = (academicYear?: string) =>
  useQuery({ queryKey: ["results", academicYear], queryFn: () => api.get<Results>("/results", { academic_year: academicYear }), retry: false });

/** Subject catalog synced from Pamplemousse: course code -> official name (empty if unavailable). */
export const useSourceSubjects = () =>
  useQuery({
    queryKey: sourceKeys.subjects,
    queryFn: async () => {
      try {
        const page = await api.get<Page<{ external_id?: string; payload: { code?: string | null; title?: string | null } }>>(
          `${S}/subjects`,
          { limit: 200 },
        );
        const names: Record<string, string> = {};
        for (const item of page.items) {
          const { code, title } = item.payload;
          if (code && title) names[code.toUpperCase()] = title;
          if (item.external_id && title) names[item.external_id] = title;
        }
        return names;
      } catch {
        return {}; // sources not running: names fall back to agenda titles
      }
    },
    staleTime: 5 * 60_000,
  });

export const useSourceSubjectRecords = () =>
  useQuery({
    queryKey: ["sources", "subjects", "records"] as const,
    queryFn: () => api.get<Page<SourceRecordItem>>(`${S}/subjects`, { limit: 200 }),
    retry: false,
  });

export const useSourceDocuments = () =>
  useQuery({
    queryKey: sourceKeys.documents,
    // Follow the cursor: a Moodle sync alone brings several hundred documents.
    queryFn: async () => {
      const items: SourceDocument[] = [];
      let cursor: string | null = null;
      do {
        const page: Page<SourceDocument> = await api.get<Page<SourceDocument>>(`${S}/documents`, { limit: 200, cursor });
        items.push(...page.items);
        cursor = page.next_cursor;
      } while (cursor && items.length < 5000);
      return { items, next_cursor: null } as Page<SourceDocument>;
    },
    retry: false,
  });

export const useDocumentVersions = (documentId?: string) =>
  useQuery({
    queryKey: sourceKeys.versions(documentId ?? ""),
    queryFn: () => api.get<Page<DocumentVersion>>(`${S}/documents/${encodeURIComponent(documentId!)}/versions`),
    enabled: Boolean(documentId),
  });

export const useDocumentChunks = (documentId?: string, versionId?: string | null) =>
  useQuery({
    queryKey: sourceKeys.chunks(documentId ?? "", versionId ?? ""),
    queryFn: () => api.get<Page<DocumentChunk> & { version: DocumentVersion }>(`${S}/documents/${encodeURIComponent(documentId!)}/versions/${encodeURIComponent(versionId!)}/chunks`, { limit: 50 }),
    enabled: Boolean(documentId && versionId),
  });

export function useConfirmDocumentMatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ documentId, subjectId, chapterId }: { documentId: string; subjectId: string; chapterId?: string | null }) =>
      api.put<{ document_id: string; subject_id: string; confirmed: boolean }>(
        `${S}/documents/${encodeURIComponent(documentId)}/match`,
        { subject_id: subjectId, ...(chapterId !== undefined ? { chapter_id: chapterId } : {}) },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceKeys.documents });
      void qc.invalidateQueries({ queryKey: ["catalog"] });
    },
  });
}

export function useAcademicOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ recordId, fields }: { recordId: string; fields: Record<string, unknown> }) =>
      api.put<{ id: string; user_override: Record<string, unknown> }>(
        `${S}/academic/records/${encodeURIComponent(recordId)}/override`,
        { fields },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["results"] });
      void qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}

export const useExamCorrectionCandidates = () =>
  useQuery({
    queryKey: sourceKeys.examCandidates,
    queryFn: () => api.get<{ items: Record<string, unknown>[] }>(`${S}/documents/exam-correction-candidates`),
    retry: false,
  });

export const documentContentUrl = (documentId: string, versionId: string) =>
  `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1${S}/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(versionId)}/content`;

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.upload<{ result: string; external_id: string }>(`${S}/documents/import`, form);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceKeys.documents });
      void qc.invalidateQueries({ queryKey: ["catalog"] });
    },
  });
}

/** Starts a sync of every consented source (or a single source); returns the job id. */
export function useStartSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scope?: SourceKind | { source: SourceKind; subject_id: string }) =>
      api.post<{ id: string }>(`${S}/sync`, typeof scope === "string" ? { source: scope } : scope ?? {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceKeys.runs });
      void qc.invalidateQueries({ queryKey: sourceKeys.pipeline });
    },
  });
}

/** Polls a job every 2 s until it reaches a terminal status. */
export const useJob = (jobId: string | null) =>
  useQuery({
    queryKey: sourceKeys.job(jobId ?? ""),
    queryFn: () => api.get<SyncJob>(`${S}/jobs/${encodeURIComponent(jobId!)}`),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data && TERMINAL.includes(query.state.data.status) ? false : 2000),
  });

/** Copies synced calendar events (timetable, exams, personal events) into the agenda and refreshes all read models. */
export function useImportFromSources() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AgendaImportReport>("/agenda/import-from-sources"),
    onSuccess: () => invalidateSyncedModels(qc),
  });
}

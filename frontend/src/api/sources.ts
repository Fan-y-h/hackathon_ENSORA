/**
 * P1 sources API (mounted under /api/v1/sources): connections, sync jobs, documents.
 * The bearer token is added by the dev proxy / server, never by browser code.
 * Types are hand-written from the P1 handlers (the mounted app is outside the planner OpenAPI).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "./client";

export type SourceKind = "moodle" | "pamplemousse" | "ensora_drive" | "google_calendar" | "local" | "academic_import";
export type ConnectionStatus = "NOT_CONFIGURED" | "CONNECTED" | "REAUTH_REQUIRED" | "ACCESS_DENIED" | "UNAVAILABLE" | "ERROR";

export interface SourceConnection {
  id: string;
  source: SourceKind;
  status: ConnectionStatus;
  last_success_at: string | null;
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

export interface SyncJob {
  id: string;
  status: JobStatus;
  started_at: string;
  finished_at: string | null;
  report: Record<string, { status?: string; documents?: number; records?: number; [key: string]: unknown }>;
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
  versions: (id: string) => ["sources", "documents", id, "versions"] as const,
  chunks: (doc: string, version: string) => ["sources", "documents", doc, version, "chunks"] as const,
  job: (id: string) => ["sources", "jobs", id] as const,
};

/** 503 = sources runtime stopped, 404 = server started without the sources module. */
export function sourcesUnavailable(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 503 || error.status === 404);
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
      api.post<AuthenticationResult>(`${S}/connections/${id}/${action}`),
    onSettled: () => void qc.invalidateQueries({ queryKey: sourceKeys.connections }),
  });
}

export function useConsent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, purpose, granted }: { id: string; purpose: "read" | "automatic_sync"; granted: boolean }) =>
      api.put(`${S}/connections/${id}/consent`, { purpose, granted }),
    onSettled: () => void qc.invalidateQueries({ queryKey: sourceKeys.connections }),
  });
}

export interface ResultsAssessment {
  key: string;
  title: string;
  coefficient: number | null;
  weight: number | null;
  grade: number | null;
  maximum: number;
}

export interface ResultsSubject {
  key: string;
  code: string | null;
  title: string;
  coefficient: number | null;
  ects: number | null;
  official_grade: number | null;
  assessments: ResultsAssessment[];
}

export interface ResultsUnit {
  key: string;
  title: string;
  official_average: number | null;
  subjects: ResultsSubject[];
}

export interface ResultsSemester {
  key: string;
  title: string;
  official_average: number | null;
  units: ResultsUnit[];
}

export interface Results {
  source: string;
  academic_year: string | null;
  general_average: number | null;
  semesters: ResultsSemester[];
}

/** UE / subject / coefficient / mark tree synced from Pamplemousse. */
export const useResults = () =>
  useQuery({ queryKey: ["results"], queryFn: () => api.get<Results>("/results"), retry: false });

/** Subject catalog synced from Pamplemousse: course code -> official name (empty if unavailable). */
export const useSourceSubjects = () =>
  useQuery({
    queryKey: ["sources", "subjects"],
    queryFn: async () => {
      try {
        const page = await api.get<Page<{ payload: { code?: string | null; title?: string | null } }>>(`${S}/subjects`, { limit: 200 });
        const names: Record<string, string> = {};
        for (const item of page.items) {
          const { code, title } = item.payload;
          if (code && title) names[code.toUpperCase()] = title;
        }
        return names;
      } catch {
        return {}; // sources not running: names fall back to agenda titles
      }
    },
    staleTime: 5 * 60_000,
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
    queryFn: () => api.get<Page<DocumentVersion>>(`${S}/documents/${documentId}/versions`),
    enabled: Boolean(documentId),
  });

export const useDocumentChunks = (documentId?: string, versionId?: string | null) =>
  useQuery({
    queryKey: sourceKeys.chunks(documentId ?? "", versionId ?? ""),
    queryFn: () => api.get<Page<DocumentChunk>>(`${S}/documents/${documentId}/versions/${versionId}/chunks`, { limit: 20 }),
    enabled: Boolean(documentId && versionId),
  });

export const documentContentUrl = (documentId: string, versionId: string) =>
  `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1${S}/documents/${documentId}/versions/${versionId}/content`;

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.upload<{ result: string; external_id: string }>(`${S}/documents/import`, form);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: sourceKeys.documents }),
  });
}

/** Starts a sync of every consented source; returns the job id. */
export function useStartSync() {
  return useMutation({
    mutationFn: (source?: SourceKind) => api.post<{ id: string }>(`${S}/sync`, source ? { source } : {}),
  });
}

/** Polls a job every 2 s until it reaches a terminal status. */
export const useJob = (jobId: string | null) =>
  useQuery({
    queryKey: sourceKeys.job(jobId ?? ""),
    queryFn: () => api.get<SyncJob>(`${S}/jobs/${jobId}`),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data && TERMINAL.includes(query.state.data.status) ? false : 2000),
  });

/** Copies synced calendar events (timetable, exams, personal events) into the agenda. */
export function useImportFromSources() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AgendaImportReport>("/agenda/import-from-sources"),
    onSuccess: () => {
      for (const key of [["events"], ["assessments"], ["planner"], ["plan"], ["availability"], ["analytics"]]) {
        void qc.invalidateQueries({ queryKey: key });
      }
    },
  });
}

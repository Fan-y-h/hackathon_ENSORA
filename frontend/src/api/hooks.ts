// React hooks for each endpoint. Pages only use these, never fetch() directly:
//   const { data, isPending, error, refetch } = useSubjects();
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, apiFetchBlob, newIdempotencyKey } from "./client";
import { queryKeys } from "./queryKeys";
import type {
  Annotation,
  CalendarEvent,
  Citation,
  Chapter,
  Concept,
  Connection,
  DocumentFilters,
  DocumentSummary,
  EventConfirmation,
  EventCreate,
  Job,
  Mastery,
  Page,
  Profile,
  ProfileUpdate,
  SearchHit,
  SearchParams,
  Subject,
  Task,
  TaskCreate,
  Uuid,
} from "./types";

// --- Profile ---------------------------------------------------------------

export function useProfile() {
  return useQuery({ queryKey: queryKeys.profile, queryFn: () => apiFetch<Profile>("/profile") });
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (update: ProfileUpdate) => apiFetch<Profile>("/profile", { method: "PATCH", body: update }),
    // The server answer is the new truth: put it in the cache directly.
    onSuccess: (profile) => queryClient.setQueryData(queryKeys.profile, profile),
  });
}

// --- Connections and sync --------------------------------------------------

export function useConnections() {
  return useQuery({
    queryKey: queryKeys.connections,
    queryFn: () => apiFetch<Page<Connection>>("/connections"),
    select: (page) => page.items,
  });
}

/** Starts a sync and returns the Job to follow with useJob. */
export function useStartSync() {
  return useMutation({
    mutationFn: (scope: { connection_id?: Uuid; subject_id?: Uuid } = {}) =>
      apiFetch<Job>("/sync", { method: "POST", body: scope, idempotencyKey: newIdempotencyKey() }),
  });
}

/**
 * Follows a job: polls every 1.5 s while it is queued/running, then stops.
 * Pass null when there is no job yet.
 */
export function useJob(jobId: Uuid | null) {
  return useQuery({
    queryKey: queryKeys.job(jobId ?? "none"),
    queryFn: () => apiFetch<Job>(`/jobs/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1500 : false;
    },
  });
}

export function useCancelJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: Uuid) => apiFetch<Job>(`/jobs/${jobId}/cancel`, { method: "POST" }),
    onSuccess: (job) => queryClient.setQueryData(queryKeys.job(job.id), job),
  });
}

// --- Subjects --------------------------------------------------------------

export function useSubjects() {
  return useQuery({
    queryKey: queryKeys.subjects,
    queryFn: () => apiFetch<Page<Subject>>("/subjects", { query: { limit: 200 } }),
    select: (page) => page.items,
  });
}

export function useSubject(subjectId: Uuid) {
  return useQuery({ queryKey: queryKeys.subject(subjectId), queryFn: () => apiFetch<Subject>(`/subjects/${subjectId}`) });
}

export function useChapters(subjectId: Uuid) {
  return useQuery({
    queryKey: queryKeys.chapters(subjectId),
    queryFn: () => apiFetch<Page<Chapter>>(`/subjects/${subjectId}/chapters`, { query: { limit: 200 } }),
    select: (page) => [...page.items].sort((a, b) => a.ordinal - b.ordinal),
    enabled: subjectId !== "",
  });
}

export function useConcepts(subjectId: Uuid) {
  return useQuery({
    queryKey: queryKeys.concepts(subjectId),
    queryFn: () => apiFetch<Page<Concept>>(`/subjects/${subjectId}/concepts`, { query: { limit: 200 } }),
    select: (page) => page.items,
    enabled: subjectId !== "",
  });
}

// --- Documents and mastery -------------------------------------------------

/** Documents, optionally filtered (subject, type, year). No filter = all documents. */
export function useDocuments(filters: DocumentFilters = {}) {
  return useQuery({
    queryKey: queryKeys.documents(filters),
    queryFn: () => apiFetch<Page<DocumentSummary>>("/documents", { query: { ...filters, limit: 200 } }),
    select: (page) => page.items,
  });
}

export function useDocument(documentId: Uuid | null) {
  return useQuery({
    queryKey: queryKeys.document(documentId ?? "none"),
    queryFn: () => apiFetch<DocumentSummary>(`/documents/${documentId}`),
    enabled: documentId !== null,
  });
}

/** Original file of a document version, as a Blob (PDF, text...). */
export function useDocumentContent(document: DocumentSummary | undefined) {
  return useQuery({
    queryKey: queryKeys.documentContent(document?.id ?? "none", document?.active_version_id ?? "none"),
    queryFn: () => apiFetchBlob(`/documents/${document?.id}/versions/${document?.active_version_id}/content`),
    enabled: document !== undefined,
    // A version never changes: no need to download it again.
    staleTime: Infinity,
  });
}

/** Full-text / semantic search. Pass null to not search (empty query). */
export function useSearch(params: SearchParams | null) {
  return useQuery({
    queryKey: queryKeys.search(params ?? { q: "", mode: "hybrid" }),
    queryFn: () => apiFetch<Page<SearchHit>>("/search", { query: { ...params, limit: 50 } }),
    select: (page) => page.items,
    enabled: params !== null,
  });
}

/** One citation by id (exact locator + excerpt), e.g. to restore a selected passage from a link. */
export function useCitation(citationId: Uuid | null | undefined) {
  return useQuery({
    queryKey: ["citations", citationId ?? "none"],
    queryFn: () => apiFetch<Citation>(`/citations/${citationId}`),
    enabled: Boolean(citationId),
    staleTime: Infinity,
  });
}

/** Uploads a file; returns the import Job (follow it with useJob). */
export function useImportDocument() {
  return useMutation({
    mutationFn: ({ file, subjectId }: { file: File; subjectId?: Uuid }) => {
      const form = new FormData();
      form.append("file", file);
      if (subjectId) form.append("subject_id", subjectId);
      return apiFetch<Job>("/documents/import", { method: "POST", body: form, idempotencyKey: newIdempotencyKey() });
    },
  });
}

/** Mastery estimates (concept x dimension). Missing concept = never assessed. */
export function useMastery(filters: { subject_id?: Uuid } = {}) {
  return useQuery({
    queryKey: queryKeys.mastery(filters),
    queryFn: () => apiFetch<Page<Mastery>>("/mastery", { query: { ...filters, limit: 200 } }),
    select: (page) => page.items,
  });
}

// --- Calendar --------------------------------------------------------------

/**
 * Events between two ISO dates (UTC), e.g. the visible week of the agenda.
 * ASSUMPTION to confirm with backend: the contract says "calendar range" without naming
 * the query parameters; we use from/to.
 */
export function useEvents(range: { from: string; to: string } | null) {
  return useQuery({
    queryKey: queryKeys.events(range ?? { from: "", to: "" }),
    queryFn: () => apiFetch<Page<CalendarEvent>>("/events", { query: { ...range, limit: 200 } }),
    select: (page) => page.items,
    // null = range not known yet (e.g. the calendar has not rendered): wait.
    enabled: range !== null,
    // Keep showing the previous week while the next one loads.
    placeholderData: (previous) => previous,
  });
}

export function useCreateEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (event: EventCreate) => apiFetch<CalendarEvent>("/events", { method: "POST", body: event }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.allEvents }),
  });
}

/** Only personal events can be deleted; the server answers 403 for imported ones. */
export function useDeleteEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (eventId: Uuid) => apiFetch<void>(`/events/${eventId}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.allEvents }),
  });
}

/** Confirms or rejects an ambiguous assessment (see EventConfirmation). */
export function useConfirmEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: EventConfirmation & { id: Uuid }) =>
      apiFetch<CalendarEvent>(`/events/${id}/confirm`, { method: "POST", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.allEvents }),
  });
}

// --- Tasks -----------------------------------------------------------------

export function useTasks() {
  return useQuery({
    queryKey: queryKeys.tasks,
    queryFn: () => apiFetch<Page<Task>>("/tasks", { query: { limit: 200 } }),
    select: (page) => page.items,
  });
}

export function useCreateTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (task: TaskCreate) => apiFetch<Task>("/tasks", { method: "POST", body: task }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.tasks }),
  });
}

export function useUpdateTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...changes }: Partial<Task> & { id: Uuid; expected_revision: number }) =>
      apiFetch<Task>(`/tasks/${id}`, { method: "PATCH", body: changes }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.tasks }),
  });
}

export function useDeleteTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskId: Uuid) => apiFetch<void>(`/tasks/${taskId}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.tasks }),
  });
}

// --- Annotations -------------------------------------------------------------

export function useAnnotations(documentId: Uuid) {
  return useQuery({
    queryKey: ["annotations", documentId],
    queryFn: () => apiFetch<Page<Annotation>>("/annotations", { query: { document_id: documentId } }),
    select: (page) => page.items,
  });
}

export function useCreateAnnotation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Omit<Annotation, "id" | "created_at">) => apiFetch<Annotation>("/annotations", { method: "POST", body: input }),
    onSuccess: (annotation) => queryClient.invalidateQueries({ queryKey: ["annotations", annotation.document_id] }),
  });
}

export function useDeleteAnnotation(documentId: Uuid) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: Uuid) => apiFetch<void>(`/annotations/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["annotations", documentId] }),
  });
}

/**
 * Query/mutation hooks. The server is authoritative: every mutation invalidates the
 * read models it can affect (docs/FRONTEND_UX_UI.md, "State and API handling").
 */
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api, ApiError, newIdempotencyKey } from "./client";
import type {
  AiPreferences,
  AiProvider,
  Analytics,
  AnnotationCreate,
  AnnotationOut,
  ArtifactGenerateRequest,
  ArtifactOut,
  ArtifactVersionOut,
  Assessment,
  AssessmentPage,
  AssessmentPatch,
  Attempt,
  AttemptResult,
  Availability,
  CalendarEvent,
  CalibrationOut,
  Card,
  CardPatch,
  CatalogSubjectDetail,
  CatalogSubjectItem,
  CitationOut,
  ConversationMessageOut,
  ConversationOut,
  Deck,
  DecomposeOut,
  ErrorCreate,
  ErrorEntry,
  ErrorPatch,
  EventConfirm,
  EventCreate,
  EventPage,
  EventPatch,
  ExamMode,
  Focus,
  FocusAction,
  FocusFinish,
  FocusStart,
  GradeObjective,
  GradeObjectiveCreate,
  GradeObjectivePatch,
  GradeProgram,
  GradeRecord,
  GradeRecordCreate,
  GradeRecordPatch,
  GradesImportRequest,
  GradesImportResult,
  GradeSummary,
  Heatmap,
  ItemGrade,
  Mastery,
  MasteryMap,
  MockExamRequest,
  Note,
  Notebook,
  NoteVersion,
  NotificationList,
  Plan,
  PlannerStatus,
  PlanPreview,
  Profile,
  ProfilePatch,
  ProgramCreate,
  Quiz,
  QuizCreate,
  Rating,
  Readiness,
  ReindexOut,
  ReplanReason,
  RequiredOut,
  RequiredRequest,
  ReviewResult,
  RulesOut,
  RulesPut,
  ScenarioCreate,
  ScenarioEvaluationOut,
  ScenarioOut,
  ScenarioPatch,
  SearchResponse,
  SessionFinish,
  SignalsOut,
  SimulateOut,
  SimulateRequest,
  StudentStatusOption,
  StudyNeed,
  StudySession,
  SubtasksCreate,
  Task,
  TaskCreate,
  TaskPage,
  TaskPatch,
  TrendsOut,
  TutorAnswerOut,
  TutorAnswerRequest,
  ValidationOut,
  CurriculumOut,
  CurriculumPut,
  NodeOut,
  NodePatch,
} from "./types";

export const keys = {
  profile: ["profile"] as const,
  studentStatuses: ["profile", "student-statuses"] as const,
  events: (start: string, end: string) => ["events", start, end] as const,
  assessments: ["assessments"] as const,
  tasks: (status?: string) => ["tasks", status ?? "all"] as const,
  availability: (start: string, end: string) => ["availability", start, end] as const,
  plan: ["plan", "current"] as const,
  plannerStatus: ["planner", "status"] as const,
  decks: ["decks"] as const,
  deckCards: (deckId: string) => ["decks", deckId, "cards"] as const,
  due: (deckId?: string) => ["reviews", "due", deckId ?? "all"] as const,
  quizzes: ["quizzes"] as const,
  calibration: ["learning", "calibration"] as const,
  mastery: ["mastery"] as const,
  studyNeeds: ["study-needs"] as const,
  examMode: ["exam-mode"] as const,
  analytics: (start: string, end: string, subjectId?: string) => ["analytics", start, end, subjectId ?? "all"] as const,
  heatmap: (start: string, end: string) => ["analytics", "heatmap", start, end] as const,
  readiness: ["analytics", "readiness"] as const,
  masteryMap: ["analytics", "mastery-map"] as const,
  focusActive: ["focus", "active"] as const,
  notifications: ["notifications"] as const,
  notebooks: ["notebooks"] as const,
  notes: (notebookId?: string) => ["notes", notebookId ?? "all"] as const,
  noteVersions: (noteId: string) => ["notes", noteId, "versions"] as const,
  annotations: (documentId?: string, noteId?: string) => ["annotations", documentId ?? "all", noteId ?? "all"] as const,
  errors: ["errors"] as const,
  gradePrograms: ["grades", "programs"] as const,
  gradeCurriculum: (programId: string) => ["grades", "curriculum", programId] as const,
  gradeRecords: (programId: string) => ["grades", "records", programId] as const,
  gradeObjectives: (programId: string) => ["grades", "objectives", programId] as const,
  gradeSummary: (programId: string) => ["grades", "summary", programId] as const,
  gradeSignals: (programId: string) => ["grades", "signals", programId] as const,
  gradeRules: (programId: string) => ["grades", "rules", programId] as const,
  gradeValidation: (programId: string) => ["grades", "validation", programId] as const,
  gradeScenarios: (programId: string) => ["grades", "scenarios", programId] as const,
  gradeScenarioEval: (scenarioId: string) => ["grades", "scenario-eval", scenarioId] as const,
  gradeTrends: (programId: string) => ["grades", "trends", programId] as const,
  catalogSubjects: ["catalog", "subjects"] as const,
  catalogSubject: (id: string) => ["catalog", "subjects", id] as const,
  ragSearch: (q: string, mode: string, subjectId?: string, resourceType?: string) =>
    ["rag", "search", q, mode, subjectId ?? "", resourceType ?? ""] as const,
  aiProviders: ["ai", "providers"] as const,
  aiPreferences: ["ai", "preferences"] as const,
  conversations: ["ai", "conversations"] as const,
  conversationMessages: (id: string) => ["ai", "conversations", id, "messages"] as const,
  artifacts: (subjectId?: string) => ["ai", "artifacts", subjectId ?? "all"] as const,
  artifactVersions: (id: string) => ["ai", "artifacts", id, "versions"] as const,
};

/** Read models touched by planning, learning and focus activity. */
function invalidateStudy(qc: QueryClient) {
  for (const key of [
    ["plan"],
    ["planner"],
    ["events"],
    ["assessments"],
    ["availability"],
    ["analytics"],
    ["mastery"],
    ["study-needs"],
    ["reviews"],
    ["decks"],
    ["tasks"],
    ["notifications"],
    ["focus"],
    ["catalog"],
  ]) {
    void qc.invalidateQueries({ queryKey: key });
  }
}

function invalidateGrades(qc: QueryClient) {
  for (const key of [["grades"], ["results"], ["planner"], ["analytics"], ["notifications"]]) {
    void qc.invalidateQueries({ queryKey: key });
  }
}

/* ------------------------------- Account ------------------------------- */

export const useProfile = () => useQuery({ queryKey: keys.profile, queryFn: () => api.get<Profile>("/profile") });

export const useRuntimeMode = () =>
  useQuery({
    queryKey: ["runtime-mode"],
    queryFn: () => api.get<{ mode: "real" | "demo" }>("/runtime-mode"),
    retry: false,
    staleTime: Infinity,
  });

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: ProfilePatch) => api.patch<Profile>("/profile", patch),
    onSuccess: (profile) => {
      qc.setQueryData(keys.profile, profile);
      void qc.invalidateQueries({ queryKey: ["planner"] });
      void qc.invalidateQueries({ queryKey: ["availability"] });
    },
  });
}

export const useStudentStatuses = () =>
  useQuery({
    queryKey: keys.studentStatuses,
    queryFn: () => api.get<StudentStatusOption[]>("/profile/student-statuses"),
    staleTime: Infinity,
  });

export function useExportAccount() {
  return useMutation({
    mutationFn: () => api.post<Record<string, unknown>>("/account/export"),
  });
}

export function useDeleteAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (confirm: string) => api.delete<void>("/account", undefined, { confirm }),
    onSuccess: () => {
      qc.clear();
    },
  });
}

/* ------------------------------- Agenda -------------------------------- */

export const useEvents = (start: string, end: string) =>
  useQuery({
    queryKey: keys.events(start, end),
    queryFn: () => api.get<EventPage>("/events", { start, end, limit: 200 }),
  });

export function useCreateEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: EventCreate) => api.post<CalendarEvent>("/events", body),
    onSuccess: () => invalidateStudy(qc),
  });
}

export function useUpdateEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: EventPatch }) => api.patch<CalendarEvent>(`/events/${id}`, patch),
    onSuccess: () => invalidateStudy(qc),
  });
}

export function useDeleteEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, revision }: { id: string; revision: number }) =>
      api.delete<void>(`/events/${id}`, { expected_revision: revision }),
    onSuccess: () => invalidateStudy(qc),
  });
}

export function useConfirmEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: EventConfirm }) =>
      api.post<CalendarEvent>(`/events/${id}/confirm`, body),
    onSuccess: () => invalidateStudy(qc),
  });
}

export const useAssessments = () =>
  useQuery({ queryKey: keys.assessments, queryFn: () => api.get<AssessmentPage>("/assessments", { limit: 200 }) });

export function usePatchAssessment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: AssessmentPatch }) =>
      api.patch<Assessment>(`/assessments/${id}`, patch),
    onSuccess: () => invalidateStudy(qc),
  });
}

export const useAvailability = (start: string, end: string) =>
  useQuery({
    queryKey: keys.availability(start, end),
    queryFn: () => api.get<Availability>("/availability", { start, end }),
  });

export const useTasks = (status?: string) =>
  useQuery({ queryKey: keys.tasks(status), queryFn: () => api.get<TaskPage>("/tasks", { status, limit: 200 }) });

export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TaskCreate) => api.post<Task>("/tasks", body),
    onSuccess: () => invalidateStudy(qc),
  });
}

export function useUpdateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: TaskPatch }) => api.patch<Task>(`/tasks/${id}`, patch),
    onSuccess: () => invalidateStudy(qc),
  });
}

export function useDeleteTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, revision }: { id: string; revision: number }) =>
      api.delete<void>(`/tasks/${id}`, { expected_revision: revision }),
    onSuccess: () => invalidateStudy(qc),
  });
}

export function useDecomposeTask() {
  return useMutation({
    mutationFn: (body: { text: string; total_estimated_min?: number | null }) =>
      api.post<DecomposeOut>("/tasks/decompose", body),
  });
}

export function useCreateSubtasks() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, body }: { taskId: string; body: SubtasksCreate }) =>
      api.post<Task[]>(`/tasks/${taskId}/subtasks`, body),
    onSuccess: () => invalidateStudy(qc),
  });
}

/* ---------------------------- Smart Planner ---------------------------- */

export const usePlannerStatus = () =>
  useQuery({ queryKey: keys.plannerStatus, queryFn: () => api.get<PlannerStatus>("/planner/status") });

export const usePlan = () => {
  const status = usePlannerStatus();
  const hasPlan = status.data?.has_plan;
  return useQuery({
    queryKey: [...keys.plan, hasPlan ?? "pending"] as const,
    enabled: hasPlan !== undefined || status.isError,
    // Avoid noisy 404 console errors when planner/status already says has_plan=false.
    queryFn: async () => {
      if (hasPlan === false) return null;
      try {
        return await api.get<Plan>("/plans/current");
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
  });
};

export function usePreviewPlan() {
  return useMutation({
    mutationFn: (input: {
      horizonDays?: number;
      algorithm?: "greedy" | "cpsat";
      replan?: { baseRevision: number; reason: ReplanReason };
    }) =>
      input.replan
        ? api.post<PlanPreview>("/planner/replan", {
            base_plan_revision: input.replan.baseRevision,
            reason: input.replan.reason,
            horizon_days: input.horizonDays ?? 7,
            ...(input.algorithm ? { algorithm: input.algorithm } : {}),
          })
        : api.post<PlanPreview>("/planner/preview", {
            horizon_days: input.horizonDays ?? 7,
            ...(input.algorithm ? { algorithm: input.algorithm } : {}),
          }),
  });
}

export function useAcceptPlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (preview: Pick<PlanPreview, "preview_id" | "input_revision">) =>
      api.post<Plan>("/planner/accept", { preview_id: preview.preview_id, input_revision: preview.input_revision }),
    onSuccess: (plan) => {
      qc.setQueryData([...keys.plan, true], plan);
      invalidateStudy(qc);
    },
  });
}

export function useUpdateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...patch }: { id: string; expected_revision: number; locked?: boolean; start_at?: string; end_at?: string }) =>
      api.patch<StudySession>(`/sessions/${id}`, patch),
    onSuccess: () => invalidateStudy(qc),
  });
}

export function useStartSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<StudySession>(`/sessions/${id}/start`),
    onSuccess: () => invalidateStudy(qc),
  });
}

/**
 * Finishes a session safely (BLK-06 fix): if the session is still `planned` and outcome
 * is `completed` or `partial`, chains `POST /sessions/{id}/start` before `POST /sessions/{id}/finish`.
 */
export function useFinishSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, status, body }: { id: string; status?: StudySession["status"]; body: SessionFinish }) => {
      if (status === "planned" && body.outcome !== "skipped") {
        await api.post<StudySession>(`/sessions/${id}/start`);
      }
      return api.post<StudySession>(`/sessions/${id}/finish`, body);
    },
    onSuccess: () => invalidateStudy(qc),
  });
}

/* ------------------------------- Learning ------------------------------ */

export const useDecks = () => useQuery({ queryKey: keys.decks, queryFn: () => api.get<Deck[]>("/decks") });

export const useDeckCards = (deckId?: string) =>
  useQuery({
    queryKey: keys.deckCards(deckId ?? ""),
    queryFn: () => api.get<Card[]>(`/decks/${deckId}/cards`),
    enabled: Boolean(deckId),
  });

export const useDueCards = (deckId?: string, enabled = true) =>
  useQuery({ queryKey: keys.due(deckId), queryFn: () => api.get<Card[]>("/reviews/due", { deck_id: deckId }), enabled });

export function useCreateDeck() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { title: string; subject_id?: string | null }) => api.post<Deck>("/decks", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.decks }),
  });
}

export function useCreateCard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ deckId, front, back, conceptId }: { deckId: string; front: string; back: string; conceptId?: string | null }) =>
      api.post<Card>(`/decks/${deckId}/cards`, { front, back, ...(conceptId ? { concept_id: conceptId } : {}) }),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: keys.decks });
      void qc.invalidateQueries({ queryKey: keys.deckCards(vars.deckId) });
      void qc.invalidateQueries({ queryKey: ["reviews"] });
    },
  });
}

export function useUpdateCard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, deckId, patch }: { id: string; deckId: string; patch: CardPatch }) =>
      api.patch<Card>(`/cards/${id}`, patch).then((card) => ({ card, deckId })),
    onSuccess: ({ deckId }) => {
      void qc.invalidateQueries({ queryKey: keys.deckCards(deckId) });
      void qc.invalidateQueries({ queryKey: ["reviews"] });
    },
  });
}

export function useReviewCard() {
  const qc = useQueryClient();
  return useMutation({
    // One key per answer: a double click or a network retry is replayed, not counted twice.
    mutationFn: ({ card, rating, key }: { card: Card; rating: Rating; key: string }) =>
      api.post<ReviewResult>(
        `/cards/${card.id}/review`,
        { rating, expected_revision: card.revision },
        { idempotencyKey: key },
      ),
    onSuccess: () => invalidateStudy(qc),
  });
}

export const useQuizzes = () => useQuery({ queryKey: keys.quizzes, queryFn: () => api.get<Quiz[]>("/quizzes") });

export function useCreateQuiz() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: QuizCreate) => api.post<Quiz>("/quizzes", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.quizzes });
      void qc.invalidateQueries({ queryKey: ["catalog"] });
    },
  });
}

export function useGenerateMockExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MockExamRequest) => api.post<Quiz>("/quizzes/mock", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.quizzes });
      void qc.invalidateQueries({ queryKey: ["catalog"] });
    },
  });
}

export function useStartAttempt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ quizId, mode }: { quizId: string; mode: "practice" | "exam" }) =>
      api.post<Attempt>(`/quizzes/${quizId}/attempts`, { mode }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.examMode }),
  });
}

export function useSaveResponses() {
  return useMutation({
    mutationFn: ({ attemptId, responses }: { attemptId: string; responses: { question_id: string; response: unknown }[] }) =>
      api.patch<Attempt>(`/attempts/${attemptId}/responses`, { responses }),
  });
}

export function useSubmitAttempt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (attemptId: string) =>
      api.post<AttemptResult>(`/attempts/${attemptId}/submit`, undefined, { idempotencyKey: newIdempotencyKey() }),
    onSuccess: () => {
      invalidateStudy(qc);
      void qc.invalidateQueries({ queryKey: keys.examMode });
    },
  });
}

export function useGradeAttemptItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ attemptId, questionId, body }: { attemptId: string; questionId: string; body: ItemGrade }) =>
      api.post<AttemptResult>(`/attempts/${attemptId}/items/${questionId}/grade`, body),
    onSuccess: () => invalidateStudy(qc),
  });
}

export const useAttempt = (id?: string | null) =>
  useQuery({
    queryKey: ["learning", "attempt", id],
    queryFn: () => api.get<Attempt>(`/attempts/${encodeURIComponent(id!)}`),
    enabled: Boolean(id),
  });

export const useExamMode = () =>
  useQuery({ queryKey: keys.examMode, queryFn: () => api.get<ExamMode>("/exam-mode"), refetchInterval: 30_000 });

export const useMastery = () => useQuery({ queryKey: keys.mastery, queryFn: () => api.get<Mastery[]>("/mastery") });

export const useStudyNeeds = () =>
  useQuery({ queryKey: keys.studyNeeds, queryFn: () => api.get<StudyNeed[]>("/study-needs") });

export const useCalibration = () =>
  useQuery({ queryKey: keys.calibration, queryFn: () => api.get<CalibrationOut>("/learning/calibration") });

/* ------------------------------- Analytics ----------------------------- */

export const useAnalytics = (start: string, end: string, subjectId?: string) =>
  useQuery({
    queryKey: keys.analytics(start, end, subjectId),
    queryFn: () => api.get<Analytics>("/analytics", { start, end, subject_id: subjectId }),
  });

export const useHeatmap = (start: string, end: string) =>
  useQuery({ queryKey: keys.heatmap(start, end), queryFn: () => api.get<Heatmap>("/analytics/heatmap", { start, end }) });

export const useReadiness = () =>
  useQuery({ queryKey: keys.readiness, queryFn: () => api.get<Readiness>("/analytics/readiness") });

export const useMasteryMap = () =>
  useQuery({ queryKey: keys.masteryMap, queryFn: () => api.get<MasteryMap>("/analytics/mastery-map") });

/* --------------------------------- Focus ------------------------------- */

export const useActiveFocus = () =>
  useQuery({ queryKey: keys.focusActive, queryFn: () => api.get<Focus | null>("/focus/active") });

export function useStartFocus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FocusStart) => api.post<Focus>("/focus/start", body),
    onSuccess: (focus) => {
      qc.setQueryData(keys.focusActive, focus);
      invalidateStudy(qc);
    },
  });
}

export function useFocusTransition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, minutes }: { id: string; action: FocusAction; minutes?: number }) =>
      api.post<Focus>(`/focus/${id}/transition`, { action, minutes }),
    onSuccess: (focus) => qc.setQueryData(keys.focusActive, focus),
  });
}

export function useFinishFocus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: FocusFinish }) => api.post<Focus>(`/focus/${id}/finish`, body),
    onSuccess: () => {
      qc.setQueryData(keys.focusActive, null);
      invalidateStudy(qc);
    },
  });
}

/* ------------------------------ Workspace ------------------------------ */

export const useNotebooks = () =>
  useQuery({ queryKey: keys.notebooks, queryFn: () => api.get<Notebook[]>("/notebooks") });

export function useCreateNotebook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { title: string; subject_id?: string | null }) => api.post<Notebook>("/notebooks", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.notebooks }),
  });
}

export const useNotes = (notebookId?: string) =>
  useQuery({
    queryKey: keys.notes(notebookId),
    queryFn: () => api.get<Note[]>("/notes", { notebook_id: notebookId }),
    enabled: Boolean(notebookId),
  });

export const useNoteVersions = (noteId?: string) =>
  useQuery({
    queryKey: keys.noteVersions(noteId ?? ""),
    queryFn: () => api.get<NoteVersion[]>(`/notes/${noteId}/versions`),
    enabled: Boolean(noteId),
  });

export function useSaveNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id?: string; notebookId: string; title: string; markdown: string; revision?: number }) =>
      input.id
        ? api.patch<Note>(`/notes/${input.id}`, {
            title: input.title,
            markdown: input.markdown,
            expected_revision: input.revision,
          })
        : api.post<Note>("/notes", { notebook_id: input.notebookId, title: input.title, markdown: input.markdown }),
    onSuccess: (note) => {
      void qc.invalidateQueries({ queryKey: ["notes"] });
      void qc.invalidateQueries({ queryKey: keys.noteVersions(note.id) });
    },
  });
}

export function useDeleteNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, revision }: { id: string; revision: number }) =>
      api.delete<void>(`/notes/${id}`, { expected_revision: revision }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notes"] }),
  });
}

export const useErrors = () => useQuery({ queryKey: keys.errors, queryFn: () => api.get<ErrorEntry[]>("/errors") });

export function useCreateError() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ErrorCreate) => api.post<ErrorEntry>("/errors", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.errors });
      void qc.invalidateQueries({ queryKey: ["planner"] });
    },
  });
}

export function useUpdateError() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ErrorPatch }) => api.patch<ErrorEntry>(`/errors/${id}`, patch),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.errors }),
  });
}

export const useAnnotations = (documentId?: string, noteId?: string) =>
  useQuery({
    queryKey: keys.annotations(documentId, noteId),
    queryFn: () => api.get<AnnotationOut[]>("/annotations", { document_id: documentId, note_id: noteId }),
    retry: false,
  });

export function useCreateAnnotation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AnnotationCreate) => api.post<AnnotationOut>("/annotations", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["annotations"] }),
  });
}

/* ---------------------------- Notifications ---------------------------- */

export const useNotifications = () =>
  useQuery({
    queryKey: keys.notifications,
    queryFn: () => api.get<NotificationList>("/notifications", { limit: 100 }),
    refetchInterval: 60_000,
  });

export function useMarkNotification() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, read }: { id: string; read: boolean }) => api.patch(`/notifications/${id}`, { read }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.notifications }),
  });
}

export function useMarkAllNotifications() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post("/notifications/read-all"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.notifications }),
  });
}

/* -------------------------------- Grades ------------------------------- */

export const useGradePrograms = () =>
  useQuery({ queryKey: keys.gradePrograms, queryFn: () => api.get<GradeProgram[]>("/grades/programs") });

export function useCreateProgram() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProgramCreate) => api.post<GradeProgram>("/grades/programs", body),
    onSuccess: () => invalidateGrades(qc),
  });
}

export const useGradeCurriculum = (programId?: string) =>
  useQuery({
    queryKey: keys.gradeCurriculum(programId ?? ""),
    queryFn: () => api.get<CurriculumOut>(`/grades/programs/${programId}/curriculum`),
    enabled: Boolean(programId),
  });

export function usePatchGradeNode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: NodePatch }) =>
      api.patch<NodeOut>(`/grades/nodes/${encodeURIComponent(id)}`, body),
    onSuccess: () => invalidateGrades(qc),
  });
}

export function usePutCurriculum() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ programId, body }: { programId: string; body: CurriculumPut }) =>
      api.put<CurriculumOut>(`/grades/programs/${programId}/curriculum`, body),
    onSuccess: () => invalidateGrades(qc),
  });
}

export const useGradeRecords = (programId?: string) =>
  useQuery({
    queryKey: keys.gradeRecords(programId ?? ""),
    queryFn: () => api.get<GradeRecord[]>("/grades/records", { program_id: programId }),
    enabled: Boolean(programId),
  });

export function useCreateGradeRecord() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GradeRecordCreate) => api.post<GradeRecord>("/grades/records", body),
    onSuccess: () => invalidateGrades(qc),
  });
}

export function usePatchGradeRecord() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: GradeRecordPatch }) =>
      api.patch<GradeRecord>(`/grades/records/${id}`, body),
    onSuccess: () => invalidateGrades(qc),
  });
}

export function useDeleteGradeRecord() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/grades/records/${id}`),
    onSuccess: () => invalidateGrades(qc),
  });
}

export const useGradeObjectives = (programId?: string) =>
  useQuery({
    queryKey: keys.gradeObjectives(programId ?? ""),
    queryFn: () => api.get<GradeObjective[]>("/grades/objectives", { program_id: programId }),
    enabled: Boolean(programId),
  });

export function useCreateObjectives() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GradeObjectiveCreate) => api.post<GradeObjective[]>("/grades/objectives", body),
    onSuccess: () => invalidateGrades(qc),
  });
}

export function usePatchObjective() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: GradeObjectivePatch }) =>
      api.patch<GradeObjective>(`/grades/objectives/${id}`, body),
    onSuccess: () => invalidateGrades(qc),
  });
}

export function useDeleteObjective() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/grades/objectives/${id}`),
    onSuccess: () => invalidateGrades(qc),
  });
}

export const useGradeSummary = (programId?: string) =>
  useQuery({
    queryKey: keys.gradeSummary(programId ?? ""),
    queryFn: () => api.get<GradeSummary>("/grades/summary", { program_id: programId }),
    enabled: Boolean(programId),
  });

export function useRequiredGrades() {
  return useMutation({
    mutationFn: (body: RequiredRequest) => api.post<RequiredOut>("/grades/required", body),
  });
}

export function useSimulateGrades() {
  return useMutation({
    mutationFn: (body: SimulateRequest) => api.post<SimulateOut>("/grades/simulate", body),
  });
}

export const useGradeSignals = (programId?: string) =>
  useQuery({
    queryKey: keys.gradeSignals(programId ?? ""),
    queryFn: () => api.get<SignalsOut>("/grades/signals", { program_id: programId }),
    enabled: Boolean(programId),
  });

export const useGradeRules = (programId?: string) =>
  useQuery({
    queryKey: keys.gradeRules(programId ?? ""),
    queryFn: () => api.get<RulesOut>(`/grades/programs/${programId}/rules`),
    enabled: Boolean(programId),
  });

export function usePutGradeRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ programId, body }: { programId: string; body: RulesPut }) =>
      api.put<RulesOut>(`/grades/programs/${programId}/rules`, body),
    onSuccess: () => invalidateGrades(qc),
  });
}

export const useGradeValidation = (programId?: string) =>
  useQuery({
    queryKey: keys.gradeValidation(programId ?? ""),
    queryFn: () => api.get<ValidationOut>("/grades/validation", { program_id: programId }),
    enabled: Boolean(programId),
  });

export const useGradeScenarios = (programId?: string) =>
  useQuery({
    queryKey: keys.gradeScenarios(programId ?? ""),
    queryFn: () => api.get<ScenarioOut[]>("/grades/scenarios", { program_id: programId }),
    enabled: Boolean(programId),
  });

export function useCreateScenario() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ScenarioCreate) => api.post<ScenarioOut>("/grades/scenarios", body),
    onSuccess: () => invalidateGrades(qc),
  });
}

export function usePatchScenario() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ScenarioPatch }) =>
      api.patch<ScenarioOut>(`/grades/scenarios/${id}`, body),
    onSuccess: () => invalidateGrades(qc),
  });
}

export function useDeleteScenario() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/grades/scenarios/${id}`),
    onSuccess: () => invalidateGrades(qc),
  });
}

export const useEvaluateScenario = (scenarioId?: string) =>
  useQuery({
    queryKey: keys.gradeScenarioEval(scenarioId ?? ""),
    queryFn: () => api.get<ScenarioEvaluationOut>(`/grades/scenarios/${scenarioId}/evaluate`),
    enabled: Boolean(scenarioId),
  });

export const useGradeTrends = (programId?: string) =>
  useQuery({
    queryKey: keys.gradeTrends(programId ?? ""),
    queryFn: () => api.get<TrendsOut>("/grades/trends", { program_id: programId }),
    enabled: Boolean(programId),
  });

export function useImportGradesFromSources() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GradesImportRequest = { source: "pamplemousse", dry_run: false }) =>
      api.post<GradesImportResult>("/grades/import", body),
    onSuccess: () => invalidateGrades(qc),
  });
}

/* ------------------------------- Catalog ------------------------------- */

export const useCatalogSubjects = () =>
  useQuery({
    queryKey: keys.catalogSubjects,
    queryFn: () => api.get<{ items: CatalogSubjectItem[] }>("/subjects"),
    retry: false,
  });

export const useCatalogSubject = (subjectId?: string) =>
  useQuery({
    queryKey: keys.catalogSubject(subjectId ?? ""),
    queryFn: () => api.get<CatalogSubjectDetail>(`/subjects/${encodeURIComponent(subjectId!)}`),
    enabled: Boolean(subjectId),
    retry: false,
  });

/* --------------------------- AI / RAG / Studio ------------------------- */

export const useRagSearch = (q: string, mode: "hybrid" | "lexical" | "semantic" = "hybrid", subjectId?: string, resourceType?: string) =>
  useQuery({
    queryKey: keys.ragSearch(q, mode, subjectId, resourceType),
    queryFn: () =>
      api.get<SearchResponse>("/search", {
        q,
        mode,
        subject_id: subjectId || undefined,
        resource_type: resourceType || undefined,
        limit: 20,
      }),
    enabled: q.trim().length >= 2,
    retry: false,
  });

export function useCitationLookup() {
  return useMutation({
    mutationFn: (chunkId: string) => api.get<CitationOut>(`/citations/${encodeURIComponent(chunkId)}`),
  });
}

export function useRagReindex() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ReindexOut>("/rag/reindex"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["rag"] }),
  });
}

export const useAiProviders = () =>
  useQuery({
    queryKey: keys.aiProviders,
    queryFn: () => api.get<{ items: AiProvider[] }>("/ai/providers"),
    retry: false,
  });

export function useRefreshAiProviders() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ items: AiProvider[] }>("/ai/providers/refresh"),
    onSuccess: (data) => qc.setQueryData(keys.aiProviders, data),
  });
}

export const useAiPreferences = () =>
  useQuery({
    queryKey: keys.aiPreferences,
    queryFn: () => api.get<AiPreferences>("/ai/preferences"),
    retry: false,
  });

export function useUpdateAiPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<AiPreferences>) => api.patch<AiPreferences>("/ai/preferences", patch),
    onSuccess: (data) => qc.setQueryData(keys.aiPreferences, data),
  });
}

export const useConversations = () =>
  useQuery({
    queryKey: keys.conversations,
    queryFn: () => api.get<ConversationOut[]>("/conversations"),
    retry: false,
  });

export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { subject_id?: string | null; title?: string | null }) =>
      api.post<ConversationOut>("/conversations", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.conversations }),
  });
}

export const useConversationMessages = (conversationId?: string) =>
  useQuery({
    queryKey: keys.conversationMessages(conversationId ?? ""),
    queryFn: () => api.get<ConversationMessageOut[]>(`/conversations/${encodeURIComponent(conversationId!)}/messages`),
    enabled: Boolean(conversationId),
    retry: false,
  });

export function useTutorAnswer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TutorAnswerRequest) => api.post<TutorAnswerOut>("/tutor/answer", body),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: keys.conversations });
      if (res.conversation_id) {
        void qc.invalidateQueries({ queryKey: keys.conversationMessages(res.conversation_id) });
      }
    },
  });
}

export const useArtifacts = (subjectId?: string) =>
  useQuery({
    queryKey: keys.artifacts(subjectId),
    queryFn: () => api.get<ArtifactOut[]>("/artifacts", { subject_id: subjectId || undefined }),
    retry: false,
  });

export function useGenerateArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ArtifactGenerateRequest) => api.post<ArtifactOut>("/artifacts/generate", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["ai", "artifacts"] });
      void qc.invalidateQueries({ queryKey: keys.decks });
      void qc.invalidateQueries({ queryKey: keys.quizzes });
      void qc.invalidateQueries({ queryKey: ["catalog"] });
    },
  });
}

export function useUpdateArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title, content_markdown }: { id: string; title?: string; content_markdown: string }) =>
      api.patch<ArtifactOut>(`/artifacts/${encodeURIComponent(id)}`, { title, content_markdown }),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: ["ai", "artifacts"] });
      void qc.invalidateQueries({ queryKey: keys.artifactVersions(vars.id) });
    },
  });
}

export const useArtifactVersions = (artifactId?: string) =>
  useQuery({
    queryKey: keys.artifactVersions(artifactId ?? ""),
    queryFn: () => api.get<ArtifactVersionOut[]>(`/artifacts/${encodeURIComponent(artifactId!)}/versions`),
    enabled: Boolean(artifactId),
    retry: false,
  });

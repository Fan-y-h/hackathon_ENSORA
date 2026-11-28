/**
 * Query/mutation hooks. The server is authoritative: every mutation invalidates the
 * read models it can affect (docs/FRONTEND_UX_UI.md, "State and API handling").
 */
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api, ApiError, newIdempotencyKey } from "./client";
import type {
  Analytics,
  AssessmentPage,
  Attempt,
  AttemptResult,
  Availability,
  Card,
  Deck,
  ErrorEntry,
  EventPage,
  ExamMode,
  Focus,
  FocusAction,
  FocusFinish,
  FocusStart,
  GradeProgram,
  GradeSummary,
  Heatmap,
  Mastery,
  MasteryMap,
  Note,
  Notebook,
  NotificationList,
  Plan,
  PlannerStatus,
  PlanPreview,
  Profile,
  ProfilePatch,
  StudentStatusOption,
  Quiz,
  Rating,
  Readiness,
  ReplanReason,
  ReviewResult,
  SessionFinish,
  StudyNeed,
  StudySession,
  Task,
  TaskCreate,
  TaskPage,
  TaskPatch,
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
  due: ["reviews", "due"] as const,
  quizzes: ["quizzes"] as const,
  mastery: ["mastery"] as const,
  studyNeeds: ["study-needs"] as const,
  examMode: ["exam-mode"] as const,
  analytics: (start: string, end: string) => ["analytics", start, end] as const,
  heatmap: (start: string, end: string) => ["analytics", "heatmap", start, end] as const,
  readiness: ["analytics", "readiness"] as const,
  masteryMap: ["analytics", "mastery-map"] as const,
  focusActive: ["focus", "active"] as const,
  notifications: ["notifications"] as const,
  notebooks: ["notebooks"] as const,
  notes: (notebookId?: string) => ["notes", notebookId ?? "all"] as const,
  errors: ["errors"] as const,
  gradePrograms: ["grades", "programs"] as const,
  gradeSummary: (programId: string) => ["grades", "summary", programId] as const,
};

/** Read models touched by planning, learning and focus activity. */
function invalidateStudy(qc: QueryClient) {
  for (const key of [
    ["plan"],
    ["planner"],
    ["analytics"],
    ["mastery"],
    ["study-needs"],
    ["reviews"],
    ["decks"],
    ["tasks"],
    ["notifications"],
    ["focus"],
  ]) {
    void qc.invalidateQueries({ queryKey: key });
  }
}

/* ------------------------------- Account ------------------------------- */

export const useProfile = () => useQuery({ queryKey: keys.profile, queryFn: () => api.get<Profile>("/profile") });

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

/* ------------------------------- Agenda -------------------------------- */

export const useEvents = (start: string, end: string) =>
  useQuery({
    queryKey: keys.events(start, end),
    queryFn: () => api.get<EventPage>("/events", { start, end, limit: 200 }),
  });

export const useAssessments = () =>
  useQuery({ queryKey: keys.assessments, queryFn: () => api.get<AssessmentPage>("/assessments", { limit: 200 }) });

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

/* ---------------------------- Smart Planner ---------------------------- */

export const usePlan = () =>
  useQuery({
    queryKey: keys.plan,
    // 404 means "no accepted plan yet": a normal empty state, not an error.
    queryFn: async () => {
      try {
        return await api.get<Plan>("/plans/current");
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
  });

export const usePlannerStatus = () =>
  useQuery({ queryKey: keys.plannerStatus, queryFn: () => api.get<PlannerStatus>("/planner/status") });

export function usePreviewPlan() {
  return useMutation({
    mutationFn: (input: { horizonDays?: number; replan?: { baseRevision: number; reason: ReplanReason } }) =>
      input.replan
        ? api.post<PlanPreview>("/planner/replan", {
            base_plan_revision: input.replan.baseRevision,
            reason: input.replan.reason,
            horizon_days: input.horizonDays ?? 7,
          })
        : api.post<PlanPreview>("/planner/preview", { horizon_days: input.horizonDays ?? 7 }),
  });
}

export function useAcceptPlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (preview: Pick<PlanPreview, "preview_id" | "input_revision">) =>
      api.post<Plan>("/planner/accept", { preview_id: preview.preview_id, input_revision: preview.input_revision }),
    onSuccess: (plan) => {
      qc.setQueryData(keys.plan, plan);
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

export function useFinishSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: SessionFinish }) =>
      api.post<StudySession>(`/sessions/${id}/finish`, body),
    onSuccess: () => invalidateStudy(qc),
  });
}

/* ------------------------------- Learning ------------------------------ */

export const useDecks = () => useQuery({ queryKey: keys.decks, queryFn: () => api.get<Deck[]>("/decks") });

export const useDueCards = () => useQuery({ queryKey: keys.due, queryFn: () => api.get<Card[]>("/reviews/due") });

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
    mutationFn: ({ deckId, front, back }: { deckId: string; front: string; back: string }) =>
      api.post<Card>(`/decks/${deckId}/cards`, { front, back }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.decks });
      void qc.invalidateQueries({ queryKey: keys.due });
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

export function useStartAttempt() {
  return useMutation({
    mutationFn: ({ quizId, mode }: { quizId: string; mode: "practice" | "exam" }) =>
      api.post<Attempt>(`/quizzes/${quizId}/attempts`, { mode }),
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

export const useExamMode = () =>
  useQuery({ queryKey: keys.examMode, queryFn: () => api.get<ExamMode>("/exam-mode"), refetchInterval: 30_000 });

export const useMastery = () => useQuery({ queryKey: keys.mastery, queryFn: () => api.get<Mastery[]>("/mastery") });

export const useStudyNeeds = () =>
  useQuery({ queryKey: keys.studyNeeds, queryFn: () => api.get<StudyNeed[]>("/study-needs") });

/* ------------------------------- Analytics ----------------------------- */

export const useAnalytics = (start: string, end: string) =>
  useQuery({ queryKey: keys.analytics(start, end), queryFn: () => api.get<Analytics>("/analytics", { start, end }) });

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
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notes"] }),
  });
}

export const useErrors = () => useQuery({ queryKey: keys.errors, queryFn: () => api.get<ErrorEntry[]>("/errors") });

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

export const useGradeSummary = (programId?: string) =>
  useQuery({
    queryKey: keys.gradeSummary(programId ?? ""),
    queryFn: () => api.get<GradeSummary>("/grades/summary", { program_id: programId }),
    enabled: Boolean(programId),
  });

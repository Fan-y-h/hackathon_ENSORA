// Hooks: assessments, Smart Planner, sessions, focus room, analytics, task decomposition.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, newIdempotencyKey } from "./client";
import { queryKeys } from "./queryKeys";
import type { Job, Page, Uuid } from "./types";
import type { Analytics, Assessment, FocusSession, Plan, PlanPreview, SessionFinish, StudySession, TaskProposal } from "./typesPlanning";

export const planningKeys = {
  assessments: ["assessments"] as const,
  plan: ["plan"] as const,
  focus: ["focus", "current"] as const,
  analytics: (range: "week" | "month") => ["analytics", range] as const,
  proposal: (id: Uuid) => ["tasks", "proposal", id] as const,
};

export function useAssessments() {
  return useQuery({ queryKey: planningKeys.assessments, queryFn: () => apiFetch<Page<Assessment>>("/assessments"), select: (p) => p.items });
}

export function useUpdateAssessment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: Uuid; expected_revision: number; scope_status?: "confirmed"; concept_ids?: Uuid[] }) =>
      apiFetch<Assessment>(`/assessments/${id}`, { method: "PATCH", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: planningKeys.assessments }),
  });
}

/** Current accepted plan, or null when none was accepted yet (204). */
export function useCurrentPlan() {
  return useQuery({ queryKey: planningKeys.plan, queryFn: async () => (await apiFetch<Plan | undefined>("/plans/current")) ?? null });
}

export function usePreviewPlan() {
  return useMutation({
    mutationFn: (kind: "preview" | "replan") => apiFetch<PlanPreview>(`/planner/${kind}`, { method: "POST", body: { horizon_days: 14 } }),
  });
}

/** Plan changes also change the agenda (planned sessions are shown there). */
function invalidatePlanViews(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: planningKeys.plan });
  void queryClient.invalidateQueries({ queryKey: queryKeys.allEvents });
  void queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
  void queryClient.invalidateQueries({ queryKey: ["analytics"] });
}

export function useAcceptPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (preview: PlanPreview) => apiFetch<Plan>("/planner/accept", { method: "POST", body: { preview_id: preview.preview_id, input_revision: preview.input_revision } }),
    onSuccess: () => invalidatePlanViews(queryClient),
  });
}

export function useLockSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ session, plan }: { session: StudySession; plan: Plan }) =>
      apiFetch<Plan>(`/sessions/${session.id}`, { method: "PATCH", body: { locked: !session.locked, expected_revision: plan.revision } }),
    onSuccess: () => invalidatePlanViews(queryClient),
  });
}

export function useStartSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: Uuid) => apiFetch<StudySession>(`/sessions/${sessionId}/start`, { method: "POST" }),
    onSuccess: () => invalidatePlanViews(queryClient),
  });
}

export function useFinishSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, ...body }: SessionFinish & { sessionId: Uuid }) => apiFetch<StudySession>(`/sessions/${sessionId}/finish`, { method: "POST", body }),
    onSuccess: () => invalidatePlanViews(queryClient),
  });
}

// --- Focus room --------------------------------------------------------------------

export function useCurrentFocus() {
  return useQuery({ queryKey: planningKeys.focus, queryFn: async () => (await apiFetch<FocusSession | undefined>("/focus/current")) ?? null });
}

export function useStartFocus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { goal: string; mode: "pomodoro" | "free"; work_min: number; break_min: number; study_session_id?: Uuid | null }) =>
      apiFetch<FocusSession>("/focus/start", { method: "POST", body: input, idempotencyKey: newIdempotencyKey() }),
    onSuccess: (session) => queryClient.setQueryData(planningKeys.focus, session),
  });
}

export function useFocusTransition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, to }: { id: Uuid; to: "work" | "break" | "pause" }) => apiFetch<FocusSession>(`/focus/${id}/transition`, { method: "POST", body: { to } }),
    onSuccess: (session) => queryClient.setQueryData(planningKeys.focus, session),
  });
}

export function useFinishFocus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reflection }: { id: Uuid; reflection: { objective_reached: boolean; difficulty: number; notes: string } }) =>
      apiFetch<FocusSession>(`/focus/${id}/finish`, { method: "POST", body: { reflection } }),
    onSuccess: () => {
      queryClient.setQueryData(planningKeys.focus, null);
      void queryClient.invalidateQueries({ queryKey: ["analytics"] });
    },
  });
}

// --- Analytics and tasks ----------------------------------------------------------------

export function useAnalytics(range: "week" | "month") {
  return useQuery({ queryKey: planningKeys.analytics(range), queryFn: () => apiFetch<Analytics>("/analytics", { query: { range } }) });
}

export function useDecomposeTask() {
  return useMutation({
    mutationFn: (input: { instructions: string; subject_id?: Uuid | null }) => apiFetch<Job>("/tasks/decompose", { method: "POST", body: input, idempotencyKey: newIdempotencyKey() }),
  });
}

export function useTaskProposal(id: Uuid | null) {
  return useQuery({ queryKey: planningKeys.proposal(id ?? "none"), queryFn: () => apiFetch<TaskProposal>(`/tasks/proposals/${id}`), enabled: id !== null });
}

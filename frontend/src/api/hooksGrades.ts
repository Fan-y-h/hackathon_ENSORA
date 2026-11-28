// Hooks: Grade Tracker & Academic Optimizer. Calculations always come from the server.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "./client";
import type { Page, Uuid } from "./types";
import type { AcademicProgram, Curriculum, GradeRecord, GradeSummary, Objective, RequiredResult, SimulationResult } from "./typesGrades";

export const gradeKeys = {
  all: ["grades"] as const,
  programs: ["grades", "programs"] as const,
  curriculum: (programId: Uuid) => ["grades", "curriculum", programId] as const,
  records: ["grades", "records"] as const,
  objectives: ["grades", "objectives"] as const,
  summary: (programId: Uuid) => ["grades", "summary", programId] as const,
  required: ["grades", "required"] as const,
};

export function usePrograms() {
  return useQuery({ queryKey: gradeKeys.programs, queryFn: () => apiFetch<Page<AcademicProgram>>("/grades/programs"), select: (p) => p.items });
}

export function useCurriculum(programId: Uuid | undefined) {
  return useQuery({
    queryKey: gradeKeys.curriculum(programId ?? "none"),
    queryFn: () => apiFetch<Curriculum>(`/grades/programs/${programId}/curriculum`),
    enabled: programId !== undefined,
  });
}

export function useGradeRecords() {
  return useQuery({ queryKey: gradeKeys.records, queryFn: () => apiFetch<Page<GradeRecord>>("/grades/records"), select: (p) => p.items });
}

export function useObjectives() {
  return useQuery({ queryKey: gradeKeys.objectives, queryFn: () => apiFetch<Page<Objective>>("/grades/objectives"), select: (p) => p.items });
}

export function useGradeSummary(programId: Uuid | undefined) {
  return useQuery({
    queryKey: gradeKeys.summary(programId ?? "none"),
    queryFn: () => apiFetch<GradeSummary>("/grades/summary", { query: { program_id: programId } }),
    enabled: programId !== undefined,
  });
}

export function useRequired(programId: Uuid | undefined) {
  return useQuery({
    queryKey: gradeKeys.required,
    queryFn: () => apiFetch<Page<RequiredResult>>("/grades/required", { method: "POST", body: { program_id: programId, objective_ids: [] } }),
    select: (p) => p.items,
    enabled: programId !== undefined,
  });
}

/** Any change to marks, weights or goals refreshes every grade view. */
function useInvalidateGrades() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: gradeKeys.all });
  };
}

export function useSaveMark() {
  const invalidate = useInvalidateGrades();
  return useMutation({
    mutationFn: ({ record, nodeId, value }: { record?: GradeRecord; nodeId: Uuid; value: string }) =>
      record
        ? apiFetch<GradeRecord>(`/grades/records/${record.id}`, { method: "PATCH", body: { value, expected_revision: record.revision } })
        : apiFetch<GradeRecord>("/grades/records", { method: "POST", body: { node_id: nodeId, value } }),
    onSuccess: invalidate,
  });
}

export function useDeleteMark() {
  const invalidate = useInvalidateGrades();
  return useMutation({ mutationFn: (recordId: Uuid) => apiFetch<void>(`/grades/records/${recordId}`, { method: "DELETE" }), onSuccess: invalidate });
}

export function useSetObjective() {
  const invalidate = useInvalidateGrades();
  return useMutation({
    mutationFn: (input: { node_id: Uuid; target_value: string }) => apiFetch<Objective>("/grades/objectives", { method: "POST", body: input }),
    onSuccess: invalidate,
  });
}

export function useSaveCurriculum(programId: Uuid) {
  const invalidate = useInvalidateGrades();
  return useMutation({
    mutationFn: (curriculum: Curriculum) =>
      apiFetch<Curriculum>(`/grades/programs/${programId}/curriculum`, { method: "PUT", body: { nodes: curriculum.nodes, expected_revision: curriculum.revision } }),
    onSuccess: invalidate,
  });
}

export function useSimulate() {
  return useMutation({
    mutationFn: (input: { program_id: Uuid; expected_revision: number; values: Array<{ node_id: Uuid; value: string }> }) =>
      apiFetch<SimulationResult>("/grades/simulate", { method: "POST", body: input }),
  });
}

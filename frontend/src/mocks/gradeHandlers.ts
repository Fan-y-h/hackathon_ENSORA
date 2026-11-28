// Fake backend: Grade Tracker. The real backend owns these calculations (Decimal arithmetic);
// this mock reproduces them with numbers rounded to 2 decimals, enough for the demo.
// Official status is always "unknown": no validation claim without verified rules.
import { http, HttpResponse } from "msw";

import type { AcademicNode, GradeSummary, NodeSummary, RequiredResult, SimulationResult } from "@/api/typesGrades";

import { api, apiError, bump, db, latency, newRecord, page } from "./core";

const SCALE_MAX = 20;
const round = (n: number) => Math.round(n * 100) / 100;
const dec = (n: number | null) => (n === null ? null : round(n).toFixed(2));

interface Computed {
  value: number | null;
  coverage: number;
}

/** Weighted mean of graded children and the share of weight already graded. */
function compute(nodeId: string, overrides: Map<string, number> = new Map(), memo = new Map<string, Computed>()): Computed {
  const cached = memo.get(nodeId);
  if (cached) return cached;
  const children = db.nodes.filter((n) => n.parent_id === nodeId);
  let result: Computed;
  if (children.length === 0) {
    const override = overrides.get(nodeId);
    const record = db.records.find((r) => r.node_id === nodeId && r.kind === "actual");
    const value = override ?? (record ? Number(record.value) : null);
    result = { value, coverage: value === null ? 0 : 1 };
  } else {
    const total = children.reduce((sum, c) => sum + Number(c.weight), 0);
    let weighted = 0;
    let gradedWeight = 0;
    let coverage = 0;
    for (const child of children) {
      const w = Number(child.weight) / total;
      const c = compute(child.id, overrides, memo);
      coverage += w * c.coverage;
      if (c.value !== null) {
        weighted += w * c.coverage * c.value;
        gradedWeight += w * c.coverage;
      }
    }
    result = { value: gradedWeight > 0 ? weighted / gradedWeight : null, coverage };
  }
  memo.set(nodeId, result);
  return result;
}

function summary(): GradeSummary {
  const memo = new Map<string, Computed>();
  const nodes: NodeSummary[] = db.nodes.map((node) => {
    const c = compute(node.id, new Map(), memo);
    return {
      node_id: node.id,
      provisional: dec(c.value),
      actual_coverage: c.coverage.toFixed(4),
      forecast: null,
      remaining_weight: (1 - c.coverage).toFixed(4),
      official_status: "unknown",
    };
  });
  return { program_id: db.programs[0].id, revision: db.curriculumRevision, nodes };
}

function required(nodeId: string, target: number): RequiredResult {
  const { value, coverage } = compute(nodeId);
  const assumptions = ["Moyenne pondérée simple, sans compensation ni note éliminatoire (règles officielles non vérifiées)."];
  if (coverage >= 0.9999) {
    return { node_id: nodeId, required_mean: null, feasibility: (value ?? 0) >= target ? "guaranteed" : "impossible", assumptions, reason_codes: ["ALL_GRADED"] };
  }
  const needed = (target - (value ?? 0) * coverage) / (1 - coverage);
  const feasibility = needed <= 0 ? "guaranteed" : needed > SCALE_MAX ? "impossible" : "feasible";
  return { node_id: nodeId, required_mean: dec(Math.max(0, needed)), feasibility, assumptions, reason_codes: [feasibility === "impossible" ? "ABOVE_SCALE" : "REMAINING_WEIGHT"] };
}

function validMark(value: unknown): value is string {
  return typeof value === "string" && /^\d{1,2}(\.\d{1,2})?$/.test(value) && Number(value) >= 0 && Number(value) <= SCALE_MAX;
}

/** Sibling fractions must add up to 1 (coefficients are free). */
function validateWeights(nodes: AcademicNode[]): string | null {
  for (const parent of new Set(nodes.map((n) => n.parent_id))) {
    const siblings = nodes.filter((n) => n.parent_id === parent);
    if (siblings.some((n) => !(Number(n.weight) > 0))) return "Chaque poids doit être strictement positif.";
    const fractions = siblings.filter((n) => n.weight_mode === "fraction");
    if (fractions.length > 0 && Math.abs(fractions.reduce((sum, n) => sum + Number(n.weight), 0) - 1) > 0.001) {
      return `Les pondérations de « ${db.nodes.find((n) => n.id === parent)?.title ?? "ce groupe"} » doivent totaliser 100 %.`;
    }
  }
  return null;
}

export const gradeHandlers = [
  http.get(api("/grades/programs"), async () => {
    await latency();
    return HttpResponse.json(page(db.programs));
  }),

  http.get(api("/grades/programs/:id/curriculum"), async ({ params }) => {
    await latency();
    if (!db.programs.some((p) => p.id === params.id)) return apiError(404, "not_found", "Programme introuvable.");
    return HttpResponse.json({ program_id: params.id, revision: db.curriculumRevision, nodes: db.nodes });
  }),

  http.put(api("/grades/programs/:id/curriculum"), async ({ params, request }) => {
    await latency();
    const input = (await request.json()) as { nodes?: AcademicNode[]; expected_revision?: number };
    if (input.expected_revision !== db.curriculumRevision) return apiError(409, "stale_revision", "Le programme a été modifié ailleurs. Rechargez la page.");
    const nodes = input.nodes ?? [];
    const problem = validateWeights(nodes);
    if (problem) return apiError(422, "invalid_weights", problem);
    db.nodes = nodes;
    db.curriculumRevision += 1;
    return HttpResponse.json({ program_id: params.id, revision: db.curriculumRevision, nodes: db.nodes });
  }),

  http.get(api("/grades/records"), async () => {
    await latency();
    return HttpResponse.json(page(db.records));
  }),

  http.post(api("/grades/records"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as { node_id?: string; value?: unknown };
    const node = db.nodes.find((n) => n.id === input.node_id);
    if (!node || db.nodes.some((n) => n.parent_id === node.id)) return apiError(422, "validation_error", "Une note se saisit sur une épreuve.", { field: "node_id" });
    if (!validMark(input.value)) return apiError(422, "validation_error", "Note entre 0 et 20 (deux décimales maximum).", { field: "value" });
    if (db.records.some((r) => r.node_id === node.id && r.kind === "actual")) return apiError(409, "already_exists", "Une note existe déjà pour cette épreuve : modifiez-la.");
    const record = { ...newRecord(), node_id: node.id, kind: "actual" as const, value: Number(input.value).toFixed(2), provenance: "manual" as const };
    db.records.push(record);
    return HttpResponse.json(record, { status: 201 });
  }),

  http.patch(api("/grades/records/:id"), async ({ params, request }) => {
    await latency();
    const index = db.records.findIndex((r) => r.id === params.id);
    if (index === -1) return apiError(404, "not_found", "Note introuvable.");
    const input = (await request.json()) as { value?: unknown; expected_revision?: number };
    if (input.expected_revision !== db.records[index].revision) return apiError(409, "stale_revision", "Cette note a changé entre-temps.");
    if (!validMark(input.value)) return apiError(422, "validation_error", "Note entre 0 et 20 (deux décimales maximum).", { field: "value" });
    db.records[index] = bump(db.records[index], { value: Number(input.value).toFixed(2) });
    return HttpResponse.json(db.records[index]);
  }),

  http.delete(api("/grades/records/:id"), async ({ params }) => {
    await latency();
    const before = db.records.length;
    db.records = db.records.filter((r) => r.id !== params.id);
    return db.records.length < before ? new HttpResponse(null, { status: 204 }) : apiError(404, "not_found", "Note introuvable.");
  }),

  http.get(api("/grades/objectives"), async () => {
    await latency();
    return HttpResponse.json(page(db.objectives));
  }),

  http.post(api("/grades/objectives"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as { node_id?: string; target_value?: unknown };
    if (!db.nodes.some((n) => n.id === input.node_id)) return apiError(422, "validation_error", "Choisissez un élément du programme.", { field: "node_id" });
    if (!validMark(input.target_value)) return apiError(422, "validation_error", "Objectif entre 0 et 20.", { field: "target_value" });
    db.objectives = db.objectives.filter((o) => o.node_id !== input.node_id);
    const objective = { ...newRecord(), node_id: input.node_id as string, target_kind: "numeric" as const, target_value: Number(input.target_value).toFixed(2), active: true };
    db.objectives.push(objective);
    return HttpResponse.json(objective, { status: 201 });
  }),

  http.delete(api("/grades/objectives/:id"), async ({ params }) => {
    await latency();
    db.objectives = db.objectives.filter((o) => o.id !== params.id);
    return new HttpResponse(null, { status: 204 });
  }),

  http.get(api("/grades/summary"), async () => {
    await latency();
    return HttpResponse.json(summary());
  }),

  http.post(api("/grades/required"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as { objective_ids?: string[] };
    const objectives = db.objectives.filter((o) => o.active && (!input.objective_ids?.length || input.objective_ids.includes(o.id)));
    return HttpResponse.json(page(objectives.map((o) => required(o.node_id, Number(o.target_value)))));
  }),

  // What-if: projects hypothetical marks without writing anything.
  http.post(api("/grades/simulate"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as { values?: Array<{ node_id: string; value: string }>; expected_revision?: number };
    if (input.expected_revision !== db.curriculumRevision) return apiError(409, "stale_revision", "Le programme a changé : rechargez la simulation.");
    const overrides = new Map<string, number>();
    for (const v of input.values ?? []) {
      if (!validMark(v.value)) return apiError(422, "validation_error", "Notes simulées entre 0 et 20.", { field: v.node_id });
      overrides.set(v.node_id, Number(v.value));
    }
    const baseMemo = new Map<string, Computed>();
    const simMemo = new Map<string, Computed>();
    const result: SimulationResult = {
      nodes: db.nodes.map((n) => ({ node_id: n.id, baseline: dec(compute(n.id, new Map(), baseMemo).value), projected: dec(compute(n.id, overrides, simMemo).value) })),
      warnings: overrides.size === 0 ? ["Saisissez au moins une note hypothétique."] : [],
    };
    return HttpResponse.json(result);
  }),
];

// The jury journey, end to end against the mock backend (docs/MVP.md "Jury demonstration")
// plus the contract tests listed in docs/API_CONTRACTS.md.
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { resetMockDb } from "@/mocks/handlers";
import { mockServer } from "@/mocks/node";

import { ApiError, apiFetch } from "./client";
import type { CalendarEvent, Job, Page, Subject } from "./types";
import type { GradeRecord, GradeSummary, RequiredResult, SimulationResult, Curriculum } from "./typesGrades";
import type { Artifact, ArtifactDetail, Attempt, AttemptResult, Conversation, Deck, Flashcard, Quiz, ReviewResult, TutorResponse } from "./typesLearning";
import type { Assessment, Plan, PlanPreview } from "./typesPlanning";

beforeAll(() => mockServer.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  mockServer.resetHandlers();
  resetMockDb();
});
afterAll(() => mockServer.close());

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("Expected an ApiError");
}

async function waitJob(job: Job): Promise<Job> {
  let current = job;
  for (let i = 0; i < 10 && (current.status === "queued" || current.status === "running"); i++) current = await apiFetch<Job>(`/jobs/${job.id}`);
  return current;
}

async function statsSubject(): Promise<Subject> {
  const { items } = await apiFetch<Page<Subject>>("/subjects");
  return items.find((s) => s.canonical_code === "STAT-INF") as Subject;
}

describe("AI tutor", () => {
  it("answers with resolvable citations when the sources cover the question", async () => {
    const conversation = await apiFetch<Conversation>("/conversations", { method: "POST", body: {} });
    const answer = await apiFetch<TutorResponse>(`/conversations/${conversation.id}/messages`, {
      method: "POST",
      body: { question: "Qu'est-ce que la p-valeur ?", mode: "explain", locale: "fr", model: "auto" },
    });
    expect(answer.evidence_status).not.toBe("insufficient");
    expect(answer.citations.length).toBeGreaterThan(0);
    expect(answer.provider).toBe("mock"); // demo answers are always labelled
    const cited = await apiFetch<{ excerpt: string }>(`/citations/${answer.citations[0].citation_id}`);
    expect(cited.excerpt).toBe(answer.citations[0].excerpt);
  });

  it("keeps only passages about the asked term (no loose word matches)", async () => {
    const conversation = await apiFetch<Conversation>("/conversations", { method: "POST", body: {} });
    const answer = await apiFetch<TutorResponse>(`/conversations/${conversation.id}/messages`, {
      method: "POST",
      body: { question: "Explique la p-valeur", mode: "explain", locale: "fr", model: "auto" },
    });
    expect(answer.evidence_status).toBe("grounded");
    expect(answer.citations.every((c) => c.excerpt.toLowerCase().includes("p-valeur"))).toBe(true);
  });

  it("says the sources are insufficient instead of inventing an answer", async () => {
    const conversation = await apiFetch<Conversation>("/conversations", { method: "POST", body: {} });
    const answer = await apiFetch<TutorResponse>(`/conversations/${conversation.id}/messages`, {
      method: "POST",
      body: { question: "Quelle est la recette de la tarte tatin ?", mode: "explain", locale: "fr", model: "auto" },
    });
    expect(answer.evidence_status).toBe("insufficient");
    expect(answer.citations).toHaveLength(0);
  });
});

describe("Learning Studio", () => {
  it("generates a sheet with citations, reuses it from cache, and keeps edits as new versions", async () => {
    const subject = await statsSubject();
    const spec = { type: "revision_sheet", subject_id: subject.id, document_ids: [], concept_ids: [], objective: "understand", locale: "fr", difficulty: "intermediate", duration_min: 20, detail_level: "standard", model: "auto", user_instructions: "", force_regenerate: false };
    const job = await waitJob(await apiFetch<Job>("/artifacts/generate", { method: "POST", body: spec }));
    expect(job.status).toBe("succeeded");
    const detail = await apiFetch<ArtifactDetail>(`/artifacts/${job.result_id}`);
    expect(detail.version.content_markdown).toContain("Fiche de révision");
    expect(detail.version.citations.length).toBeGreaterThan(0);

    const cached = await apiFetch<Artifact>("/artifacts/generate", { method: "POST", body: spec });
    expect(cached.id).toBe(detail.artifact.id); // same request: no new generation

    const edited = await apiFetch<ArtifactDetail>(`/artifacts/${detail.artifact.id}`, { method: "PATCH", body: { content_markdown: "# Ma fiche", expected_revision: detail.artifact.revision } });
    expect(edited.version.edited_by_user).toBe(true);
    const versions = await apiFetch<Page<{ id: string }>>(`/artifacts/${detail.artifact.id}/versions`);
    expect(versions.items).toHaveLength(2); // the generated version is still there
  });
});

describe("quiz and mastery", () => {
  it("never sends the answer key during an attempt, grades once, and updates mastery", async () => {
    const { items: quizzes } = await apiFetch<Page<Quiz>>("/quizzes");
    const attempt = await apiFetch<Attempt>(`/quizzes/${quizzes[0].id}/attempts`, { method: "POST", body: {} });
    expect(JSON.stringify(attempt)).not.toMatch(/"answer"|"explanation"/);
    expect((await failure(apiFetch(`/attempts/${attempt.id}/result`))).status).toBe(403);

    const first = attempt.questions[0];
    await apiFetch(`/attempts/${attempt.id}/responses`, { method: "PATCH", body: { responses: { [first.id]: "a" } } }); // wrong answer
    const result = await apiFetch<AttemptResult>(`/attempts/${attempt.id}/submit`, { method: "POST" });
    const again = await apiFetch<AttemptResult>(`/attempts/${attempt.id}/submit`, { method: "POST" });
    expect(again).toEqual(result); // idempotent submit
    expect(result.items[0].correct).toBe(false);
    expect(result.mastery_updates.length).toBeGreaterThan(0);
    const errors = await apiFetch<Page<{ description: string }>>("/errors");
    expect(errors.items.some((e) => e.description.includes(first.prompt))).toBe(true);
  });

  it("schedules flashcards: 'again' comes back tomorrow, 'easy' later", async () => {
    const { items: decks } = await apiFetch<Page<Deck>>("/decks");
    const { items: due } = await apiFetch<Page<Flashcard>>("/reviews/due", { query: { deck_id: decks[0].id } });
    const hard = await apiFetch<ReviewResult>(`/cards/${due[0].id}/review`, { method: "POST", body: { rating: "again", expected_revision: due[0].revision } });
    const easy = await apiFetch<ReviewResult>(`/cards/${due[1].id}/review`, { method: "POST", body: { rating: "easy", expected_revision: due[1].revision } });
    expect(hard.interval_days).toBe(1);
    expect(easy.interval_days).toBeGreaterThanOrEqual(3);
    // Reviewing twice with the old revision is refused.
    expect((await failure(apiFetch(`/cards/${due[0].id}/review`, { method: "POST", body: { rating: "good", expected_revision: due[0].revision } }))).status).toBe(409);
  });
});

describe("Smart Planner", () => {
  async function confirmAll() {
    const { items } = await apiFetch<Page<Assessment>>("/assessments");
    for (const a of items.filter((x) => x.scope_status === "proposed")) {
      const error = await failure(apiFetch(`/assessments/${a.id}`, { method: "PATCH", body: { scope_status: "confirmed", expected_revision: a.revision } }));
      expect(error.status).toBe(422); // its calendar slot must be confirmed first
    }
    return items;
  }

  it("previews a plan without overlaps or cap overflow, then accepts it", async () => {
    await confirmAll();
    const preview = await apiFetch<PlanPreview>("/planner/preview", { method: "POST", body: {} });
    expect(preview.sessions.length).toBeGreaterThan(0);
    expect(preview.warnings.some((w) => w.includes("Contrôle Probabilités"))).toBe(true); // unconfirmed scope is reported

    const { items: events } = await apiFetch<Page<CalendarEvent>>("/events");
    for (const s of preview.sessions) {
      for (const e of events.filter((x) => x.type !== "deadline")) {
        const overlap = s.start_at < e.end_at && e.start_at < s.end_at;
        expect(overlap, `${s.title} overlaps ${e.title}`).toBe(false);
      }
    }
    const perDay = new Map<string, number>();
    for (const s of preview.sessions) perDay.set(new Date(s.start_at).toDateString(), (perDay.get(new Date(s.start_at).toDateString()) ?? 0) + s.estimated_min);
    expect(Math.max(...perDay.values())).toBeLessThanOrEqual(120);

    const plan = await apiFetch<Plan>("/planner/accept", { method: "POST", body: { preview_id: preview.preview_id, input_revision: preview.input_revision } });
    expect(plan.sessions.length).toBe(preview.sessions.length);
    const { items: withPlan } = await apiFetch<Page<CalendarEvent>>("/events");
    expect(withPlan.some((e) => e.source === "planner")).toBe(true); // planned sessions show in the agenda
  });

  it("refuses a stale preview with 409", async () => {
    const preview = await apiFetch<PlanPreview>("/planner/preview", { method: "POST", body: {} });
    await apiFetch("/tasks", { method: "POST", body: { title: "Nouvelle tâche urgente", estimated_min: 60 } });
    const error = await failure(apiFetch("/planner/accept", { method: "POST", body: { preview_id: preview.preview_id, input_revision: preview.input_revision } }));
    expect(error.status).toBe(409);
  });
});

describe("grades", () => {
  async function load() {
    const { items } = await apiFetch<Page<{ id: string }>>("/grades/programs");
    const curriculum = await apiFetch<Curriculum>(`/grades/programs/${items[0].id}/curriculum`);
    return { programId: items[0].id, curriculum };
  }

  it("treats a missing mark as missing, not as zero", async () => {
    const { programId } = await load();
    const summary = await apiFetch<GradeSummary>("/grades/summary", { query: { program_id: programId } });
    const ungraded = summary.nodes.filter((n) => Number(n.actual_coverage) === 0);
    expect(ungraded.length).toBeGreaterThan(0);
    expect(ungraded.every((n) => n.provisional === null)).toBe(true);
    expect(summary.nodes.every((n) => n.official_status === "unknown")).toBe(true);
  });

  it("simulates without writing, and computes a required mean for the goal", async () => {
    const { programId, curriculum } = await load();
    const before = await apiFetch<Page<GradeRecord>>("/grades/records");
    const leaf = curriculum.nodes.find((n) => n.kind === "assessment" && !before.items.some((r) => r.node_id === n.id)) as { id: string };
    const sim = await apiFetch<SimulationResult>("/grades/simulate", { method: "POST", body: { program_id: programId, expected_revision: curriculum.revision, values: [{ node_id: leaf.id, value: "18" }] } });
    expect(sim.nodes.some((n) => n.projected !== n.baseline)).toBe(true);
    const after = await apiFetch<Page<GradeRecord>>("/grades/records");
    expect(after.items).toHaveLength(before.items.length); // nothing saved

    const required = await apiFetch<Page<RequiredResult>>("/grades/required", { method: "POST", body: { program_id: programId, objective_ids: [] } });
    expect(required.items[0].feasibility).toBe("feasible");
    expect(Number(required.items[0].required_mean)).toBeGreaterThan(0);
  });

  it("rejects out-of-range marks and fractions that do not sum to 100 %", async () => {
    const { programId, curriculum } = await load();
    const leaf = curriculum.nodes.find((n) => n.kind === "assessment") as { id: string };
    expect((await failure(apiFetch("/grades/records", { method: "POST", body: { node_id: leaf.id, value: "21" } }))).status).toBe(422);
    const broken = curriculum.nodes.map((n) => (n.weight_mode === "fraction" ? { ...n, weight: "0.9" } : n));
    expect((await failure(apiFetch(`/grades/programs/${programId}/curriculum`, { method: "PUT", body: { nodes: broken, expected_revision: curriculum.revision } }))).status).toBe(422);
  });
});

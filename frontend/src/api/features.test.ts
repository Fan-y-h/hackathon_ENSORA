// Contract behaviour of the second batch of features: login, deduced availability,
// annotations, note restructuring, readiness, curriculum editing.
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { resetMockDb } from "@/mocks/handlers";
import { mockServer } from "@/mocks/node";

import { ApiError, apiFetch } from "./client";
import type { Annotation, CalendarEvent, Job, Page, SearchHit } from "./types";
import type { Curriculum } from "./typesGrades";
import type { Analytics, Assessment } from "./typesPlanning";
import type { AuthSession, AvailabilitySuggestion, LoginResult, Note } from "./typesWorkspace";

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

describe("session", () => {
  it("validates credentials, opens the demo account and logs out", async () => {
    expect((await failure(apiFetch("/auth/login", { method: "POST", body: { username: "", password: "x" } }))).status).toBe(422);
    expect((await failure(apiFetch("/auth/login", { method: "POST", body: { username: "jean@ensai.fr", password: "x" } }))).status).toBe(422);
    const login = await apiFetch<LoginResult>("/auth/login", { method: "POST", body: { username: "id2a123", password: "secret" } });
    expect(login.authenticated).toBe(true);
    // Signing in starts importing the student's ENSAI data (Moodle, Pamplemousse, Drive Ensora).
    let job = login.sync_job as Job;
    while (job.status !== "succeeded") job = await apiFetch<Job>(`/jobs/${job.id}`);
    const { items } = await apiFetch<Page<{ source_kind: string; status: string }>>("/connections");
    for (const kind of ["moodle", "pamplemousse", "ensora_drive"]) expect(items.find((c) => c.source_kind === kind)?.status).toBe("connected");
    const demo = await apiFetch<LoginResult>("/auth/login", { method: "POST", body: { demo: true } });
    expect(demo.demo).toBe(true);
    await apiFetch("/auth/logout", { method: "POST" });
    expect((await apiFetch<AuthSession>("/auth/session")).authenticated).toBe(false);
  });
});

describe("availability", () => {
  it("deduces free slots that never overlap classes or personal events", async () => {
    const suggestion = await apiFetch<AvailabilitySuggestion>("/availability/suggestions");
    expect(suggestion.based_on).toContain("pamplemousse");
    expect(suggestion.missing).toContain("google_calendar"); // not connected in the demo data
    expect(suggestion.windows.length).toBeGreaterThan(0);
    const { items: events } = await apiFetch<Page<CalendarEvent>>("/events");
    for (const event of events.filter((e) => e.type !== "deadline" && e.source !== "google_calendar")) {
      const start = new Date(event.start_at);
      const weekday = start.getDay() === 0 ? 7 : start.getDay();
      const hhmm = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      // Only events of the coming week are used for the deduction.
      if (start.getTime() - Date.now() > 6 * 86_400_000) continue;
      for (const w of suggestion.windows.filter((x) => x.day === weekday)) {
        const overlaps = w.start < hhmm(new Date(event.end_at)) && hhmm(start) < w.end;
        expect(overlaps, `${w.start}-${w.end} overlaps ${event.title}`).toBe(false);
      }
    }
  });
});

describe("library annotations", () => {
  it("anchors an annotation to the exact passage", async () => {
    const { items } = await apiFetch<Page<SearchHit>>("/search", { query: { q: "p-valeur", mode: "lexical" } });
    const c = items[0].citation;
    expect((await failure(apiFetch("/annotations", { method: "POST", body: { document_id: c.document_id, comment: " " } }))).status).toBe(422);
    await apiFetch<Annotation>("/annotations", { method: "POST", body: { document_id: c.document_id, document_version_id: c.version_id, locator: c.locator, excerpt: c.excerpt, comment: "À revoir en TD" } });
    const list = await apiFetch<Page<Annotation>>("/annotations", { query: { document_id: c.document_id } });
    expect(list.items).toHaveLength(1);
    expect(list.items[0].locator).toEqual(c.locator);
  });
});

describe("notes", () => {
  it("proposes a restructured version without changing the note", async () => {
    const { items } = await apiFetch<Page<Note>>("/notes");
    const note = items[0];
    let job = await apiFetch<Job>(`/notes/${note.id}/restructure`, { method: "POST" });
    while (job.status !== "succeeded") job = await apiFetch<Job>(`/jobs/${job.id}`);
    const proposal = await apiFetch<{ markdown: string }>(`/notes/proposals/${job.result_id}`);
    expect(proposal.markdown).toContain("Points clés");
    const after = await apiFetch<Page<Note>>("/notes");
    expect(after.items.find((n) => n.id === note.id)?.markdown).toBe(note.markdown);
  });
});

describe("analytics", () => {
  it("reports readiness as acquired concepts out of the confirmed scope, and a 4-week heatmap", async () => {
    const analytics = await apiFetch<Analytics>("/analytics", { query: { range: "week" } });
    expect(analytics.heatmap).toHaveLength(4);
    expect(analytics.heatmap.every((w) => w.minutes.length === 7)).toBe(true);
    const { items: assessments } = await apiFetch<Page<Assessment>>("/assessments");
    const confirmed = assessments.filter((a) => a.scope_status === "confirmed");
    expect(analytics.readiness).toHaveLength(confirmed.length);
    for (const r of analytics.readiness) expect(r.acquired).toBeLessThanOrEqual(r.total);
  });
});

describe("curriculum editing", () => {
  it("adds an assessment when the fractions still add up to 100 %", async () => {
    const { items } = await apiFetch<Page<{ id: string }>>("/grades/programs");
    const curriculum = await apiFetch<Curriculum>(`/grades/programs/${items[0].id}/curriculum`);
    const subject = curriculum.nodes.find((n) => n.kind === "subject" && n.title === "Statistique inférentielle") as { id: string };
    const nodes = curriculum.nodes.map((n) => (n.parent_id === subject.id && n.title === "Partiel" ? { ...n, weight: "0.4" } : n));
    nodes.push({ id: crypto.randomUUID(), parent_id: subject.id, kind: "assessment", title: "Projet", weight: "0.2", weight_mode: "fraction", subject_id: null, event_id: null });
    const saved = await apiFetch<Curriculum>(`/grades/programs/${items[0].id}/curriculum`, { method: "PUT", body: { nodes, expected_revision: curriculum.revision } });
    expect(saved.nodes.some((n) => n.title === "Projet")).toBe(true);
    expect(saved.revision).toBe(curriculum.revision + 1);
  });
});

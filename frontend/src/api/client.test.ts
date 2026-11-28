// Checks the API client against the mock backend: contract shapes, errors,
// revisions and job polling. Run with "npm test".
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { mockServer } from "@/mocks/node";
import { resetMockDb } from "@/mocks/handlers";

import { ApiError, apiFetch, apiFetchBlob } from "./client";
import type { CalendarEvent, DocumentSummary, Job, Page, Profile, SearchHit, Subject, Task } from "./types";

beforeAll(() => mockServer.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  mockServer.resetHandlers();
  resetMockDb();
});
afterAll(() => mockServer.close());

/** Runs a request that must fail and returns the ApiError. */
async function expectApiError(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.then(
    () => {
      throw new Error("Expected the request to fail");
    },
    (e: unknown) => e,
  );
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

describe("lists", () => {
  it("return {items, next_cursor} with at least two synthetic subjects", async () => {
    const page = await apiFetch<Page<Subject>>("/subjects");
    expect(page.next_cursor).toBeNull();
    expect(page.items.length).toBeGreaterThanOrEqual(2);
  });

  it("filter events by date range", async () => {
    const all = await apiFetch<Page<CalendarEvent>>("/events");
    const from = new Date().toISOString();
    const to = new Date(Date.now() + 3 * 86_400_000).toISOString();
    const someDays = await apiFetch<Page<CalendarEvent>>("/events", { query: { from, to } });
    expect(someDays.items.length).toBeGreaterThan(0);
    expect(someDays.items.length).toBeLessThan(all.items.length);
    expect(someDays.items.every((e) => e.end_at >= from && e.start_at <= to)).toBe(true);
  });

  it("never invent a TD group: unknown groups are null, not empty strings", async () => {
    const { items } = await apiFetch<Page<CalendarEvent>>("/events");
    expect(items.every((e) => e.group === null || e.group.length > 0)).toBe(true);
  });
});

describe("revisions", () => {
  it("accept an update with the current revision and increment it", async () => {
    const profile = await apiFetch<Profile>("/profile");
    const updated = await apiFetch<Profile>("/profile", {
      method: "PATCH",
      body: { cohort: "3A", expected_revision: profile.revision },
    });
    expect(updated.cohort).toBe("3A");
    expect(updated.revision).toBe(profile.revision + 1);
  });

  it("reject a stale revision with 409", async () => {
    const error = await expectApiError(apiFetch("/profile", { method: "PATCH", body: { cohort: "1A", expected_revision: 0 } }));
    expect(error.status).toBe(409);
    expect(error.isConflict).toBe(true);
    expect(error.requestId).toBeTruthy();
  });
});

describe("tasks", () => {
  it("validate input with 422 and the contract error body", async () => {
    const error = await expectApiError(apiFetch("/tasks", { method: "POST", body: { title: "  " } }));
    expect(error.status).toBe(422);
    expect(error.code).toBe("validation_error");
    expect(error.message).toBe("Le titre est obligatoire.");
  });

  it("create, then delete a task", async () => {
    const created = await apiFetch<Task>("/tasks", { method: "POST", body: { title: "Réviser", estimated_min: 30 } });
    expect(created.remaining_min).toBe(30);
    await apiFetch<void>(`/tasks/${created.id}`, { method: "DELETE" });
    const { items } = await apiFetch<Page<Task>>("/tasks");
    expect(items.some((t) => t.id === created.id)).toBe(false);
  });
});

describe("events", () => {
  const start = "2026-12-01T17:00:00.000Z";
  const end = "2026-12-01T19:00:00.000Z";

  it("create a personal event, reject an end before the start", async () => {
    const created = await apiFetch<CalendarEvent>("/events", { method: "POST", body: { type: "personal", title: "Sport", start_at: start, end_at: end } });
    expect(created.source).toBe("manual");
    const error = await expectApiError(apiFetch("/events", { method: "POST", body: { type: "personal", title: "Sport", start_at: end, end_at: start } }));
    expect(error.status).toBe(422);
    expect(error.details).toEqual({ field: "end_at" });
  });

  it("refuse to delete an imported event (read-only)", async () => {
    const { items } = await apiFetch<Page<CalendarEvent>>("/events");
    const imported = items.find((e) => e.source === "pamplemousse");
    const error = await expectApiError(apiFetch(`/events/${imported?.id}`, { method: "DELETE" }));
    expect(error.status).toBe(403);
  });

  it("confirm an ambiguous assessment once, then answer 409", async () => {
    const { items } = await apiFetch<Page<CalendarEvent>>("/events");
    const pending = items.find((e) => e.confirmation_required);
    expect(pending).toBeDefined();
    const confirmed = await apiFetch<CalendarEvent>(`/events/${pending?.id}/confirm`, { method: "POST", body: { decision: "confirm" } });
    expect(confirmed.confirmation_required).toBe(false);
    expect(confirmed.type).toBe("exam");
    const again = await expectApiError(apiFetch(`/events/${pending?.id}/confirm`, { method: "POST", body: { decision: "reject" } }));
    expect(again.status).toBe(409);
  });
});

describe("library", () => {
  it("search ignores accents and returns citations with an exact location", async () => {
    const { items } = await apiFetch<Page<SearchHit>>("/search", { query: { q: "theoreme central", mode: "hybrid" } });
    expect(items.length).toBeGreaterThan(0);
    const first = items[0].citation;
    expect(first.excerpt).toMatch(/théorème central limite/i);
    expect(first.locator.kind).toBeDefined();
  });

  it("rejects a search without a real word", async () => {
    const error = await expectApiError(apiFetch("/search", { query: { q: "a", mode: "lexical" } }));
    expect(error.status).toBe(422);
  });

  it("serves the document content that contains the cited excerpt", async () => {
    const { items } = await apiFetch<Page<SearchHit>>("/search", { query: { q: "p-valeur", mode: "lexical" } });
    const citation = items[0].citation;
    const blob = await apiFetchBlob(`/documents/${citation.document_id}/versions/${citation.version_id}/content`);
    expect(await blob.text()).toContain(citation.excerpt);
  });

  it("does not duplicate a document imported twice", async () => {
    const importOnce = async () => {
      const form = new FormData();
      form.append("file", new File(["cours"], "mon-cours.pdf", { type: "application/pdf" }));
      let job = await apiFetch<Job>("/documents/import", { method: "POST", body: form });
      while (job.status !== "succeeded") job = await apiFetch<Job>(`/jobs/${job.id}`);
      return job.result_id;
    };
    const first = await importOnce();
    const second = await importOnce();
    expect(first).toBeTruthy();
    expect(second).toBe(first);
    const { items } = await apiFetch<Page<DocumentSummary>>("/documents");
    expect(items.filter((d) => d.title === "mon-cours")).toHaveLength(1);
  });
});

describe("jobs", () => {
  it("progress from queued to succeeded when polled", async () => {
    const job = await apiFetch<Job>("/sync", { method: "POST", body: {}, idempotencyKey: "test-key" });
    expect(job.status).toBe("queued");
    let current = job;
    for (let i = 0; i < 10 && current.status !== "succeeded"; i++) {
      current = await apiFetch<Job>(`/jobs/${job.id}`);
    }
    expect(current.status).toBe("succeeded");
    expect(current.progress).toBe(1);
  });
});

describe("errors", () => {
  it("say honestly when an endpoint is not available in demo mode", async () => {
    const error = await expectApiError(apiFetch("/shares/export", { method: "POST", body: {} }));
    expect(error.status).toBe(501);
  });

  it("turn a network failure into a readable French message", async () => {
    mockServer.close();
    const error = await expectApiError(apiFetch("/subjects"));
    expect(error.code).toBe("network_error");
    mockServer.listen({ onUnhandledRequest: "error" });
  });
});

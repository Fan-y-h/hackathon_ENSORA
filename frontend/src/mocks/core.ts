// Shared state and helpers of the fake backend. Every *Handlers.ts module imports from here.
import { delay, HttpResponse } from "msw";

import type { ApiErrorBody, Citation, DocumentSummary, Job, MasteryDimension, Page, Uuid } from "@/api/types";
import { API_BASE_URL } from "@/app/config";

import { createFixtures } from "./fixtures";
import { createGradeFixtures } from "./fixturesGrades";
import { createLearningFixtures } from "./fixturesLearning";
import { createWorkspaceFixtures } from "./fixturesWorkspace";
import { PASSAGES } from "./passages";

function createDb() {
  const base = createFixtures();
  return {
    ...base,
    ...createLearningFixtures(base),
    ...createGradeFixtures(base),
    ...createWorkspaceFixtures(base),
    // Tests run signed in; the browser demo starts on the login screen.
    session: { authenticated: import.meta.env.MODE === "test", demo: false },
    annotations: [] as Array<import("@/api/types").Annotation>,
    noteProposals: [] as Array<{ id: string; note_id: string; markdown: string }>,
  };
}

export type MockDb = ReturnType<typeof createDb>;

/**
 * Saved copy of the demo data in the browser, so a reload keeps what the student did
 * (the jury demo ends with "restart and check the state is kept"). Bump the version
 * whenever fixtures change shape.
 */
const STORAGE_KEY = "ensora.demo-db.v3";

function loadSaved(): MockDb | null {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as MockDb) : null;
  } catch {
    return null;
  }
}

/** Saves the demo data (called after each change). Silently skipped if storage is unavailable. */
export function persistDb(): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(db));
  } catch {
    // Private window or quota: the demo keeps working, it just will not survive a reload.
  }
}

/** The whole demo database, in memory. Mutate its fields; reset with resetMockDb(). */
export const db: MockDb = { ...createDb(), ...(loadSaved() ?? {}) };

/** A fake job; onSuccess runs once when it completes and may return the result id. */
type MockJob = Job & { polls: number; onSuccess?: () => string | null };
export const jobs = new Map<string, MockJob>();

/** Import deduplication: same file name + size = same document (no duplicate on re-import). */
export const importedFiles = new Map<string, string>();

/** Tests call this to start from clean data. */
export function resetMockDb(): void {
  Object.assign(db, createDb());
  jobs.clear();
  importedFiles.clear();
  try {
    globalThis.localStorage?.removeItem(STORAGE_KEY);
  } catch {
    // nothing saved
  }
}

export function createJob(kind: string, onSuccess?: () => string | null): Job {
  const job: Job = { id: crypto.randomUUID(), kind, status: "queued", progress: 0, result_id: null, error: null };
  jobs.set(job.id, { ...job, polls: 0, onSuccess });
  return job;
}

// "*" matches any origin (browser or tests), followed by /api/v1.
export const api = (path: string) => `*${API_BASE_URL}${path}`;

/** Small pause so loading states are visible, like a real network. 0 in tests. */
export const latency = () => delay(import.meta.env.MODE === "test" ? 0 : 250);

export function page<T>(items: T[]): Page<T> {
  return { items, next_cursor: null };
}

export function apiError(status: number, code: string, message: string, details?: unknown) {
  const body: ApiErrorBody = { error: { code, message, details, request_id: `mock-${crypto.randomUUID().slice(0, 8)}` } };
  return HttpResponse.json(body, { status });
}

export function bump<T extends { revision: number; updated_at: string }>(record: T, changes: Partial<T>): T {
  return { ...record, ...changes, revision: record.revision + 1, updated_at: new Date().toISOString() };
}

/** Local calendar day "yyyy-mm-dd" (not UTC: a session at 00:30 belongs to that local day). */
export function localDay(date: Date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function newRecord(): { id: Uuid; created_at: string; updated_at: string; revision: number } {
  const now = new Date().toISOString();
  return { id: crypto.randomUUID(), created_at: now, updated_at: now, revision: 1 };
}

// --- Library helpers ---------------------------------------------------------

/** Passages (chunks) of a document, with a citation pointing at each one. */
export function citationsOf(document: DocumentSummary): Citation[] {
  const passages = PASSAGES[document.title] ?? [{ locator: { kind: "page" as const, page: 1 }, text: `Document « ${document.title} ».` }];
  return passages.map((passage, index) => {
    const suffix = `${document.id.slice(-6)}${String(index).padStart(6, "0")}`;
    return {
      citation_id: `c0000000-0000-4000-8000-${suffix}`,
      document_id: document.id,
      version_id: document.active_version_id,
      chunk_id: `a0000000-0000-4000-8000-${suffix}`,
      title: document.title,
      locator: passage.locator,
      excerpt: passage.text,
    };
  });
}

export function allCitations(): Citation[] {
  return db.documents.flatMap(citationsOf);
}

/** Lowercase without accents, so "théorème" matches "theoreme". */
export function normalize(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** French function words: they match everything, so they must not count as evidence. */
const STOPWORDS = new Set(
  "les des une est que qui quoi quel quelle quels quelles pour dans avec sur par pas son ses aux mais ont sont elle ils leur cette ces comme plus moins tout tous fait faire etre avoir entre quand comment pourquoi explique expliquer difference definition".split(" "),
);

/** Meaningful words of 3+ letters, normalized: the keyword search of the demo. */
export function keywords(text: string): string[] {
  return normalize(text)
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length >= 3 && !STOPWORDS.has(w));
}

/** Citations matching the words, best first. */
export function searchCitations(text: string, filter: (c: Citation) => boolean = () => true): Array<{ citation: Citation; score: number }> {
  const words = keywords(text);
  // Hyphenated terms ("p-valeur", "khi-deux") are worth more than their separate words.
  const terms = normalize(text)
    .split(/\s+/)
    .map((w) => w.replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, ""))
    .filter((w) => /[a-z0-9]-[a-z0-9]/.test(w));
  return allCitations()
    .filter(filter)
    .map((citation) => {
      const haystack = normalize(`${citation.title} ${citation.excerpt}`);
      const score = words.filter((w) => haystack.includes(w)).length + 2 * terms.filter((t) => haystack.includes(t)).length;
      return { citation, score };
    })
    .filter((hit) => hit.score > 0)
    .sort((a, b) => b.score - a.score);
}

export function locatorText(citation: Citation): string {
  const { locator } = citation;
  if (locator.kind === "slide") return `Diapositive ${locator.slide}`;
  if (locator.kind === "cell") return `Cellule ${locator.cell}`;
  return `Page ${locator.page ?? 1}`;
}

// --- Subject / concept helpers -------------------------------------------------

export function conceptsOfSubject(subjectId: Uuid) {
  const chapterIds = new Set(db.chapters.filter((c) => c.subject_id === subjectId).map((c) => c.id));
  return db.concepts.filter((c) => chapterIds.has(c.chapter_id));
}

export function subjectOfConcept(conceptId: Uuid): Uuid | undefined {
  const concept = db.concepts.find((c) => c.id === conceptId);
  return db.chapters.find((ch) => ch.id === concept?.chapter_id)?.subject_id;
}

/**
 * Records one objective result (0..1) for a concept, with the EMA of docs/ADAPTIVE_LEARNING.md
 * (alpha 0.35). First evidence initializes the score. Returns the before/after values.
 */
export function recordEvidence(conceptId: Uuid, dimension: MasteryDimension, score: number, weight = 1, dueInDays = 2) {
  const existing = db.mastery.find((m) => m.concept_id === conceptId && m.dimension === dimension);
  const previous = existing?.score ?? null;
  const next = previous === null ? score : (1 - 0.35 * weight) * previous + 0.35 * weight * score;
  const now = new Date();
  const due = new Date(now.getTime() + dueInDays * 86_400_000).toISOString();
  if (existing) {
    existing.score = Math.round(next * 100) / 100;
    existing.evidence_count += 1;
    existing.confidence = Math.min(1, existing.evidence_count / 5);
    existing.last_review_at = now.toISOString();
    existing.next_due_at = due;
  } else {
    db.mastery.push({ concept_id: conceptId, dimension, score: Math.round(next * 100) / 100, evidence_count: 1, confidence: 0.2, last_review_at: now.toISOString(), next_due_at: due });
  }
  return { concept_id: conceptId, dimension, previous_score: previous, score: Math.round(next * 100) / 100 };
}

// Fake backend used when VITE_USE_MOCKS is not "false".
// It answers like docs/API_CONTRACTS.md: {items,next_cursor} lists, error bodies,
// revisions (409 when stale), 422 validation and jobs that progress over time.
// Data lives in memory: a page reload resets the demo.
import { http, HttpResponse } from "msw";

import type { CalendarEvent, ResourceType, SearchHit, Task, TaskStatus } from "@/api/types";

import { api, apiError, bump, citationsOf, createJob, db, importedFiles, jobs, keywords, latency, locatorText, page, searchCitations } from "./core";
import { learningHandlers } from "./learningHandlers";
import { planningHandlers } from "./planningHandlers";
import { gradeHandlers } from "./gradeHandlers";
import { workspaceHandlers } from "./workspaceHandlers";

export { resetMockDb } from "./core";

const TASK_STATUSES: TaskStatus[] = ["todo", "in_progress", "done", "cancelled"];

export const handlers = [
  http.get(api("/health"), () => HttpResponse.json({ status: "ok", mode: "mock" })),

  // --- Profile --------------------------------------------------------------
  http.get(api("/profile"), async () => {
    await latency();
    return HttpResponse.json(db.profile);
  }),

  http.patch(api("/profile"), async ({ request }) => {
    await latency();
    const { expected_revision, ...changes } = (await request.json()) as Record<string, unknown>;
    if (expected_revision !== db.profile.revision) {
      return apiError(409, "stale_revision", "Le profil a été modifié ailleurs. Rechargez la page.");
    }
    db.profile = bump(db.profile, changes);
    return HttpResponse.json(db.profile);
  }),

  // --- Connections, sync and jobs ------------------------------------------
  http.get(api("/connections"), async () => {
    await latency();
    return HttpResponse.json(page(db.connections));
  }),

  http.post(api("/sync"), async () => {
    await latency();
    const job = createJob("sync", () => {
      db.connections = db.connections.map((c) =>
        c.status === "disconnected" ? c : bump(c, { status: "connected", last_success_at: new Date().toISOString() }),
      );
      return null;
    });
    return HttpResponse.json(job, { status: 202 });
  }),

  // Each poll moves the fake job forward: queued -> running 25/50/75 % -> succeeded.
  http.get(api("/jobs/:id"), async ({ params }) => {
    await latency();
    const job = jobs.get(String(params.id));
    if (!job) return apiError(404, "not_found", "Tâche introuvable.");
    if (job.status === "queued" || job.status === "running") {
      job.polls += 1;
      job.progress = Math.min(1, job.polls / 4);
      job.status = job.progress >= 1 ? "succeeded" : "running";
      if (job.status === "succeeded") job.result_id = job.onSuccess?.() ?? null;
    }
    const { polls: _polls, onSuccess: _onSuccess, ...publicJob } = job;
    return HttpResponse.json(publicJob);
  }),

  http.post(api("/jobs/:id/cancel"), async ({ params }) => {
    await latency();
    const job = jobs.get(String(params.id));
    if (!job) return apiError(404, "not_found", "Tâche introuvable.");
    if (job.status === "queued" || job.status === "running") job.status = "cancelled";
    const { polls: _polls, onSuccess: _onSuccess, ...publicJob } = job;
    return HttpResponse.json(publicJob);
  }),

  // --- Subjects -------------------------------------------------------------
  http.get(api("/subjects"), async () => {
    await latency();
    return HttpResponse.json(page(db.subjects));
  }),

  http.get(api("/subjects/:id"), async ({ params }) => {
    await latency();
    const subject = db.subjects.find((s) => s.id === params.id);
    return subject ? HttpResponse.json(subject) : apiError(404, "not_found", "Matière introuvable.");
  }),

  http.get(api("/subjects/:id/chapters"), async ({ params }) => {
    await latency();
    if (!db.subjects.some((s) => s.id === params.id)) return apiError(404, "not_found", "Matière introuvable.");
    return HttpResponse.json(page(db.chapters.filter((c) => c.subject_id === params.id)));
  }),

  http.get(api("/subjects/:id/concepts"), async ({ params }) => {
    await latency();
    if (!db.subjects.some((s) => s.id === params.id)) return apiError(404, "not_found", "Matière introuvable.");
    const chapterIds = new Set(db.chapters.filter((c) => c.subject_id === params.id).map((c) => c.id));
    return HttpResponse.json(page(db.concepts.filter((c) => chapterIds.has(c.chapter_id))));
  }),

  // --- Documents and mastery --------------------------------------------------
  http.get(api("/documents"), async ({ request }) => {
    await latency();
    const params = new URL(request.url).searchParams;
    const subjectId = params.get("subject_id");
    const type = params.get("resource_type");
    const year = params.get("year");
    const items = db.documents.filter(
      (d) => (!subjectId || d.subject_id === subjectId) && (!type || d.resource_type === type) && (!year || d.year === Number(year)),
    );
    return HttpResponse.json(page(items));
  }),

  http.get(api("/documents/:id"), async ({ params }) => {
    await latency();
    const document = db.documents.find((d) => d.id === params.id);
    return document ? HttpResponse.json(document) : apiError(404, "not_found", "Document introuvable.");
  }),

  // The demo has no real files: the content is a plain-text rendering of its passages.
  http.get(api("/documents/:id/versions/:versionId/content"), async ({ params }) => {
    await latency();
    const document = db.documents.find((d) => d.id === params.id && d.active_version_id === params.versionId);
    if (!document) return apiError(404, "not_found", "Version introuvable.");
    const body = citationsOf(document)
      .map((c) => `[${locatorText(c)}]\n${c.excerpt}`)
      .join("\n\n");
    return new HttpResponse(`${document.title}\n\n${body}\n`, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }),

  // Multipart upload -> job. Re-importing the same file returns the existing document.
  http.post(api("/documents/import"), async ({ request }) => {
    await latency();
    const form = await request.formData();
    const file = form.get("file");
    if (!(file instanceof File) || file.size === 0) return apiError(422, "validation_error", "Choisissez un fichier non vide.", { field: "file" });
    if (!/\.(pdf|txt|md)$/i.test(file.name)) return apiError(422, "validation_error", "Formats acceptés : PDF, TXT, Markdown.", { field: "file" });
    const subjectId = form.get("subject_id");
    const key = `${file.name}:${file.size}`;
    const job = createJob("import", () => {
      const existing = importedFiles.get(key);
      if (existing) return existing;
      const now = new Date().toISOString();
      const id = crypto.randomUUID();
      db.documents.push({
        id,
        created_at: now,
        updated_at: now,
        revision: 1,
        subject_id: typeof subjectId === "string" && subjectId ? subjectId : null,
        chapter_id: null,
        title: file.name.replace(/\.[^.]+$/, ""),
        resource_type: "other" as ResourceType,
        source: "local",
        year: null,
        remote_modified_at: now,
        active_version_id: crypto.randomUUID(),
      });
      importedFiles.set(key, id);
      return id;
    });
    return HttpResponse.json(job, { status: 202 });
  }),

  // Demo search: always a keyword match, whatever the mode (no embeddings in the mock).
  http.get(api("/search"), async ({ request }) => {
    await latency();
    const params = new URL(request.url).searchParams;
    const q = params.get("q") ?? "";
    if (keywords(q).length === 0) return apiError(422, "validation_error", "Saisissez au moins un mot significatif de 3 lettres.", { field: "q" });
    const subjectId = params.get("subject_id");
    const docs = new Set(db.documents.filter((d) => !subjectId || d.subject_id === subjectId).map((d) => d.id));
    const hits: SearchHit[] = searchCitations(q, (c) => docs.has(c.document_id));
    return HttpResponse.json(page(hits));
  }),

  http.get(api("/citations/:id"), async ({ params }) => {
    await latency();
    const citation = db.documents.flatMap(citationsOf).find((c) => c.citation_id === params.id);
    return citation ? HttpResponse.json(citation) : apiError(404, "not_found", "Citation introuvable ou source retirée.");
  }),

  http.get(api("/mastery"), async ({ request }) => {
    await latency();
    const subjectId = new URL(request.url).searchParams.get("subject_id");
    if (!subjectId) return HttpResponse.json(page(db.mastery));
    const chapterIds = new Set(db.chapters.filter((c) => c.subject_id === subjectId).map((c) => c.id));
    const conceptIds = new Set(db.concepts.filter((c) => chapterIds.has(c.chapter_id)).map((c) => c.id));
    return HttpResponse.json(page(db.mastery.filter((m) => conceptIds.has(m.concept_id))));
  }),

  // --- Events (range filter: event overlaps [from, to]) ---------------------
  http.get(api("/events"), async ({ request }) => {
    await latency();
    const url = new URL(request.url);
    const from = url.searchParams.get("from");
    const to = url.searchParams.get("to");
    // Accepted study sessions are returned too, with origin "planner" (docs/API_CONTRACTS.md).
    const planned: CalendarEvent[] = (db.plan?.sessions ?? [])
      .filter((s) => s.status === "planned" || s.status === "in_progress")
      .map((s) => ({
        id: s.id,
        created_at: db.plan?.accepted_at ?? s.start_at,
        updated_at: db.plan?.accepted_at ?? s.start_at,
        revision: db.plan?.revision ?? 1,
        subject_id: s.subject_id,
        title: s.title,
        type: "other",
        start_at: s.start_at,
        end_at: s.end_at,
        source: "planner",
        room: null,
        teacher: null,
        group: null,
        confirmation_required: false,
      }));
    const items = [...db.events, ...planned]
      .filter((e) => (!from || e.end_at >= from) && (!to || e.start_at <= to))
      .sort((a, b) => a.start_at.localeCompare(b.start_at));
    return HttpResponse.json(page(items));
  }),

  http.post(api("/events"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as Partial<CalendarEvent>;
    const title = typeof input.title === "string" ? input.title.trim() : "";
    if (!title) return apiError(422, "validation_error", "Le titre est obligatoire.", { field: "title" });
    if (input.type !== "personal") return apiError(422, "validation_error", "Seuls les événements personnels peuvent être créés.", { field: "type" });
    if (!input.start_at || !input.end_at || Number.isNaN(Date.parse(input.start_at)) || Number.isNaN(Date.parse(input.end_at))) {
      return apiError(422, "validation_error", "Les dates sont invalides.", { field: "start_at" });
    }
    if (input.end_at <= input.start_at) {
      return apiError(422, "validation_error", "La fin doit être après le début.", { field: "end_at" });
    }
    const now = new Date().toISOString();
    const event: CalendarEvent = {
      id: crypto.randomUUID(),
      created_at: now,
      updated_at: now,
      revision: 1,
      subject_id: input.subject_id ?? null,
      title,
      type: "personal",
      start_at: new Date(input.start_at).toISOString(),
      end_at: new Date(input.end_at).toISOString(),
      source: "manual",
      room: null,
      teacher: null,
      group: null,
      confirmation_required: false,
    };
    db.events.push(event);
    return HttpResponse.json(event, { status: 201 });
  }),

  http.delete(api("/events/:id"), async ({ params }) => {
    await latency();
    const event = db.events.find((e) => e.id === params.id);
    if (!event) return apiError(404, "not_found", "Événement introuvable.");
    if (event.source !== "manual") return apiError(403, "read_only", "Les événements importés ne peuvent pas être supprimés.");
    db.events = db.events.filter((e) => e.id !== params.id);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post(api("/events/:id/confirm"), async ({ params, request }) => {
    await latency();
    const index = db.events.findIndex((e) => e.id === params.id);
    if (index === -1) return apiError(404, "not_found", "Événement introuvable.");
    const { decision } = (await request.json()) as { decision?: string };
    if (decision !== "confirm" && decision !== "reject") {
      return apiError(422, "validation_error", "Décision inconnue.", { field: "decision" });
    }
    const event = db.events[index];
    if (!event.confirmation_required) return apiError(409, "already_resolved", "Cet événement a déjà été traité.");
    // Rejected: it stays in the calendar, but no longer counts as an assessment.
    db.events[index] = bump(event, { confirmation_required: false, type: decision === "confirm" ? event.type : "other" });
    return HttpResponse.json(db.events[index]);
  }),

  // --- Tasks ----------------------------------------------------------------
  http.get(api("/tasks"), async () => {
    await latency();
    return HttpResponse.json(page([...db.tasks].sort((a, b) => a.priority - b.priority)));
  }),

  http.post(api("/tasks"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as Partial<Task>;
    const title = typeof input.title === "string" ? input.title.trim() : "";
    if (!title) {
      return apiError(422, "validation_error", "Le titre est obligatoire.", { field: "title" });
    }
    const now = new Date().toISOString();
    const task: Task = {
      id: crypto.randomUUID(),
      created_at: now,
      updated_at: now,
      revision: 1,
      parent_task_id: null,
      subject_id: input.subject_id ?? null,
      title,
      description: input.description ?? null,
      deadline_at: input.deadline_at ?? null,
      estimated_min: input.estimated_min ?? null,
      remaining_min: input.estimated_min ?? null,
      priority: input.priority ?? 3,
      status: "todo",
      locked: false,
    };
    db.tasks.push(task);
    return HttpResponse.json(task, { status: 201 });
  }),

  http.patch(api("/tasks/:id"), async ({ params, request }) => {
    await latency();
    const index = db.tasks.findIndex((t) => t.id === params.id);
    if (index === -1) return apiError(404, "not_found", "Tâche introuvable.");
    const { expected_revision, ...changes } = (await request.json()) as Partial<Task> & { expected_revision?: number };
    if (expected_revision !== db.tasks[index].revision) {
      return apiError(409, "stale_revision", "Cette tâche a été modifiée entre-temps. Rechargez la liste.");
    }
    if (changes.status !== undefined && !TASK_STATUSES.includes(changes.status)) {
      return apiError(422, "validation_error", "Statut inconnu.", { field: "status" });
    }
    db.tasks[index] = bump(db.tasks[index], changes);
    return HttpResponse.json(db.tasks[index]);
  }),

  http.delete(api("/tasks/:id"), async ({ params }) => {
    await latency();
    const before = db.tasks.length;
    db.tasks = db.tasks.filter((t) => t.id !== params.id);
    return db.tasks.length < before ? new HttpResponse(null, { status: 204 }) : apiError(404, "not_found", "Tâche introuvable.");
  }),

  ...learningHandlers,
  ...planningHandlers,
  ...gradeHandlers,
  ...workspaceHandlers,

  // Any other /api/v1 route: not mocked yet. Say so honestly instead of faking success.
  http.all(api("/*"), () => apiError(501, "not_implemented", "Cette fonction n'est pas encore disponible en mode démonstration.")),
];

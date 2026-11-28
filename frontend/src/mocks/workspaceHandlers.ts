// Fake backend: settings, AI providers, notes, error notebook, notifications, connections, account.
import { http, HttpResponse } from "msw";

import type { Annotation, SourceKind } from "@/api/types";
import { ENSAI_SOURCES, type AvailabilitySuggestion, type NoteInput, type Preferences, type Settings, type WorkWindow } from "@/api/typesWorkspace";

import { api, apiError, bump, createJob, db, latency, newRecord, page, resetMockDb } from "./core";

const SOURCES: SourceKind[] = ["moodle", "ensora_drive", "pamplemousse", "google_calendar", "personal_drive", "local"];

function validPreferences(p: Preferences): string | null {
  if (p.min_session_min < 10 || p.max_session_min > 240 || p.min_session_min > p.max_session_min) return "Durée de session : minimum 10 min, maximum 240 min, minimum ≤ maximum.";
  if (p.daily_max_min < p.min_session_min || p.daily_max_min > 720) return "Le plafond quotidien doit être compris entre la durée minimale d'une session et 12 h.";
  if (p.work_windows.some((w) => w.start >= w.end)) return "Chaque créneau doit finir après son début.";
  return null;
}

/** Free time per weekday between 08:00 and 21:00, outside busy events of the next 7 days and lunch. */
function deduceWindows(): WorkWindow[] {
  const usable = new Set(db.connections.filter((c) => c.status !== "disconnected").map((c) => c.source_kind));
  const busy = db.events.filter((e) => e.type !== "deadline" && (e.source === "manual" || usable.has(e.source as SourceKind)));
  const windows: WorkWindow[] = [];
  const toMin = (d: Date) => d.getHours() * 60 + d.getMinutes();
  const hhmm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  for (let offset = 0; offset < 7; offset++) {
    const day = new Date();
    day.setDate(day.getDate() + offset);
    const weekday = day.getDay() === 0 ? 7 : day.getDay();
    const blocks: Array<[number, number]> = [[12 * 60 + 15, 13 * 60 + 30]]; // lunch break
    for (const e of busy) {
      const start = new Date(e.start_at);
      if (start.toDateString() !== day.toDateString()) continue;
      blocks.push([toMin(start) - 15, toMin(new Date(e.end_at)) + 15]); // 15 min to travel / breathe
    }
    blocks.sort((a, b) => a[0] - b[0]);
    let cursor = 8 * 60;
    for (const [bs, be] of [...blocks, [21 * 60, 24 * 60] as [number, number]]) {
      if (bs - cursor >= 45) windows.push({ day: weekday, start: hhmm(cursor), end: hhmm(Math.min(bs, 21 * 60)) });
      cursor = Math.max(cursor, be);
    }
  }
  return windows.sort((a, b) => a.day - b.day || a.start.localeCompare(b.start));
}

export const workspaceHandlers = [
  // --- Session (demo login) ---
  http.get(api("/auth/session"), async () => {
    await latency();
    return HttpResponse.json(db.session);
  }),

  // ENSAI account login: the backend uses these credentials to sign in to Moodle, Pamplemousse
  // and the Ensora Drive, then imports everything about the student (here: a progressing job).
  http.post(api("/auth/login"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as { username?: string; password?: string; demo?: boolean };
    if (!input.demo) {
      const username = input.username?.trim() ?? "";
      if (!username) return apiError(422, "validation_error", "Saisissez votre identifiant ENSAI.", { field: "username" });
      if (!/^[A-Za-z0-9._-]{2,40}$/.test(username)) return apiError(422, "validation_error", "Identifiant ENSAI invalide (lettres, chiffres, point ou tiret, sans @).", { field: "username" });
      if (!input.password) return apiError(422, "validation_error", "Saisissez votre mot de passe.", { field: "password" });
    }
    // The demo has no real ENSAI accounts: any credentials open the demo student.
    db.session = { authenticated: true, demo: Boolean(input.demo) };
    for (const kind of ENSAI_SOURCES) {
      const index = db.connections.findIndex((c) => c.source_kind === kind);
      if (index === -1) db.connections.push({ ...newRecord(), source_kind: kind, status: "syncing", last_success_at: null, is_fixture: true });
      else db.connections[index] = bump(db.connections[index], { status: "syncing" });
    }
    const syncJob = createJob("ensai_import", () => {
      const now = new Date().toISOString();
      db.connections = db.connections.map((c) => ((ENSAI_SOURCES as readonly string[]).includes(c.source_kind) ? bump(c, { status: "connected", last_success_at: now }) : c));
      return null;
    });
    return HttpResponse.json({ ...db.session, sync_job: syncJob });
  }),

  http.post(api("/auth/logout"), async () => {
    await latency();
    db.session = { authenticated: false, demo: false };
    return new HttpResponse(null, { status: 204 });
  }),

  // Mock only: start the demo again from the original data.
  http.post(api("/demo/reset"), async () => {
    await latency();
    resetMockDb();
    db.session = { authenticated: true, demo: true };
    return new HttpResponse(null, { status: 204 });
  }),

  // --- Availability deduced from Pamplemousse + Google Agenda ---
  http.get(api("/availability/suggestions"), async () => {
    await latency();
    const connected = (kind: SourceKind) => db.connections.some((c) => c.source_kind === kind && c.status !== "disconnected");
    const result: AvailabilitySuggestion = {
      windows: deduceWindows(),
      based_on: (["pamplemousse", "google_calendar"] as const).filter((k) => connected(k)),
      missing: (["pamplemousse", "google_calendar"] as const).filter((k) => !connected(k)),
    };
    return HttpResponse.json(result);
  }),

  // --- Annotations on library passages ---
  http.get(api("/annotations"), async ({ request }) => {
    await latency();
    const documentId = new URL(request.url).searchParams.get("document_id");
    return HttpResponse.json(page(db.annotations.filter((a) => !documentId || a.document_id === documentId)));
  }),

  http.post(api("/annotations"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as Partial<Annotation>;
    if (!input.comment?.trim()) return apiError(422, "validation_error", "Écrivez votre annotation.", { field: "comment" });
    if (!input.document_id || !db.documents.some((d) => d.id === input.document_id)) return apiError(422, "validation_error", "Document inconnu.", { field: "document_id" });
    const annotation: Annotation = {
      id: crypto.randomUUID(),
      document_id: input.document_id,
      document_version_id: input.document_version_id ?? "",
      locator: input.locator ?? { kind: "page", page: 1 },
      excerpt: input.excerpt ?? "",
      comment: input.comment.trim(),
      created_at: new Date().toISOString(),
    };
    db.annotations.push(annotation);
    return HttpResponse.json(annotation, { status: 201 });
  }),

  http.delete(api("/annotations/:id"), async ({ params }) => {
    await latency();
    db.annotations = db.annotations.filter((a) => a.id !== params.id);
    return new HttpResponse(null, { status: 204 });
  }),

  // --- Note restructuring (demo: deterministic outline, labelled, never applied without consent) ---
  http.post(api("/notes/:id/restructure"), async ({ params }) => {
    await latency();
    const note = db.notes.find((n) => n.id === params.id);
    if (!note) return apiError(404, "not_found", "Note introuvable.");
    if (note.markdown.trim().length < 10) return apiError(422, "validation_error", "La note est trop courte pour être restructurée.");
    const job = createJob("note_restructure", () => {
      const lines = note.markdown.split("\n").map((l) => l.trim()).filter(Boolean);
      const headings = lines.filter((l) => l.startsWith("#")).map((l) => l.replace(/^#+\s*/, ""));
      const bullets = lines.filter((l) => /^[-*]\s/.test(l)).map((l) => l.replace(/^[-*]\s*/, ""));
      const prose = lines.filter((l) => !l.startsWith("#") && !/^[-*>]\s?/.test(l));
      const parts = [`# ${note.title}`, "", "## Points clés", "", ...(bullets.length ? bullets : prose.slice(0, 3)).map((b) => `- ${b}`), ""];
      if (headings.length) parts.push("## Plan", "", ...headings.map((h, i) => `${i + 1}. ${h}`), "");
      if (prose.length) parts.push("## Développement", "", ...prose, "");
      parts.push("> Proposition générée en mode démonstration, sans IA. Vérifiez-la avant de l'appliquer.");
      const proposal = { id: crypto.randomUUID(), note_id: note.id, markdown: parts.join("\n") };
      db.noteProposals.push(proposal);
      return proposal.id;
    });
    return HttpResponse.json(job, { status: 202 });
  }),

  http.get(api("/notes/proposals/:id"), async ({ params }) => {
    await latency();
    const proposal = db.noteProposals.find((p) => p.id === params.id);
    return proposal ? HttpResponse.json(proposal) : apiError(404, "not_found", "Proposition introuvable.");
  }),

  // --- Settings and providers ---
  http.get(api("/settings"), async () => {
    await latency();
    return HttpResponse.json(db.settings);
  }),

  http.patch(api("/settings"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as Partial<Settings> & { expected_revision?: number };
    if (input.expected_revision !== db.settings.revision) return apiError(409, "stale_revision", "Les paramètres ont changé ailleurs. Rechargez la page.");
    if (input.preferences) {
      const problem = validPreferences(input.preferences);
      if (problem) return apiError(422, "validation_error", problem, { field: "preferences" });
    }
    const { expected_revision: _rev, ...changes } = input;
    db.settings = bump(db.settings, changes);
    return HttpResponse.json(db.settings);
  }),

  http.get(api("/providers"), async () => {
    await latency();
    return HttpResponse.json(page(db.providers));
  }),

  http.get(api("/usage"), async () => {
    await latency();
    return HttpResponse.json({ period: "day", tokens_used: db.tokensUsed, token_limit: 200_000, estimated_cost: null, currency: null });
  }),

  // --- Connections ---
  http.post(api("/connections"), async ({ request }) => {
    await latency();
    const { source_kind } = (await request.json()) as { source_kind?: SourceKind };
    if (!source_kind || !SOURCES.includes(source_kind)) return apiError(422, "validation_error", "Source inconnue.", { field: "source_kind" });
    const existing = db.connections.findIndex((c) => c.source_kind === source_kind);
    // Demo: "connecting" only switches the fixture on; no real account is contacted.
    const connection = { ...(existing === -1 ? newRecord() : db.connections[existing]), source_kind, status: "connected" as const, last_success_at: new Date().toISOString(), is_fixture: true };
    if (existing === -1) db.connections.push(connection);
    else db.connections[existing] = bump(db.connections[existing], connection);
    return HttpResponse.json(existing === -1 ? connection : db.connections[existing], { status: existing === -1 ? 201 : 200 });
  }),

  http.delete(api("/connections/:id"), async ({ params }) => {
    await latency();
    const index = db.connections.findIndex((c) => c.id === params.id);
    if (index === -1) return apiError(404, "not_found", "Connexion introuvable.");
    db.connections[index] = bump(db.connections[index], { status: "disconnected", last_success_at: null });
    return new HttpResponse(null, { status: 204 });
  }),

  // --- Notes ---
  http.get(api("/notes"), async ({ request }) => {
    await latency();
    const subjectId = new URL(request.url).searchParams.get("subject_id");
    return HttpResponse.json(page(db.notes.filter((n) => !subjectId || n.subject_id === subjectId).sort((a, b) => b.updated_at.localeCompare(a.updated_at))));
  }),

  http.post(api("/notes"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as NoteInput;
    if (!input.title?.trim()) return apiError(422, "validation_error", "Le titre est obligatoire.", { field: "title" });
    const note = { ...newRecord(), subject_id: input.subject_id ?? null, title: input.title.trim(), markdown: input.markdown ?? "", citations: input.citations ?? [] };
    db.notes.push(note);
    return HttpResponse.json(note, { status: 201 });
  }),

  http.patch(api("/notes/:id"), async ({ params, request }) => {
    await latency();
    const index = db.notes.findIndex((n) => n.id === params.id);
    if (index === -1) return apiError(404, "not_found", "Note introuvable.");
    const input = (await request.json()) as Partial<NoteInput> & { expected_revision?: number };
    if (input.expected_revision !== db.notes[index].revision) return apiError(409, "stale_revision", "Cette note a été modifiée ailleurs (autre onglet ?). Rechargez-la avant d'enregistrer.");
    if (input.title !== undefined && !input.title.trim()) return apiError(422, "validation_error", "Le titre est obligatoire.", { field: "title" });
    const { expected_revision: _rev, ...changes } = input;
    db.notes[index] = bump(db.notes[index], changes);
    return HttpResponse.json(db.notes[index]);
  }),

  http.delete(api("/notes/:id"), async ({ params }) => {
    await latency();
    db.notes = db.notes.filter((n) => n.id !== params.id);
    return new HttpResponse(null, { status: 204 });
  }),

  // --- Error notebook ---
  http.get(api("/errors"), async () => {
    await latency();
    return HttpResponse.json(page(db.errors));
  }),

  http.patch(api("/errors/:id"), async ({ params, request }) => {
    await latency();
    const entry = db.errors.find((e) => e.id === params.id);
    if (!entry) return apiError(404, "not_found", "Erreur introuvable.");
    const { resolved } = (await request.json()) as { resolved?: boolean };
    entry.resolved_at = resolved ? new Date().toISOString() : null;
    return HttpResponse.json(entry);
  }),

  // --- Notifications ---
  http.get(api("/notifications"), async () => {
    await latency();
    return HttpResponse.json(page([...db.notifications].sort((a, b) => b.created_at.localeCompare(a.created_at))));
  }),

  http.patch(api("/notifications/:id"), async ({ params, request }) => {
    await latency();
    const notification = db.notifications.find((n) => n.id === params.id);
    if (!notification) return apiError(404, "not_found", "Notification introuvable.");
    const { read } = (await request.json()) as { read?: boolean };
    notification.read_at = read ? new Date().toISOString() : null;
    return HttpResponse.json(notification);
  }),

  // --- Account ---
  http.post(api("/account/export"), async () => {
    await latency();
    return HttpResponse.json(createJob("account_export", () => null), { status: 202 });
  }),

  http.delete(api("/account"), async () => {
    await latency();
    return apiError(403, "demo_mode", "La suppression du compte est désactivée en mode démonstration.");
  }),
];

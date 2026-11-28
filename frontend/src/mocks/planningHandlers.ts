// Fake backend: assessments, Smart Planner, sessions, focus room, analytics, task decomposition.
// The planner is a small deterministic greedy algorithm (docs/SMART_PLANNER.md "MVP"):
// it never overlaps busy events, respects work windows and the daily cap, and reports
// what does not fit instead of exceeding limits.
import { http, HttpResponse } from "msw";

import type { CalendarEvent, Uuid } from "@/api/types";
import type { ActivityType, Analytics, FocusSession, PlanPreview, SessionFinish, StudySession, UnscheduledNeed } from "@/api/typesPlanning";

import { WORK_METHODS } from "@/lib/workMethods";

import { api, apiError, bump, conceptsOfSubject, createJob, db, latency, localDay, page } from "./core";

const HORIZON_DAYS = 14;
const MINUTE = 60_000;

interface Need {
  key: string;
  label: string;
  subject_id: Uuid | null;
  task_id: Uuid | null;
  assessment_id: Uuid | null;
  concept_ids: Uuid[];
  activity: ActivityType;
  minutes: number;
  deadline: number;
  objective: string;
  reason: string;
}

/** Changes whenever an input of the planner changes: an old preview then becomes stale (409). */
export function inputRevision(): string {
  const parts = [
    db.events.map((e) => `${e.id}:${e.revision}`).join(","),
    db.tasks.map((t) => `${t.id}:${t.revision}`).join(","),
    db.assessments.map((a) => `${a.id}:${a.revision}`).join(","),
    db.mastery.map((m) => `${m.concept_id}:${m.evidence_count}`).join(","),
    String(db.settings.revision),
    db.plan ? `${db.plan.id}:${db.plan.revision}` : "none",
  ];
  let hash = 0;
  for (const char of parts.join("|")) hash = (hash * 31 + char.charCodeAt(0)) | 0;
  return `rev-${(hash >>> 0).toString(16)}`;
}

function bestScore(conceptId: Uuid): number | null {
  const scores = db.mastery.filter((m) => m.concept_id === conceptId && m.score !== null).map((m) => m.score as number);
  return scores.length ? Math.max(...scores) : null;
}

function buildNeeds(now: number): { needs: Need[]; warnings: string[]; unscheduled: UnscheduledNeed[] } {
  const needs: Need[] = [];
  const warnings: string[] = [];
  const unscheduled: UnscheduledNeed[] = [];
  const conceptLabel = (id: Uuid) => db.concepts.find((c) => c.id === id)?.label ?? "Notion";

  for (const assessment of db.assessments) {
    const event = db.events.find((e) => e.id === assessment.event_id);
    if (event && event.type !== "exam" && event.type !== "presentation") continue; // rejected by the student
    const deadline = new Date(assessment.deadline_at).getTime();
    if (deadline <= now) continue;
    if (assessment.scope_status !== "confirmed") {
      warnings.push(`« ${assessment.title} » n'est pas confirmé : son programme n'est pas encore pris en compte.`);
      continue;
    }
    for (const conceptId of assessment.concept_ids) {
      const score = bestScore(conceptId);
      const label = conceptLabel(conceptId);
      const base = { subject_id: assessment.subject_id, task_id: null, assessment_id: assessment.id, concept_ids: [conceptId], deadline };
      if (score === null) {
        needs.push({ ...base, key: `${assessment.id}:${conceptId}:learn`, label: `Découvrir : ${label}`, activity: "learn", minutes: 45, objective: `Comprendre « ${label} » à partir du cours`, reason: "Notion jamais évaluée avant l'examen" });
      } else if (score < 0.5) {
        needs.push({ ...base, key: `${assessment.id}:${conceptId}:learn`, label: `Reprendre : ${label}`, activity: "learn", minutes: 45, objective: `Revoir le cours sur « ${label} »`, reason: `Niveau en apprentissage (${Math.round(score * 100)} %)` });
        needs.push({ ...base, key: `${assessment.id}:${conceptId}:practice`, label: `S'entraîner : ${label}`, activity: "practice", minutes: 30, objective: `Faire des exercices sur « ${label} »`, reason: "Consolider après la reprise du cours" });
      } else if (score < 0.7) {
        needs.push({ ...base, key: `${assessment.id}:${conceptId}:practice`, label: `S'entraîner : ${label}`, activity: "practice", minutes: 30, objective: `Exercices ciblés sur « ${label} »`, reason: `Niveau fragile (${Math.round(score * 100)} %)` });
      } else {
        needs.push({ ...base, key: `${assessment.id}:${conceptId}:review`, label: `Réviser : ${label}`, activity: "review", minutes: 25, objective: `Rappel actif sur « ${label} »`, reason: "Entretien de la mémoire avant l'examen" });
      }
    }
  }

  for (const task of db.tasks) {
    if (task.status === "done" || task.status === "cancelled" || !task.remaining_min) continue;
    const deadline = task.deadline_at ? new Date(task.deadline_at).getTime() : now + HORIZON_DAYS * 86_400_000;
    if (deadline <= now) {
      unscheduled.push({ need_id: task.id, label: task.title, subject_id: task.subject_id, remaining_min: task.remaining_min, reason: "DEADLINE_PASSED" });
      continue;
    }
    needs.push({ key: `task:${task.id}`, label: task.title, subject_id: task.subject_id, task_id: task.id, assessment_id: null, concept_ids: [], activity: "task", minutes: task.remaining_min, deadline, objective: task.title, reason: `Tâche de priorité ${task.priority}` });
  }

  // Earliest deadline first, then the stable key (deterministic result).
  needs.sort((a, b) => a.deadline - b.deadline || a.key.localeCompare(b.key));
  return { needs, warnings, unscheduled };
}

/** Free [start, end) intervals of one day inside the work windows, minus busy events and kept sessions. */
function freeIntervals(day: Date, now: number, busy: Array<[number, number]>): Array<[number, number]> {
  const weekday = day.getDay() === 0 ? 7 : day.getDay();
  const result: Array<[number, number]> = [];
  for (const window of db.settings.preferences.work_windows.filter((w) => w.day === weekday)) {
    const [sh, sm] = window.start.split(":").map(Number);
    const [eh, em] = window.end.split(":").map(Number);
    const start = new Date(day);
    start.setHours(sh, sm, 0, 0);
    const end = new Date(day);
    end.setHours(eh, em, 0, 0);
    // Never schedule in the past: start at the next quarter hour.
    let cursor = Math.max(start.getTime(), Math.ceil(now / (15 * MINUTE)) * 15 * MINUTE);
    const windowEnd = end.getTime();
    for (const [bs, be] of busy.filter(([bs, be]) => be > cursor && bs < windowEnd).sort((a, b) => a[0] - b[0])) {
      if (bs > cursor) result.push([cursor, Math.min(bs, windowEnd)]);
      cursor = Math.max(cursor, be);
    }
    if (cursor < windowEnd) result.push([cursor, windowEnd]);
  }
  return result.filter(([s, e]) => e - s >= db.settings.preferences.min_session_min * MINUTE);
}

function eventBusy(e: CalendarEvent): [number, number] | null {
  if (e.type === "deadline") return null; // an instant, not busy time
  return [new Date(e.start_at).getTime(), new Date(e.end_at).getTime()];
}

function computePreview(): PlanPreview {
  const now = Date.now();
  const prefs = db.settings.preferences;
  const kept = (db.plan?.sessions ?? []).filter((s) => s.locked || s.status === "completed" || s.status === "in_progress" || s.status === "partial");
  const { needs, warnings, unscheduled } = buildNeeds(now);
  const busy: Array<[number, number]> = [
    ...db.events.map(eventBusy).filter((b): b is [number, number] => b !== null),
    ...kept.map((s) => [new Date(s.start_at).getTime(), new Date(s.end_at).getTime()] as [number, number]),
  ];
  const sessions: StudySession[] = [];
  const usedByDay = new Map<string, number>();
  for (const s of kept) usedByDay.set(localDay(new Date(s.start_at)), (usedByDay.get(localDay(new Date(s.start_at))) ?? 0) + s.estimated_min);

  for (const need of needs) {
    let remaining = need.minutes;
    for (let d = 0; d < HORIZON_DAYS && remaining > 0; d++) {
      const day = new Date(now);
      day.setDate(day.getDate() + d);
      day.setHours(0, 0, 0, 0);
      const dayKey = localDay(day);
      for (const [fs, fe] of freeIntervals(day, now, busy)) {
        const budget = prefs.daily_max_min - (usedByDay.get(dayKey) ?? 0);
        if (remaining <= 0 || budget < prefs.min_session_min) break;
        const length = Math.min(remaining, prefs.max_session_min, budget, Math.floor((fe - fs) / MINUTE));
        if (length < Math.min(prefs.min_session_min, remaining)) continue;
        const end = fs + length * MINUTE;
        if (end > need.deadline) continue; // never after the deadline
        sessions.push({
          id: crypto.randomUUID(),
          subject_id: need.subject_id,
          task_id: need.task_id,
          assessment_id: need.assessment_id,
          concept_ids: need.concept_ids,
          activity_type: need.activity,
          title: need.label,
          start_at: new Date(fs).toISOString(),
          end_at: new Date(end).toISOString(),
          estimated_min: length,
          actual_min: null,
          remaining_min: null,
          objective: need.objective,
          success_criteria: need.activity === "review" ? "Répondre sans regarder le cours" : "Pouvoir réexpliquer la notion seul",
          locked: false,
          status: "planned",
          reason: need.reason,
        });
        busy.push([fs, end + WORK_METHODS[prefs.work_method].pause * MINUTE]); // keep the method's break after each session
        usedByDay.set(dayKey, (usedByDay.get(dayKey) ?? 0) + length);
        remaining -= length;
      }
    }
    if (remaining > 0) unscheduled.push({ need_id: crypto.randomUUID(), label: need.label, subject_id: need.subject_id, remaining_min: remaining, reason: "CAPACITY" });
  }

  const unscheduledMin = unscheduled.reduce((sum, u) => sum + u.remaining_min, 0);
  if (unscheduled.some((u) => u.reason === "CAPACITY")) {
    warnings.push(`${unscheduledMin} min de travail ne tiennent pas dans vos créneaux disponibles. Ajoutez du temps de travail ou acceptez un plan partiel.`);
  }
  const days = new Map<string, number>();
  for (const s of sessions) days.set(localDay(new Date(s.start_at)), (days.get(localDay(new Date(s.start_at))) ?? 0) + s.estimated_min);

  // Diff with the current plan, matched by title (one title per need).
  let diff: PlanPreview["diff"] = null;
  if (db.plan) {
    const current = db.plan.sessions.filter((s) => s.status === "planned" && !s.locked);
    diff = {
      added: sessions.filter((s) => !current.some((c) => c.title === s.title)).map((s) => s.id),
      moved: sessions.filter((s) => current.some((c) => c.title === s.title && c.start_at !== s.start_at)).map((s) => s.id),
      removed: current.filter((c) => !sessions.some((s) => s.title === c.title)).map((c) => c.id),
    };
  }

  const preview: PlanPreview = {
    preview_id: crypto.randomUUID(),
    input_revision: inputRevision(),
    sessions: [...kept, ...sessions].sort((a, b) => a.start_at.localeCompare(b.start_at)),
    unscheduled,
    warnings,
    summary: { planned_min: sessions.reduce((sum, s) => sum + s.estimated_min, 0), unscheduled_min: unscheduledMin, days: [...days].map(([date, minutes]) => ({ date, minutes })) },
    diff,
  };
  db.previews = [preview];
  return preview;
}

function findSession(id: string): StudySession | undefined {
  return db.plan?.sessions.find((s) => s.id === id);
}

function addWork(minutes: number) {
  const today = localDay();
  const entry = db.workLog.find((w) => w.date === today);
  if (entry) entry.minutes += minutes;
  else db.workLog.push({ date: today, minutes });
}

function closeInterval(session: FocusSession, at: string) {
  const open = session.intervals.at(-1);
  if (open && open.ended_at === null) open.ended_at = at;
}

function workMinutes(session: FocusSession): number {
  return Math.round(
    session.intervals.filter((i) => i.kind === "work").reduce((sum, i) => sum + (new Date(i.ended_at ?? Date.now()).getTime() - new Date(i.started_at).getTime()), 0) / MINUTE,
  );
}

export const planningHandlers = [
  // --- Assessments (scope confirmation) ---
  http.get(api("/assessments"), async () => {
    await latency();
    return HttpResponse.json(page([...db.assessments].sort((a, b) => a.deadline_at.localeCompare(b.deadline_at))));
  }),

  http.patch(api("/assessments/:id"), async ({ params, request }) => {
    await latency();
    const index = db.assessments.findIndex((a) => a.id === params.id);
    if (index === -1) return apiError(404, "not_found", "Évaluation introuvable.");
    const input = (await request.json()) as { scope_status?: "confirmed"; concept_ids?: string[]; expected_revision?: number };
    const assessment = db.assessments[index];
    if (input.expected_revision !== assessment.revision) return apiError(409, "stale_revision", "Cette évaluation a changé. Rechargez la page.");
    const event = db.events.find((e) => e.id === assessment.event_id);
    if (input.scope_status === "confirmed" && event?.confirmation_required) {
      return apiError(422, "event_unconfirmed", "Confirmez d'abord dans l'agenda que ce créneau est bien un examen.");
    }
    const valid = new Set(conceptsOfSubject(assessment.subject_id).map((c) => c.id));
    if (input.concept_ids && (input.concept_ids.length === 0 || input.concept_ids.some((id) => !valid.has(id)))) {
      return apiError(422, "validation_error", "Choisissez au moins une notion de la matière.", { field: "concept_ids" });
    }
    db.assessments[index] = bump(assessment, { ...(input.scope_status ? { scope_status: input.scope_status } : {}), ...(input.concept_ids ? { concept_ids: input.concept_ids } : {}) });
    return HttpResponse.json(db.assessments[index]);
  }),

  // --- Planner ---
  http.get(api("/plans/current"), async () => {
    await latency();
    return db.plan ? HttpResponse.json(db.plan) : new HttpResponse(null, { status: 204 });
  }),

  http.post(api("/planner/preview"), async () => {
    await latency();
    return HttpResponse.json(computePreview());
  }),

  http.post(api("/planner/replan"), async () => {
    await latency();
    return HttpResponse.json(computePreview());
  }),

  http.post(api("/planner/accept"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as { preview_id?: string; input_revision?: string };
    const preview = db.previews.find((p) => p.preview_id === input.preview_id);
    if (!preview) return apiError(404, "not_found", "Proposition expirée : générez-en une nouvelle.");
    if (input.input_revision !== inputRevision()) {
      return apiError(409, "stale_preview", "Vos données ont changé depuis cette proposition (agenda, tâches ou résultats). Générez une nouvelle proposition.");
    }
    const now = new Date();
    db.plan = {
      id: db.plan?.id ?? crypto.randomUUID(),
      revision: (db.plan?.revision ?? 0) + 1,
      accepted_at: now.toISOString(),
      horizon_start: now.toISOString(),
      horizon_end: new Date(now.getTime() + HORIZON_DAYS * 86_400_000).toISOString(),
      sessions: preview.sessions,
    };
    db.previews = [];
    return HttpResponse.json(db.plan);
  }),

  http.patch(api("/sessions/:id"), async ({ params, request }) => {
    await latency();
    const session = findSession(String(params.id));
    if (!session || !db.plan) return apiError(404, "not_found", "Session introuvable.");
    const input = (await request.json()) as { locked?: boolean; expected_revision?: number };
    if (input.expected_revision !== db.plan.revision) return apiError(409, "stale_revision", "Le planning a changé. Rechargez la page.");
    if (typeof input.locked === "boolean") session.locked = input.locked;
    db.plan = { ...db.plan, revision: db.plan.revision + 1 };
    return HttpResponse.json(db.plan);
  }),

  http.post(api("/sessions/:id/start"), async ({ params }) => {
    await latency();
    const session = findSession(String(params.id));
    if (!session || !db.plan) return apiError(404, "not_found", "Session introuvable.");
    if (session.status !== "planned" && session.status !== "partial") return apiError(409, "invalid_state", "Cette session est déjà commencée ou terminée.");
    session.status = "in_progress";
    db.plan = { ...db.plan, revision: db.plan.revision + 1 };
    return HttpResponse.json(session);
  }),

  http.post(api("/sessions/:id/finish"), async ({ params, request }) => {
    await latency();
    const session = findSession(String(params.id));
    if (!session || !db.plan) return apiError(404, "not_found", "Session introuvable.");
    const input = (await request.json()) as SessionFinish;
    if (!["completed", "partial", "skipped"].includes(input.status)) return apiError(422, "validation_error", "Statut inconnu.", { field: "status" });
    if (!Number.isInteger(input.actual_min) || input.actual_min < 0 || input.actual_min > 600) return apiError(422, "validation_error", "Durée réelle invalide.", { field: "actual_min" });
    session.status = input.status;
    session.actual_min = input.actual_min;
    session.remaining_min = input.status === "partial" ? Math.max(0, input.remaining_min) : 0;
    if (session.task_id) {
      const index = db.tasks.findIndex((t) => t.id === session.task_id);
      if (index !== -1 && db.tasks[index].remaining_min !== null) {
        db.tasks[index] = bump(db.tasks[index], { remaining_min: Math.max(0, (db.tasks[index].remaining_min ?? 0) - input.actual_min), status: "in_progress" });
      }
    }
    db.plan = { ...db.plan, revision: db.plan.revision + 1 };
    return HttpResponse.json(session);
  }),

  // --- Focus room (server keeps the intervals so the timer survives a reload) ---
  http.get(api("/focus/current"), async () => {
    await latency();
    const current = db.focusSessions.find((f) => f.status !== "finished");
    return current ? HttpResponse.json(current) : new HttpResponse(null, { status: 204 });
  }),

  http.post(api("/focus/start"), async ({ request }) => {
    await latency();
    if (db.focusSessions.some((f) => f.status !== "finished")) return apiError(409, "already_running", "Une séance est déjà en cours.");
    const input = (await request.json()) as Partial<FocusSession>;
    const now = new Date().toISOString();
    const session: FocusSession = {
      id: crypto.randomUUID(),
      study_session_id: input.study_session_id ?? null,
      goal: input.goal?.trim() || "Séance libre",
      mode: input.mode === "free" ? "free" : "pomodoro",
      work_min: input.work_min ?? 25,
      break_min: input.break_min ?? 5,
      status: "running",
      intervals: [{ kind: "work", started_at: now, ended_at: null }],
    };
    db.focusSessions.push(session);
    return HttpResponse.json(session, { status: 201 });
  }),

  http.post(api("/focus/:id/transition"), async ({ params, request }) => {
    await latency();
    const session = db.focusSessions.find((f) => f.id === params.id);
    if (!session || session.status === "finished") return apiError(404, "not_found", "Séance introuvable ou terminée.");
    const { to } = (await request.json()) as { to?: "work" | "break" | "pause" };
    const now = new Date().toISOString();
    closeInterval(session, now);
    if (to === "pause") session.status = "paused";
    else if (to === "work" || to === "break") {
      session.status = "running";
      session.intervals.push({ kind: to, started_at: now, ended_at: null });
    } else return apiError(422, "validation_error", "Transition inconnue.");
    return HttpResponse.json(session);
  }),

  http.post(api("/focus/:id/finish"), async ({ params }) => {
    await latency();
    const session = db.focusSessions.find((f) => f.id === params.id);
    if (!session) return apiError(404, "not_found", "Séance introuvable.");
    if (session.status === "finished") return HttpResponse.json(session); // idempotent
    closeInterval(session, new Date().toISOString());
    session.status = "finished";
    addWork(workMinutes(session));
    return HttpResponse.json(session);
  }),

  // --- Analytics (only from recorded data: work log, attempts, mastery) ---
  http.get(api("/analytics"), async ({ request }) => {
    await latency();
    const range = new URL(request.url).searchParams.get("range") === "month" ? 30 : 7;
    const to = new Date();
    const from = new Date(to.getTime() - (range - 1) * 86_400_000);
    from.setHours(0, 0, 0, 0);
    const days = Array.from({ length: range }, (_, i) => {
      const day = new Date(from);
      day.setDate(from.getDate() + i);
      return localDay(day);
    });
    const sessionsInRange = (db.plan?.sessions ?? []).filter((s) => s.start_at >= from.toISOString() && s.start_at <= to.toISOString());
    const scores: Analytics["scores"] = [];
    for (const attempt of db.attempts.filter((a) => a.status === "graded")) {
      const quiz = db.quizzes.find((q) => q.id === attempt.quiz_id);
      const questions = db.questions.filter((q) => q.quiz_id === attempt.quiz_id);
      const max = questions.reduce((sum, q) => sum + q.points, 0);
      const got = questions.reduce((sum, q) => {
        const r = attempt.responses[q.id];
        const ok = Array.isArray(q.answer) ? Array.isArray(r) && r.length === q.answer.length && q.answer.every((a) => r.includes(a)) : typeof r === "string" && r.trim().toLowerCase() === q.answer.toLowerCase();
        return sum + (q.type !== "open" && ok ? q.points : 0);
      }, 0);
      if (quiz && max > 0) scores.push({ date: localDay(new Date(attempt.started_at)), subject_id: quiz.subject_id, score: Math.round((got / max) * 100) / 100 });
    }
    const analytics: Analytics = {
      range: { from: from.toISOString(), to: to.toISOString() },
      work_by_day: days.map((date) => ({ date, minutes: db.workLog.find((w) => w.date === date)?.minutes ?? 0 })),
      planned_min: sessionsInRange.reduce((sum, s) => sum + s.estimated_min, 0),
      completed_min: sessionsInRange.filter((s) => s.status === "completed" || s.status === "partial").reduce((sum, s) => sum + (s.actual_min ?? 0), 0),
      scores,
      mastery_by_subject: db.subjects.map((subject) => {
        const counts = { subject_id: subject.id, unknown: 0, learning: 0, fragile: 0, acquired: 0 };
        for (const concept of conceptsOfSubject(subject.id)) {
          const score = bestScore(concept.id);
          if (score === null) counts.unknown += 1;
          else if (score < 0.5) counts.learning += 1;
          else if (score < 0.7) counts.fragile += 1;
          else counts.acquired += 1;
        }
        return counts;
      }),
      weak_concepts: db.concepts
        .map((c) => ({ concept_id: c.id, label: c.label, subject_id: db.chapters.find((ch) => ch.id === c.chapter_id)?.subject_id ?? "", score: bestScore(c.id) }))
        .filter((c): c is { concept_id: string; label: string; subject_id: string; score: number } => c.score !== null && c.score < 0.7)
        .sort((a, b) => a.score - b.score)
        .slice(0, 6),
      heatmap: Array.from({ length: 4 }, (_, w) => {
        // Monday of the week, 3 weeks ago ... this week.
        const monday = new Date();
        monday.setHours(0, 0, 0, 0);
        monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7) - (3 - w) * 7);
        return {
          week_start: localDay(monday),
          minutes: Array.from({ length: 7 }, (_, d) => {
            const day = new Date(monday);
            day.setDate(monday.getDate() + d);
            return db.workLog.find((x) => x.date === localDay(day))?.minutes ?? 0;
          }),
        };
      }),
      readiness: db.assessments
        .filter((a) => a.scope_status === "confirmed" && new Date(a.deadline_at).getTime() > Date.now())
        .map((a) => ({
          assessment_id: a.id,
          title: a.title,
          deadline_at: a.deadline_at,
          acquired: a.concept_ids.filter((id) => (bestScore(id) ?? 0) >= 0.7).length,
          total: a.concept_ids.length,
          planned_min: (db.plan?.sessions ?? []).filter((s) => s.assessment_id === a.id && s.status === "planned").reduce((sum, s) => sum + s.estimated_min, 0),
        })),
    };
    return HttpResponse.json(analytics);
  }),

  // --- Task decomposition (demo: one step per line or sentence) ---
  http.post(api("/tasks/decompose"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as { instructions?: string };
    const text = input.instructions?.trim() ?? "";
    if (text.length < 10) return apiError(422, "validation_error", "Collez les consignes du projet (au moins une phrase).", { field: "instructions" });
    const job = createJob("decomposition", () => {
      const parts = text
        .split(/\n+|(?<=[.;!?])\s+/)
        .map((line) => line.replace(/^[-*\d.)\s]+/, "").trim())
        .filter((line) => line.length > 3)
        .slice(0, 12);
      const proposal = { id: crypto.randomUUID(), steps: parts.map((title) => ({ title: title.slice(0, 120), estimated_min: Math.min(180, Math.max(30, Math.round(title.length / 2 / 15) * 15)) })) };
      db.taskProposals.push(proposal);
      return proposal.id;
    });
    return HttpResponse.json(job, { status: 202 });
  }),

  http.get(api("/tasks/proposals/:id"), async ({ params }) => {
    await latency();
    const proposal = db.taskProposals.find((p) => p.id === params.id);
    return proposal ? HttpResponse.json(proposal) : apiError(404, "not_found", "Proposition introuvable.");
  }),
];

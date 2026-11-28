/**
 * Turns agenda events + planned study sessions into positioned calendar blocks.
 * Pure: no React, unit-tested in tests/calendar.test.ts.
 */
import type { CalendarEvent, StudySession } from "../../api/types";
import { dayKey, minutesOfDay } from "../../lib/time";

/** Visual families of the mock-ups: recognisable by icon, word and shape, not colour alone. */
export type BlockKind = "course" | "revision" | "task" | "exam" | "personal";

export interface Block {
  id: string;
  kind: BlockKind;
  title: string;
  subtitle: string;
  subjectId: string | null;
  startAt: string;
  endAt: string;
  day: string;
  startMin: number;
  endMin: number;
  locked: boolean;
  origin: "agenda" | "planner";
  event?: CalendarEvent;
  session?: StudySession;
  /** Lane index and lane count when blocks overlap. */
  lane: number;
  lanes: number;
}

export const KIND_LABEL: Record<BlockKind, string> = {
  course: "Cours",
  revision: "Révision",
  task: "Devoir",
  exam: "Évaluation",
  personal: "Personnel",
};

export const ACTIVITY_LABEL: Record<string, string> = {
  discovery: "Découverte",
  revision: "Révision",
  practice: "Exercices",
  active_recall: "Rappel actif",
  flashcards: "Flashcards",
  task_work: "Travail sur la tâche",
  diagnostic: "Diagnostic",
};

export function eventKind(type: CalendarEvent["type"]): BlockKind {
  switch (type) {
    case "class":
    case "td":
    case "tp":
      return "course";
    case "exam":
    case "presentation":
      return "exam";
    case "deadline":
      return "task";
    default:
      return "personal";
  }
}

const EVENT_TYPE_LABEL: Record<CalendarEvent["type"], string> = {
  class: "Cours",
  td: "TD",
  tp: "TP",
  exam: "Examen",
  presentation: "Soutenance",
  deadline: "Échéance",
  personal: "Personnel",
  other: "Autre",
};

export function sessionKind(session: Pick<StudySession, "kind" | "activity_type">): BlockKind {
  return session.kind === "task" || session.activity_type === "task_work" ? "task" : "revision";
}

function base(startAt: string, endAt: string, tz: string) {
  const day = dayKey(startAt, tz);
  const startMin = minutesOfDay(startAt, tz);
  // A block ending after midnight is clipped to the end of its first day.
  const endMin = dayKey(endAt, tz) === day ? minutesOfDay(endAt, tz) : 24 * 60;
  // Minimum visible height of 10 min, kept inside the day (a 23:59 deadline must not create a "25 h" row).
  const end = Math.min(Math.max(endMin, startMin + 10), 24 * 60);
  return { day, startMin: Math.min(startMin, end - 10), endMin: end };
}

export function buildBlocks(events: CalendarEvent[], sessions: StudySession[], tz: string): Block[] {
  const blocks: Omit<Block, "lane" | "lanes">[] = [];

  for (const ev of events) {
    if (!ev.start_at || !ev.end_at || ev.status === "cancelled") continue;
    blocks.push({
      id: `event:${ev.id}`,
      kind: eventKind(ev.type),
      title: ev.title,
      subtitle: [EVENT_TYPE_LABEL[ev.type], ev.room].filter(Boolean).join(" · "),
      subjectId: ev.subject_id ?? null,
      startAt: ev.start_at,
      endAt: ev.end_at,
      locked: ev.read_only,
      origin: "agenda",
      event: ev,
      ...base(ev.start_at, ev.end_at, tz),
    });
  }

  for (const s of sessions) {
    if (s.status === "cancelled") continue;
    blocks.push({
      id: `session:${s.id}`,
      kind: sessionKind(s),
      title: s.title,
      subtitle: ACTIVITY_LABEL[s.activity_type] ?? s.activity_type,
      subjectId: s.subject_id ?? null,
      startAt: s.start_at,
      endAt: s.end_at,
      locked: s.locked,
      origin: "planner",
      session: s,
      ...base(s.start_at, s.end_at, tz),
    });
  }

  return assignLanes(blocks);
}

/** Greedy interval partitioning per day so overlapping blocks sit side by side. */
export function assignLanes(blocks: Omit<Block, "lane" | "lanes">[]): Block[] {
  const byDay = new Map<string, Omit<Block, "lane" | "lanes">[]>();
  for (const b of blocks) byDay.set(b.day, [...(byDay.get(b.day) ?? []), b]);

  const out: Block[] = [];
  for (const dayBlocks of byDay.values()) {
    dayBlocks.sort((a, b) => a.startMin - b.startMin || b.endMin - a.endMin);
    let cluster: Block[] = [];
    let clusterEnd = -1;
    let laneEnds: number[] = [];
    const flush = () => {
      const lanes = Math.max(1, laneEnds.length);
      for (const c of cluster) c.lanes = lanes;
      out.push(...cluster);
      cluster = [];
      laneEnds = [];
    };
    for (const b of dayBlocks) {
      if (b.startMin >= clusterEnd && cluster.length) flush();
      let lane = laneEnds.findIndex((end) => end <= b.startMin);
      if (lane === -1) {
        lane = laneEnds.length;
        laneEnds.push(b.endMin);
      } else {
        laneEnds[lane] = b.endMin;
      }
      cluster.push({ ...b, lane, lanes: 1 });
      clusterEnd = Math.max(clusterEnd, b.endMin);
    }
    flush();
  }
  return out;
}

/** Visible hour window: at least 8 h–20 h, widened to fit every block. */
export function hourWindow(blocks: Block[]): { from: number; to: number } {
  let from = 8 * 60;
  let to = 20 * 60;
  for (const b of blocks) {
    from = Math.min(from, Math.floor(b.startMin / 60) * 60);
    to = Math.max(to, Math.ceil(b.endMin / 60) * 60);
  }
  return { from: Math.max(from, 0), to: Math.min(to, 24 * 60) };
}

/** Human message for a planner warning code (`CODE:id`). */
export function warningLabel(code: string): string {
  const [kind] = code.split(":");
  const map: Record<string, string> = {
    ASSESSMENT_SLOT_UNCONFIRMED: "Un créneau d'évaluation doit être confirmé dans l'agenda.",
    SCOPE_UNCONFIRMED: "Le programme d'une évaluation n'est pas confirmé : l'estimation reste approximative.",
    ESTIMATE_HEURISTIC: "Durée de révision estimée par défaut (aucune notion confirmée).",
    TASK_WITHOUT_ESTIMATE: "Une tâche n'a pas de durée estimée.",
    PREREQUISITE_OUT_OF_SCOPE: "Un prérequis hors programme n'a pas pu être planifié.",
    CAPACITY: "Temps disponible insuffisant avant l'échéance.",
    PENDING_CONFIRMATIONS: "Des créneaux importés attendent votre confirmation dans l'agenda.",
    LEARNED_DURATION_FACTOR: "Durées ajustées d'après vos séances passées.",
  };
  return map[kind] ?? kind.replace(/_/g, " ").toLowerCase();
}

export function unscheduledReason(reason: string): string {
  const map: Record<string, string> = {
    CAPACITY: "votre plafond de travail serait dépassé",
    DEADLINE: "l'échéance est trop proche",
    NO_SLOT: "aucun créneau libre",
    PREREQUISITE_BLOCKED: "un prérequis n'a pas pu être planifié",
  };
  return map[reason] ?? reason.toLowerCase().replace(/_/g, " ");
}

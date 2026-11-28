/**
 * Turns agenda events + planned study sessions into positioned calendar blocks.
 * Pure: no React, unit-tested in tests/calendar.test.ts.
 */
import type { CalendarEvent, StudySession } from "../../api/types";
import { dayKey, minutesOfDay } from "../../lib/time";

/**
 * Visual families (proposition v3): recognisable by shape, icon and word, not colour alone.
 * Weight follows what the event demands: exam > deadline > revision/task > course > personal.
 * A deadline is an instant: drawn as a marker (line + flag), never as a block.
 */
export type BlockKind = "course" | "revision" | "task" | "exam" | "deadline" | "personal";
export type SourceOrigin = "imported" | "personal" | "generated";

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
  sourceOrigin: SourceOrigin;
  busy: boolean;
  allDay?: boolean;
  event?: CalendarEvent;
  session?: StudySession;
  /** Lane index and lane count when blocks overlap. */
  lane: number;
  lanes: number;
  /** True when this busy block genuinely overlaps another busy block (never masked by lanes). */
  conflict: boolean;
  conflictWith: string[];
  /** Short uppercase word shown on the block ("PARTIEL", "CM", "RAPPEL ACTIF"). */
  tag: string;
  /** Exam slot imported but not yet confirmed by the student. */
  tentative: boolean;
}

export interface ConflictPair {
  id: string;
  day: string;
  a: Block;
  b: Block;
}

export const KIND_LABEL: Record<BlockKind, string> = {
  course: "Cours",
  revision: "Révision",
  task: "Devoir",
  exam: "Évaluation",
  deadline: "Échéance",
  personal: "Personnel",
};

export const ORIGIN_LABEL: Record<SourceOrigin, string> = {
  imported: "Importé",
  personal: "Personnel",
  generated: "Session générée",
};

export const ACTIVITY_LABEL: Record<string, string> = {
  discovery: "Découverte",
  revision: "Révision",
  practice: "Exercices",
  active_recall: "Rappel actif",
  flashcards: "Flashcards",
  task_work: "Devoir",
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
      return "deadline";
    default:
      return "personal";
  }
}

const EVENT_TAG: Record<CalendarEvent["type"], string> = {
  class: "CM",
  td: "TD",
  tp: "TP",
  exam: "PARTIEL",
  presentation: "SOUTENANCE",
  deadline: "RENDU",
  personal: "PERSO",
  other: "PERSO",
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
  const blocks: Omit<Block, "lane" | "lanes" | "conflict" | "conflictWith">[] = [];

  for (const ev of events) {
    if (ev.status === "cancelled") continue;
    if (!ev.start_at || !ev.end_at) {
      if (ev.all_day_date) {
        blocks.push({
          id: `event:${ev.id}`,
          kind: eventKind(ev.type),
          title: ev.title,
          subtitle: ev.room ?? "Toute la journée",
          tag: EVENT_TAG[ev.type],
          tentative: ev.confirmation_required || ev.status === "tentative",
          subjectId: ev.subject_id ?? null,
          startAt: `${ev.all_day_date}T00:00:00Z`,
          endAt: `${ev.all_day_date}T23:59:00Z`,
          day: ev.all_day_date,
          startMin: 0,
          endMin: 0,
          locked: ev.read_only,
          origin: "agenda",
          sourceOrigin: ev.source === "manual" ? "personal" : "imported",
          busy: Boolean(ev.busy),
          allDay: true,
          event: ev,
        });
      }
      continue;
    }
    blocks.push({
      id: `event:${ev.id}`,
      kind: eventKind(ev.type),
      title: ev.title,
      subtitle: ev.room ?? "",
      tag: EVENT_TAG[ev.type],
      tentative: ev.confirmation_required || ev.status === "tentative",
      subjectId: ev.subject_id ?? null,
      startAt: ev.start_at,
      endAt: ev.end_at,
      locked: ev.read_only,
      origin: "agenda",
      sourceOrigin: ev.source === "manual" ? "personal" : "imported",
      busy: ev.busy ?? ev.type !== "deadline",
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
      subtitle: "",
      tag: (ACTIVITY_LABEL[s.activity_type] ?? s.activity_type).toUpperCase(),
      tentative: false,
      subjectId: s.subject_id ?? null,
      startAt: s.start_at,
      endAt: s.end_at,
      locked: s.locked,
      origin: "planner",
      sourceOrigin: "generated",
      busy: s.status !== "skipped",
      session: s,
      ...base(s.start_at, s.end_at, tz),
    });
  }

  return annotateConflicts(assignLanes(blocks));
}

/** Day-level highlights for the column header: exams, hand-in deadlines, and all-day events. */
export function dayHighlights(blocks: Block[], day: string): Block[] {
  return blocks
    .filter((b) => b.day === day && (b.kind === "exam" || b.kind === "deadline" || b.allDay))
    .sort((a, b) => a.startMin - b.startMin);
}

/** Greedy interval partitioning per day so overlapping blocks sit side by side. */
export function assignLanes(blocks: Omit<Block, "lane" | "lanes" | "conflict" | "conflictWith">[]): Block[] {
  const byDay = new Map<string, Omit<Block, "lane" | "lanes" | "conflict" | "conflictWith">[]>();
  const out: Block[] = [];
  for (const b of blocks) {
    if (b.kind === "deadline" || b.allDay) {
      out.push({ ...b, lane: 0, lanes: 1, conflict: false, conflictWith: [] });
    } else {
      byDay.set(b.day, [...(byDay.get(b.day) ?? []), b]);
    }
  }

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
      cluster.push({ ...b, lane, lanes: 1, conflict: false, conflictWith: [] });
      clusterEnd = Math.max(clusterEnd, b.endMin);
    }
    flush();
  }
  return out;
}

/** Marks genuine time conflicts between busy timed blocks so lanes never hide clashes. */
export function annotateConflicts(blocks: Block[]): Block[] {
  const timedBusy = blocks.filter((b) => b.busy && !b.allDay && b.kind !== "deadline");
  const byDay = new Map<string, Block[]>();
  for (const b of timedBusy) {
    byDay.set(b.day, [...(byDay.get(b.day) ?? []), b]);
  }
  for (const list of byDay.values()) {
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) {
        const a = list[i];
        const b = list[j];
        if (a.startMin < b.endMin && b.startMin < a.endMin) {
          a.conflict = true;
          b.conflict = true;
          if (!a.conflictWith.includes(b.title)) a.conflictWith.push(b.title);
          if (!b.conflictWith.includes(a.title)) b.conflictWith.push(a.title);
        }
      }
    }
  }
  return blocks;
}

export function listConflictPairs(blocks: Block[]): ConflictPair[] {
  const pairs: ConflictPair[] = [];
  const timedBusy = blocks.filter((b) => b.busy && !b.allDay && b.kind !== "deadline");
  for (let i = 0; i < timedBusy.length; i += 1) {
    for (let j = i + 1; j < timedBusy.length; j += 1) {
      const a = timedBusy[i];
      const b = timedBusy[j];
      if (a.day === b.day && a.startMin < b.endMin && b.startMin < a.endMin) {
        pairs.push({ id: `${a.id}__${b.id}`, day: a.day, a, b });
      }
    }
  }
  return pairs.sort((x, y) => x.day.localeCompare(y.day) || x.a.startMin - y.a.startMin);
}

/**
 * Visible hour window: at least 8 h–20 h, widened to fit every timed block.
 * Deadlines and all-day events do not widen it.
 */
export function hourWindow(blocks: Block[]): { from: number; to: number } {
  let from = 8 * 60;
  let to = 20 * 60;
  for (const b of blocks) {
    if (b.kind === "deadline" || b.allDay) continue;
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
    AGENDA_CONFLICT: "Des événements occupés se chevauchent dans l'agenda.",
    OFFICIAL_CONFLICT: "Conflit horaire entre événements importés.",
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

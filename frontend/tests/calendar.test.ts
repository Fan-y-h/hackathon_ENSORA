import { describe, expect, it } from "vitest";

import type { CalendarEvent, StudySession } from "../src/api/types";
import { buildBlocks, eventKind, hourWindow, sessionKind } from "../src/features/planner/calendar";
import { reasonLabels } from "../src/features/planner/reasons";

const TZ = "Europe/Paris";

const event = (over: Partial<CalendarEvent>): CalendarEvent =>
  ({
    id: "e1",
    type: "class",
    title: "Statistique inférentielle (CM)",
    subject_id: "stat",
    start_at: "2026-11-30T07:30:00Z",
    end_at: "2026-11-30T09:30:00Z",
    status: "confirmed",
    read_only: true,
    source: "fixture",
    ...over,
  }) as CalendarEvent;

const session = (over: Partial<StudySession>): StudySession =>
  ({
    id: "s1",
    kind: "flashcards",
    activity_type: "flashcards",
    title: "flashcards — stat",
    subject_id: "stat",
    start_at: "2026-11-30T08:30:00Z",
    end_at: "2026-11-30T09:00:00Z",
    status: "planned",
    locked: false,
    ...over,
  }) as StudySession;

describe("calendar blocks", () => {
  it("maps event and session types to the mock-up families", () => {
    expect(eventKind("td")).toBe("course");
    expect(eventKind("exam")).toBe("exam");
    expect(eventKind("presentation")).toBe("exam");
    expect(eventKind("deadline")).toBe("task");
    expect(sessionKind({ kind: "task", activity_type: "task_work" })).toBe("task");
    expect(sessionKind({ kind: "assessment", activity_type: "practice" })).toBe("revision");
  });

  it("positions blocks in local time and splits overlaps into lanes", () => {
    const blocks = buildBlocks([event({})], [session({})], TZ);
    expect(blocks).toHaveLength(2);
    const course = blocks.find((b) => b.origin === "agenda")!;
    expect(course.day).toBe("2026-11-30");
    expect(course.startMin).toBe(8 * 60 + 30);
    expect(course.lanes).toBe(2);
    expect(new Set(blocks.map((b) => b.lane))).toEqual(new Set([0, 1]));
  });

  it("drops cancelled items and widens the hour window when needed", () => {
    const blocks = buildBlocks(
      [event({ id: "late", start_at: "2026-11-30T20:00:00Z", end_at: "2026-11-30T21:30:00Z" }), event({ id: "x", status: "cancelled" })],
      [session({ id: "c", status: "cancelled" })],
      TZ,
    );
    expect(blocks).toHaveLength(1);
    expect(hourWindow(blocks)).toEqual({ from: 8 * 60, to: 23 * 60 });
  });

  it("keeps a 23:59 deadline inside the day", () => {
    const [b] = buildBlocks([event({ type: "deadline", start_at: "2026-12-04T22:59:00Z", end_at: "2026-12-04T22:59:00Z" })], [], TZ);
    expect(b.endMin).toBe(24 * 60);
    expect(b.startMin).toBe(24 * 60 - 10);
    expect(hourWindow([b])).toEqual({ from: 8 * 60, to: 24 * 60 });
  });

  it("decodes planner reason codes", () => {
    expect(reasonLabels("CARDS_DUE;ACADEMIC_SIGNAL(score=0.94,feasible)")).toEqual(["cartes à réviser", "objectif de note"]);
    expect(reasonLabels(null)).toEqual([]);
  });
});

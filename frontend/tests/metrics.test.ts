import { describe, expect, it } from "vitest";

import { currentStreak, rampStep } from "../src/features/analytics/metrics";
import { focusClock } from "../src/features/focus/timer";
import type { Focus } from "../src/api/types";
import { confusableWith, resolveSubjectColor, subjectAcronym } from "../src/lib/subjects";

const day = (date: string, study_min: number) => ({ date, study_min, focus_min: 0, reviews: 0, evidence: 0 });

describe("analytics metrics", () => {
  it("keeps unknown distinct from zero", () => {
    expect(rampStep(null)).toBeNull();
    expect(rampStep(0)).toBe(0);
    expect(rampStep(1)).toBe(5);
  });

  it("counts the current streak, tolerating an empty today", () => {
    const days = [day("2026-11-24", 30), day("2026-11-25", 40), day("2026-11-26", 20), day("2026-11-27", 0)];
    expect(currentStreak(days, "2026-11-27")).toBe(3);
    expect(currentStreak(days, "2026-11-30")).toBe(0);
  });
});

describe("subjects", () => {
  it("derives acronyms and stable default colours", () => {
    expect(subjectAcronym("stat")).toBe("STAT");
    expect(subjectAcronym("machine-learning")).toBe("MACH");
    const ctx = { overrides: {}, accessible: false, dark: false };
    expect(resolveSubjectColor("ml", ["ml", "stat"], ctx)).toBe("#0072B2");
    expect(resolveSubjectColor("ml", ["ml"], { ...ctx, overrides: { ml: "brun" } })).toBe("#8C5A2B");
    expect(resolveSubjectColor("ml", ["ml"], { ...ctx, accessible: true })).toBe("#004488");
  });

  it("flags confusable pairs", () => {
    expect(confusableWith("prune", ["bleu", "vert"])).toBe("bleu");
    expect(confusableWith("vert", ["bleu"])).toBeNull();
  });
});

describe("focus clock", () => {
  const base = {
    id: "f",
    mode: "classic",
    status: "working",
    work_min: 25,
    break_min: 5,
    started_at: "2026-11-27T10:00:00Z",
    phase_started_at: "2026-11-27T10:00:00Z",
    phase_remaining_min: 20,
    intervals: [{ kind: "work", started_at: "2026-11-27T10:00:00Z", ended_at: null }],
    active_min: 5,
  } as unknown as Focus;

  it("counts down from the server phase", () => {
    const fetchedAt = Date.parse("2026-11-27T10:05:00Z");
    const clock = focusClock(base, fetchedAt, Date.parse("2026-11-27T10:06:30Z"));
    expect(clock.countdown).toBe(true);
    expect(clock.label).toBe("18:30");
  });

  it("counts up in free mode", () => {
    const free = { ...base, mode: "free", work_min: null, phase_remaining_min: null } as Focus;
    const clock = focusClock(free, 0, Date.parse("2026-11-27T10:01:05Z"));
    expect(clock.countdown).toBe(false);
    expect(clock.label).toBe("01:05");
  });
});

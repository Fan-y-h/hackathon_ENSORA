import { describe, expect, it } from "vitest";

import { addDays, countdownLabel, dayKey, formatClock, formatDuration, minutesOfDay, mondayOf, startOfLocalDay } from "../src/lib/time";

const TZ = "Europe/Paris";

describe("time helpers", () => {
  it("formats durations the way the mock-ups do", () => {
    expect(formatDuration(45)).toBe("45 min");
    expect(formatDuration(95)).toBe("1 h 35");
    expect(formatDuration(120)).toBe("2 h");
    expect(formatDuration(null)).toBe("—");
  });

  it("converts UTC instants to Paris local time (winter, UTC+1)", () => {
    expect(dayKey("2026-11-27T23:30:00Z", TZ)).toBe("2026-11-28");
    expect(minutesOfDay("2026-11-27T13:40:00Z", TZ)).toBe(14 * 60 + 40);
    expect(formatClock("2026-11-27T13:05:00Z", TZ)).toBe("14 h 05");
  });

  it("finds local midnight across a DST change", () => {
    expect(startOfLocalDay("2026-11-27", TZ).toISOString()).toBe("2026-11-26T23:00:00.000Z");
    expect(startOfLocalDay("2026-07-01", TZ).toISOString()).toBe("2026-06-30T22:00:00.000Z");
  });

  it("navigates weeks", () => {
    expect(mondayOf("2026-11-27")).toBe("2026-11-23");
    expect(mondayOf("2026-11-23")).toBe("2026-11-23");
    expect(addDays("2026-11-30", 2)).toBe("2026-12-02");
  });

  it("labels countdowns", () => {
    const now = new Date("2026-11-27T10:00:00Z");
    expect(countdownLabel("2026-12-03T09:00:00Z", now, TZ)).toBe("J‑6");
    expect(countdownLabel("2026-11-27T18:00:00Z", now, TZ)).toBe("Aujourd'hui");
  });
});

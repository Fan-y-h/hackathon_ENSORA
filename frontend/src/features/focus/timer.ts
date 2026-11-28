import type { Focus, FocusMode } from "../../api/types";

export const MODES: { id: FocusMode; label: string; detail: string }[] = [
  { id: "classic", label: "Pomodoro classique", detail: "25 min + 5 min, pause longue après 4 cycles" },
  { id: "long", label: "Pomodoro long", detail: "50 min + 10 min" },
  { id: "deep", label: "Deep Work", detail: "90 min puis pause" },
  { id: "custom", label: "Personnalisé", detail: "Durées libres" },
  { id: "free", label: "Chronomètre libre", detail: "Sans durée prédéfinie" },
];

export interface FocusClock {
  label: string;
  countdown: boolean;
  remainingSec: number | null;
  progress: number | null;
}

const pad = (n: number) => String(n).padStart(2, "0");
const mmss = (sec: number) => `${pad(Math.floor(sec / 60))}:${pad(sec % 60)}`;

/**
 * Display-only clock. The server owns the intervals; the client derives the phase end
 * from `phase_started_at` + the remaining minutes it reported at fetch time, so a reload
 * shows the same value (docs/FRONTEND_UX_UI.md: timer survives reload, pauses excluded).
 */
export function focusClock(focus: Focus, fetchedAt: number, now: number): FocusClock {
  const phaseStart = focus.phase_started_at ? new Date(focus.phase_started_at).getTime() : null;

  if (focus.phase_remaining_min !== null && focus.phase_remaining_min !== undefined && phaseStart !== null) {
    const elapsedAtFetch = Math.max(0, Math.round((fetchedAt - phaseStart) / 60_000));
    const lengthMin = Math.max(1, elapsedAtFetch + focus.phase_remaining_min);
    const end = phaseStart + lengthMin * 60_000;
    const remainingSec = Math.max(0, Math.round((end - now) / 1000));
    return { label: mmss(remainingSec), countdown: true, remainingSec, progress: remainingSec / (lengthMin * 60) };
  }

  // Free mode or paused: count active work time.
  const running = focus.status === "working" && phaseStart !== null ? Math.max(0, Math.floor((now - phaseStart) / 1000)) : 0;
  const closedSec = focus.intervals
    .filter((i) => i.kind === "work" && i.ended_at)
    .reduce((s, i) => s + (new Date(i.ended_at!).getTime() - new Date(i.started_at).getTime()) / 1000, 0);
  const total = Math.round(closedSec + running);
  return { label: mmss(total), countdown: false, remainingSec: null, progress: null };
}

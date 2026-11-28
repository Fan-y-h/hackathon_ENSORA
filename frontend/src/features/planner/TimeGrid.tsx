import { useEffect, useState } from "react";

import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { dayKey, formatClock, minutesOfDay, shortDay, weekdayOfKey } from "../../lib/time";
import { dayHighlights, hourWindow, type Block } from "./calendar";
import { CalendarBlock } from "./CalendarBlock";

function useNow(intervalMs = 60_000) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Next planner session of today (or the one in progress): the only item ringed in coral. */
export function nextSessionId(blocks: Block[], today: string, nowMin: number): string | null {
  const todays = blocks.filter((b) => b.origin === "planner" && b.day === today).sort((a, b) => a.startMin - b.startMin);
  const running = todays.find((b) => b.session?.status === "in_progress");
  if (running) return running.id;
  return todays.find((b) => b.session?.status === "planned" && b.endMin > nowMin)?.id ?? null;
}

export function TimeGrid({
  days,
  blocks,
  pxPerHour,
  onOpen,
  onSelectDay,
  detailed = false,
  label,
}: {
  days: string[];
  blocks: Block[];
  pxPerHour: number;
  onOpen: (block: Block) => void;
  onSelectDay?: (day: string) => void;
  detailed?: boolean;
  label: string;
}) {
  const { timezone } = useSubjects();
  const now = useNow();
  const today = dayKey(now, timezone);
  const nowMin = minutesOfDay(now, timezone);
  const visible = blocks.filter((b) => days.includes(b.day));
  const { from, to } = hourWindow(visible);
  const pxPerMin = pxPerHour / 60;
  const height = (to - from) * pxPerMin;
  const step = detailed ? 30 : 60;
  const ticks: number[] = [];
  for (let m = from; m <= to; m += step) ticks.push(m);
  const nextId = nextSessionId(blocks, today, nowMin);
  const showBanners = !detailed && days.some((d) => dayHighlights(visible, d).length > 0);

  return (
    <section className="cal" aria-label={label}>
      <div className="cal__grid" style={{ gridTemplateColumns: `${detailed ? 64 : 48}px repeat(${days.length}, minmax(0, 1fr))` }}>
        <span />
        {days.map((d) => {
          const isToday = d === today;
          const content = (
            <>
              <span className="muted">{shortDay(weekdayOfKey(d))}</span>
              <strong>{Number(d.slice(8))}</strong>
              {isToday && <span className="sr-only">(aujourd'hui)</span>}
            </>
          );
          return onSelectDay ? (
            <button key={d} type="button" className={`cal__dayhead${isToday ? " cal__dayhead--today" : ""}`} onClick={() => onSelectDay(d)}>
              {content}
            </button>
          ) : (
            <div key={d} className={`cal__dayhead${isToday ? " cal__dayhead--today" : ""}`}>
              {content}
            </div>
          );
        })}

        {showBanners && (
          <>
            <span />
            {days.map((d) => (
              <div key={d} className="cal__banners">
                {dayHighlights(visible, d).map((b) => (
                  <button
                    key={b.id}
                    type="button"
                    className={`cal__banner cal__banner--${b.kind}${b.tentative ? " cal__banner--tentative" : ""}`}
                    onClick={() => onOpen(b)}
                    title={b.title}
                  >
                    <Icon name={b.kind === "exam" ? "clock" : "flag"} size={11} />
                    <span>
                      {b.tag} · {formatClock(b.startAt, timezone)}
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </>
        )}

        <div className="cal__hours" style={{ height }} aria-hidden="true">
          {ticks.map((m) => (
            <span key={m} className={`cal__hour${m % 60 === 0 ? " cal__hour--major" : ""}`} style={{ top: (m - from) * pxPerMin }}>
              {m % 60 === 0 ? `${m / 60} h` : ""}
            </span>
          ))}
        </div>

        {days.map((d) => (
          <div key={d} className={`cal__col${d === today && days.length > 1 ? " cal__col--today" : ""}`} style={{ height }}>
            {ticks.map((m) => (
              <span key={m} className="cal__line" style={{ top: (m - from) * pxPerMin, opacity: m % 60 === 0 ? 1 : 0.5 }} />
            ))}
            {visible
              .filter((b) => b.day === d && (b.kind !== "deadline" || (b.startMin >= from && b.startMin <= to)))
              .map((b) => (
                <CalendarBlock
                  key={b.id}
                  block={b}
                  top={(b.startMin - from) * pxPerMin}
                  height={(b.endMin - b.startMin) * pxPerMin - 3}
                  onOpen={onOpen}
                  isNext={b.id === nextId}
                />
              ))}
            {d === today && nowMin >= from && nowMin <= to && (
              <div className="cal__now" style={{ top: (nowMin - from) * pxPerMin }} role="img" aria-label={`Maintenant, ${formatClock(now, timezone)}`}>
                <span className="cal__now-label">{formatClock(now, timezone)}</span>
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

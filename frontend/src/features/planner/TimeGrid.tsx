import { useEffect, useState } from "react";

import { useSubjects } from "../../app/subjects";
import { dayKey, formatClock, minutesOfDay, shortDay, weekdayOfKey } from "../../lib/time";
import { hourWindow, type Block } from "./calendar";
import { CalendarBlock } from "./CalendarBlock";

function useNow(intervalMs = 60_000) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
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
              {isToday && <span className="cal__today-tag">Aujourd'hui</span>}
            </>
          );
          return onSelectDay ? (
            <button
              key={d}
              type="button"
              className={`cal__dayhead${isToday ? " cal__dayhead--today" : ""}`}
              onClick={() => onSelectDay(d)}
              aria-label={`Ouvrir la vue détaillée du ${shortDay(weekdayOfKey(d))} ${Number(d.slice(8))}`}
            >
              {content}
            </button>
          ) : (
            <div key={d} className={`cal__dayhead${isToday ? " cal__dayhead--today" : ""}`}>
              {content}
            </div>
          );
        })}

        <div className="cal__hours" style={{ height }} aria-hidden="true">
          {ticks.map((m) => (
            <span key={m} className={`cal__hour${m % 60 === 0 ? " cal__hour--major" : ""}`} style={{ top: (m - from) * pxPerMin }}>
              {m % 60 === 0 ? `${m / 60} h` : `${Math.floor(m / 60)} h 30`}
            </span>
          ))}
        </div>

        {days.map((d) => (
          <div key={d} className={`cal__col${d === today && days.length > 1 ? " cal__col--today" : ""}`} style={{ height }}>
            {ticks.map((m) => (
              <span key={m} className="cal__line" style={{ top: (m - from) * pxPerMin, opacity: m % 60 === 0 ? 1 : 0.5 }} />
            ))}
            {visible
              .filter((b) => b.day === d)
              .map((b) => (
                <CalendarBlock
                  key={b.id}
                  block={b}
                  top={(b.startMin - from) * pxPerMin}
                  height={(b.endMin - b.startMin) * pxPerMin - 3}
                  onOpen={onOpen}
                  detailed={detailed}
                />
              ))}
            {d === today && nowMin >= from && nowMin <= to && (
              <div className="cal__now" style={{ top: (nowMin - from) * pxPerMin }} role="img" aria-label={`Maintenant, ${formatClock(now, timezone)}`}>
                <span className="cal__now-label">Maintenant · {formatClock(now, timezone)}</span>
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

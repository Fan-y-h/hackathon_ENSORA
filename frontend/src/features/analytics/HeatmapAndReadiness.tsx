import type { Analytics } from "@/api/typesPlanning";
import { Card, CardHeader, EmptyState } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatMinutes, formatRelative, formatShortDay } from "@/lib/format";

const DAYS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];

/** Sequential buckets (one hue, light -> dark); 0 minutes is a neutral cell, never a color. */
const BUCKETS = [
  { max: 0, label: "Aucun travail", className: "bg-surface-2" },
  { max: 30, label: "Moins de 30 min", className: "bg-(--chart-seq-1)" },
  { max: 75, label: "30 à 75 min", className: "bg-(--chart-seq-2)" },
  { max: Infinity, label: "Plus de 75 min", className: "bg-(--chart-seq-3)" },
];
const bucket = (minutes: number) => BUCKETS.find((b) => minutes <= b.max) ?? BUCKETS[BUCKETS.length - 1];

export function WorkHeatmap({ weeks }: { weeks: Analytics["heatmap"] }) {
  return (
    <Card>
      <CardHeader title="Régularité sur 4 semaines" description="Minutes travaillées par jour. Survolez ou parcourez au clavier une case pour la valeur exacte." />
      <div className="overflow-x-auto">
        <table className="min-w-[360px] border-separate border-spacing-1 text-xs">
          <caption className="sr-only">Minutes de travail par jour, sur les 4 dernières semaines</caption>
          <thead>
            <tr>
              <th scope="col" className="w-24 text-left font-medium text-muted">Semaine du</th>
              {DAYS.map((d) => (
                <th key={d} scope="col" className="font-medium text-muted">
                  {d}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {weeks.map((week) => (
              <tr key={week.week_start}>
                <th scope="row" className="pr-2 text-left font-normal text-muted">
                  {formatShortDay(`${week.week_start}T12:00:00`)}
                </th>
                {week.minutes.map((minutes, i) => (
                  <td key={i} className="p-0">
                    <span
                      tabIndex={0}
                      title={`${DAYS[i]} : ${minutes ? formatMinutes(minutes) : "aucun travail"}`}
                      className={cn("focus-ring block h-7 min-w-8 rounded", bucket(minutes).className)}
                    >
                      <span className="sr-only">{minutes ? formatMinutes(minutes) : "aucun travail"}</span>
                    </span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Légende">
        {BUCKETS.map((b) => (
          <li key={b.label} className="inline-flex items-center gap-1.5">
            <span className={cn("size-3 rounded-sm border border-line", b.className)} aria-hidden="true" />
            {b.label}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** For each confirmed upcoming assessment: acquired concepts out of the scope, plus planned time left. */
export function Readiness({ items }: { items: Analytics["readiness"] }) {
  return (
    <Card>
      <CardHeader title="Prêt pour les évaluations ?" description="Notions acquises (70 % et plus) parmi le programme confirmé." />
      {items.length === 0 ? (
        <EmptyState title="Aucune évaluation confirmée à venir" description="Confirmez le programme d'une évaluation dans le Smart Planner." />
      ) : (
        <ul className="flex flex-col gap-4">
          {items.map((r) => (
            <li key={r.assessment_id}>
              <div className="mb-1 flex flex-wrap justify-between gap-2 text-sm">
                <span className="font-medium">{r.title}</span>
                <span className="text-xs text-muted">
                  {formatShortDay(r.deadline_at)} · {formatRelative(r.deadline_at)}
                </span>
              </div>
              <div
                role="meter"
                aria-label={`${r.title} : notions acquises`}
                aria-valuemin={0}
                aria-valuemax={r.total}
                aria-valuenow={r.acquired}
                aria-valuetext={`${r.acquired} notions acquises sur ${r.total}`}
                className="h-2.5 overflow-hidden rounded-full bg-surface-2"
              >
                <div className="h-full rounded-full bg-(--chart-seq-3)" style={{ width: `${r.total ? (r.acquired / r.total) * 100 : 0}%` }} />
              </div>
              <p className="mt-1 text-xs text-muted">
                <span className="font-medium text-ink tabular-nums">
                  {r.acquired} / {r.total}
                </span>{" "}
                notions acquises · {r.planned_min ? `${formatMinutes(r.planned_min)} de révision encore planifiées` : "aucune révision planifiée"}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

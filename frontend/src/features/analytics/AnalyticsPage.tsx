import { TrendingUp } from "lucide-react";
import { Link, useSearchParams } from "react-router";

import { useSubjects } from "@/api/hooks";
import { useAnalytics } from "@/api/hooksPlanning";
import type { Analytics } from "@/api/typesPlanning";
import { QueryStatus } from "@/components/QueryStatus";
import { Card, CardHeader, EmptyState, MasteryIndicator, PageHeader, buttonClasses } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatMinutes, formatPercent } from "@/lib/format";

import { Readiness, WorkHeatmap } from "./HeatmapAndReadiness";
import { WorkChart } from "./WorkChart";

const LEVELS = [
  { key: "acquired", label: "Acquis", className: "bg-(--chart-seq-3)" },
  { key: "fragile", label: "Fragile", className: "bg-(--chart-seq-2)" },
  { key: "learning", label: "En apprentissage", className: "bg-(--chart-seq-1)" },
  { key: "unknown", label: "Non évalué", className: "chart-unknown" },
] as const;

function Stat({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <Card className="p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {detail && <p className="mt-0.5 text-xs text-muted">{detail}</p>}
    </Card>
  );
}

/** One stacked bar per subject: share of concepts in each mastery state, with a 2px gap between segments. */
function MasteryBars({ data }: { data: Analytics["mastery_by_subject"] }) {
  const subjects = useSubjects();
  const title = (id: string) => subjects.data?.find((s) => s.id === id)?.title ?? "Matière";
  return (
    <>
      <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Légende">
        {LEVELS.map((level) => (
          <li key={level.key} className="inline-flex items-center gap-1.5">
            <span className={cn("size-3 rounded-sm border border-line", level.className)} aria-hidden="true" />
            {level.label}
          </li>
        ))}
      </ul>
      <ul className="flex flex-col gap-3">
        {data.map((row) => {
          const total = row.acquired + row.fragile + row.learning + row.unknown;
          return (
            <li key={row.subject_id}>
              <p className="mb-1 flex justify-between gap-2 text-sm">
                <span className="font-medium">{title(row.subject_id)}</span>
                <span className="text-xs text-muted">
                  {row.acquired} acquise{row.acquired > 1 ? "s" : ""} sur {total} notions
                </span>
              </p>
              <div className="flex h-4 gap-[2px] overflow-hidden rounded" aria-hidden="true">
                {LEVELS.map((level) =>
                  row[level.key] > 0 ? (
                    <div key={level.key} title={`${level.label} : ${row[level.key]}`} className={cn("h-full first:rounded-l last:rounded-r", level.className)} style={{ width: `${(row[level.key] / total) * 100}%` }} />
                  ) : null,
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <details className="mt-4 text-sm">
        <summary className="cursor-pointer text-muted">Voir le tableau</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-line text-xs text-muted">
                <th scope="col" className="py-1.5 pr-3 font-medium">Matière</th>
                {LEVELS.map((l) => (
                  <th key={l.key} scope="col" className="px-2 py-1.5 text-right font-medium">
                    {l.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((row) => (
                <tr key={row.subject_id} className="border-b border-line/60">
                  <th scope="row" className="py-1.5 pr-3 font-normal">{title(row.subject_id)}</th>
                  {LEVELS.map((l) => (
                    <td key={l.key} className="px-2 py-1.5 text-right tabular-nums">
                      {row[l.key]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}

/** Work time, plan adherence, quiz scores and mastery. Only recorded data, nothing estimated as real. */
export function AnalyticsPage() {
  const [params, setParams] = useSearchParams();
  const range = params.get("range") === "month" ? "month" : "week";
  const analytics = useAnalytics(range);
  const subjects = useSubjects();
  const data = analytics.data;
  const total = data?.work_by_day.reduce((sum, d) => sum + d.minutes, 0) ?? 0;
  const days = data?.work_by_day.length ?? 7;
  const scoreAvg = data && data.scores.length ? data.scores.reduce((s, x) => s + x.score, 0) / data.scores.length : null;

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Learning Analytics"
        icon={<TrendingUp className="size-5" aria-hidden="true" />}
        description="Calculé uniquement à partir de votre travail enregistré, de vos quiz et de vos révisions."
        actions={
          <div role="group" aria-label="Période" className="inline-flex rounded-lg border border-line p-0.5">
            {(
              [
                ["week", "7 jours"],
                ["month", "30 jours"],
              ] as const
            ).map(([id, label]) => (
              <button key={id} type="button" aria-pressed={range === id} onClick={() => setParams(id === "week" ? {} : { range: id }, { replace: true })} className={cn("focus-ring rounded-md px-3 py-1.5 text-sm", range === id ? "bg-accent text-accent-ink" : "text-muted hover:text-ink")}>
                {label}
              </button>
            ))}
          </div>
        }
      />
      <QueryStatus query={analytics} />
      {data && (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Temps de travail" value={formatMinutes(total)} detail={`soit ${formatMinutes(Math.round(total / days))} par jour en moyenne`} />
            <Stat
              label="Planning suivi"
              value={data.planned_min > 0 ? formatPercent(Math.min(1, data.completed_min / data.planned_min)) : "—"}
              detail={data.planned_min > 0 ? `${formatMinutes(data.completed_min)} faites sur ${formatMinutes(data.planned_min)} prévues` : "Aucune session planifiée sur la période"}
            />
            <Stat label="Score moyen aux quiz" value={scoreAvg === null ? "—" : formatPercent(scoreAvg)} detail={data.scores.length ? `${data.scores.length} quiz corrigé${data.scores.length > 1 ? "s" : ""}` : "Aucun quiz corrigé"} />
          </div>

          <Card>
            <CardHeader title="Temps de travail par jour (minutes)" description="Temps chronométré dans la Focus Room ou déclaré à la fin d'une session." />
            <WorkChart data={data.work_by_day} />
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer text-muted">Voir le tableau</summary>
              <table className="mt-2 w-full max-w-sm text-left">
                <thead>
                  <tr className="border-b border-line text-xs text-muted">
                    <th scope="col" className="py-1.5 font-medium">Jour</th>
                    <th scope="col" className="py-1.5 text-right font-medium">Minutes</th>
                  </tr>
                </thead>
                <tbody>
                  {data.work_by_day.map((d) => (
                    <tr key={d.date} className="border-b border-line/60">
                      <th scope="row" className="py-1 font-normal">{new Intl.DateTimeFormat("fr", { weekday: "short", day: "numeric", month: "short" }).format(new Date(`${d.date}T12:00:00`))}</th>
                      <td className="py-1 text-right tabular-nums">{d.minutes}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </Card>

          <div className="grid items-start gap-5 xl:grid-cols-2">
            <Readiness items={data.readiness} />
            <WorkHeatmap weeks={data.heatmap} />
          </div>

          <div className="grid items-start gap-5 xl:grid-cols-2">
            <Card>
              <CardHeader title="Maîtrise par matière" description="Répartition des notions selon votre niveau estimé." />
              <MasteryBars data={data.mastery_by_subject} />
            </Card>
            <Card>
              <CardHeader title="Notions à travailler en priorité" />
              {data.weak_concepts.length === 0 ? (
                <EmptyState title="Aucune notion fragile" description="Les notions non évaluées n'apparaissent pas ici : faites un quiz diagnostic pour les mesurer." />
              ) : (
                <ul className="flex flex-col gap-3">
                  {data.weak_concepts.map((c) => (
                    <li key={c.concept_id}>
                      <div className="mb-1 flex flex-wrap justify-between gap-2 text-sm">
                        <span className="font-medium">{c.label}</span>
                        <Link to={`/subjects/${c.subject_id}`} className="text-xs text-accent hover:underline">
                          {subjects.data?.find((s) => s.id === c.subject_id)?.title}
                        </Link>
                      </div>
                      <MasteryIndicator value={c.score} label={c.label} />
                    </li>
                  ))}
                </ul>
              )}
              <Link to="/quiz" className={buttonClasses("secondary", "sm", "mt-4")}>
                S'entraîner
              </Link>
            </Card>
          </div>
        </>
      )}
    </section>
  );
}

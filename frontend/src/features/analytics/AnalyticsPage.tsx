import { useState } from "react";

import { useAnalytics, useHeatmap, useMasteryMap, useReadiness } from "../../api/queries";
import { DIMENSIONS, type HeatmapDay } from "../../api/types";
import { useSubjects } from "../../app/subjects";
import { Card, EmptyState, PageHeader, QueryState, Stat } from "../../components/ui";
import { addDays, countdownLabel, dayKey, dayRange, formatDuration, formatPercent, mondayOf, shortDay, weekdayOfKey } from "../../lib/time";
import { activeMinutes, cellLabel, DIMENSION_LABEL, minutesStep, rampStep } from "./metrics";
import "./analytics.css";

const RANGES = [
  { id: "7", label: "7 j", days: 7 },
  { id: "30", label: "30 j", days: 30 },
  { id: "120", label: "Semestre", days: 120 },
] as const;

export function AnalyticsPage() {
  const { timezone: tz, name } = useSubjects();
  const [rangeId, setRangeId] = useState<(typeof RANGES)[number]["id"]>("30");
  const days = RANGES.find((r) => r.id === rangeId)!.days;
  const today = dayKey(new Date(), tz);
  const range = dayRange(addDays(today, -(days - 1)), addDays(today, 1), tz);
  const heatStart = mondayOf(addDays(today, -7 * 11));
  const heatRange = dayRange(heatStart, addDays(today, 1), tz);

  const analytics = useAnalytics(range.start, range.end);
  const heatmap = useHeatmap(heatRange.start, heatRange.end);
  const masteryMap = useMasteryMap();
  const readiness = useReadiness();

  return (
    <div className="page">
      <PageHeader
        title="Learning Analytics"
        actions={
          <div className="segmented" role="group" aria-label="Période">
            {RANGES.map((r) => (
              <button key={r.id} type="button" aria-pressed={rangeId === r.id} onClick={() => setRangeId(r.id)}>
                {r.label}
              </button>
            ))}
          </div>
        }
      />

      <QueryState query={analytics} lines={2}>
        {(a) => (
          <section aria-label="Synthèse" className="grid-auto" style={{ ["--min" as string]: "190px" }}>
            <Stat
              label="Maîtrise moyenne"
              value={formatPercent(a.mastery.mean_known_score)}
              delta={`sur ${a.mastery.known_count} notion·dimension évaluées`}
            />
            <Stat
              label="Temps de travail"
              value={formatDuration(a.study.actual_min)}
              delta={`planifié : ${formatDuration(a.study.planned_min)}`}
            />
            <Stat
              label="Score aux quiz"
              value={a.quizzes.score_ratio.value === null ? "—" : `${(a.quizzes.score_ratio.value * 20).toFixed(1).replace(".", ",")} / 20`}
              delta={`${a.quizzes.score_ratio.earned_points} / ${a.quizzes.score_ratio.possible_points} points · ${a.quizzes.attempts} tentative(s)`}
            />
            <Stat
              label="Respect du planning"
              value={formatPercent(a.study.adherence.value)}
              delta={`${a.study.adherence.numerator} / ${a.study.adherence.denominator} séances`}
              deltaTone={a.study.adherence.denominator === 0 ? "neutral" : undefined}
            />
          </section>
        )}
      </QueryState>

      <Card
        title="Maîtrise par notion"
       
        aside={<RampLegend />}
      >
        <QueryState query={masteryMap} lines={5}>
          {(map) =>
            map.concepts.length === 0 ? (
              <EmptyState title="Pas encore de mesure">Faites un quiz ou une session de flashcards : les notions évaluées apparaîtront ici.</EmptyState>
            ) : (
              <div className="table-wrap">
                <table className="mastery-grid">
                  <caption className="sr-only">Score de maîtrise estimé par notion (lignes) et dimension (colonnes)</caption>
                  <thead>
                    <tr>
                      <th scope="col">Notion</th>
                      {DIMENSIONS.map((d) => (
                        <th key={d} scope="col">
                          {DIMENSION_LABEL[d]}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {map.concepts.map((c) => (
                      <tr key={c.concept_id}>
                        <th scope="row">{c.concept_id}</th>
                        {DIMENSIONS.map((d) => {
                          const cell = c.dimensions[d];
                          const step = rampStep(cell?.score);
                          return (
                            <td key={d} title={`${c.concept_id} — ${DIMENSION_LABEL[d]} : ${cellLabel(cell)}`}>
                              <span className={`mcell ${step === null ? "mcell--unknown" : `mcell--s${step}`}`}>
                                {step === null ? <span className="sr-only">non évalué</span> : Math.round(cell!.score! * 100)}
                              </span>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </QueryState>
      </Card>

      <div className="grid-auto" style={{ ["--min" as string]: "380px" }}>
        <Card title="Régularité · 12 sem.">
          <QueryState query={heatmap}>
            {(h) => <Calendar days={h.days} start={heatStart} today={today} />}
          </QueryState>
        </Card>

        <Card title="Préparation">
          <QueryState query={readiness}>
            {(r) =>
              r.assessments.length === 0 ? (
                <EmptyState title="Aucune évaluation à venir" />
              ) : (
                <ul className="list">
                  {r.assessments.map((a) => (
                    <li key={a.assessment_id} style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
                      <div className="row" style={{ justifyContent: "space-between" }}>
                        <strong className="small">{a.title}</strong>
                        <span className="small muted">{a.deadline_at ? countdownLabel(a.deadline_at, new Date(), tz) : ""}</span>
                      </div>
                      <div className="row small muted" style={{ justifyContent: "space-between" }}>
                        <span>
                          {a.readiness.estimate === null ? "Non évaluée" : `Préparation ${formatPercent(a.readiness.estimate)}`}
                          {a.readiness.concepts > 0 && ` · ${a.readiness.known}/${a.readiness.concepts} notions mesurées`}
                        </span>
                        <span>
                          {formatDuration(a.studied_min)} faits · {formatDuration(a.planned_min_remaining)} prévus
                        </span>
                      </div>
                      {a.scope_status !== "confirmed" && <span className="xsmall" style={{ color: "var(--warning-text)" }}>Programme non confirmé</span>}
                    </li>
                  ))}
                </ul>
              )
            }
          </QueryState>
        </Card>

        <Card title="Temps par matière">
          <QueryState query={analytics}>
            {(a) =>
              a.per_subject.length === 0 ? (
                <EmptyState title="Aucune séance sur la période" />
              ) : (
                <div className="stack">
                  {a.per_subject.map((s) => {
                    const max = Math.max(...a.per_subject.map((x) => Math.max(x.planned_min, x.actual_min)), 1);
                    return (
                      <div key={s.subject_id ?? "none"} className="hbar" title={`${name(s.subject_id)} : ${formatDuration(s.actual_min)} réalisés sur ${formatDuration(s.planned_min)} planifiés`}>
                        <span className="small">{name(s.subject_id)}</span>
                        <div className="hbar__track">
                          <span className="hbar__planned" style={{ width: `${(s.planned_min / max) * 100}%` }} />
                          <span className="hbar__actual" style={{ width: `${(s.actual_min / max) * 100}%` }} />
                        </div>
                        <span className="small tabular strong">{formatDuration(s.actual_min)}</span>
                      </div>
                    );
                  })}
                </div>
              )
            }
          </QueryState>
        </Card>

        <Card title="Flashcards">
          <QueryState query={analytics}>
            {(a) => {
              const r = a.flashcards.ratings;
              const total = a.flashcards.reviews;
              return total === 0 ? (
                <EmptyState title="Aucune révision sur la période" />
              ) : (
                <div className="stack">
                  {(
                    [
                      ["again", "À revoir"],
                      ["hard", "Difficile"],
                      ["good", "Correct"],
                      ["easy", "Facile"],
                    ] as const
                  ).map(([k, label]) => (
                    <div key={k} className="hbar">
                      <span className="small">{label}</span>
                      <div className="hbar__track">
                        <span className="hbar__actual" style={{ width: `${(r[k] / total) * 100}%` }} />
                      </div>
                      <span className="small tabular strong">{r[k]}</span>
                    </div>
                  ))}
                  <span className="xsmall muted">
                    Taux d'oubli : {formatPercent(a.flashcards.again_rate.value)} ({a.flashcards.again_rate.numerator}/
                    {a.flashcards.again_rate.denominator})
                  </span>
                </div>
              );
            }}
          </QueryState>
        </Card>
      </div>
    </div>
  );
}

function RampLegend() {
  return (
    <span className="ramp-legend">
      <span className="mcell mcell--unknown mcell--mini" aria-hidden /> Non évalué
      <span style={{ marginLeft: 8 }}>0 %</span>
      {[0, 1, 2, 3, 4, 5].map((s) => (
        <span key={s} className={`mcell mcell--s${s} mcell--mini`} aria-hidden />
      ))}
      <span>100 %</span>
    </span>
  );
}

function Calendar({ days, start, today }: { days: HeatmapDay[]; start: string; today: string }) {
  const byDate = new Map(days.map((d) => [d.date, d]));
  const cells: { date: string; minutes: number; future: boolean }[] = [];
  for (let i = 0; i < 84; i += 1) {
    const date = addDays(start, i);
    const d = byDate.get(date);
    cells.push({ date, minutes: d ? activeMinutes(d) : 0, future: date > today });
  }
  return (
    <div className="stack">
      <div className="heat">
        <div className="heat__days" aria-hidden>
          {[0, 1, 2, 3, 4, 5, 6].map((w) => (
            <span key={w}>{w % 2 === 0 ? shortDay(w) : ""}</span>
          ))}
        </div>
        <div className="heat__grid" role="img" aria-label="Calendrier de régularité sur 12 semaines">
          {cells.map((c) => {
            const step = minutesStep(c.minutes);
            return (
              <span
                key={c.date}
                className={`heat__cell ${c.future ? "heat__cell--future" : step === null ? "" : `mcell--s${step}`}`}
                title={`${shortDay(weekdayOfKey(c.date))} ${c.date} : ${c.future ? "à venir" : formatDuration(c.minutes)}`}
              />
            );
          })}
        </div>
      </div>
      <div className="ramp-legend" style={{ justifyContent: "flex-end" }}>
        0 min <span className="heat__cell" aria-hidden />
        {[0, 1, 3, 5].map((s) => (
          <span key={s} className={`heat__cell mcell--s${s}`} aria-hidden />
        ))}{" "}
        2 h +
      </div>
    </div>
  );
}

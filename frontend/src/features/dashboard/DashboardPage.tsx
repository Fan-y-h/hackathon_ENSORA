import { useMemo } from "react";
import { Link } from "react-router-dom";

import {
  useAnalytics,
  useDueCards,
  useEvents,
  useHeatmap,
  useMastery,
  usePlan,
  usePlannerStatus,
  useProfile,
  useReadiness,
} from "../../api/queries";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Card, EmptyState, Meter, PageHeader, QueryState, Stat, StatusLine, SubjectBadge } from "../../components/ui";
import { addDays, countdownLabel, dayKey, dayRange, formatClock, formatDuration, formatLongDate, formatPercent, shortDay, weekdayOfKey } from "../../lib/time";
import { buildBlocks, KIND_LABEL } from "../planner/calendar";
import { KIND_ICON } from "../planner/CalendarBlock";
import { currentStreak } from "../analytics/metrics";
import { SyncButton } from "../sources/SyncButton";
import "./dashboard.css";
import { StudentStatusPicker, useStudentStatusLabel } from "../profile/StudentStatusPicker";

export function DashboardPage() {
  const subjects = useSubjects();
  const tz = subjects.timezone;
  const today = dayKey(new Date(), tz);
  const todayRange = dayRange(today, addDays(today, 1), tz);
  const weekRange = dayRange(addDays(today, -6), addDays(today, 1), tz);
  const heatRange = dayRange(addDays(today, -59), addDays(today, 1), tz);

  const profile = useProfile();
  const events = useEvents(todayRange.start, todayRange.end);
  const plan = usePlan();
  const status = usePlannerStatus();
  const due = useDueCards();
  const analytics = useAnalytics(weekRange.start, weekRange.end);
  const heatmap = useHeatmap(heatRange.start, heatRange.end);
  const readiness = useReadiness();
  const mastery = useMastery();

  const blocks = useMemo(
    () => buildBlocks(events.data?.items ?? [], plan.data?.sessions ?? [], tz).filter((b) => b.day === today).sort((a, b) => a.startMin - b.startMin),
    [events.data, plan.data, tz, today],
  );
  const sessionsToday = blocks.filter((b) => b.origin === "planner");
  const nextSession = sessionsToday.find((b) => b.session?.status === "planned" && new Date(b.endAt) > new Date());

  const todayStats = analytics.data?.per_day.find((d) => d.date === today);
  const dailyMax = profile.data?.preferences.daily_max_min ?? 180;
  const streak = heatmap.data ? currentStreak(heatmap.data.days, today) : null;
  const nextExam = readiness.data?.assessments
    .filter((a) => a.deadline_at && new Date(a.deadline_at) > new Date())
    .sort((a, b) => (a.deadline_at! < b.deadline_at! ? -1 : 1))[0];

  const atRisk = (mastery.data ?? [])
    .filter((m) => m.status === "weak" || m.status === "consolidate" || (m.next_due_at && new Date(m.next_due_at) < new Date()))
    .sort((a, b) => (a.score ?? 0) - (b.score ?? 0))
    .slice(0, 4);

  const firstName = profile.data?.display_name?.split(" ")[0];
  const statusLabel = useStudentStatusLabel();

  return (
    <div className="page">
      <PageHeader
        eyebrow={[formatLongDate(today), statusLabel].filter(Boolean).join(" · ")}
        title={`Bonjour${firstName ? ` ${firstName}` : ""}`}
        actions={
          <>
            <SyncButton />
            {nextSession ? (
              <Link className="btn btn--primary" to={`/focus?session=${nextSession.session!.id}`}>
                <Icon name="play" /> Démarrer · {formatClock(nextSession.startAt, tz)}
              </Link>
            ) : (
              <Link className="btn btn--primary" to="/planner">
                Planner
              </Link>
            )}
          </>
        }
      />

      {profile.data && !profile.data.student_status && (
        <Card title="Élève fonctionnaire ou ingénieur·e ?">
          <StudentStatusPicker />
        </Card>
      )}

      {status.data?.dirty && (
        <StatusLine tone="warning" label="Planning à mettre à jour">
          <Link to="/planner">recalculer</Link>
        </StatusLine>
      )}

      <section aria-label="Indicateurs du jour" className="grid-auto" style={{ ["--min" as string]: "200px" }}>
        <Stat label="Travail aujourd'hui" value={formatDuration(todayStats?.actual_min ?? 0)} unit={`/ ${formatDuration(dailyMax)}`}>
          <Meter value={todayStats?.actual_min ?? 0} max={dailyMax} label={`${formatDuration(todayStats?.actual_min ?? 0)} sur ${formatDuration(dailyMax)}`} />
        </Stat>
        <Stat
          label="À réviser"
          value={due.data?.length ?? "—"}
          unit="cartes"
          delta={due.data?.length ? <Link to="/flashcards">Réviser</Link> : undefined}
        />
        <Stat
          label="Régularité"
          value={streak ?? "—"}
          unit={streak === 1 ? "jour d'affilée" : "jours d'affilée"}
        />
        {nextExam ? (
          <Stat
            tone="accent"
            label={nextExam.title}
            value={countdownLabel(nextExam.deadline_at!, new Date(), tz)}
            delta={nextExam.readiness.estimate === null ? undefined : `Prêt à ${formatPercent(nextExam.readiness.estimate)}`}
          />
        ) : (
          <Stat label="Prochaine évaluation" value="—" />
        )}
      </section>

      <div className="grid-2">
        <Card title="Aujourd'hui">
          {events.isPending || plan.isPending ? null : blocks.length === 0 ? (
            <EmptyState title="Journée libre" />
          ) : (
            <ol className="today-list">
              {blocks.map((b) => (
                <li key={b.id}>
                  <span className="tabular muted">{formatClock(b.startAt, tz)}</span>
                  <div
                    className={`today-item today-item--${b.kind}${b === nextSession ? " today-item--next" : ""}`}
                    style={{ borderLeftColor: b.kind === "personal" ? undefined : subjects.color(b.subjectId) }}
                    aria-label={`${KIND_LABEL[b.kind]} : ${b.title}${b === nextSession ? ", prochaine" : ""}`}
                  >
                    <Icon name={KIND_ICON[b.kind]} size={15} />
                    <div className="stack" style={{ gap: 0, flex: 1, minWidth: 0 }}>
                      <span className="today-item__tag">{b.tag}</span>
                      <strong>{b.title}</strong>
                    </div>
                    <SubjectBadge acronym={subjects.acronym(b.subjectId)} color={subjects.color(b.subjectId)} />
                    {b.kind !== "deadline" && <span className="xsmall tabular">{formatDuration(b.endMin - b.startMin)}</span>}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Card>

        <Card title="Préparation">
          <QueryState query={readiness}>
            {(data) =>
              data.assessments.length === 0 ? (
                <EmptyState title="Aucune évaluation" />
              ) : (
                <div className="stack">
                  {data.assessments.slice(0, 4).map((a) => (
                    <div key={a.assessment_id} className="stack" style={{ gap: 6 }}>
                      <div className="row" style={{ justifyContent: "space-between" }}>
                        <strong className="small">{a.title}</strong>
                        <span className="small muted tabular">
                          {a.scope_status !== "confirmed" && (
                            <span style={{ color: "var(--warning)", marginRight: 4 }}>
                              <Icon name="warning" size={12} label="Programme à confirmer" />
                            </span>
                          )}
                          {a.deadline_at ? countdownLabel(a.deadline_at, new Date(), tz) : "date ?"} ·{" "}
                          <strong style={{ color: "var(--text)" }}>
                            {a.readiness.estimate === null ? "non évaluée" : formatPercent(a.readiness.estimate)}
                          </strong>
                        </span>
                      </div>
                      <ReadinessBar low={a.readiness.low} high={a.readiness.high} estimate={a.readiness.estimate} />
                    </div>
                  ))}
                </div>
              )
            }
          </QueryState>
        </Card>

        <Card title="7 derniers jours" aside={analytics.data ? formatDuration(analytics.data.study.actual_min) : undefined}>
          <QueryState query={heatmap}>
            {(data) => <WeekBars days={data.days} today={today} />}
          </QueryState>
        </Card>

        <Card title="À risque d'oubli" aside={<Link to="/analytics">Analyse</Link>}>
          <QueryState query={mastery}>
            {() =>
              atRisk.length === 0 ? (
                <EmptyState title="Rien à signaler" />
              ) : (
                <ul className="list">
                  {atRisk.map((m) => (
                    <li key={`${m.concept_id}-${m.dimension}`}>
                      <span style={{ color: "var(--warning)" }}>
                        <Icon name="warning" size={18} />
                      </span>
                      <div className="stack" style={{ gap: 2, flex: 1 }}>
                        <strong className="small">{m.concept_id}</strong>
                        <span className="xsmall muted">
                          {subjects.name(m.subject_id)} · {m.status === "weak" ? "insuffisant" : "à consolider"}
                          {m.last_review_at && ` · revue le ${formatLongDate(dayKey(m.last_review_at, tz)).toLowerCase()}`}
                        </span>
                      </div>
                      <Link className="btn btn--small" to="/flashcards">
                        Rappel
                      </Link>
                    </li>
                  ))}
                </ul>
              )
            }
          </QueryState>
        </Card>
      </div>
    </div>
  );
}

function ReadinessBar({ low, high, estimate }: { low: number | null; high: number | null; estimate: number | null }) {
  if (estimate === null) {
    return <div className="readiness readiness--unknown" role="img" aria-label="Préparation non évaluée" />;
  }
  return (
    <div className="readiness" role="img" aria-label={`Préparation estimée ${formatPercent(estimate)}, entre ${formatPercent(low)} et ${formatPercent(high)}`}>
      {low !== null && high !== null && <span className="readiness__band" style={{ left: `${low * 100}%`, width: `${(high - low) * 100}%` }} />}
      <span className="readiness__bar" style={{ width: `${estimate * 100}%` }} />
    </div>
  );
}

function WeekBars({ days, today }: { days: { date: string; study_min: number; focus_min: number }[]; today: string }) {
  const last7 = Array.from({ length: 7 }, (_, i) => addDays(today, i - 6)).map((date) => {
    const d = days.find((x) => x.date === date);
    return { date, minutes: d ? Math.max(d.study_min, d.focus_min) : 0 };
  });
  const max = Math.max(60, ...last7.map((d) => d.minutes));
  const best = Math.max(...last7.map((d) => d.minutes));
  return (
    <div>
      <div className="bars" role="img" aria-label={`Minutes de travail par jour : ${last7.map((d) => `${shortDay(weekdayOfKey(d.date))} ${formatDuration(d.minutes)}`).join(", ")}`}>
        {last7.map((d) => (
          <div key={d.date} className="bars__col" title={`${shortDay(weekdayOfKey(d.date))} : ${formatDuration(d.minutes)}`}>
            <span className="xsmall muted">{d.date === today || (d.minutes === best && best > 0) ? formatDuration(d.minutes) : ""}</span>
            <div className={`bars__bar${d.date === today ? " bars__bar--today" : ""}`} style={{ height: `${(d.minutes / max) * 130}px` }} />
          </div>
        ))}
      </div>
      <div className="bars__labels">
        {last7.map((d) => (
          <span key={d.date} className={d.date === today ? "strong" : "muted"}>
            {shortDay(weekdayOfKey(d.date))}
          </span>
        ))}
      </div>
    </div>
  );
}

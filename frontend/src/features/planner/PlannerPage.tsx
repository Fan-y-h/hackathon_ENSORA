import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { errorMessage } from "../../api/client";
import { useAvailability, useEvents, usePlan, usePlannerStatus, usePreviewPlan, useProfile } from "../../api/queries";
import type { PlanPreview } from "../../api/types";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Card, ErrorPanel, Loading, PageHeader, StatusLine, SubjectBadge } from "../../components/ui";
import {
  addDays,
  dayKey,
  dayRange,
  formatClock,
  formatDuration,
  formatLongDate,
  formatShortDate,
  minutesOfDay,
  mondayOf,
  shortDay,
  weekdayOfKey,
} from "../../lib/time";
import { BlockDialog } from "./BlockDialog";
import { buildBlocks, KIND_LABEL, warningLabel, type Block, type BlockKind } from "./calendar";
import { KIND_ICON } from "./CalendarBlock";
import { PreviewDialog } from "./PreviewDialog";
import { reasonLabels } from "./reasons";
import { TimeGrid } from "./TimeGrid";
import "./planner.css";

const LEGEND: BlockKind[] = ["course", "revision", "task", "exam"];

export function PlannerPage() {
  const [params, setParams] = useSearchParams();
  const subjects = useSubjects();
  const tz = subjects.timezone;
  const today = dayKey(new Date(), tz);
  const view = params.get("view") === "day" ? "day" : "week";
  // On Saturday/Sunday the week view opens on the coming week: the past school week is over.
  const defaultDate = view === "week" && weekdayOfKey(today) > 4 ? addDays(mondayOf(today), 7) : today;
  const selected = params.get("date") ?? defaultDate;
  const monday = mondayOf(selected);
  const weekDays = Array.from({ length: 7 }, (_, i) => addDays(monday, i));

  const range = dayRange(monday, addDays(monday, 7), tz);
  const events = useEvents(range.start, range.end);
  const plan = usePlan();
  const status = usePlannerStatus();
  const profile = useProfile();
  const dayAvail = dayRange(selected, addDays(selected, 1), tz);
  const availability = useAvailability(dayAvail.start, dayAvail.end);
  const previewMutation = usePreviewPlan();
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [open, setOpen] = useState<Block | null>(null);

  const blocks = useMemo(
    () => buildBlocks(events.data?.items ?? [], plan.data?.sessions ?? [], tz),
    [events.data, plan.data, tz],
  );

  // Hide the weekend in week view when nothing happens then, as in the mock-ups.
  const weekendUsed = blocks.some((b) => b.day === weekDays[5] || b.day === weekDays[6]);
  const shownWeek = weekendUsed ? weekDays : weekDays.slice(0, 5);

  const go = (next: { view?: string; date?: string }) => {
    const p = new URLSearchParams(params);
    if (next.view) p.set("view", next.view);
    if (next.date) p.set("date", next.date);
    setParams(p, { replace: true });
  };

  const compute = () => {
    const current = plan.data;
    previewMutation.mutate(
      current ? { replan: { baseRevision: current.revision, reason: "manual" } } : {},
      { onSuccess: setPreview },
    );
  };

  const dailyMax = profile.data?.preferences.daily_max_min ?? 240;
  const planned = (day: string) =>
    blocks.filter((b) => b.origin === "planner" && b.day === day).reduce((sum, b) => sum + (b.endMin - b.startMin), 0);
  const weekPlannedMin = weekDays.reduce((s, d) => s + planned(d), 0);
  const weekReasons = [
    ...new Set(
      (plan.data?.sessions ?? [])
        .filter((s) => weekDays.includes(dayKey(s.start_at, tz)))
        .flatMap((s) => reasonLabels(s.reason)),
    ),
  ];

  const title =
    view === "week"
      ? plan.data
        ? `${formatDuration(weekPlannedMin)} de travail planifiées cette semaine`
        : "Aucun planning accepté pour l'instant"
      : formatLongDate(selected);

  return (
    <div className="page">
      <PageHeader
        eyebrow={
          view === "week"
            ? `Smart Planner · semaine du ${formatShortDate(weekDays[0])} au ${formatShortDate(weekDays[shownWeek.length - 1])}`
            : "Smart Planner · vue détaillée"
        }
        title={title}
        actions={
          <>
            <nav className="segmented" aria-label="Vue">
              <button type="button" aria-pressed={view === "day"} onClick={() => go({ view: "day" })}>
                Jour
              </button>
              <button type="button" aria-pressed={view === "week"} onClick={() => go({ view: "week" })}>
                Semaine
              </button>
            </nav>
            <button type="button" className="btn btn--primary" onClick={compute} disabled={previewMutation.isPending}>
              <Icon name="refresh" />
              {previewMutation.isPending ? "Calcul…" : plan.data ? "Replanifier" : "Générer mon planning"}
            </button>
          </>
        }
      />

      <div className="row" style={{ justifyContent: "space-between" }}>
        <nav className="day-strip" aria-label={view === "week" ? "Choisir une semaine" : "Choisir un jour"}>
          <button
            type="button"
            className="day-strip__btn day-strip__arrow"
            aria-label={view === "week" ? "Semaine précédente" : "Jour précédent"}
            onClick={() => go({ date: addDays(selected, view === "week" ? -7 : -1) })}
          >
            <Icon name="chevronLeft" />
          </button>
          {view === "day" &&
            (weekendUsed || weekdayOfKey(selected) > 4 ? weekDays : weekDays.slice(0, 5)).map((d) => (
              <button key={d} type="button" className="day-strip__btn" aria-pressed={d === selected} onClick={() => go({ date: d })}>
                <span>{shortDay(weekdayOfKey(d))}</span>
                <span>{Number(d.slice(8))}</span>
              </button>
            ))}
          {view === "week" && (
            <button type="button" className="btn btn--small" onClick={() => go({ date: today })}>
              Cette semaine
            </button>
          )}
          <button
            type="button"
            className="day-strip__btn day-strip__arrow"
            aria-label={view === "week" ? "Semaine suivante" : "Jour suivant"}
            onClick={() => go({ date: addDays(selected, view === "week" ? 7 : 1) })}
          >
            <Icon name="chevronRight" />
          </button>
        </nav>
        <div className="legend" aria-label="Légende">
          {LEGEND.map((k) => (
            <span key={k} className="legend__item">
              <span className={`cal-block--${k}`} style={{ position: "static", width: 18, height: 14, borderRadius: 3, display: "inline-flex", alignItems: "center", justifyContent: "center", border: "1px solid var(--border-meaningful)" }}>
                <Icon name={KIND_ICON[k]} size={10} />
              </span>
              {KIND_LABEL[k]}
            </span>
          ))}
          <span className="legend__item">
            <Icon name="lock" size={13} /> Verrouillée
          </span>
          <Link to="/settings#couleurs" className="small">
            Couleurs des matières
          </Link>
        </div>
      </div>

      {previewMutation.error && <ErrorPanel error={previewMutation.error} />}

      <div className="split">
        <div className="split__main">
          {events.isPending || plan.isPending ? (
            <div className="card">
              <Loading lines={8} />
            </div>
          ) : events.error || plan.error ? (
            <ErrorPanel error={events.error ?? plan.error} onRetry={() => void (events.refetch(), plan.refetch())} />
          ) : view === "week" ? (
            <TimeGrid
              label="Agenda de la semaine"
              days={shownWeek}
              blocks={blocks}
              pxPerHour={44}
              onOpen={setOpen}
              onSelectDay={(d) => go({ view: "day", date: d })}
            />
          ) : (
            <TimeGrid label="Journée heure par heure" days={[selected]} blocks={blocks} pxPerHour={64} onOpen={setOpen} detailed />
          )}
        </div>

        <aside className="split__side">
          {status.data?.dirty && (
            <Card tone="warning">
              <StatusLine tone="warning" label="Planning à mettre à jour" />
              <p className="small">
                Modifié depuis l'acceptation : {status.data.changed_inputs.join(", ") || "données d'entrée"}.
              </p>
              <button type="button" className="btn btn--small" onClick={compute}>
                Voir la proposition
              </button>
            </Card>
          )}

          {view === "week" ? (
            <>
              <Card title="Charge par jour">
                {shownWeek.map((d) => {
                  const m = planned(d);
                  return (
                    <div key={d} className={`load-row${m > dailyMax ? " load-row--over" : ""}`}>
                      <span className="muted">{shortDay(weekdayOfKey(d))}</span>
                      <div className="meter" role="img" aria-label={`${shortDay(weekdayOfKey(d))} : ${formatDuration(m)} sur ${formatDuration(dailyMax)}`}>
                        <div style={{ width: `${Math.min(100, (m / dailyMax) * 100)}%` }} />
                      </div>
                      <span className="tabular strong" style={{ textAlign: "right" }}>
                        {formatDuration(m)}
                      </span>
                    </div>
                  );
                })}
                <span className="xsmall muted">Plafond fixé dans Mon profil : {formatDuration(dailyMax)} / jour.</span>
              </Card>
              {(plan.data?.warnings.length ?? 0) > 0 && (
                <Card tone="warning" title="Points d'attention">
                  <ul className="diff-list small">
                    {[...new Set(plan.data!.warnings.map(warningLabel))].map((w) => (
                      <li key={w}>
                        <Icon name="warning" size={14} /> {w}
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
              <Card title="Pourquoi ce planning ?">
                {weekReasons.length ? (
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14, lineHeight: 1.6 }}>
                    {weekReasons.map((r) => (
                      <li key={r}>{r.charAt(0).toUpperCase() + r.slice(1)}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="small muted">Aucune séance planifiée sur cette semaine.</p>
                )}
              </Card>
            </>
          ) : (
            <DaySidebar day={selected} blocks={blocks.filter((b) => b.day === selected)} dailyMax={dailyMax} freeMin={availability.data?.days[0]?.free_min} />
          )}
        </aside>
      </div>

      {open && <BlockDialog block={open} onClose={() => setOpen(null)} />}
      {preview && (
        <PreviewDialog
          preview={preview}
          onClose={() => setPreview(null)}
          onRecompute={() => {
            setPreview(null);
            compute();
          }}
        />
      )}
      {previewMutation.error && <span className="sr-only">{errorMessage(previewMutation.error)}</span>}
    </div>
  );
}

function DaySidebar({ day, blocks, dailyMax, freeMin }: { day: string; blocks: Block[]; dailyMax: number; freeMin?: number }) {
  const subjects = useSubjects();
  const tz = subjects.timezone;
  const nowMin = dayKey(new Date(), tz) === day ? minutesOfDay(new Date(), tz) : -1;
  const sorted = [...blocks].sort((a, b) => a.startMin - b.startMin);
  const next = sorted.find((b) => b.origin === "planner" && b.endMin > nowMin && b.session?.status === "planned");
  const courseMin = sorted.filter((b) => b.kind === "course").reduce((s, b) => s + b.endMin - b.startMin, 0);
  const personalMin = sorted.filter((b) => b.origin === "planner").reduce((s, b) => s + b.endMin - b.startMin, 0);
  const last = sorted.at(-1);

  return (
    <>
      {next && (
        <Card tone="navy">
          <span className="small" style={{ opacity: 0.85 }}>
            Prochaine activité · {formatClock(next.startAt, tz)}
          </span>
          <strong style={{ fontSize: 20 }}>{next.title}</strong>
          <span className="small" style={{ opacity: 0.85 }}>
            {formatClock(next.startAt, tz)} – {formatClock(next.endAt, tz)} · {formatDuration(next.endMin - next.startMin)}
          </span>
          <Link className="btn btn--primary" to={`/focus?session=${next.session!.id}`} style={{ alignSelf: "flex-start" }}>
            Lancer la Focus Room
          </Link>
        </Card>
      )}
      <Card title="Bilan de la journée">
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div className="stat">
            <span className="stat__label">Cours</span>
            <span className="stat__value" style={{ fontSize: 22 }}>{formatDuration(courseMin)}</span>
          </div>
          <div className="stat">
            <span className="stat__label">Travail perso</span>
            <span className="stat__value" style={{ fontSize: 22 }}>
              {formatDuration(personalMin)} <span className="stat__unit">/ {formatDuration(dailyMax)}</span>
            </span>
          </div>
          <div className="stat">
            <span className="stat__label">Temps libre</span>
            <span className="stat__value" style={{ fontSize: 22 }}>{freeMin === undefined ? "—" : formatDuration(Math.max(0, freeMin - personalMin))}</span>
          </div>
          <div className="stat">
            <span className="stat__label">Fin de journée</span>
            <span className="stat__value" style={{ fontSize: 22 }}>{last ? formatClock(last.endAt, tz) : "—"}</span>
          </div>
        </div>
        <div className="meter" role="img" aria-label={`Travail perso ${formatDuration(personalMin)} sur ${formatDuration(dailyMax)}`}>
          <div style={{ width: `${Math.min(100, (personalMin / dailyMax) * 100)}%` }} />
        </div>
      </Card>
      <Card title="Déroulé">
        {sorted.length === 0 ? (
          <p className="small muted">Rien de prévu ce jour-là.</p>
        ) : (
          <ol className="timeline">
            {sorted.map((b) => (
              <li key={b.id}>
                <span className="tabular">
                  <strong>{formatClock(b.startAt, tz)}</strong>
                  <br />
                  <span className="muted">{formatClock(b.endAt, tz)}</span>
                </span>
                <span>
                  <span className="timeline__title">
                    <Icon name={KIND_ICON[b.kind]} size={13} />
                    <strong>{b.title}</strong>
                    <SubjectBadge acronym={subjects.acronym(b.subjectId)} color={subjects.color(b.subjectId)} />
                  </span>
                  <span className="small muted">
                    {KIND_LABEL[b.kind]} · {formatDuration(b.endMin - b.startMin)}
                  </span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </>
  );
}


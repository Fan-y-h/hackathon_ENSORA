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
import { buildBlocks, listConflictPairs, ORIGIN_LABEL, warningLabel, type Block, type BlockKind, type SourceOrigin } from "./calendar";
import { KIND_ICON } from "./CalendarBlock";
import { SyncButton } from "../sources/SyncButton";
import { EventFormDialog } from "./EventFormDialog";
import { PreviewDialog } from "./PreviewDialog";
import { reasonLabels } from "./reasons";
import { TimeGrid } from "./TimeGrid";
import "./planner.css";

/** Ordered by weight, as on the calendar: high stakes first. */
const LEGEND: { kind: BlockKind; label: string }[] = [
  { kind: "exam", label: "Partiel" },
  { kind: "deadline", label: "Rendu" },
  { kind: "revision", label: "Révision" },
  { kind: "task", label: "Devoir" },
  { kind: "course", label: "Cours" },
  { kind: "personal", label: "Perso" },
];

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
  const weekDays = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(monday, i)), [monday]);

  const range = useMemo(() => dayRange(monday, addDays(monday, 7), tz), [monday, tz]);
  const dayAvail = useMemo(() => dayRange(selected, addDays(selected, 1), tz), [selected, tz]);

  const events = useEvents(range.start, range.end);
  const plan = usePlan();
  const status = usePlannerStatus();
  const profile = useProfile();
  const availability = useAvailability(dayAvail.start, dayAvail.end);
  const previewMutation = usePreviewPlan();

  const [horizonDays, setHorizonDays] = useState(7);
  const [originFilter, setOriginFilter] = useState<SourceOrigin | "all">("all");
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [open, setOpen] = useState<Block | null>(null);
  const [addingEvent, setAddingEvent] = useState(false);

  const allBlocks = useMemo(
    () => buildBlocks(events.data?.items ?? [], plan.data?.sessions ?? [], tz),
    [events.data, plan.data, tz],
  );

  const blocks = useMemo(
    () => (originFilter === "all" ? allBlocks : allBlocks.filter((b) => b.sourceOrigin === originFilter)),
    [allBlocks, originFilter],
  );

  const visibleWeekBlocks = useMemo(() => allBlocks.filter((b) => weekDays.includes(b.day)), [allBlocks, weekDays]);
  const conflicts = useMemo(() => listConflictPairs(visibleWeekBlocks), [visibleWeekBlocks]);
  const unconfirmed = useMemo(
    () => visibleWeekBlocks.filter((b) => b.event?.confirmation_required),
    [visibleWeekBlocks],
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
      current
        ? { horizonDays, replan: { baseRevision: current.revision, reason: "manual" } }
        : { horizonDays },
      { onSuccess: setPreview },
    );
  };

  const dailyMax = profile.data?.preferences.daily_max_min ?? 240;
  const planned = (day: string) =>
    allBlocks.filter((b) => b.origin === "planner" && b.day === day).reduce((sum, b) => sum + (b.endMin - b.startMin), 0);
  const weekPlannedMin = weekDays.reduce((s, d) => s + planned(d), 0);
  const weekReasons = [
    ...new Set(
      (plan.data?.sessions ?? [])
        .filter((s) => weekDays.includes(dayKey(s.start_at, tz)))
        .flatMap((s) => reasonLabels(s.reason)),
    ),
  ];

  const selectedDayFreeMin =
    availability.data?.days.find((d) => d.date === selected)?.free_min ?? availability.data?.days[0]?.free_min;

  const title = view === "week" ? `Semaine du ${formatShortDate(weekDays[0])}` : formatLongDate(selected);

  return (
    <div className="page">
      <PageHeader
        eyebrow={view === "week" && plan.data ? `${formatDuration(weekPlannedMin)} de travail planifié` : undefined}
        title={title}
        actions={
          <>
            <SyncButton />
            <button type="button" className="btn" onClick={() => setAddingEvent(true)}>
              <Icon name="plus" /> Événement
            </button>
            <select
              className="select"
              style={{ width: "auto" }}
              aria-label="Horizon de planification"
              value={horizonDays}
              onChange={(e) => setHorizonDays(Number(e.target.value))}
            >
              <option value={7}>Horizon 7 j</option>
              <option value={14}>Horizon 14 j</option>
              <option value={21}>Horizon 21 j</option>
            </select>
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
              {previewMutation.isPending ? "Calcul…" : plan.data ? "Recalculer" : "Planifier"}
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
              Aujourd'hui
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

        <div className="row" style={{ gap: 12 }}>
          <div className="segmented" role="group" aria-label="Filtrer par origine">
            {(
              [
                ["all", "Tous"],
                ["imported", "Importés"],
                ["personal", "Perso"],
                ["generated", "Générés"],
              ] as const
            ).map(([k, label]) => (
              <button key={k} type="button" aria-pressed={originFilter === k} onClick={() => setOriginFilter(k)}>
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="legend" aria-label="Légende">
        {LEGEND.map(({ kind, label }) => (
          <span key={kind} className="legend__item">
            <span
              className={kind === "deadline" ? "legend__swatch cal-marker__flag" : `legend__swatch cal-block cal-block--${kind}`}
              style={kind === "deadline" ? { position: "static" } : undefined}
            >
              <Icon name={KIND_ICON[kind]} size={10} />
            </span>
            {label}
          </span>
        ))}
        <span className="legend__item" title="Posé par le planner : cliquez pour déplacer ou verrouiller">
          <span className="cal-block__grip" aria-hidden="true" style={{ opacity: 1 }}>⋮⋮</span> session générée
        </span>
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
          {conflicts.length > 0 && (
            <Card title={`Conflits horaires (${conflicts.length})`} tone="warning">
              <ul className="diff-list small">
                {conflicts.map((pair) => (
                  <li key={pair.id} style={{ flexDirection: "column", gap: 4 }}>
                    <span className="strong" style={{ color: "var(--critical-text)" }}>
                      <Icon name="warning" size={13} /> {formatShortDate(pair.day)} · {formatClock(pair.a.startAt, tz)}
                    </span>
                    <span>
                      <button type="button" className="library__open" onClick={() => setOpen(pair.a)}>
                        {pair.a.title} ({ORIGIN_LABEL[pair.a.sourceOrigin]})
                      </button>{" "}
                      chevauche{" "}
                      <button type="button" className="library__open" onClick={() => setOpen(pair.b)}>
                        {pair.b.title} ({ORIGIN_LABEL[pair.b.sourceOrigin]})
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {unconfirmed.length > 0 && (
            <Card tone="warning" title={`À confirmer (${unconfirmed.length})`}>
              <ul className="diff-list small">
                {unconfirmed.map((b) => (
                  <li key={b.id}>
                    <button type="button" className="library__open" onClick={() => setOpen(b)}>
                      {b.title} · {formatShortDate(b.day)} {formatClock(b.startAt, tz)}
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {status.data?.dirty && (
            <Card tone="warning">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <StatusLine tone="warning" label="Planning à mettre à jour" />
                <button type="button" className="btn btn--small" onClick={compute}>
                  Recalculer
                </button>
              </div>
            </Card>
          )}

          {view === "week" ? (
            <>
              <Card title="Charge par jour" aside={`max ${formatDuration(dailyMax)}`}>
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
              </Card>
              {(plan.data?.warnings.length ?? 0) > 0 && (
                <Card tone="warning" title="À vérifier">
                  <ul className="diff-list small">
                    {[...new Set(plan.data!.warnings.map(warningLabel))].map((w) => (
                      <li key={w}>
                        <Icon name="warning" size={14} /> {w}
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
              {weekReasons.length > 0 && (
                <details className="card disclosure">
                  <summary className="card__title">Pourquoi ce planning ?</summary>
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14, lineHeight: 1.6 }}>
                    {weekReasons.map((r) => (
                      <li key={r}>{r.charAt(0).toUpperCase() + r.slice(1)}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          ) : (
            <DaySidebar
              day={selected}
              blocks={blocks.filter((b) => b.day === selected)}
              dailyMax={dailyMax}
              freeMin={selectedDayFreeMin}
              onOpen={setOpen}
            />
          )}
        </aside>
      </div>

      {open && <BlockDialog block={open} onClose={() => setOpen(null)} />}
      {addingEvent && <EventFormDialog initialDay={selected} onClose={() => setAddingEvent(false)} />}
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

function DaySidebar({
  day,
  blocks,
  dailyMax,
  freeMin,
  onOpen,
}: {
  day: string;
  blocks: Block[];
  dailyMax: number;
  freeMin?: number;
  onOpen: (b: Block) => void;
}) {
  const subjects = useSubjects();
  const tz = subjects.timezone;
  const nowMin = dayKey(new Date(), tz) === day ? minutesOfDay(new Date(), tz) : -1;
  const sorted = [...blocks].sort((a, b) => a.startMin - b.startMin);
  const next = sorted.find((b) => b.origin === "planner" && b.endMin > nowMin && (b.session?.status === "planned" || b.session?.status === "in_progress"));
  const courseMin = sorted.filter((b) => b.kind === "course" && !b.allDay).reduce((s, b) => s + b.endMin - b.startMin, 0);
  const personalMin = sorted.filter((b) => b.origin === "planner").reduce((s, b) => s + b.endMin - b.startMin, 0);
  const last = sorted.filter((b) => !b.allDay).at(-1);

  return (
    <>
      {next && (
        <Card tone="navy">
          <span className="small" style={{ opacity: 0.85 }}>
            Prochaine · {formatClock(next.startAt, tz)} – {formatClock(next.endAt, tz)}
          </span>
          <strong style={{ fontSize: 20 }}>{next.title}</strong>
          <Link className="btn btn--primary" to={`/focus?session=${next.session!.id}`} style={{ alignSelf: "flex-start" }}>
            <Icon name="play" /> Démarrer
          </Link>
        </Card>
      )}
      <Card title="Bilan">
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
          <p className="small muted">Rien de prévu.</p>
        ) : (
          <ol className="timeline">
            {sorted.map((b) => (
              <li key={b.id}>
                <span className="tabular">
                  {b.allDay ? (
                    <span className="muted">Journée</span>
                  ) : (
                    <>
                      <strong>{formatClock(b.startAt, tz)}</strong>
                      <br />
                      <span className="muted">{formatClock(b.endAt, tz)}</span>
                    </>
                  )}
                </span>
                <span>
                  <button type="button" className="library__open timeline__title" onClick={() => onOpen(b)}>
                    <Icon name={b.conflict ? "warning" : KIND_ICON[b.kind]} size={13} />
                    <strong>{b.title}</strong>
                    <SubjectBadge acronym={subjects.acronym(b.subjectId)} color={subjects.color(b.subjectId)} />
                  </button>
                  <span className="small muted">
                    {ORIGIN_LABEL[b.sourceOrigin]} · {b.allDay ? "Toute la journée" : b.kind === "deadline" ? "Rendu" : formatDuration(b.endMin - b.startMin)}
                    {b.conflict && " · Conflit"}
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

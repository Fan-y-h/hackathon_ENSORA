import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { errorMessage } from "../../api/client";
import { useActiveFocus, useFinishFocus, useFocusTransition, usePlan, useStartFocus } from "../../api/queries";
import type { Focus, FocusMode } from "../../api/types";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Dialog, ErrorPanel, Loading, SubjectBadge } from "../../components/ui";
import { formatClock, formatDuration } from "../../lib/time";
import { focusClock, MODES } from "./timer";
import "./focus.css";

export function FocusPage() {
  const active = useActiveFocus();
  if (active.isPending) return <div className="page"><Loading /></div>;
  if (active.error) return <div className="page"><ErrorPanel error={active.error} onRetry={() => void active.refetch()} /></div>;
  return active.data ? <Running focus={active.data} fetchedAt={active.dataUpdatedAt} /> : <StartForm />;
}

function StartForm() {
  const [params] = useSearchParams();
  const plan = usePlan();
  const subjects = useSubjects();
  const start = useStartFocus();
  const sessionId = params.get("session");
  const session = plan.data?.sessions.find((s) => s.id === sessionId);
  const [mode, setMode] = useState<FocusMode>("classic");
  const [goal, setGoal] = useState("");
  const [workMin, setWorkMin] = useState(30);
  const [breakMin, setBreakMin] = useState(5);

  useEffect(() => {
    if (session) setGoal(session.objective ?? session.title);
  }, [session]);

  return (
    <div className="focus">
      <div className="focus__inner">
        <header className="focus__header">
          <div>
            <div className="focus__eyebrow">Focus Room</div>
            <h1 className="focus__title">Préparer une séance</h1>
          </div>
        </header>

        {session && (
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{session.title}</strong>
              <SubjectBadge acronym={subjects.acronym(session.subject_id)} color={subjects.color(session.subject_id)} />
            </div>
            <span className="small muted">
              Séance planifiée {formatClock(session.start_at, subjects.timezone)} – {formatClock(session.end_at, subjects.timezone)} ·{" "}
              {formatDuration(session.estimated_min)}
            </span>
            {session.success_criteria && <span className="small">Critère de réussite : {session.success_criteria}</span>}
          </div>
        )}

        <form
          className="card stack"
          onSubmit={(e) => {
            e.preventDefault();
            start.mutate({
              mode,
              goal: goal || null,
              study_session_id: session?.id ?? null,
              task_id: session?.task_id ?? null,
              work_min: mode === "custom" ? workMin : null,
              break_min: mode === "custom" ? breakMin : null,
            });
          }}
        >
          <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="strong" style={{ marginBottom: 8 }}>Méthode</legend>
            <div className="focus__modes">
              {MODES.map((m) => (
                <label key={m.id} className={`focus__mode${mode === m.id ? " focus__mode--on" : ""}`}>
                  <input type="radio" name="mode" value={m.id} checked={mode === m.id} onChange={() => setMode(m.id)} className="sr-only" />
                  <strong>{m.label}</strong>
                  <span className="xsmall muted">{m.detail}</span>
                </label>
              ))}
            </div>
          </fieldset>
          {mode === "custom" && (
            <div className="form-grid">
              <label className="field">
                <span>Travail (min)</span>
                <input className="input" type="number" min={5} max={180} value={workMin} onChange={(e) => setWorkMin(Number(e.target.value))} />
              </label>
              <label className="field">
                <span>Pause (min)</span>
                <input className="input" type="number" min={1} max={60} value={breakMin} onChange={(e) => setBreakMin(Number(e.target.value))} />
              </label>
            </div>
          )}
          <label className="field">
            <span>Objectif de la séance</span>
            <input className="input" value={goal} onChange={(e) => setGoal(e.target.value)} maxLength={500} placeholder="Ex. restituer le test de Wald sans notes" />
          </label>
          {start.error && <div className="status status--critical">{errorMessage(start.error)}</div>}
          <button type="submit" className="btn btn--primary" disabled={start.isPending}>
            <Icon name="play" /> Commencer
          </button>
          {!session && (
            <span className="xsmall muted">
              Astuce : lancez une séance depuis le <Link to="/planner">Smart Planner</Link> pour la relier à votre planning.
            </span>
          )}
        </form>
      </div>
    </div>
  );
}

function Running({ focus, fetchedAt }: { focus: Focus; fetchedAt: number }) {
  const transition = useFocusTransition();
  const [now, setNow] = useState(() => Date.now());
  const [finishing, setFinishing] = useState(false);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const clock = focusClock(focus, fetchedAt, now);
  const R = 104;
  const C = 2 * Math.PI * R;
  const paused = focus.status === "paused";
  const onBreak = focus.status === "break";
  const act = (action: "pause" | "resume" | "break" | "work" | "extend", minutes?: number) =>
    transition.mutate({ id: focus.id, action, minutes });

  return (
    <div className="focus">
      <div className="focus__inner">
        <header className="focus__header">
          <div>
            <div className="focus__eyebrow">Focus Room · {MODES.find((m) => m.id === focus.mode)?.label}</div>
            <div className="strong">{onBreak ? "Pause" : paused ? "En pause" : "Concentration"}</div>
          </div>
          <button type="button" className="btn" aria-label="Terminer la session" onClick={() => setFinishing(true)}>
            <Icon name="close" />
          </button>
        </header>

        <div className="focus__timer">
          <svg width="232" height="232" viewBox="0 0 232 232" role="img" aria-label={`${clock.label} ${clock.countdown ? "restantes" : "écoulées"}`}>
            <circle cx="116" cy="116" r={R} fill="none" stroke="var(--focus-track)" strokeWidth="10" />
            {clock.progress !== null && (
              <circle
                cx="116"
                cy="116"
                r={R}
                fill="none"
                stroke={onBreak ? "var(--type-task)" : "var(--accent)"}
                strokeWidth="10"
                strokeLinecap="round"
                strokeDasharray={C}
                strokeDashoffset={C * (1 - clock.progress)}
                transform="rotate(-90 116 116)"
              />
            )}
            <text x="116" y="120" textAnchor="middle" fontSize="52" fontWeight="600" fill="var(--text-title)">
              {clock.label}
            </text>
            <text x="116" y="150" textAnchor="middle" fontSize="13" fill="var(--text-2)">
              {formatDuration(focus.active_min)} de travail effectif
            </text>
          </svg>
          <div className="focus__goal">{focus.goal || "Séance libre"}</div>
          {clock.countdown && clock.remainingSec === 0 && (
            <div className="status status--good" role="status">
              <Icon name="check" /> {onBreak ? "Pause terminée : reprenez quand vous voulez." : "Cycle terminé : prenez une pause."}
            </div>
          )}
        </div>

        {transition.error && <div className="status status--critical">{errorMessage(transition.error)}</div>}

        <div className="row" style={{ justifyContent: "center" }}>
          {paused ? (
            <button type="button" className="btn" onClick={() => act("resume")} disabled={transition.isPending}>
              <Icon name="play" /> Reprendre
            </button>
          ) : (
            <button type="button" className="btn" onClick={() => act("pause")} disabled={transition.isPending || onBreak}>
              <Icon name="pause" /> Pause
            </button>
          )}
          {onBreak ? (
            <button type="button" className="btn" onClick={() => act("work")} disabled={transition.isPending}>
              Reprendre le travail
            </button>
          ) : (
            <button type="button" className="btn" onClick={() => act("break")} disabled={transition.isPending || paused}>
              Pause café
            </button>
          )}
          <button type="button" className="btn" onClick={() => act("extend", 5)} disabled={transition.isPending || focus.work_min === null}>
            +5 min
          </button>
        </div>

        <button type="button" className="btn btn--primary focus__finish" onClick={() => setFinishing(true)}>
          Terminer et s'évaluer
        </button>
        <p className="xsmall muted" style={{ textAlign: "center" }}>
          Le temps est enregistré par le serveur : vous pouvez recharger la page sans le perdre. Pauses exclues.
        </p>
      </div>
      {finishing && <FinishDialog focus={focus} onClose={() => setFinishing(false)} />}
    </div>
  );
}

function FinishDialog({ focus, onClose }: { focus: Focus; onClose: () => void }) {
  const finish = useFinishFocus();
  const [outcome, setOutcome] = useState<"completed" | "partial" | "abandoned">("completed");
  const [objectiveMet, setObjectiveMet] = useState(true);
  const [difficulty, setDifficulty] = useState(3);
  const [recall, setRecall] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const [notes, setNotes] = useState("");

  return (
    <Dialog title="Fin de séance" onClose={onClose}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          finish.mutate({
            id: focus.id,
            body: {
              outcome,
              remaining_min: outcome === "partial" ? remaining : null,
              reflection: { objective_met: objectiveMet, difficulty, active_recall_done: recall, notes: notes || null },
            },
          });
        }}
      >
        <label className="field">
          <span>Bilan</span>
          <select className="select" value={outcome} onChange={(e) => setOutcome(e.target.value as typeof outcome)}>
            <option value="completed">Séance terminée</option>
            <option value="partial">Partiellement terminée</option>
            <option value="abandoned">Interrompue</option>
          </select>
        </label>
        {outcome === "partial" && (
          <label className="field">
            <span>Travail restant estimé (min)</span>
            <input className="input" type="number" min={0} max={600} value={remaining} onChange={(e) => setRemaining(Number(e.target.value))} />
          </label>
        )}
        <label className="checkbox">
          <input type="checkbox" checked={objectiveMet} onChange={(e) => setObjectiveMet(e.target.checked)} />
          Objectif atteint
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={recall} onChange={(e) => setRecall(e.target.checked)} />
          J'ai fait du rappel actif (sans support)
        </label>
        <label className="field">
          <span>Difficulté ressentie : {difficulty} / 5</span>
          <input type="range" min={1} max={5} value={difficulty} onChange={(e) => setDifficulty(Number(e.target.value))} />
        </label>
        <label className="field">
          <span>Difficultés, notions à réexpliquer</span>
          <textarea className="textarea" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={4000} />
        </label>
        <p className="xsmall muted">Votre ressenti ajuste le planning ; il ne compte pas comme preuve de maîtrise.</p>
        {finish.error && <div className="status status--critical">{errorMessage(finish.error)}</div>}
        <button type="submit" className="btn btn--primary" disabled={finish.isPending}>
          Enregistrer
        </button>
      </form>
    </Dialog>
  );
}

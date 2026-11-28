import { Coffee, Pause, Play, Square, Timer } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { useCurrentFocus, useCurrentPlan, useFinishFocus, useFocusTransition, useStartFocus } from "@/api/hooksPlanning";
import type { FocusSession } from "@/api/typesPlanning";
import { useCreateNote } from "@/api/hooksWorkspace";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Button, Card, CardHeader, Dialog, ErrorPanel, PageHeader, SelectField, TextField, buttonClasses } from "@/components/ui";

import { SessionResources, useFocusNotes } from "./SessionSidePanel";

/** Seconds spent in the current interval and total work, computed from server intervals (survives a reload). */
function useElapsed(session: FocusSession | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!session || session.status !== "running") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [session]);
  if (!session) return { current: 0, work: 0 };
  const open = session.intervals.at(-1);
  const length = (i: FocusSession["intervals"][number]) => ((i.ended_at ? new Date(i.ended_at).getTime() : now) - new Date(i.started_at).getTime()) / 1000;
  return {
    current: open && !open.ended_at ? Math.floor(length(open)) : 0,
    work: Math.floor(session.intervals.filter((i) => i.kind === "work").reduce((sum, i) => sum + length(i), 0)),
  };
}

const clock = (seconds: number) => `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;

function FinishDialog({ session, sessionNotes, subjectId, onDone, onClose }: { session: FocusSession; sessionNotes: string; subjectId: string | null; onDone: () => void; onClose: () => void }) {
  const finish = useFinishFocus();
  const createNote = useCreateNote();

  async function end(reached: string, difficulty: string, notes: string) {
    await finish.mutateAsync({ id: session.id, reflection: { objective_reached: reached === "yes", difficulty: Number(difficulty), notes } });
    // Notes typed during the session become a real note, linked to the subject.
    if (sessionNotes.trim()) await createNote.mutateAsync({ title: `Séance — ${session.goal}`.slice(0, 90), subject_id: subjectId, markdown: sessionNotes });
    onDone();
    onClose();
  }
  const [reached, setReached] = useState("yes");
  const [difficulty, setDifficulty] = useState("3");
  const [notes, setNotes] = useState("");
  return (
    <Dialog
      open
      title="Bilan de la séance"
      onClose={onClose}
      footer={
        <Button loading={finish.isPending || createNote.isPending} onClick={() => void end(reached, difficulty, notes).catch(() => undefined)}>
          Terminer la séance
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted">Objectif : {session.goal}</p>
        <SelectField
          label="Objectif atteint ?"
          value={reached}
          onChange={(e) => setReached(e.target.value)}
          options={[
            { value: "yes", label: "Oui" },
            { value: "no", label: "Pas complètement" },
          ]}
        />
        <SelectField
          label="Difficulté ressentie"
          value={difficulty}
          onChange={(e) => setDifficulty(e.target.value)}
          options={["1", "2", "3", "4", "5"].map((v) => ({ value: v, label: `${v} / 5` }))}
        />
        <TextField label="Remarque (facultatif)" value={notes} onChange={(e) => setNotes(e.target.value)} />
        {sessionNotes.trim() && <p className="text-xs text-muted">Vos notes de séance seront ajoutées à Mes notes.</p>}
        <p className="text-xs text-muted">Votre ressenti ne change pas votre niveau estimé : seuls les quiz et révisions le font.</p>
        {(finish.error ?? createNote.error) && <p className="text-sm text-danger">{(finish.error ?? createNote.error)?.message}</p>}
      </div>
    </Dialog>
  );
}

/** Pomodoro or free timer. The server stores every work/break interval; breaks never count as work. */
export function FocusPage() {
  const [params] = useSearchParams();
  const sessionId = params.get("session");
  const focus = useCurrentFocus();
  const plan = useCurrentPlan();
  const start = useStartFocus();
  const transition = useFocusTransition();
  const [finishing, setFinishing] = useState(false);
  const planned = plan.data?.sessions.find((s) => s.id === sessionId);
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<"pomodoro" | "free">("pomodoro");
  const [workMin, setWorkMin] = useState("25");
  const [breakMin, setBreakMin] = useState("5");
  const session = focus.data ?? null;
  const { current, work } = useElapsed(session);
  const sessionNotes = useFocusNotes(session?.id);
  const linked = plan.data?.sessions.find((s) => s.id === (session?.study_session_id ?? sessionId));

  useEffect(() => {
    if (planned && !goal) setGoal(planned.objective);
  }, [planned, goal]);

  // Pomodoro: when the work or break time is over, suggest the next phase (never forced).
  const phase = session?.intervals.at(-1);
  const target = phase?.kind === "break" ? (session?.break_min ?? 5) * 60 : (session?.work_min ?? 25) * 60;
  const overTime = session?.mode === "pomodoro" && session.status === "running" && current >= target;

  // When a phase is over, the tab title tells it (useful when the tab is in the background),
  // then the page's own title comes back.
  useEffect(() => {
    if (!overTime) return;
    const previous = document.title;
    document.title = phase?.kind === "break" ? "Pause terminée · ENSORA+" : "Temps de pause · ENSORA+";
    return () => {
      document.title = previous;
    };
  }, [overTime, phase?.kind]);

  function submit(event: FormEvent) {
    event.preventDefault();
    start.mutate({ goal, mode, work_min: Number(workMin) || 25, break_min: Number(breakMin) || 5, study_session_id: sessionId });
  }

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader title="Focus Room" icon={<Timer className="size-5" aria-hidden="true" />} description="Un minuteur pour travailler concentré. Le temps de travail réel alimente vos statistiques." />
      {focus.isPending && <QueryStatus query={focus} />}

      {!focus.isPending && !session && (
        <Card className="mx-auto w-full max-w-xl">
          <CardHeader title="Nouvelle séance" />
          {planned && (
            <p className="mb-4 text-sm">
              Session prévue : <span className="font-medium">{planned.title}</span>
            </p>
          )}
          <form onSubmit={submit} className="flex flex-col gap-4">
            <TextField label="Objectif de la séance" required value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Ex. : refaire l'exercice 2 du TD 4" />
            <SelectField
              label="Mode"
              value={mode}
              onChange={(e) => setMode(e.target.value as "pomodoro" | "free")}
              options={[
                { value: "pomodoro", label: "Pomodoro (travail / pause)" },
                { value: "free", label: "Libre (chronomètre)" },
              ]}
            />
            {mode === "pomodoro" && (
              <div className="grid grid-cols-2 gap-4">
                <TextField label="Travail (min)" type="number" min={5} value={workMin} onChange={(e) => setWorkMin(e.target.value)} />
                <TextField label="Pause (min)" type="number" min={1} value={breakMin} onChange={(e) => setBreakMin(e.target.value)} />
              </div>
            )}
            {start.error && <ErrorPanel message={start.error.message} />}
            <Button type="submit" loading={start.isPending} className="self-end">
              <Play className="size-4" aria-hidden="true" />
              Commencer
            </Button>
          </form>
        </Card>
      )}

      {session && (
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card className="flex w-full flex-col items-center gap-4 text-center">
          <Badge tone={session.status === "paused" ? "warning" : phase?.kind === "break" ? "success" : "accent"}>{session.status === "paused" ? "En pause" : phase?.kind === "break" ? "Pause" : "Travail"}</Badge>
          <p className="text-sm text-muted">{session.goal}</p>
          <p className="text-6xl font-semibold tabular-nums" role="timer" aria-live="off" aria-label={`Temps écoulé : ${Math.floor(current / 60)} minutes`}>
            {session.mode === "pomodoro" && session.status === "running" ? clock(Math.max(0, target - current)) : clock(current)}
          </p>
          {overTime && (
            <p role="status" className="text-sm font-medium text-accent">
              {phase?.kind === "break" ? "Pause terminée : on reprend ?" : "Bon travail ! C'est le moment de faire une pause."}
            </p>
          )}
          <p className="text-sm text-muted">Travail effectif : {Math.floor(work / 60)} min</p>
          <div className="flex flex-wrap justify-center gap-2">
            {session.status === "paused" || phase?.kind === "break" ? (
              <Button loading={transition.isPending} onClick={() => transition.mutate({ id: session.id, to: "work" })}>
                <Play className="size-4" aria-hidden="true" />
                Reprendre le travail
              </Button>
            ) : (
              <>
                <Button variant="secondary" loading={transition.isPending} onClick={() => transition.mutate({ id: session.id, to: "break" })}>
                  <Coffee className="size-4" aria-hidden="true" />
                  Pause
                </Button>
                <Button variant="ghost" onClick={() => transition.mutate({ id: session.id, to: "pause" })}>
                  <Pause className="size-4" aria-hidden="true" />
                  Suspendre
                </Button>
              </>
            )}
            <Button variant="danger" onClick={() => setFinishing(true)}>
              <Square className="size-4" aria-hidden="true" />
              Terminer
            </Button>
          </div>
          {transition.error && <ErrorPanel message={transition.error.message} />}
          {session.study_session_id && (
            <Link to="/planner" className={buttonClasses("ghost", "sm")}>
              Après la séance, notez le résultat dans le planning
            </Link>
          )}
        </Card>
        <div className="flex flex-col gap-5">
          {linked && <SessionResources session={linked} />}
          <Card>
            <CardHeader title="Notes de séance" description="Gardées si vous rechargez la page, ajoutées à Mes notes à la fin." />
            <label htmlFor="focus-notes" className="sr-only">
              Notes de séance
            </label>
            <textarea id="focus-notes" rows={8} value={sessionNotes.notes} onChange={(e) => sessionNotes.update(e.target.value)} placeholder="Idées, questions pour le TD, formules à retenir…" className="focus-ring w-full rounded-lg border border-line bg-surface p-3 text-sm" />
          </Card>
        </div>
        </div>
      )}
      {finishing && session && (
        <FinishDialog session={session} sessionNotes={sessionNotes.notes} subjectId={linked?.subject_id ?? null} onDone={sessionNotes.clear} onClose={() => setFinishing(false)} />
      )}
    </section>
  );
}

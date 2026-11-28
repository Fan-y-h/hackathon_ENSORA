import { Clock } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useAttempt, useAttemptResult, useSaveResponses, useSubmitAttempt } from "@/api/hooksLearning";
import type { AttemptQuestion, AttemptResponseValue } from "@/api/typesLearning";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, Card, ConfirmDialog, ErrorPanel } from "@/components/ui";
import { cn } from "@/lib/cn";

import { ResultView } from "./ResultView";

function QuestionInput({ question, value, onChange, disabled }: { question: AttemptQuestion; value: AttemptResponseValue | undefined; onChange: (v: AttemptResponseValue) => void; disabled: boolean }) {
  const name = `q-${question.id}`;
  if (question.type === "single" || question.type === "true_false") {
    return (
      <div className="flex flex-col gap-2" role="radiogroup" aria-labelledby={`${name}-label`}>
        {question.options?.map((option) => (
          <label key={option.id} className={cn("flex cursor-pointer items-start gap-2.5 rounded-lg border p-3 text-sm has-focus-visible:outline-2 has-focus-visible:outline-accent", value === option.id ? "border-accent bg-accent-soft/40" : "border-line")}>
            <input type="radio" name={name} value={option.id} checked={value === option.id} disabled={disabled} onChange={() => onChange(option.id)} className="mt-0.5 accent-(--accent)" />
            {option.label}
          </label>
        ))}
      </div>
    );
  }
  if (question.type === "multiple") {
    const selected = Array.isArray(value) ? value : [];
    return (
      <div className="flex flex-col gap-2" role="group" aria-labelledby={`${name}-label`}>
        <p className="text-xs text-muted">Plusieurs réponses possibles.</p>
        {question.options?.map((option) => (
          <label key={option.id} className={cn("flex cursor-pointer items-start gap-2.5 rounded-lg border p-3 text-sm has-focus-visible:outline-2 has-focus-visible:outline-accent", selected.includes(option.id) ? "border-accent bg-accent-soft/40" : "border-line")}>
            <input
              type="checkbox"
              checked={selected.includes(option.id)}
              disabled={disabled}
              onChange={(e) => onChange(e.target.checked ? [...selected, option.id] : selected.filter((id) => id !== option.id))}
              className="mt-0.5 accent-(--accent)"
            />
            {option.label}
          </label>
        ))}
      </div>
    );
  }
  const text = typeof value === "string" ? value : "";
  return question.type === "open" ? (
    <textarea aria-labelledby={`${name}-label`} rows={4} value={text} disabled={disabled} onChange={(e) => onChange(e.target.value)} className="focus-ring w-full rounded-lg border border-line bg-surface p-3 text-sm" />
  ) : (
    <input aria-labelledby={`${name}-label`} type="text" value={text} disabled={disabled} onChange={(e) => onChange(e.target.value)} className="focus-ring h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm" />
  );
}

/** Remaining time from the server's expires_at. The server decides; this only displays it. */
function useCountdown(expiresAt: string | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);
  return expiresAt ? Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / 1000)) : null;
}

interface AttemptRunnerProps {
  attemptId: string;
  /** Exam mode: countdown, no hints, automatic submission at the end of time. */
  examMode?: boolean;
  onExit: () => void;
}

/** Answers are saved as the student goes; the answer key only arrives after submission. */
export function AttemptRunner({ attemptId, examMode = false, onExit }: AttemptRunnerProps) {
  const attempt = useAttempt(attemptId);
  const save = useSaveResponses(attemptId);
  const submit = useSubmitAttempt(attemptId);
  const closed = attempt.data !== undefined && attempt.data.status !== "active";
  const result = useAttemptResult(attemptId, closed || submit.isSuccess);
  const [responses, setResponses] = useState<Record<string, AttemptResponseValue>>({});
  const [confirming, setConfirming] = useState(false);
  const pendingSave = useRef<number | undefined>(undefined);
  const remaining = useCountdown(attempt.data?.status === "active" ? attempt.data.expires_at : null);

  // Resume: start from what the server already has.
  useEffect(() => {
    if (attempt.data) setResponses((current) => ({ ...attempt.data.responses, ...current }));
  }, [attempt.data]);

  function answer(questionId: string, value: AttemptResponseValue) {
    const next = { ...responses, [questionId]: value };
    setResponses(next);
    window.clearTimeout(pendingSave.current);
    // Send every answer given so far (not only the last one): a quick series of answers
    // followed by "Reprendre plus tard" must not lose any of them.
    pendingSave.current = window.setTimeout(() => save.mutate(next), 500);
  }

  async function finish() {
    window.clearTimeout(pendingSave.current);
    setConfirming(false);
    try {
      await save.mutateAsync(responses);
    } catch {
      // Time is over or already closed: the server keeps what it had; grade it anyway.
    }
    submit.mutate();
  }

  // Time over: submit automatically (the server also refuses late answers).
  useEffect(() => {
    if (remaining === 0 && !submit.isPending && !submit.isSuccess) void finish();
    // finish() reads the latest responses through state on each render.
  }, [remaining]);

  if (submit.data || result.data) return <ResultView result={(submit.data ?? result.data)!} onExit={onExit} />;
  if (!attempt.data) return <QueryStatus query={attempt} />;
  if (closed) return <QueryStatus query={result} loadingLabel="Correction en cours…" />;

  const questions = attempt.data.questions;
  const unanswered = questions.filter((q) => {
    const v = responses[q.id];
    return v === undefined || v === "" || (Array.isArray(v) && v.length === 0);
  }).length;
  const minutes = remaining === null ? null : Math.floor(remaining / 60);
  const seconds = remaining === null ? null : remaining % 60;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <div className="sticky top-16 z-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface px-4 py-2.5 text-sm">
        <span>
          {questions.length - unanswered} / {questions.length} réponses
          <span className="ml-2 text-xs text-muted" aria-live="polite">
            {save.isPending ? "Enregistrement…" : save.isError ? "Non enregistré" : "Enregistré"}
          </span>
        </span>
        {remaining !== null && (
          <span className={cn("inline-flex items-center gap-1.5 font-semibold tabular-nums", remaining < 300 ? "text-danger" : "")} role="timer" aria-label={`Temps restant : ${minutes} minutes`}>
            <Clock className="size-4" aria-hidden="true" />
            {String(minutes).padStart(2, "0")}:{String(seconds).padStart(2, "0")}
          </span>
        )}
      </div>

      {questions.map((question, index) => (
        <Card key={question.id}>
          <p id={`q-${question.id}-label`} className="mb-3 font-medium">
            <span className="text-muted">Question {index + 1}.</span> {question.prompt}{" "}
            <span className="text-xs font-normal text-muted">
              ({question.points} pt{question.points > 1 ? "s" : ""})
            </span>
          </p>
          <QuestionInput question={question} value={responses[question.id]} onChange={(v) => answer(question.id, v)} disabled={submit.isPending} />
        </Card>
      ))}

      {submit.error && <ErrorPanel message={submit.error.message} onRetry={() => submit.mutate()} />}
      <div className="flex flex-wrap justify-end gap-2">
        {!examMode && (
          <Button variant="ghost" onClick={onExit}>
            Reprendre plus tard
          </Button>
        )}
        <Button loading={submit.isPending} onClick={() => (unanswered > 0 ? setConfirming(true) : void finish())}>
          Rendre ma copie
        </Button>
      </div>

      <ConfirmDialog
        open={confirming}
        title="Rendre la copie ?"
        description={`${unanswered} question${unanswered > 1 ? "s" : ""} sans réponse. Une fois rendue, la copie ne peut plus être modifiée.`}
        confirmLabel="Rendre quand même"
        onConfirm={() => void finish()}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
}

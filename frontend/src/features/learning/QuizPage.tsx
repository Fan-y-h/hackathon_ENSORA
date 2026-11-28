import { useEffect, useState } from "react";

import { errorMessage } from "../../api/client";
import { useQuizzes, useSaveResponses, useStartAttempt, useSubmitAttempt } from "../../api/queries";
import type { Attempt, AttemptResult, Question } from "../../api/types";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Card, EmptyState, PageHeader, QueryState, SubjectBadge } from "../../components/ui";
import "./learning.css";

type Answers = Record<string, unknown>;

export function QuizPage() {
  const quizzes = useQuizzes();
  const subjects = useSubjects();
  const start = useStartAttempt();
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [result, setResult] = useState<AttemptResult | null>(null);

  if (result && attempt) {
    return <ResultView attempt={attempt} result={result} onBack={() => (setAttempt(null), setResult(null))} />;
  }
  if (attempt) return <AttemptView attempt={attempt} onSubmitted={setResult} />;

  return (
    <div className="page">
      <PageHeader eyebrow="Quiz" title="Quiz et diagnostics" />
      {start.error && <div className="status status--critical">{errorMessage(start.error)}</div>}
      <Card title="Quiz disponibles" subtitle="Mode examen : temps limité, sans corrigé ni AI Tutor jusqu'à la remise.">
        <QueryState query={quizzes}>
          {(list) =>
            list.length === 0 ? (
              <EmptyState title="Aucun quiz" />
            ) : (
              <ul className="list">
                {list.map((q) => (
                  <li key={q.id}>
                    <SubjectBadge acronym={subjects.acronym(q.subject_id)} color={subjects.color(q.subject_id)} />
                    <div className="stack" style={{ gap: 2, flex: 1 }}>
                      <strong className="small">{q.title}</strong>
                      <span className="xsmall muted">
                        {q.question_count} question(s) · {q.total_points} pt · {q.type}
                        {q.time_limit_min ? ` · ${q.time_limit_min} min` : ""}
                      </span>
                    </div>
                    <button type="button" className="btn btn--small" disabled={start.isPending} onClick={() => start.mutate({ quizId: q.id, mode: "practice" }, { onSuccess: setAttempt })}>
                      S'entraîner
                    </button>
                    <button type="button" className="btn btn--small" disabled={start.isPending} onClick={() => start.mutate({ quizId: q.id, mode: "exam" }, { onSuccess: setAttempt })}>
                      <Icon name="clock" size={14} /> Mode examen
                    </button>
                  </li>
                ))}
              </ul>
            )
          }
        </QueryState>
      </Card>
    </div>
  );
}

function useCountdown(expiresAt: string | null | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [expiresAt]);
  if (!expiresAt) return null;
  return Math.max(0, Math.round((new Date(expiresAt).getTime() - now) / 1000));
}

function AttemptView({ attempt, onSubmitted }: { attempt: Attempt; onSubmitted: (r: AttemptResult) => void }) {
  const [answers, setAnswers] = useState<Answers>(() => ({ ...(attempt.responses as Answers) }));
  const save = useSaveResponses();
  const submit = useSubmitAttempt();
  const remaining = useCountdown(attempt.expires_at);
  const questions = attempt.questions as Question[];

  const set = (id: string, value: unknown) => setAnswers((a) => ({ ...a, [id]: value }));

  const doSubmit = async () => {
    const responses = Object.entries(answers)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([question_id, response]) => ({ question_id, response }));
    if (responses.length) await save.mutateAsync({ attemptId: attempt.id, responses });
    submit.mutate(attempt.id, { onSuccess: onSubmitted });
  };

  return (
    <div className="page" style={{ maxWidth: 820 }}>
      <PageHeader
        eyebrow={attempt.mode === "exam" ? "Mode examen" : "Entraînement"}
        title="Répondez aux questions"
        actions={
          remaining !== null && (
            <span className="pill" role="timer" aria-live="off">
              <Icon name="clock" size={14} /> {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, "0")}
            </span>
          )
        }
      />
      <form
        className="card"
        onSubmit={(e) => {
          e.preventDefault();
          void doSubmit();
        }}
      >
        {questions.map((q, i) => (
          <QuestionField key={q.id} index={i} question={q} value={answers[q.id]} onChange={(v) => set(q.id, v)} />
        ))}
        {(save.error || submit.error) && <div className="status status--critical">{errorMessage(save.error ?? submit.error)}</div>}
        <button type="submit" className="btn btn--primary" disabled={save.isPending || submit.isPending}>
          Remettre ma copie
        </button>
        {attempt.mode === "exam" && <span className="xsmall muted">Le chronomètre fait foi côté serveur ; une copie expirée est corrigée telle quelle.</span>}
      </form>
    </div>
  );
}

function QuestionField({ question: q, index, value, onChange }: { question: Question; index: number; value: unknown; onChange: (v: unknown) => void }) {
  const name = `q-${q.id}`;
  const options = (q.options ?? []) as { id: string; text: string }[];
  return (
    <fieldset className="quiz-q" style={{ border: 0, margin: 0 }}>
      <legend>
        {index + 1}. {q.prompt} <span className="xsmall muted">({q.points} pt)</span>
      </legend>
      {q.type === "true_false" &&
        [
          [true, "Vrai"],
          [false, "Faux"],
        ].map(([v, label]) => (
          <label key={String(v)} className="quiz-opt">
            <input type="radio" name={name} checked={value === v} onChange={() => onChange(v)} />
            {label}
          </label>
        ))}
      {q.type === "single_choice" &&
        options.map((o) => (
          <label key={o.id} className="quiz-opt">
            <input type="radio" name={name} checked={value === o.id} onChange={() => onChange(o.id)} />
            {o.text}
          </label>
        ))}
      {q.type === "multiple_choice" &&
        options.map((o) => {
          const current = Array.isArray(value) ? (value as string[]) : [];
          return (
            <label key={o.id} className="quiz-opt">
              <input
                type="checkbox"
                checked={current.includes(o.id)}
                onChange={(e) => onChange(e.target.checked ? [...current, o.id] : current.filter((x) => x !== o.id))}
              />
              {o.text}
            </label>
          );
        })}
      {q.type === "numeric" && (
        <input className="input" type="number" step="any" value={(value as number | undefined) ?? ""} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} aria-label="Réponse numérique" />
      )}
      {q.type === "fill_blank" && <input className="input" value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} aria-label="Réponse" />}
      {q.type === "open" && <textarea className="textarea" value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} aria-label="Réponse rédigée" />}
    </fieldset>
  );
}

function formatAnswer(value: unknown, q?: Question): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Vrai" : "Faux";
  const options = (q?.options ?? []) as { id: string; text: string }[];
  const text = (id: unknown) => options.find((o) => o.id === id)?.text ?? String(id);
  if (Array.isArray(value)) return value.map(text).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return text(value);
}

function ResultView({ attempt, result, onBack }: { attempt: Attempt; result: AttemptResult; onBack: () => void }) {
  const questions = attempt.questions as Question[];
  return (
    <div className="page" style={{ maxWidth: 820 }}>
      <PageHeader
        eyebrow={result.status === "expired" ? "Copie expirée" : "Copie corrigée"}
        title={`${result.score} / ${result.max_score} points`}
        actions={<button type="button" className="btn" onClick={onBack}>Retour aux quiz</button>}
      />
      {result.pending_review > 0 && <div className="status status--warning">{result.pending_review} réponse(s) ouverte(s) en attente de correction.</div>}
      <Card title="Correction">
        {result.items.map((item, i) => {
          const q = questions.find((x) => x.id === item.question_id);
          const ok = (item.earned ?? 0) >= item.points;
          return (
            <div key={item.question_id} className="quiz-q">
              <strong>
                {i + 1}. {q?.prompt}
              </strong>
              <span className={ok ? "quiz-result--ok" : "quiz-result--ko"}>
                <Icon name={ok ? "check" : "alert"} size={14} /> {ok ? "Correct" : item.status === "pending_review" ? "À corriger" : "Incorrect"} ·{" "}
                {item.earned} / {item.points}
              </span>
              <span className="small">Votre réponse : {formatAnswer(item.response, q)}</span>
              {!ok && <span className="small">Réponse attendue : {formatAnswer(item.correct_answer, q)}</span>}
              {item.explanation && <span className="small muted">{item.explanation}</span>}
            </div>
          );
        })}
      </Card>
      {result.recommendations.length > 0 && (
        <Card title="À retravailler">
          <ul className="list">
            {result.recommendations.map((r) => (
              <li key={String(r.concept_id)}>
                <strong className="small">{String(r.concept_id)}</strong>
                <span className="small muted">{String(r.suggestion ?? "")}</span>
              </li>
            ))}
          </ul>
          {result.errors_logged > 0 && <span className="xsmall muted">{result.errors_logged} erreur(s) ajoutée(s) au carnet d'erreurs.</span>}
        </Card>
      )}
    </div>
  );
}

import { CheckCircle2, CircleHelp, XCircle } from "lucide-react";
import { Link } from "react-router";

import type { AttemptResult } from "@/api/typesLearning";
import { useConcepts } from "@/api/hooks";
import { useQuizzes } from "@/api/hooksLearning";
import { CitationList } from "@/components/Citations";
import { Badge, Button, Card, buttonClasses } from "@/components/ui";
import { formatPercent } from "@/lib/format";

/** Correction after submission: score, expected answers, explanations, sources and mastery change. */
export function ResultView({ result, onExit }: { result: AttemptResult; onExit: () => void }) {
  const quizzes = useQuizzes();
  const concepts = useConcepts(quizzes.data?.find((q) => q.id === result.quiz_id)?.subject_id ?? "");
  const wrong = result.items.filter((i) => i.correct === false).length;
  const open = result.items.filter((i) => i.correct === null).length;
  const label = (id: string) => concepts.data?.find((c) => c.id === id)?.label;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <Card className="text-center">
        <p className="text-sm text-muted">Résultat</p>
        <p className="mt-1 text-3xl font-semibold tabular-nums">
          {result.score} / {result.max_score}
        </p>
        <p className="mt-1 text-sm text-muted">
          {open > 0 && `${open} réponse${open > 1 ? "s" : ""} ouverte${open > 1 ? "s" : ""} à comparer vous-même avec la réponse attendue. `}
          {wrong > 0 && `${wrong} erreur${wrong > 1 ? "s" : ""} ajoutée${wrong > 1 ? "s" : ""} au carnet d'erreurs.`}
        </p>
        {result.mastery_updates.length > 0 && (
          <div className="mt-4 text-left">
            <p className="mb-1 text-sm font-medium">Niveau mis à jour</p>
            <ul className="flex flex-col gap-1 text-sm">
              {result.mastery_updates.map((u, i) => (
                <li key={`${u.concept_id}-${i}`} className="flex justify-between gap-3">
                  <span>{label(u.concept_id) ?? "Notion"}</span>
                  <span className="text-muted tabular-nums">
                    {u.previous_score === null ? "Non évalué" : formatPercent(u.previous_score)} → <span className="font-medium text-ink">{u.score === null ? "Non évalué" : formatPercent(u.score)}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {wrong > 0 && (
            <Link to="/notes?tab=errors" className={buttonClasses("secondary", "sm")}>
              Voir le carnet d'erreurs
            </Link>
          )}
          <Link to="/planner" className={buttonClasses("secondary", "sm")}>
            Adapter mon planning
          </Link>
          <Button size="sm" onClick={onExit}>
            Terminer
          </Button>
        </div>
      </Card>

      {result.items.map((item, index) => (
        <Card key={item.question_id}>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {item.correct === true && <Badge tone="success" icon={<CheckCircle2 className="size-3" aria-hidden="true" />}>Juste</Badge>}
            {item.correct === false && <Badge tone="danger" icon={<XCircle className="size-3" aria-hidden="true" />}>Faux</Badge>}
            {item.correct === null && <Badge tone="warning" icon={<CircleHelp className="size-3" aria-hidden="true" />}>À auto-évaluer</Badge>}
            <span className="text-xs text-muted">
              {item.score ?? "–"} / {item.points} pt
            </span>
          </div>
          <p className="font-medium">
            <span className="text-muted">Question {index + 1}.</span> {item.prompt}
          </p>
          <dl className="mt-2 grid gap-1 text-sm">
            <div>
              <dt className="inline text-muted">Votre réponse : </dt>
              <dd className="inline">{item.response === null || item.response === "" ? "—" : Array.isArray(item.response) ? item.response.join(", ") : item.response}</dd>
            </div>
            <div>
              <dt className="inline text-muted">Réponse attendue : </dt>
              <dd className="inline font-medium">{item.expected}</dd>
            </div>
          </dl>
          <p className="mt-2 text-sm">{item.explanation}</p>
          <CitationList citations={item.citations} />
        </Card>
      ))}
    </div>
  );
}

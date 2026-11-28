import { CheckCircle2 } from "lucide-react";
import { useEffect, useState } from "react";

import { useConcepts, useSubjects } from "@/api/hooks";
import { useDueCards, useReviewCard } from "@/api/hooksLearning";
import type { Deck, MasteryUpdate, ReviewRating } from "@/api/typesLearning";
import { CitationList } from "@/components/Citations";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, Card, EmptyState, ErrorPanel } from "@/components/ui";
import { formatPercent } from "@/lib/format";

const RATINGS: Array<{ id: ReviewRating; label: string; hint: string; variant: "danger" | "secondary" | "primary" }> = [
  { id: "again", label: "À revoir", hint: "1", variant: "danger" },
  { id: "hard", label: "Difficile", hint: "2", variant: "secondary" },
  { id: "good", label: "Correct", hint: "3", variant: "secondary" },
  { id: "easy", label: "Facile", hint: "4", variant: "primary" },
];

/** Due cards one by one: reveal, then rate. Each rating is sent to the server, which schedules the next review. */
export function ReviewSession({ deck, onExit }: { deck: Deck; onExit: () => void }) {
  const due = useDueCards(deck.id);
  const review = useReviewCard();
  const subjects = useSubjects();
  const concepts = useConcepts(deck.subject_id);
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [updates, setUpdates] = useState<MasteryUpdate[]>([]);
  const [lastInterval, setLastInterval] = useState<number | null>(null);

  const cards = due.data ?? [];
  const card = cards[index];
  const finished = due.data !== undefined && index >= cards.length;

  function rate(rating: ReviewRating) {
    if (!card || review.isPending) return;
    review.mutate(
      { card, rating },
      {
        onSuccess: (result) => {
          setUpdates((u) => [...u, ...result.mastery_updates]);
          setLastInterval(result.interval_days);
          setRevealed(false);
          setIndex((i) => i + 1);
        },
      },
    );
  }

  // Keyboard: Space reveals, 1-4 rate.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === " " && !revealed) {
        event.preventDefault();
        setRevealed(true);
      } else if (revealed && ["1", "2", "3", "4"].includes(event.key)) {
        rate(RATINGS[Number(event.key) - 1].id);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (due.isPending || due.error) return <QueryStatus query={due} />;

  if (cards.length === 0) {
    return <EmptyState title="Aucune carte à réviser maintenant" description="Revenez plus tard : les prochaines révisions sont planifiées." action={<Button onClick={onExit}>Retour aux paquets</Button>} />;
  }

  if (finished) {
    // Latest value per concept, to show the change once.
    const byConcept = new Map(updates.map((u) => [u.concept_id, u]));
    const firstBefore = new Map<string, number | null>();
    for (const u of updates) if (!firstBefore.has(u.concept_id)) firstBefore.set(u.concept_id, u.previous_score);
    const label = (id: string) => concepts.data?.find((c) => c.id === id)?.label ?? "Notion";
    return (
      <Card className="flex flex-col items-center gap-4 text-center">
        <CheckCircle2 className="size-10 text-success" aria-hidden="true" />
        <h2 className="text-lg font-semibold">Révision terminée : {cards.length} carte{cards.length > 1 ? "s" : ""}</h2>
        {byConcept.size > 0 && (
          <div className="w-full max-w-md text-left">
            <p className="mb-2 text-sm font-medium">Mémorisation mise à jour</p>
            <ul className="flex flex-col gap-1 text-sm">
              {[...byConcept.values()].map((u) => {
                const before = firstBefore.get(u.concept_id);
                return (
                  <li key={u.concept_id} className="flex justify-between gap-3">
                    <span>{label(u.concept_id)}</span>
                    <span className="tabular-nums text-muted">
                      {before === null || before === undefined ? "Non évalué" : formatPercent(before)} → <span className="font-medium text-ink">{u.score === null ? "Non évalué" : formatPercent(u.score)}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        <Button onClick={onExit}>Retour aux paquets</Button>
      </Card>
    );
  }

  const subject = subjects.data?.find((s) => s.id === deck.subject_id);
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-4">
      <div className="flex items-center justify-between text-sm text-muted">
        <span>
          {deck.title}
          {subject && ` · ${subject.title}`}
        </span>
        <span aria-live="polite">
          Carte {index + 1} / {cards.length}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
        <div className="h-full bg-accent transition-[width]" style={{ width: `${(index / cards.length) * 100}%` }} />
      </div>

      <Card className="flex min-h-64 flex-col justify-center gap-4 text-center">
        <p className="text-xs font-semibold tracking-wider text-muted uppercase">Question</p>
        <p className="text-lg font-medium">{card.front}</p>
        {revealed && (
          <div className="border-t border-line pt-4" aria-live="polite">
            <p className="text-xs font-semibold tracking-wider text-muted uppercase">Réponse</p>
            <p className="mt-2">{card.back}</p>
            <CitationList citations={card.citations} />
          </div>
        )}
      </Card>

      {!revealed ? (
        <Button className="self-center" onClick={() => setRevealed(true)}>
          Afficher la réponse <kbd className="ml-1 rounded border border-current/40 px-1 text-xs">Espace</kbd>
        </Button>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" role="group" aria-label="Comment avez-vous répondu ?">
          {RATINGS.map((r) => (
            <Button key={r.id} variant={r.variant} loading={review.isPending && review.variables?.rating === r.id} disabled={review.isPending} onClick={() => rate(r.id)}>
              {r.label} <kbd className="ml-1 rounded border border-current/40 px-1 text-xs">{r.hint}</kbd>
            </Button>
          ))}
        </div>
      )}
      {lastInterval !== null && (
        <p className="text-center text-xs text-muted" aria-live="polite">
          Carte précédente : prochaine révision dans {lastInterval} jour{lastInterval > 1 ? "s" : ""}.
        </p>
      )}
      {review.error && <ErrorPanel message={review.error.message} />}
      <Button variant="ghost" className="self-center" onClick={onExit}>
        Arrêter la révision
      </Button>
    </div>
  );
}

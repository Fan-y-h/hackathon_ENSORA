import { useMemo, useState } from "react";

import { errorMessage, newIdempotencyKey } from "../../api/client";
import { useCreateCard, useCreateDeck, useDecks, useDueCards, useReviewCard } from "../../api/queries";
import type { Card as CardT, Rating, ReviewResult } from "../../api/types";
import { useSubjects } from "../../app/subjects";
import { Card, EmptyState, PageHeader, QueryState, SubjectBadge } from "../../components/ui";
import "./learning.css";

const RATINGS: { id: Rating; label: string; hint: string }[] = [
  { id: "again", label: "À revoir", hint: "Oublié" },
  { id: "hard", label: "Difficile", hint: "Retrouvé avec effort" },
  { id: "good", label: "Correct", hint: "Retrouvé" },
  { id: "easy", label: "Facile", hint: "Immédiat" },
];

export function FlashcardsPage() {
  const decks = useDecks();
  const due = useDueCards();
  const subjects = useSubjects();
  const [reviewing, setReviewing] = useState(false);

  return (
    <div className="page">
      <PageHeader
        title={due.data ? `${due.data.length} carte${due.data.length > 1 ? "s" : ""} à réviser` : "Flashcards"}
        actions={
          <button type="button" className="btn btn--primary" disabled={!due.data?.length} onClick={() => setReviewing(true)}>
            Commencer la révision
          </button>
        }
      />

      {reviewing && due.data && due.data.length > 0 ? (
        <ReviewSession cards={due.data} onExit={() => setReviewing(false)} />
      ) : (
        <div className="grid-auto">
          <Card title="Mes paquets">
            <QueryState query={decks}>
              {(list) =>
                list.length === 0 ? (
                  <EmptyState title="Aucun paquet" />
                ) : (
                  <ul className="list">
                    {list.map((d) => (
                      <li key={d.id}>
                        <SubjectBadge acronym={subjects.acronym(d.subject_id)} color={subjects.color(d.subject_id)} />
                        <div className="stack" style={{ gap: 2, flex: 1 }}>
                          <strong className="small">{d.title}</strong>
                          <span className="xsmall muted">
                            {d.card_count} carte(s) · {d.due_count} à réviser
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )
              }
            </QueryState>
          </Card>
          <NewCardForm />
        </div>
      )}
    </div>
  );
}

function ReviewSession({ cards, onExit }: { cards: CardT[]; onExit: () => void }) {
  // Freeze the queue so refetches after each answer do not reshuffle it.
  const queue = useMemo(() => cards, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [last, setLast] = useState<ReviewResult | null>(null);
  const [key, setKey] = useState(newIdempotencyKey);
  const review = useReviewCard();
  const card = queue[index];

  if (!card) {
    return (
      <Card>
        <EmptyState title="Révision terminée" action={<button type="button" className="btn" onClick={onExit}>Retour aux paquets</button>}>
          {last ? `Prochaine échéance de la dernière carte dans ${last.interval_days} jour(s).` : null}
        </EmptyState>
      </Card>
    );
  }

  const answer = (rating: Rating) =>
    review.mutate(
      { card, rating, key },
      {
        onSuccess: (result) => {
          setLast(result);
          setRevealed(false);
          setIndex((i) => i + 1);
          setKey(newIdempotencyKey());
        },
      },
    );

  return (
    <section className="flash" aria-label="Révision des cartes">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="small muted">
          Carte {index + 1} / {queue.length}
        </span>
        <button type="button" className="btn btn--small btn--ghost" onClick={onExit}>
          Arrêter
        </button>
      </div>
      <div className="flash__card" aria-live="polite">
        <div className="flash__front">{card.front}</div>
        {revealed ? (
          <div className="flash__back">{card.back}</div>
        ) : (
          <p className="small muted">Répondez de tête, puis retournez.</p>
        )}
      </div>
      {review.error && <div className="status status--critical">{errorMessage(review.error)}</div>}
      {revealed ? (
        <div className="flash__ratings" role="group" aria-label="Évaluer votre réponse">
          {RATINGS.map((r) => (
            <button key={r.id} type="button" className="btn" disabled={review.isPending} onClick={() => answer(r.id)}>
              <span className="stack" style={{ gap: 0 }}>
                <span>{r.label}</span>
                <span className="xsmall muted">{r.hint}</span>
              </span>
            </button>
          ))}
        </div>
      ) : (
        <button type="button" className="btn btn--primary" onClick={() => setRevealed(true)}>
          Retourner la carte
        </button>
      )}
    </section>
  );
}

function NewCardForm() {
  const decks = useDecks();
  const createDeck = useCreateDeck();
  const createCard = useCreateCard();
  const subjects = useSubjects();
  const [deckId, setDeckId] = useState("");
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [newDeck, setNewDeck] = useState("");
  const [newDeckSubject, setNewDeckSubject] = useState("");
  const selected = deckId || decks.data?.[0]?.id || "";

  return (
    <Card title="Ajouter des cartes">
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (!selected || !front.trim() || !back.trim()) return;
          createCard.mutate({ deckId: selected, front: front.trim(), back: back.trim() }, { onSuccess: () => (setFront(""), setBack("")) });
        }}
      >
        <label className="field">
          <span>Paquet</span>
          <select className="select" value={selected} onChange={(e) => setDeckId(e.target.value)}>
            {(decks.data ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.title}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Question (recto)</span>
          <input className="input" value={front} onChange={(e) => setFront(e.target.value)} required maxLength={2000} />
        </label>
        <label className="field">
          <span>Réponse (verso)</span>
          <textarea className="textarea" style={{ minHeight: 80 }} value={back} onChange={(e) => setBack(e.target.value)} required maxLength={4000} />
        </label>
        {createCard.error && <div className="status status--critical">{errorMessage(createCard.error)}</div>}
        <button type="submit" className="btn btn--primary" disabled={!selected || createCard.isPending}>
          Ajouter la carte
        </button>
      </form>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          if (!newDeck.trim()) return;
          createDeck.mutate({ title: newDeck.trim(), subject_id: newDeckSubject || null }, { onSuccess: (d) => (setNewDeck(""), setDeckId(d.id)) });
        }}
      >
        <label className="field" style={{ flex: 2 }}>
          <span>Nouveau paquet</span>
          <input className="input" value={newDeck} onChange={(e) => setNewDeck(e.target.value)} maxLength={200} />
        </label>
        <label className="field" style={{ flex: 1 }}>
          <span>Matière</span>
          <select className="select" value={newDeckSubject} onChange={(e) => setNewDeckSubject(e.target.value)}>
            <option value="">—</option>
            {subjects.ids.map((id) => (
              <option key={id} value={id}>
                {subjects.name(id)}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn" style={{ alignSelf: "flex-end" }} disabled={createDeck.isPending}>
          Créer
        </button>
      </form>
    </Card>
  );
}

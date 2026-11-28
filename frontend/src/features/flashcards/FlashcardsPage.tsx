import { Layers, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { ApiError } from "@/api/client";
import { useSubjects } from "@/api/hooks";
import { useCards, useCreateCard, useCreateDeck, useDecks, useDeleteCard } from "@/api/hooksLearning";
import type { Deck } from "@/api/typesLearning";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Button, Card, CardHeader, Dialog, EmptyState, PageHeader, SelectField, TextField, buttonClasses } from "@/components/ui";
import { formatShortDay } from "@/lib/format";

import { ReviewSession } from "./ReviewSession";

function DeckCards({ deck }: { deck: Deck }) {
  const cards = useCards(deck.id);
  const createCard = useCreateCard(deck.id);
  const deleteCard = useDeleteCard(deck.id);
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const field = createCard.error instanceof ApiError ? (createCard.error.details as { field?: string } | undefined)?.field : undefined;

  function submit(event: FormEvent) {
    event.preventDefault();
    createCard.mutate(
      { front, back },
      {
        onSuccess: () => {
          setFront("");
          setBack("");
        },
      },
    );
  }

  return (
    <Card>
      <CardHeader title={`Cartes de « ${deck.title} »`} />
      <form onSubmit={submit} className="mb-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <TextField label="Recto (question)" value={front} onChange={(e) => setFront(e.target.value)} error={field === "front" ? createCard.error?.message : undefined} />
        <TextField label="Verso (réponse)" value={back} onChange={(e) => setBack(e.target.value)} error={field === "back" ? createCard.error?.message : undefined} />
        <Button type="submit" loading={createCard.isPending}>
          <Plus className="size-4" aria-hidden="true" />
          Ajouter
        </Button>
      </form>
      <QueryStatus query={cards} />
      <ul className="flex flex-col divide-y divide-line">
        {cards.data?.map((card) => (
          <li key={card.id} className="flex items-start gap-3 py-2.5 text-sm">
            <div className="min-w-0 flex-1">
              <p className="font-medium">{card.front}</p>
              <p className="text-muted">{card.back}</p>
            </div>
            <span className="shrink-0 text-xs text-muted">{card.due_at ? `Révision : ${formatShortDay(card.due_at)}` : "Nouvelle"}</span>
            <button
              type="button"
              onClick={() => deleteCard.mutate(card.id)}
              className="focus-ring shrink-0 rounded p-1 text-muted hover:text-danger"
              aria-label={`Supprimer la carte « ${card.front} »`}
            >
              <Trash2 className="size-4" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Decks with their due count; spaced review; card management. */
export function FlashcardsPage() {
  const [params, setParams] = useSearchParams();
  const deckId = params.get("deck");
  const reviewing = params.get("review") === "1";
  const decks = useDecks();
  const subjects = useSubjects();
  const createDeck = useCreateDeck();
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [subjectId, setSubjectId] = useState("");

  const selected = decks.data?.find((d) => d.id === deckId);
  const subjectTitle = (id: string) => subjects.data?.find((s) => s.id === id)?.title ?? "";
  const totalDue = decks.data?.reduce((sum, d) => sum + d.due_count, 0) ?? 0;

  if (reviewing && selected) {
    return (
      <section aria-label="Révision des flashcards">
        <ReviewSession deck={selected} onExit={() => setParams({ deck: selected.id }, { replace: true })} />
      </section>
    );
  }

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Flashcards"
        icon={<Layers className="size-5" aria-hidden="true" />}
        description={decks.data ? `${totalDue} carte${totalDue > 1 ? "s" : ""} à réviser aujourd'hui. Les révisions sont espacées selon vos réponses.` : undefined}
        actions={
          <Button variant="secondary" onClick={() => setCreating(true)}>
            <Plus className="size-4" aria-hidden="true" />
            Nouveau paquet
          </Button>
        }
      />

      <QueryStatus query={decks} />
      {decks.data?.length === 0 && (
        <EmptyState
          title="Aucun paquet"
          description="Créez un paquet ou générez des flashcards depuis Learning Studio."
          action={
            <Link to="/studio" className={buttonClasses("primary", "sm")}>
              Ouvrir Learning Studio
            </Link>
          }
        />
      )}
      <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {decks.data?.map((deck) => (
          <li key={deck.id}>
            <Card className={deck.id === deckId ? "border-accent" : ""}>
              <p className="text-xs text-muted">{subjectTitle(deck.subject_id)}</p>
              <h2 className="mt-1 font-semibold">{deck.title}</h2>
              <div className="mt-3 flex flex-wrap gap-2">
                <Badge>{deck.card_count} cartes</Badge>
                <Badge tone={deck.due_count > 0 ? "warning" : "success"}>{deck.due_count > 0 ? `${deck.due_count} à réviser` : "À jour"}</Badge>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button size="sm" disabled={deck.due_count === 0} onClick={() => setParams({ deck: deck.id, review: "1" }, { replace: true })}>
                  Réviser
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setParams(deck.id === deckId ? {} : { deck: deck.id }, { replace: true })}>
                  {deck.id === deckId ? "Masquer les cartes" : "Voir les cartes"}
                </Button>
              </div>
            </Card>
          </li>
        ))}
      </ul>

      {selected && <DeckCards key={selected.id} deck={selected} />}

      <Dialog open={creating} title="Nouveau paquet" onClose={() => setCreating(false)}>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            createDeck.mutate(
              { subject_id: subjectId, title },
              {
                onSuccess: (deck) => {
                  setCreating(false);
                  setTitle("");
                  setParams({ deck: deck.id }, { replace: true });
                },
              },
            );
          }}
        >
          <TextField label="Titre" required value={title} onChange={(e) => setTitle(e.target.value)} />
          <SelectField
            label="Matière"
            required
            value={subjectId}
            onChange={(e) => setSubjectId(e.target.value)}
            options={[{ value: "", label: "Choisir" }, ...(subjects.data ?? []).map((s) => ({ value: s.id, label: s.title }))]}
          />
          {createDeck.error && <p className="text-sm text-danger">{createDeck.error.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setCreating(false)}>
              Annuler
            </Button>
            <Button type="submit" loading={createDeck.isPending}>
              Créer
            </Button>
          </div>
        </form>
      </Dialog>
    </section>
  );
}

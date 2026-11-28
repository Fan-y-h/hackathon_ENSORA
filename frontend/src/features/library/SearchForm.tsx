import { Search, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import type { SearchMode } from "@/api/types";
import { Button } from "@/components/ui";
import { cn } from "@/lib/cn";

const MODES: Array<{ id: SearchMode; label: string; hint: string }> = [
  { id: "hybrid", label: "Les deux", hint: "Mots exacts et sens proche" },
  { id: "lexical", label: "Mots exacts", hint: "Formules, noms de théorèmes, code" },
  { id: "semantic", label: "Par le sens", hint: "Questions formulées librement" },
];

interface SearchFormProps {
  query: string;
  mode: SearchMode;
  onSearch: (query: string, mode: SearchMode) => void;
}

/** Search field + search mode. The search runs on submit, not at every key press. */
export function SearchForm({ query, mode, onSearch }: SearchFormProps) {
  const [draft, setDraft] = useState(query);
  // Back button or a link can change the query in the URL: follow it.
  useEffect(() => setDraft(query), [query]);

  function submit(event: FormEvent) {
    event.preventDefault();
    onSearch(draft.trim(), mode);
  }

  return (
    <form role="search" onSubmit={submit} className="flex flex-col gap-3">
      <div className="flex gap-2">
        <label className="relative flex-1">
          <span className="sr-only">Rechercher dans les documents</span>
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden="true" />
          <input
            type="search"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Ex. : intervalle de confiance, p-valeur, Dijkstra…"
            className="focus-ring h-10 w-full rounded-lg border border-line bg-surface pr-9 pl-9 text-sm placeholder:text-muted"
          />
          {draft && (
            <button
              type="button"
              onClick={() => {
                setDraft("");
                onSearch("", mode);
              }}
              className="focus-ring absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 text-muted hover:text-ink"
              aria-label="Effacer la recherche"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          )}
        </label>
        <Button type="submit">Rechercher</Button>
      </div>

      <fieldset className="flex flex-wrap items-center gap-2 text-sm">
        <legend className="sr-only">Type de recherche</legend>
        {MODES.map((option) => (
          <label
            key={option.id}
            title={option.hint}
            className={cn(
              "cursor-pointer rounded-full border px-3 py-1 has-focus-visible:outline-2 has-focus-visible:outline-accent",
              option.id === mode ? "border-accent bg-accent-soft font-medium text-accent" : "border-line text-muted hover:text-ink",
            )}
          >
            <input
              type="radio"
              name="search-mode"
              value={option.id}
              checked={option.id === mode}
              onChange={() => onSearch(draft.trim(), option.id)}
              className="sr-only"
            />
            {option.label}
          </label>
        ))}
      </fieldset>
    </form>
  );
}

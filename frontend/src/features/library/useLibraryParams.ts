import { useSearchParams } from "react-router";

import type { ResourceType, SearchMode } from "@/api/types";
import { RESOURCE_TYPE_ORDER } from "@/lib/labels";

const MODES: SearchMode[] = ["hybrid", "lexical", "semantic"];

/**
 * Library state kept in the URL (?q=...&type=td&doc=...): reload, back button
 * and shared links restore exactly the same search and preview.
 */
export function useLibraryParams() {
  const [params, setParams] = useSearchParams();

  const mode = params.get("mode");
  const type = params.get("type");
  const year = params.get("year");

  const state = {
    q: params.get("q") ?? "",
    mode: (MODES.includes(mode as SearchMode) ? mode : "hybrid") as SearchMode,
    subjectId: params.get("subject") ?? undefined,
    type: RESOURCE_TYPE_ORDER.includes(type as ResourceType) ? (type as ResourceType) : undefined,
    year: year && /^\d{4}$/.test(year) ? Number(year) : undefined,
    /** Document shown in the preview. */
    docId: params.get("doc") ?? undefined,
    /** Passage to highlight in the preview. */
    citationId: params.get("cite") ?? undefined,
  };

  /** Changes some values; undefined or "" removes the parameter. */
  function update(changes: Partial<Record<"q" | "mode" | "subject" | "type" | "year" | "doc" | "cite", string | undefined>>) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const [key, value] of Object.entries(changes)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        return next;
      },
      { replace: true },
    );
  }

  return { ...state, update };
}

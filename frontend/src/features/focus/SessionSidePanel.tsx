import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { useConcepts, useDocuments } from "@/api/hooks";
import type { StudySession } from "@/api/typesPlanning";
import { Card, CardHeader } from "@/components/ui";
import { RESOURCE_TYPE_LABELS } from "@/lib/labels";

const draftKey = (focusId: string) => `ensora.focus-notes.${focusId}`;

/** Notes typed during a focus session. Kept in the browser until the session ends (survives a reload). */
export function useFocusNotes(focusId: string | undefined) {
  const [notes, setNotes] = useState("");
  useEffect(() => {
    if (!focusId) return;
    try {
      setNotes(localStorage.getItem(draftKey(focusId)) ?? "");
    } catch {
      setNotes("");
    }
  }, [focusId]);
  function update(value: string) {
    setNotes(value);
    try {
      if (focusId) localStorage.setItem(draftKey(focusId), value);
    } catch {
      // storage unavailable: notes stay in memory only
    }
  }
  function clear() {
    setNotes("");
    try {
      if (focusId) localStorage.removeItem(draftKey(focusId));
    } catch {
      // nothing saved
    }
  }
  return { notes, update, clear };
}

/** Resources of the planned session (its subject's course material) and the concepts to work on. */
export function SessionResources({ session }: { session: StudySession }) {
  const documents = useDocuments({ subject_id: session.subject_id ?? undefined });
  const concepts = useConcepts(session.subject_id ?? "");
  const focusConcepts = concepts.data?.filter((c) => session.concept_ids.includes(c.id)) ?? [];
  const useful = (documents.data ?? []).filter((d) => d.resource_type === "course" || d.resource_type === "td" || d.resource_type === "sheet").slice(0, 5);

  return (
    <Card>
      <CardHeader title="Pour cette séance" description={session.objective} />
      {focusConcepts.length > 0 && (
        <p className="mb-3 text-sm">
          <span className="text-muted">Notions : </span>
          {focusConcepts.map((c) => c.label).join(", ")}
        </p>
      )}
      <p className="mb-2 text-sm text-muted">Réussi si : {session.success_criteria}</p>
      {useful.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {useful.map((d) => (
            <li key={d.id}>
              <Link to={`/library?doc=${d.id}`} target="_blank" rel="noopener" className="focus-ring inline-flex items-center gap-2 rounded text-sm text-accent hover:underline">
                <FileText className="size-4" aria-hidden="true" />
                {d.title} <span className="text-xs text-muted">({RESOURCE_TYPE_LABELS[d.resource_type]})</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {useful.length === 0 && <p className="text-sm text-muted">Pas de ressource liée à cette séance.</p>}
    </Card>
  );
}

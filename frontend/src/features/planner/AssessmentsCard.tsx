import { CircleAlert, CircleCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { useConcepts, useEvents } from "@/api/hooks";
import { useAssessments, useUpdateAssessment } from "@/api/hooksPlanning";
import type { Assessment } from "@/api/typesPlanning";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Button, Card, CardHeader, Dialog, EmptyState, ErrorPanel } from "@/components/ui";
import { dayRange, formatRelative, formatShortDay } from "@/lib/format";

function ScopeDialog({ assessment, onClose }: { assessment: Assessment; onClose: () => void }) {
  const concepts = useConcepts(assessment.subject_id);
  const update = useUpdateAssessment();
  const [selected, setSelected] = useState<string[]>(assessment.concept_ids);

  return (
    <Dialog
      open
      title={`Programme de « ${assessment.title} »`}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Annuler
          </Button>
          <Button
            loading={update.isPending}
            disabled={selected.length === 0}
            onClick={() => update.mutate({ id: assessment.id, expected_revision: assessment.revision, scope_status: "confirmed", concept_ids: selected }, { onSuccess: onClose })}
          >
            Confirmer ce programme
          </Button>
        </>
      }
    >
      <p className="mb-3 text-sm text-muted">Décochez les notions qui ne seront pas évaluées. Le planning ne préparera que les notions confirmées.</p>
      <QueryStatus query={concepts} />
      <div className="flex max-h-80 flex-col gap-1.5 overflow-y-auto">
        {concepts.data?.map((concept) => (
          <label key={concept.id} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="focus-ring size-4 accent-(--accent)"
              checked={selected.includes(concept.id)}
              onChange={(e) => setSelected((ids) => (e.target.checked ? [...ids, concept.id] : ids.filter((id) => id !== concept.id)))}
            />
            {concept.label}
          </label>
        ))}
      </div>
      {update.error && (
        <div className="mt-3">
          <ErrorPanel message={update.error.message} />
        </div>
      )}
    </Dialog>
  );
}

/** Step 1 of planning: the student confirms what each assessment covers. */
export function AssessmentsCard() {
  const assessments = useAssessments();
  const events = useEvents(dayRange(-1, 120));
  const [editing, setEditing] = useState<Assessment | null>(null);
  const upcoming = assessments.data?.filter((a) => new Date(a.deadline_at).getTime() > Date.now());

  return (
    <Card>
      <CardHeader title="Évaluations à préparer" description="Le planning ne prépare que les programmes confirmés." />
      <QueryStatus query={assessments} />
      {upcoming?.length === 0 && <EmptyState title="Aucune évaluation à venir" />}
      <ul className="flex flex-col divide-y divide-line">
        {upcoming?.map((assessment) => {
          const event = events.data?.find((e) => e.id === assessment.event_id);
          const slotUnconfirmed = event?.confirmation_required === true;
          const rejected = event !== undefined && event.type !== "exam" && event.type !== "presentation";
          return (
            <li key={assessment.id} className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
              <div className="min-w-0 flex-1 basis-full sm:basis-auto">
                <p className="font-medium">{assessment.title}</p>
                <p className="text-sm text-muted">
                  {formatShortDay(assessment.deadline_at)} · {formatRelative(assessment.deadline_at)} · {assessment.concept_ids.length} notions
                </p>
              </div>
              {rejected ? (
                <Badge>Pas une évaluation</Badge>
              ) : slotUnconfirmed ? (
                <Link to="/agenda" className="focus-ring inline-flex items-center gap-1 rounded text-sm font-medium text-warning hover:underline">
                  <CircleAlert className="size-4" aria-hidden="true" />
                  Confirmer d'abord le créneau
                </Link>
              ) : assessment.scope_status === "confirmed" ? (
                <>
                  <Badge tone="success" icon={<CircleCheck className="size-3" aria-hidden="true" />}>
                    Programme confirmé
                  </Badge>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(assessment)}>
                    Modifier
                  </Button>
                </>
              ) : (
                <Button size="sm" onClick={() => setEditing(assessment)}>
                  Confirmer le programme
                </Button>
              )}
            </li>
          );
        })}
      </ul>
      {editing && <ScopeDialog key={editing.id} assessment={editing} onClose={() => setEditing(null)} />}
    </Card>
  );
}

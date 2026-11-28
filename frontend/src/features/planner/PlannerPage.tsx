import { CalendarClock, CircleAlert, Sparkles } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/api/client";
import { useAcceptPlan, useCurrentPlan, usePreviewPlan } from "@/api/hooksPlanning";
import type { PlanPreview } from "@/api/typesPlanning";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, Card, CardHeader, EmptyState, ErrorPanel, PageHeader } from "@/components/ui";
import { formatDay, formatMinutes } from "@/lib/format";

import { AssessmentsCard } from "./AssessmentsCard";
import { groupByDay } from "./labels";
import { SessionItem } from "./SessionItem";

function DayList({ sessions, preview, plan }: { sessions: PlanPreview["sessions"]; preview?: PlanPreview; plan?: Parameters<typeof SessionItem>[0]["plan"] }) {
  return (
    <div className="flex flex-col gap-4">
      {groupByDay(sessions).map(([day, items]) => (
        <section key={day} aria-label={formatDay(items[0].start_at)}>
          <h3 className="mb-2 text-sm font-semibold capitalize">
            {formatDay(items[0].start_at)} <span className="font-normal text-muted">· {formatMinutes(items.reduce((sum, s) => sum + s.estimated_min, 0))}</span>
          </h3>
          <ul className="flex flex-col divide-y divide-line rounded-lg border border-line px-3 py-3">
            {items.map((session) => (
              <SessionItem
                key={session.id}
                session={session}
                plan={plan}
                change={preview?.diff?.added.includes(session.id) ? "new" : preview?.diff?.moved.includes(session.id) ? "moved" : undefined}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** Confirm scopes, preview a feasible plan, accept it, then follow and adjust sessions. */
export function PlannerPage() {
  const plan = useCurrentPlan();
  const previewPlan = usePreviewPlan();
  const accept = useAcceptPlan();
  const [preview, setPreview] = useState<PlanPreview | null>(null);

  function propose() {
    accept.reset();
    previewPlan.mutate(plan.data ? "replan" : "preview", { onSuccess: setPreview });
  }

  const stale = accept.error instanceof ApiError && accept.error.isConflict;
  const upcoming = plan.data?.sessions.filter((s) => new Date(s.end_at).getTime() > Date.now() - 86_400_000) ?? [];
  const removed = preview && plan.data ? plan.data.sessions.filter((s) => preview.diff?.removed.includes(s.id)) : [];

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Smart Planner"
        icon={<CalendarClock className="size-5" aria-hidden="true" />}
        description="Un planning réaliste : il respecte vos cours, vos créneaux de travail et votre plafond quotidien, et dit ce qui ne rentre pas."
        actions={
          <Button onClick={propose} loading={previewPlan.isPending}>
            <Sparkles className="size-4" aria-hidden="true" />
            {plan.data ? "Proposer une mise à jour" : "Générer une proposition"}
          </Button>
        }
      />

      <AssessmentsCard />
      {previewPlan.error && <ErrorPanel message={previewPlan.error.message} />}

      {preview && (
        <Card className="border-accent">
          <CardHeader
            title="Proposition (pas encore appliquée)"
            description={`${formatMinutes(preview.summary.planned_min)} planifiées${preview.summary.unscheduled_min ? ` · ${formatMinutes(preview.summary.unscheduled_min)} non placées` : ""}`}
            actions={
              <>
                <Button variant="secondary" onClick={() => setPreview(null)}>
                  Ignorer
                </Button>
                <Button loading={accept.isPending} onClick={() => accept.mutate(preview, { onSuccess: () => setPreview(null) })}>
                  Accepter
                </Button>
              </>
            }
          />
          {stale && (
            <div className="mb-4">
              <ErrorPanel title="Proposition périmée" message={accept.error?.message ?? ""} onRetry={propose} />
            </div>
          )}
          {accept.error && !stale && (
            <div className="mb-4">
              <ErrorPanel message={accept.error.message} />
            </div>
          )}
          {preview.warnings.length > 0 && (
            <ul className="mb-4 flex flex-col gap-1.5 rounded-lg bg-warning-soft p-3 text-sm">
              {preview.warnings.map((w) => (
                <li key={w} className="flex gap-2">
                  <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
                  {w}
                </li>
              ))}
            </ul>
          )}
          {preview.unscheduled.length > 0 && (
            <details className="mb-4 text-sm">
              <summary className="cursor-pointer font-medium">Travail non placé ({preview.unscheduled.length})</summary>
              <ul className="mt-2 flex flex-col gap-1 text-muted">
                {preview.unscheduled.map((u) => (
                  <li key={u.need_id}>
                    {u.label} — {formatMinutes(u.remaining_min)} ({u.reason === "CAPACITY" ? "plus de créneau libre avant l'échéance" : u.reason === "DEADLINE_PASSED" ? "échéance dépassée" : "programme non confirmé"})
                  </li>
                ))}
              </ul>
            </details>
          )}
          {removed.length > 0 && (
            <p className="mb-4 text-sm text-muted">
              Retirées : {removed.map((s) => s.title).join(", ")}
            </p>
          )}
          {preview.sessions.length === 0 ? (
            <EmptyState title="Rien à planifier" description="Confirmez le programme d'une évaluation ou ajoutez des tâches avec un temps restant." />
          ) : (
            <DayList sessions={preview.sessions} preview={preview} />
          )}
        </Card>
      )}

      <Card>
        <CardHeader title="Mon planning" description={plan.data ? "Verrouillez une session pour qu'elle ne soit jamais déplacée." : undefined} />
        <QueryStatus query={plan} />
        {plan.data === null && <EmptyState icon={<CalendarClock className="size-5" aria-hidden="true" />} title="Aucun planning accepté" description="Confirmez vos évaluations puis générez une proposition." />}
        {plan.data && upcoming.length === 0 && <EmptyState title="Aucune session à venir" description="Proposez une mise à jour pour planifier la suite." />}
        {plan.data && upcoming.length > 0 && <DayList sessions={upcoming} plan={plan.data} />}
      </Card>
    </section>
  );
}

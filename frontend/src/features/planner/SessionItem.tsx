import { Lock, LockOpen, Play } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";

import { useSubjects } from "@/api/hooks";
import { useFinishSession, useLockSession, useStartSession } from "@/api/hooksPlanning";
import type { Plan, StudySession } from "@/api/typesPlanning";
import { Badge, Button, Dialog, SelectField, TextField } from "@/components/ui";
import { formatTime } from "@/lib/format";

import { ACTIVITY_LABELS, STATUS_LABELS } from "./labels";

function FinishDialog({ session, onClose }: { session: StudySession; onClose: () => void }) {
  const finish = useFinishSession();
  const [status, setStatus] = useState<"completed" | "partial" | "skipped">("completed");
  const [actual, setActual] = useState(String(session.estimated_min));
  const [remaining, setRemaining] = useState("15");

  return (
    <Dialog
      open
      title="Comment s'est passée la session ?"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Annuler
          </Button>
          <Button
            loading={finish.isPending}
            onClick={() =>
              finish.mutate(
                { sessionId: session.id, status, actual_min: status === "skipped" ? 0 : Number(actual) || 0, remaining_min: status === "partial" ? Number(remaining) || 0 : 0, reflection: "" },
                { onSuccess: onClose },
              )
            }
          >
            Enregistrer
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted">{session.title}</p>
        <SelectField
          label="Résultat"
          value={status}
          onChange={(e) => setStatus(e.target.value as typeof status)}
          options={[
            { value: "completed", label: "Terminée" },
            { value: "partial", label: "Faite en partie" },
            { value: "skipped", label: "Pas faite" },
          ]}
        />
        {status !== "skipped" && <TextField label="Temps réellement travaillé (min)" type="number" min={0} value={actual} onChange={(e) => setActual(e.target.value)} />}
        {status === "partial" && <TextField label="Temps restant estimé (min)" type="number" min={0} value={remaining} onChange={(e) => setRemaining(e.target.value)} hint="Il sera replanifié à la prochaine proposition." />}
        {finish.error && <p className="text-sm text-danger">{finish.error.message}</p>}
      </div>
    </Dialog>
  );
}

interface SessionItemProps {
  session: StudySession;
  /** Present for the accepted plan (actions enabled); absent in a preview. */
  plan?: Plan;
  /** Preview marker: "new" or "moved". */
  change?: "new" | "moved";
}

export function SessionItem({ session, plan, change }: SessionItemProps) {
  const navigate = useNavigate();
  const subjects = useSubjects();
  const lock = useLockSession();
  const start = useStartSession();
  const [finishing, setFinishing] = useState(false);
  const subject = subjects.data?.find((s) => s.id === session.subject_id);
  const status = STATUS_LABELS[session.status];
  const open = session.status === "planned" || session.status === "in_progress";

  return (
    <li className="flex flex-wrap items-start gap-3 py-3 first:pt-0 last:pb-0">
      <p className="w-14 shrink-0 text-sm font-semibold tabular-nums">
        {formatTime(session.start_at)}
        <span className="block text-xs font-normal text-muted">{session.estimated_min} min</span>
      </p>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium">{session.title}</p>
          <Badge tone="accent">{ACTIVITY_LABELS[session.activity_type]}</Badge>
          {change === "new" && <Badge tone="success">Nouvelle</Badge>}
          {change === "moved" && <Badge tone="warning">Déplacée</Badge>}
          {plan && session.status !== "planned" && <Badge tone={status.tone}>{status.text}</Badge>}
          {session.locked && <Badge icon={<Lock className="size-3" aria-hidden="true" />}>Verrouillée</Badge>}
        </div>
        <p className="mt-0.5 text-xs text-muted">
          {subject && `${subject.title} · `}
          Pourquoi : {session.reason}
        </p>
      </div>
      {plan && open && (
        <div className="flex flex-wrap gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            loading={lock.isPending}
            onClick={() => lock.mutate({ session, plan })}
            aria-label={session.locked ? "Déverrouiller la session" : "Verrouiller la session"}
            title={session.locked ? "Déverrouiller : pourra être déplacée" : "Verrouiller : ne sera jamais déplacée"}
          >
            {session.locked ? <LockOpen className="size-4" aria-hidden="true" /> : <Lock className="size-4" aria-hidden="true" />}
          </Button>
          {session.status === "planned" && (
            <Button size="sm" variant="secondary" loading={start.isPending} onClick={() => start.mutate(session.id, { onSuccess: () => void navigate(`/focus?session=${session.id}`) })}>
              <Play className="size-4" aria-hidden="true" />
              Démarrer
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => setFinishing(true)}>
            Terminer
          </Button>
        </div>
      )}
      {finishing && <FinishDialog session={session} onClose={() => setFinishing(false)} />}
    </li>
  );
}

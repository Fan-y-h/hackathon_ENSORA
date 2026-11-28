import { Plus, RotateCcw, UserRound, Users, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";

import { ApiError } from "@/api/client";
import { useConnections, useProfile, useUpdateProfile } from "@/api/hooks";
import { useSettings, useUpdateSettings } from "@/api/hooksWorkspace";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, Card, CardHeader, PageHeader, buttonClasses } from "@/components/ui";

import { AvailabilityFields, normalizeAvailability, type AvailabilityValue } from "./AvailabilityFields";
import { EnsaiIdentity } from "./EnsaiIdentity";

function IdentityCard() {
  const profile = useProfile();
  const update = useUpdateProfile();
  const [goals, setGoals] = useState<string[]>([]);
  const [newGoal, setNewGoal] = useState("");

  useEffect(() => {
    if (profile.data) setGoals(profile.data.goals);
  }, [profile.data]);

  function save(next: string[]) {
    setGoals(next);
    if (profile.data) update.mutate({ goals: next, expected_revision: profile.data.revision });
  }

  function add(event: FormEvent) {
    event.preventDefault();
    if (!newGoal.trim()) return;
    save([...goals, newGoal.trim()]);
    setNewGoal("");
  }

  return (
    <Card>
      <CardHeader title="Identité et scolarité" />
      <EnsaiIdentity />
      <div className="mt-5 border-t border-line pt-4">
        <h3 className="mb-2 text-sm font-semibold">Mes objectifs personnels</h3>
        <ul className="mb-2 flex flex-col gap-1.5">
          {goals.map((goal) => (
            <li key={goal} className="flex items-center justify-between gap-2 rounded-md bg-surface-2 px-3 py-1.5 text-sm">
              {goal}
              <button type="button" onClick={() => save(goals.filter((g) => g !== goal))} className="focus-ring rounded p-0.5 text-muted hover:text-danger" aria-label={`Retirer l'objectif « ${goal} »`}>
                <X className="size-4" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
        <form onSubmit={add} className="flex gap-2">
          <label htmlFor="new-goal" className="sr-only">
            Nouvel objectif
          </label>
          <input id="new-goal" value={newGoal} onChange={(e) => setNewGoal(e.target.value)} placeholder="Ex. : valider la 2A" className="focus-ring h-10 flex-1 rounded-lg border border-line bg-surface px-3 text-sm" />
          <Button type="submit" variant="secondary" disabled={!newGoal.trim()} loading={update.isPending}>
            <Plus className="size-4" aria-hidden="true" />
            Ajouter
          </Button>
        </form>
        {update.error && <p className="mt-2 text-sm text-danger">{update.error instanceof ApiError && update.error.isConflict ? "Profil modifié ailleurs : rechargez la page." : update.error.message}</p>}
      </div>
    </Card>
  );
}

/** The TD/TP group is never typed by the student: it only comes from Pamplemousse. */
function GroupCard() {
  const connections = useConnections();
  const pamplemousse = connections.data?.find((c) => c.source_kind === "pamplemousse");
  const connected = pamplemousse && pamplemousse.status !== "disconnected";
  return (
    <Card>
      <CardHeader title="Groupe de TD / TP" />
      <p className="flex items-start gap-2 text-sm">
        <Users className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
        {connected ? (
          <span>Votre groupe est lu dans Pamplemousse et indiqué sur chaque TD et TP de l'agenda. Il peut changer selon le cours.</span>
        ) : (
          <span>
            Non disponible : connectez Pamplemousse dans les{" "}
            <Link to="/settings" className="text-accent hover:underline">
              paramètres
            </Link>{" "}
            pour l'afficher.
          </span>
        )}
      </p>
    </Card>
  );
}

function AvailabilityCard() {
  const settings = useSettings();
  const updateSettings = useUpdateSettings();
  const [value, setValue] = useState<AvailabilityValue | null>(null);

  useEffect(() => {
    if (settings.data) setValue(normalizeAvailability(settings.data.preferences));
  }, [settings.data]);

  return (
    <Card>
      <CardHeader title="Disponibilités et méthode de travail" description="Le planning ne placera jamais de travail en dehors de ces créneaux ni au-delà de votre limite." />
      {!settings.data && <QueryStatus query={settings} />}
      {value && settings.data && (
        <>
          <AvailabilityFields value={value} onChange={setValue} />
          {updateSettings.error && <p className="mt-3 text-sm text-danger">{updateSettings.error.message}</p>}
          {updateSettings.isSuccess && (
            <p role="status" className="mt-3 text-sm text-success">
              Enregistré. Proposez une mise à jour du planning pour en tenir compte.
            </p>
          )}
          <div className="mt-4 flex justify-end">
            <Button loading={updateSettings.isPending} onClick={() => settings.data && updateSettings.mutate({ preferences: { ...settings.data.preferences, ...value }, expected_revision: settings.data.revision })}>
              Enregistrer
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}

export function ProfilePage() {
  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Mon profil"
        icon={<UserRound className="size-5" aria-hidden="true" />}
        description="Ces informations adaptent votre planning et vos supports."
        actions={
          <Link to="/onboarding" className={buttonClasses("secondary")}>
            <RotateCcw className="size-4" aria-hidden="true" />
            Refaire la configuration
          </Link>
        }
      />
      <div className="grid items-start gap-5 xl:grid-cols-2">
        <IdentityCard />
        <GroupCard />
      </div>
      <AvailabilityCard />
    </section>
  );
}

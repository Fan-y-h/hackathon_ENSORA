// Developer-only page (/dev/ui, never built for production).
// Shows every design-system component in all its states, to check
// light/dark themes, keyboard focus and narrow screens in one place.
import { Palette, Plus, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import {
  Badge,
  Button,
  Card,
  CardHeader,
  ConfirmDialog,
  EmptyState,
  ErrorPanel,
  JobStatus,
  LoadingState,
  MasteryIndicator,
  PageHeader,
  SelectField,
  Skeleton,
  SourceStatus,
  TextField,
} from "@/components/ui";

import { ApiPlayground } from "./ApiPlayground";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} />
      <div className="flex flex-col gap-4">{children}</div>
    </Card>
  );
}

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

export function UiGallery() {
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-6">
      <PageHeader
        title="Composants"
        description="Vitrine du design system, visible uniquement en développement."
        icon={<Palette className="size-5" aria-hidden="true" />}
      />

      <ApiPlayground />

      <Section title="Boutons">
        <div className="flex flex-wrap gap-2">
          <Button>
            <Plus className="size-4" aria-hidden="true" />
            Principal
          </Button>
          <Button variant="secondary">Secondaire</Button>
          <Button variant="ghost">Discret</Button>
          <Button variant="danger">
            <Trash2 className="size-4" aria-hidden="true" />
            Supprimer
          </Button>
          <Button loading>Enregistrement</Button>
          <Button disabled>Désactivé</Button>
          <Button size="sm" variant="secondary">
            Petit
          </Button>
        </div>
      </Section>

      <Section title="Badges">
        <div className="flex flex-wrap gap-2">
          <Badge>Neutre</Badge>
          <Badge tone="accent">Info</Badge>
          <Badge tone="success">Réussi</Badge>
          <Badge tone="warning">Attention</Badge>
          <Badge tone="danger">Erreur</Badge>
        </div>
      </Section>

      <Section title="Maîtrise (non évalué ≠ 0 %)">
        <MasteryIndicator label="Notion A" value={null} />
        <MasteryIndicator label="Notion B" value={0} />
        <MasteryIndicator label="Notion C" value={0.55} />
        <MasteryIndicator label="Notion D" value={0.86} />
      </Section>

      <Section title="Sources">
        <SourceStatus source="Pamplemousse" state="connected" lastSyncAt={minutesAgo(4)} />
        <SourceStatus source="Moodle" state="syncing" lastSyncAt={minutesAgo(90)} />
        <SourceStatus source="Calendrier" state="stale" lastSyncAt={minutesAgo(60 * 30)} />
        <SourceStatus source="Moodle" state="error" lastSyncAt={minutesAgo(200)} />
        <SourceStatus source="Pamplemousse" state="disconnected" />
      </Section>

      <Section title="Tâches longues">
        <JobStatus label="Génération de la fiche" state="queued" onCancel={() => {}} />
        <JobStatus label="Import du cours" state="running" progress={0.4} onCancel={() => {}} />
        <JobStatus label="Synchronisation" state="succeeded" />
        <JobStatus label="Génération du quiz" state="failed" error="Le fournisseur IA ne répond pas." onRetry={() => {}} />
        <JobStatus label="Import" state="cancelled" />
      </Section>

      <Section title="Formulaires">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Nom" placeholder="Ex. : Statistique" required />
          <TextField label="Durée (min)" type="number" hint="Entre 5 et 240 minutes." />
          <TextField label="Note" defaultValue="23" error="La note doit être comprise entre 0 et 20." />
          <SelectField
            label="Semestre"
            options={[
              { value: "s1", label: "Semestre 1" },
              { value: "s2", label: "Semestre 2" },
            ]}
          />
        </div>
      </Section>

      <Section title="États de chargement, vide et erreur">
        <LoadingState />
        <div className="flex flex-col gap-2">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
        </div>
        <EmptyState
          title="Aucune tâche"
          description="Ajoutez une tâche ou importez un projet pour commencer."
          action={<Button size="sm">Ajouter une tâche</Button>}
        />
        <ErrorPanel message="Le serveur est indisponible (503)." requestId="req_8f2c1a" onRetry={() => {}} />
      </Section>

      <Section title="Confirmation">
        <div>
          <Button variant="danger" onClick={() => setDialogOpen(true)}>
            Ouvrir la confirmation
          </Button>
        </div>
        <ConfirmDialog
          open={dialogOpen}
          tone="danger"
          title="Supprimer cette tâche ?"
          description="Cette action est définitive."
          confirmLabel="Supprimer"
          onConfirm={() => setDialogOpen(false)}
          onCancel={() => setDialogOpen(false)}
        />
      </Section>
    </section>
  );
}

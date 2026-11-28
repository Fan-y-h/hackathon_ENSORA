import { FlaskConical, GraduationCap, Info, Pencil, Target } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import { useProfile } from "@/api/hooks";

import { useCurriculum, useGradeRecords, useGradeSummary, useObjectives, usePrograms, useRequired, useSaveCurriculum, useSetObjective, useSimulate } from "@/api/hooksGrades";
import type { AcademicNode, Feasibility } from "@/api/typesGrades";
import { QueryStatus } from "@/components/QueryStatus";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorPanel, PageHeader, SelectField, TextField, buttonClasses, type Tone } from "@/components/ui";

import { GradeTree, frNumber, toDecimal } from "./GradeTree";

const FEASIBILITY: Record<Feasibility, { text: string; tone: Tone }> = {
  feasible: { text: "Atteignable", tone: "accent" },
  guaranteed: { text: "Déjà assuré", tone: "success" },
  impossible: { text: "Hors de portée", tone: "danger" },
  unknown: { text: "Inconnu", tone: "neutral" },
};

/** Marks, weighted means, goals, required marks and what-if simulation. Calculations come from the server. */
export function GradesPage() {
  const programs = usePrograms();
  const profile = useProfile();
  const semester = profile.data?.semester ?? 1;
  const program = programs.data?.find((p) => p.semester === semester);
  const curriculum = useCurriculum(program?.id);
  const records = useGradeRecords();
  const summary = useGradeSummary(program?.id);
  const objectives = useObjectives();
  const required = useRequired(program?.id);
  const setObjective = useSetObjective();
  const saveCurriculum = useSaveCurriculum(program?.id ?? "");
  const simulate = useSimulate();

  // Copy of the curriculum being edited (null = not editing). Saved in one PUT.
  const [draft, setDraft] = useState<AcademicNode[] | null>(null);
  const editWeights = draft !== null;
  const [simulating, setSimulating] = useState(false);
  const [hypotheses, setHypotheses] = useState<Record<string, string>>({});
  const [goalNode, setGoalNode] = useState("");
  const [goalValue, setGoalValue] = useState("");

  const nodes = curriculum.data?.nodes ?? [];
  const shown = draft ?? nodes;

  function changeNode(id: string, changes: Partial<AcademicNode>) {
    setDraft((list) => (list ?? nodes).map((n) => (n.id === id ? { ...n, ...changes } : n)));
  }

  function addAssessment(parentId: string) {
    setDraft((list) => {
      const current = list ?? nodes;
      const siblings = current.filter((n) => n.parent_id === parentId);
      return [...current, { id: crypto.randomUUID(), parent_id: parentId, kind: "assessment", title: "Nouvelle épreuve", weight: siblings[0]?.weight_mode === "coefficient" ? "1" : "0", weight_mode: siblings[0]?.weight_mode ?? "fraction", subject_id: null, event_id: null }];
    });
  }

  function removeNode(id: string) {
    setDraft((list) => (list ?? nodes).filter((n) => n.id !== id && n.parent_id !== id));
  }
  const title = (id: string) => nodes.find((n) => n.id === id)?.title ?? "";

  function saveWeights() {
    if (!curriculum.data) return;
    saveCurriculum.mutate({ ...curriculum.data, nodes: shown }, { onSuccess: () => setDraft(null) });
  }

  function runSimulation() {
    if (!program || !curriculum.data) return;
    const values = Object.entries(hypotheses)
      .filter(([, v]) => v.trim() !== "")
      .map(([node_id, v]) => ({ node_id, value: toDecimal(v) }));
    simulate.mutate({ program_id: program.id, expected_revision: curriculum.data.revision, values });
  }

  function stopSimulation() {
    setSimulating(false);
    setHypotheses({});
    simulate.reset();
  }

  function submitGoal(event: FormEvent) {
    event.preventDefault();
    setObjective.mutate({ node_id: goalNode, target_value: toDecimal(goalValue) }, { onSuccess: () => setGoalValue("") });
  }

  // Idle (disabled) queries do not count as loading: e.g. no program configured yet.
  const loading = [programs, curriculum, records, summary].find((q) => (q.isPending && q.fetchStatus !== "idle") || q.error);

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Résultats et objectifs"
        icon={<GraduationCap className="size-5" aria-hidden="true" />}
        description={program ? `${program.title} · ${program.academic_year}` : undefined}
      />

      <p className="flex items-start gap-2 rounded-lg border border-line bg-surface px-4 py-3 text-sm text-muted">
        <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        Moyennes indicatives calculées à partir de vos notes et des pondérations saisies. Les règles officielles de validation (compensation, notes éliminatoires) ne sont pas vérifiées : aucun statut « validé » n'est affiché.
      </p>

      {programs.data && !program && (
        <EmptyState
          title={`Programme du semestre ${semester} pas encore disponible`}
          description="Il apparaîtra dès qu'il sera récupéré depuis Pamplemousse. Vous pouvez changer de semestre dans Mon profil."
          action={
            <Link to="/profile" className={buttonClasses("secondary", "sm")}>
              Mon profil
            </Link>
          }
        />
      )}
      {loading && <QueryStatus query={loading} />}

      {!loading && program && (
        <>
          <Card>
            <CardHeader title="Mes objectifs" description="La moyenne nécessaire porte sur ce qui reste à noter." />
            <QueryStatus query={required} />
            {required.data?.length === 0 && <p className="mb-4 text-sm text-muted">Aucun objectif fixé.</p>}
            <ul className="mb-4 flex flex-col gap-2">
              {required.data?.map((r) => {
                const objective = objectives.data?.find((o) => o.node_id === r.node_id);
                const f = FEASIBILITY[r.feasibility];
                return (
                  <li key={r.node_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line p-3 text-sm">
                    <Target className="size-4 text-accent" aria-hidden="true" />
                    <span className="font-medium">
                      {title(r.node_id)} : {frNumber(objective?.target_value ?? null)} / 20
                    </span>
                    <Badge tone={f.tone}>{f.text}</Badge>
                    <span className="text-muted">
                      {r.feasibility === "feasible" && r.required_mean && `Il vous faut au moins ${frNumber(r.required_mean)} de moyenne sur les épreuves restantes.`}
                      {r.feasibility === "impossible" && "Même 20 partout ne suffirait pas : envisagez un objectif plus bas."}
                      {r.feasibility === "guaranteed" && "L'objectif est atteint quelles que soient les notes restantes."}
                    </span>
                  </li>
                );
              })}
            </ul>
            <form onSubmit={submitGoal} className="grid gap-3 sm:grid-cols-[2fr_1fr_auto] sm:items-end">
              <SelectField
                label="Objectif pour"
                value={goalNode}
                onChange={(e) => setGoalNode(e.target.value)}
                options={[{ value: "", label: "Choisir" }, ...nodes.filter((n) => n.kind !== "assessment").map((n) => ({ value: n.id, label: n.title }))]}
              />
              <TextField label="Moyenne visée" inputMode="decimal" placeholder="13" value={goalValue} onChange={(e) => setGoalValue(e.target.value)} error={setObjective.error?.message} />
              <Button type="submit" disabled={!goalNode || !goalValue} loading={setObjective.isPending}>
                Fixer
              </Button>
            </form>
          </Card>

          <Card>
            <CardHeader
              title="Notes et moyennes"
              description={simulating ? "Mode simulation : les notes hypothétiques ne sont jamais enregistrées." : "Saisissez une note puis Entrée. Une épreuve sans note n'est pas comptée comme 0."}
              actions={
                simulating ? (
                  <>
                    <Button size="sm" onClick={runSimulation} loading={simulate.isPending}>
                      Calculer
                    </Button>
                    <Button size="sm" variant="secondary" onClick={stopSimulation}>
                      Quitter la simulation
                    </Button>
                  </>
                ) : editWeights ? (
                  <>
                    <Button size="sm" onClick={saveWeights} loading={saveCurriculum.isPending}>
                      Enregistrer le programme
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        setDraft(null);
                        saveCurriculum.reset();
                      }}
                    >
                      Annuler
                    </Button>
                  </>
                ) : (
                  <>
                    <Button size="sm" variant="secondary" onClick={() => setSimulating(true)}>
                      <FlaskConical className="size-4" aria-hidden="true" />
                      Simuler
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setDraft(nodes)}>
                      <Pencil className="size-4" aria-hidden="true" />
                      Modifier le programme
                    </Button>
                  </>
                )
              }
            />
            {saveCurriculum.error && (
              <div className="mb-3">
                <ErrorPanel message={saveCurriculum.error.message} />
              </div>
            )}
            {simulate.error && (
              <div className="mb-3">
                <ErrorPanel message={simulate.error.message} />
              </div>
            )}
            {simulate.data?.warnings.map((w) => (
              <p key={w} className="mb-3 text-sm text-warning">
                {w}
              </p>
            ))}
            <GradeTree
              nodes={shown}
              records={records.data ?? []}
              summaries={summary.data?.nodes ?? []}
              editing={editWeights}
              onNodeChange={changeNode}
              onAdd={addAssessment}
              onRemove={removeNode}
              simulating={simulating}
              hypotheses={hypotheses}
              onHypothesis={(id, v) => setHypotheses((h) => ({ ...h, [id]: v }))}
              simulation={simulating ? (simulate.data ?? null) : null}
            />
            {editWeights && <p className="mt-3 text-xs text-muted">Renommez, ajoutez (+) ou supprimez des épreuves. Les pourcentages d'un même groupe doivent totaliser 100 % (saisir 0,4 pour 40 %) ; les coefficients sont libres.</p>}
          </Card>
        </>
      )}
    </section>
  );
}

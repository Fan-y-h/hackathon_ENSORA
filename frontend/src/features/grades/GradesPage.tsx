import { useEffect, useMemo, useState } from "react";

import { ApiError, errorMessage } from "../../api/client";
import {
  useCreateGradeRecord,
  useCreateObjectives,
  useCreateProgram,
  useCreateScenario,
  useDeleteGradeRecord,
  useDeleteObjective,
  useDeleteScenario,
  useGradeCurriculum,
  useGradeObjectives,
  useGradePrograms,
  useGradeRecords,
  useGradeScenarios,
  useGradeSummary,
  useGradeValidation,
  useImportGradesFromSources,
  usePatchGradeRecord,
  usePatchGradeNode,
  usePutCurriculum,
  useRequiredGrades,
  useSimulateGrades,
} from "../../api/queries";
import {
  sourcesUnavailable,
  useResults,
  type Results,
  type ResultsAssessment,
  type ResultsSemester,
  type ResultsSubject,
  type ResultsUnit,
} from "../../api/sources";
import type {
  GradeObjective,
  GradeProgram,
  GradeRecord,
  GradeSummaryNode,
  NodeIn,
  NodeOut,
  RequirementOut,
} from "../../api/types";
import { Icon } from "../../components/Icon";
import { Card, Dialog, EmptyState, ErrorPanel, Loading, PageHeader, StatusLine } from "../../components/ui";
import { SyncButton } from "../sources/SyncButton";
import {
  assessmentMark,
  effectiveCoefficient,
  fmt,
  fmtCoef,
  fmtDec,
  parseMark,
  requiredForTarget,
  semesterAverage,
  subjectMark,
  unitAverage,
  type CustomCoefficients,
  type Simulations,
} from "./average";
import "./results.css";

const SIM_KEY = "ensora.results.simulations.v1";
const COEF_KEY = "ensora.results.custom_coefs.v1";

function loadJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

const FEASIBILITY_BADGE: Record<RequirementOut["feasibility"], { label: string; tone: "good" | "warning" | "critical" | "neutral" }> = {
  achieved: { label: "Objectif atteint", tone: "good" },
  guaranteed: { label: "Objectif garanti", tone: "good" },
  feasible: { label: "Atteignable", tone: "neutral" },
  impossible: { label: "Objectif impossible", tone: "critical" },
  unknown: { label: "Faisabilité inconnue", tone: "warning" },
};

const NODE_KIND_LABEL: Record<NodeOut["kind"], string> = {
  year: "Année",
  semester: "Semestre",
  unit: "UE",
  subject: "Matière",
  assessment_group: "Groupe",
  assessment: "Évaluation",
};

/**
 * Grade Tracker & Academic Optimizer (R35–R44):
 * - Calculator mode: backed by `/api/v1/grades/*` (server Decimal arithmetic, official/custom/simulated
 *   distinction, editable missing coefficients, objectives, required marks, saved scenarios).
 * - Pamplemousse sheet mode: backed by `/api/v1/results` (read-only official tree + local what-if &
 *   custom coefficients + 1-click import bridge into the calculator).
 */
export function GradesPage() {
  const programs = useGradePrograms();
  const [academicYear, setAcademicYear] = useState("");
  const availableResults = useResults();
  const results = useResults(academicYear || undefined);
  const importBridge = useImportGradesFromSources();

  const hasPrograms = (programs.data?.length ?? 0) > 0;
  const hasPamplemousse = (results.data?.semesters.length ?? 0) > 0;
  const [tab, setTab] = useState<"calculator" | "pamplemousse" | null>(null);
  const activeTab: "calculator" | "pamplemousse" =
    tab ?? (hasPrograms ? "calculator" : hasPamplemousse ? "pamplemousse" : "calculator");

  const [selectedProgramId, setSelectedProgramId] = useState<string>("");
  const [creatingProgram, setCreatingProgram] = useState(false);

  const activeProgram =
    programs.data?.find((p) => p.id === selectedProgramId) ?? programs.data?.[0] ?? null;

  return (
    <div className="results">
      <div className="page" style={{ paddingBottom: 0 }}>
        <PageHeader
          eyebrow="Suivi académique · R35–R44"
          title="Mes résultats & Objectifs"
          actions={
            <>
              <div className="segmented" role="group" aria-label="Vue des résultats">
                <button
                  type="button"
                  aria-pressed={activeTab === "calculator"}
                  onClick={() => setTab("calculator")}
                >
                  Calculateur & Objectifs
                </button>
                <button
                  type="button"
                  aria-pressed={activeTab === "pamplemousse"}
                  onClick={() => setTab("pamplemousse")}
                >
                  Relevé Pamplemousse
                </button>
              </div>
              {!!availableResults.data?.academic_years?.length && activeTab === "pamplemousse" && (
                <label className="field"><span>Année du relevé</span><select className="select" value={academicYear || availableResults.data.academic_year || ""} onChange={(event) => setAcademicYear(event.target.value)}>
                  {availableResults.data.academic_years.map((year) => <option value={year} key={year}>{year}</option>)}
                </select></label>
              )}
              {hasPamplemousse && (
                <button
                  type="button"
                  className="btn btn--small"
                  disabled={importBridge.isPending}
                  onClick={() =>
                    importBridge.mutate(
                      { source: "pamplemousse", dry_run: false, academic_year: results.data?.academic_year ?? undefined },
                      {
                        onSuccess: (res) => {
                          if (res.program_id) setSelectedProgramId(res.program_id);
                          setTab("calculator");
                        },
                      },
                    )
                  }
                >
                  <Icon name="refresh" size={14} />
                  {importBridge.isPending ? "Import…" : "Importer Pamplemousse"}
                </button>
              )}
              <button type="button" className="btn btn--small btn--primary" onClick={() => setCreatingProgram(true)}>
                <Icon name="plus" size={14} /> Nouveau cursus
              </button>
              <SyncButton />
            </>
          }
        />

        {importBridge.error && (
          <div className="status status--warning small" role="status">
            {importBridge.error instanceof ApiError && importBridge.error.status === 404
              ? "Le pont d'import automatique Pamplemousse → Calculateur (/api/v1/grades/import) est en attente d'intégration backend."
              : errorMessage(importBridge.error)}
          </div>
        )}
        {importBridge.isSuccess && (
          <div className="status status--good small" role="status">
            <Icon name="check" size={14} /> {importBridge.data.nodes_imported} nœuds et {importBridge.data.grades_imported} notes importés dans « {importBridge.data.program_title} ».
          </div>
        )}
      </div>

      {activeTab === "calculator" ? (
        <div className="page" style={{ paddingTop: 16 }}>
          {programs.isPending ? (
            <Card>
              <Loading lines={6} />
            </Card>
          ) : programs.error ? (
            <ErrorPanel error={programs.error} onRetry={() => void programs.refetch()} />
          ) : !activeProgram ? (
            <Card>
              <EmptyState
                title="Aucun cursus configuré dans le calculateur"
                action={
                  <div className="row">
                    <button type="button" className="btn btn--primary" onClick={() => setCreatingProgram(true)}>
                      <Icon name="plus" size={14} /> Créer un cursus
                    </button>
                    {hasPamplemousse && (
                      <button type="button" className="btn" onClick={() => setTab("pamplemousse")}>
                        Voir le relevé Pamplemousse
                      </button>
                    )}
                  </div>
                }
              >
                Créez votre cursus (Semestre → UE → Matière → Évaluation) ou importez vos notes Pamplemousse pour suivre vos moyennes, coefficients, objectifs et simulations.
              </EmptyState>
            </Card>
          ) : (
            <ProgramCalculatorView
              programs={programs.data!}
              program={activeProgram}
              onSelectProgram={setSelectedProgramId}
            />
          )}
        </div>
      ) : (
        <PamplemousseView resultsQuery={results} />
      )}

      {creatingProgram && (
        <CreateProgramDialog
          onClose={() => setCreatingProgram(false)}
          onCreated={(p) => {
            setSelectedProgramId(p.id);
            setTab("calculator");
            setCreatingProgram(false);
          }}
        />
      )}
    </div>
  );
}

/* ========================================================================== */
/*                    MODE 1 : BACKEND GRADE TRACKER (S4)                     */
/* ========================================================================== */

function ProgramCalculatorView({
  programs,
  program,
  onSelectProgram,
}: {
  programs: GradeProgram[];
  program: GradeProgram;
  onSelectProgram: (id: string) => void;
}) {
  const curriculum = useGradeCurriculum(program.id);
  const summary = useGradeSummary(program.id);
  const records = useGradeRecords(program.id);
  const objectives = useGradeObjectives(program.id);
  const scenarios = useGradeScenarios(program.id);
  const validation = useGradeValidation(program.id);

  const createRecord = useCreateGradeRecord();
  const patchRecord = usePatchGradeRecord();
  const deleteRecord = useDeleteGradeRecord();
  const createObjective = useCreateObjectives();
  const deleteObjective = useDeleteObjective();
  const putCurriculum = usePutCurriculum();
  const patchNode = usePatchGradeNode();
  const requiredMutation = useRequiredGrades();
  const simulateMutation = useSimulateGrades();
  const createScenario = useCreateScenario();
  const deleteScenario = useDeleteScenario();

  const [markDrafts, setMarkDrafts] = useState<Record<string, string>>({});
  const [simDrafts, setSimDrafts] = useState<Record<string, string>>({});
  const [coefDrafts, setCoefDrafts] = useState<Record<string, string>>({});
  const [objNodeId, setObjNodeId] = useState("");
  const [objTarget, setObjTarget] = useState("14");
  const [scenarioName, setScenarioName] = useState("");
  const [addingNodeParent, setAddingNodeParent] = useState<NodeOut | null>(null);

  const nodes = useMemo(() => curriculum.data?.nodes ?? [], [curriculum.data]);
  const summaryById = useMemo(
    () => new Map<string, GradeSummaryNode>((summary.data?.nodes ?? []).map((n) => [n.node_id, n])),
    [summary.data],
  );
  const recordByNodeId = useMemo(() => {
    const map = new Map<string, GradeRecord>();
    for (const r of records.data ?? []) {
      if (r.kind === "actual" || !map.has(r.node_id)) map.set(r.node_id, r);
    }
    return map;
  }, [records.data]);
  const objectiveByNodeId = useMemo(
    () => new Map<string, GradeObjective>((objectives.data ?? []).filter((o) => o.active).map((o) => [o.node_id, o])),
    [objectives.data],
  );

  // Recompute required marks from server whenever objectives or program revision change.
  const objCount = objectives.data?.length ?? 0;
  const programRevision = summary.data?.revision ?? program.revision;
  useEffect(() => {
    if (objCount > 0 && nodes.length > 0) {
      requiredMutation.mutate({ program_id: program.id, assumptions: [] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [program.id, objCount, programRevision, nodes.length]);

  const reqByNodeId = useMemo(
    () => new Map<string, RequirementOut>((requiredMutation.data?.requirements ?? []).map((r) => [r.node_id, r])),
    [requiredMutation.data],
  );
  const simProjByNodeId = useMemo(
    () => new Map((simulateMutation.data?.projections ?? []).map((p) => [p.node_id, p])),
    [simulateMutation.data],
  );

  const childrenOf = useMemo(() => {
    const map = new Map<string | null, NodeOut[]>();
    for (const n of nodes) {
      const k = n.parent_key ?? null;
      map.set(k, [...(map.get(k) ?? []), n]);
    }
    return map;
  }, [nodes]);

  const root = nodes.find((n) => n.parent_key === null);
  const rootSummary = root ? summaryById.get(root.id) : undefined;
  const rootSim = root ? simProjByNodeId.get(root.id) : undefined;

  const simValues = useMemo(
    () =>
      Object.entries(simDrafts)
        .map(([node_id, raw]) => ({ node_id, value: parseMark(raw) }))
        .filter((x): x is { node_id: string; value: number } => x.value !== null && x.value >= 0),
    [simDrafts],
  );

  const runSimulation = () => {
    if (simValues.length === 0) return;
    simulateMutation.mutate({
      program_id: program.id,
      expected_revision: curriculum.data?.revision,
      values: simValues.map((v) => ({ node_id: v.node_id, value: v.value })),
    });
  };

  const saveMark = (node: NodeOut, kind: "actual" | "estimated") => {
    const raw = markDrafts[node.id];
    const val = parseMark(raw);
    if (val === null) return;
    const existing = recordByNodeId.get(node.id);
    if (existing && existing.kind === kind) {
      patchRecord.mutate(
        { id: existing.id, body: { expected_revision: existing.revision, value: val } },
        { onSuccess: () => setMarkDrafts((d) => { const n = { ...d }; delete n[node.id]; return n; }) },
      );
    } else {
      createRecord.mutate(
        { node_id: node.id, kind, value: val, grade_status: "graded" },
        { onSuccess: () => setMarkDrafts((d) => { const n = { ...d }; delete n[node.id]; return n; }) },
      );
    }
  };

  const saveCoefficient = (node: NodeOut) => {
    const raw = coefDrafts[node.id];
    const val = parseMark(raw);
    if (val === null || val < 0 || !curriculum.data) return;
    patchNode.mutate(
      { id: node.id, body: { expected_revision: curriculum.data.revision, weight: val } },
      {
        onSuccess: () =>
          setCoefDrafts((d) => {
            const next = { ...d };
            delete next[node.id];
            return next;
          }),
      },
    );
  };

  const renderTree = (parentKey: string | null, depth: number): React.ReactNode => {
    const list = childrenOf.get(parentKey) ?? [];
    return list.map((node) => {
      const sum = summaryById.get(node.id);
      const rec = recordByNodeId.get(node.id);
      const obj = objectiveByNodeId.get(node.id);
      const req = reqByNodeId.get(node.id);
      const proj = simProjByNodeId.get(node.id);
      const isOfficial = rec?.provenance === "pamplemousse";
      const currentVal = markDrafts[node.id] ?? (rec ? String(rec.value) : "");
      const currentCoef = coefDrafts[node.id] ?? String(node.weight ?? "");
      const coefEdited = coefDrafts[node.id] !== undefined && coefDrafts[node.id] !== String(node.weight ?? "");
      const markEdited = markDrafts[node.id] !== undefined && markDrafts[node.id] !== (rec ? String(rec.value) : "");
      const reqBadge = req ? FEASIBILITY_BADGE[req.feasibility] : null;

      return (
        <div key={node.id} className={`grade-node grade-node--${node.kind}`} style={{ marginLeft: depth > 0 ? Math.min(depth * 18, 54) : 0 }}>
          <div className="grade-node__row">
            <div className="grade-node__title">
              <span className="pill">{NODE_KIND_LABEL[node.kind]}</span>
              <strong>{node.title}</strong>
              {rec && (
                <span className={`pill${isOfficial ? "" : " status--good"}`}>
                  {isOfficial ? "Officielle" : rec.kind === "estimated" ? "Prévision" : "Personnalisée"}
                </span>
              )}
              {!rec && simDrafts[node.id] && <span className="pill status--warning">Simulée</span>}
              {obj && (
                <span className="pill">
                  Objectif : {fmtDec(obj.target, 1)}/20
                </span>
              )}
              {req && reqBadge && (
                <span className={`pill${reqBadge.tone === "neutral" ? "" : ` status--${reqBadge.tone}`}`}>
                  {req.feasibility === "feasible" && req.required_mean
                    ? `Nécessaire : ${fmtDec(req.required_mean)}/20`
                    : req.feasibility === "impossible"
                      ? `Impossible (max ${fmtDec(req.bound_max)}/20)`
                      : reqBadge.label}
                </span>
              )}
            </div>

            <div className="grade-node__controls">
              {node.parent_key !== null && (
                <label className="grade-inline-field" title="Coefficient académique (jamais remplacé par les crédits ECTS)">
                  <span className="xsmall muted">Coef</span>
                  <input
                    className="input ue__input"
                    style={{ width: 64 }}
                    inputMode="decimal"
                    aria-label={`Coefficient de ${node.title}`}
                    value={currentCoef}
                    onChange={(e) => setCoefDrafts((d) => ({ ...d, [node.id]: e.target.value }))}
                  />
                  {coefEdited && (
                    <button
                      type="button"
                      className="btn btn--small"
                      disabled={patchNode.isPending}
                      onClick={() => saveCoefficient(node)}
                    >
                      OK
                    </button>
                  )}
                </label>
              )}

              {node.is_leaf ? (
                <>
                  <label className="grade-inline-field">
                    <span className="xsmall muted">Note /{fmtCoef(node.scale_max)}</span>
                    <input
                      className={`input ue__input${rec ? " ue__mark--official" : ""}`}
                      style={{ width: 76 }}
                      inputMode="decimal"
                      placeholder="–"
                      aria-label={`Note réelle pour ${node.title}`}
                      readOnly={isOfficial}
                      value={currentVal}
                      onChange={(e) => setMarkDrafts((d) => ({ ...d, [node.id]: e.target.value }))}
                    />
                  </label>
                  {markEdited && !isOfficial && (
                    <button
                      type="button"
                      className="btn btn--small btn--primary"
                      disabled={createRecord.isPending || patchRecord.isPending}
                      onClick={() => saveMark(node, "actual")}
                    >
                      Enregistrer
                    </button>
                  )}
                  {rec && !isOfficial && (
                    <button
                      type="button"
                      className="btn btn--small btn--ghost btn--danger"
                      aria-label={`Supprimer la note de ${node.title}`}
                      disabled={deleteRecord.isPending}
                      onClick={() => deleteRecord.mutate(rec.id)}
                    >
                      <Icon name="trash" size={14} />
                    </button>
                  )}
                  {!rec && (
                    <label className="grade-inline-field">
                      <span className="xsmall muted">Simul.</span>
                      <input
                        className={`input ue__input${simDrafts[node.id] ? " ue__input--sim" : ""}`}
                        style={{ width: 72 }}
                        inputMode="decimal"
                        placeholder="what-if"
                        aria-label={`Note simulée pour ${node.title}`}
                        value={simDrafts[node.id] ?? ""}
                        onChange={(e) => setSimDrafts((d) => ({ ...d, [node.id]: e.target.value }))}
                      />
                    </label>
                  )}
                </>
              ) : (
                <div className="row" style={{ gap: 8 }}>
                  <span className="ue__avg" style={{ fontSize: 18 }}>
                    {fmtDec(sum?.provisional)}
                    <span className="xsmall muted"> /20</span>
                  </span>
                  {proj?.projected && (
                    <span className="pill status--warning">
                      Simulé : {fmtDec(proj.projected)}/20
                    </span>
                  )}
                  <span className="xsmall muted">
                    {sum ? `${Math.round(Number(sum.coverage) * 100)} % noté` : ""}
                  </span>
                  {node.kind !== "assessment" && (
                    <button
                      type="button"
                      className="btn btn--small btn--ghost"
                      onClick={() => setAddingNodeParent(node)}
                      title="Ajouter un élément enfant"
                    >
                      <Icon name="plus" size={13} />
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
          {renderTree(node.key, depth + 1)}
        </div>
      );
    });
  };

  return (
    <div className="stack">
      {/* Program selector & Hero */}
      <header className="results__hero" style={{ borderRadius: 14 }}>
        <div className="results__wrap">
          <div className="results__top">
            <div>
              <h2 style={{ fontSize: 28, color: "#fff", margin: "0 0 4px" }}>{program.title}</h2>
              <p>
                {[program.academic_year, program.cohort, program.specialization, `Source : ${program.provenance}`]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </div>
            {programs.length > 1 && (
              <select
                className="select"
                style={{ width: "auto", background: "#fff", color: "#15314b" }}
                aria-label="Choisir un cursus"
                value={program.id}
                onChange={(e) => onSelectProgram(e.target.value)}
              >
                {programs.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.title}
                  </option>
                ))}
              </select>
            )}
          </div>
          <div className="results__grid">
            <div>
              <div className="results__big">
                {fmtDec(rootSim?.projected ?? rootSummary?.provisional)}
                <small> /20</small>
              </div>
              <div className="results__sub">
                {rootSim?.projected
                  ? `Moyenne simulée (actuelle : ${fmtDec(rootSummary?.provisional)} /20 · ne modifie pas les vraies notes)`
                  : rootSummary
                    ? `Moyenne actuelle provisoire · couverture ${Math.round(Number(rootSummary.coverage) * 100)} % · bornes [${fmtDec(rootSummary.bound_min)} ; ${fmtDec(rootSummary.bound_max)}]`
                    : "Ajoutez des matières et notes pour calculer votre moyenne."}
              </div>
              <div className="results__sub" style={{ marginTop: 4 }}>
                Statut officiel :{" "}
                <strong>
                  {validation.data?.verification_status === "verified" ? "Règles vérifiées" : "Règles officielles inconnues / non vérifiées"}
                </strong>
              </div>
            </div>

            <div className="results__bars">
              {(summary.data?.nodes ?? [])
                .filter((n) => n.kind === "unit" || n.kind === "semester")
                .slice(0, 6)
                .map((n) => {
                  const valStr = simProjByNodeId.get(n.node_id)?.projected ?? n.provisional;
                  const val = valStr ? Number.parseFloat(valStr) : null;
                  return (
                    <div key={n.node_id} className="results__bar">
                      <div>
                        <div className="results__label">
                          {n.title} ({Math.round(Number(n.coverage) * 100)} %)
                        </div>
                        <div className="results__track" role="img" aria-label={`${n.title} : ${fmt(val)} sur 20`}>
                          <div className="results__fill" style={{ width: `${val === null ? 0 : Math.min(100, (val / 20) * 100)}%` }} />
                          <div className="results__ten" />
                        </div>
                      </div>
                      <span className="results__v">{fmt(val)}</span>
                    </div>
                  );
                })}
              <div className="results__legend">Le trait jaune marque 10/20. Arithmétique décimale calculée côté serveur.</div>
            </div>
          </div>
        </div>
      </header>

      {(createRecord.error || patchRecord.error || patchNode.error || putCurriculum.error || simulateMutation.error || createObjective.error) && (
        <div className="status status--critical small" role="alert">
          {errorMessage(createRecord.error ?? patchRecord.error ?? patchNode.error ?? putCurriculum.error ?? simulateMutation.error ?? createObjective.error)}
        </div>
      )}

      {/* Main hierarchy + Sidebar (Objectives & Scenarios) */}
      <div className="split">
        <div className="split__main stack">
          <Card
            title="Arborescence Semestre → UE → Matière → Évaluation"
            subtitle="Saisissez vos notes réelles ou personnalisez les coefficients manquants. Les cases « Simul. » restent isolées."
            aside={
              <div className="row">
                <button
                  type="button"
                  className="btn btn--small btn--primary"
                  disabled={simValues.length === 0 || simulateMutation.isPending}
                  onClick={runSimulation}
                >
                  {simulateMutation.isPending ? "Simulation…" : `Simuler (${simValues.length})`}
                </button>
                {(simValues.length > 0 || simulateMutation.data) && (
                  <button
                    type="button"
                    className="btn btn--small"
                    onClick={() => {
                      setSimDrafts({});
                      simulateMutation.reset();
                    }}
                  >
                    Réinitialiser la simulation
                  </button>
                )}
              </div>
            }
          >
            {curriculum.isPending || summary.isPending ? (
              <Loading lines={6} />
            ) : curriculum.error ? (
              <ErrorPanel error={curriculum.error} onRetry={() => void curriculum.refetch()} />
            ) : nodes.length === 0 ? (
              <EmptyState
                title="Cursus vide"
                action={
                  <button
                    type="button"
                    className="btn btn--primary"
                    disabled={putCurriculum.isPending}
                    onClick={() =>
                      putCurriculum.mutate({
                        programId: program.id,
                        body: {
                          expected_revision: curriculum.data?.revision,
                          nodes: [
                            { key: "sem-1", parent_key: null, kind: "semester", title: "Semestre 1", weight: 1, weight_mode: "coefficient", weight_provenance: "manual", scale_min: 0, scale_max: 20 },
                            { key: "ue-1", parent_key: "sem-1", kind: "unit", title: "UE 1", weight: 1, weight_mode: "coefficient", weight_provenance: "manual", scale_min: 0, scale_max: 20 },
                            { key: "mat-1", parent_key: "ue-1", kind: "subject", title: "Matière 1", weight: 2, weight_mode: "coefficient", weight_provenance: "manual", scale_min: 0, scale_max: 20 },
                            { key: "eval-1", parent_key: "mat-1", kind: "assessment", title: "Examen final", weight: 1, weight_mode: "coefficient", weight_provenance: "manual", scale_min: 0, scale_max: 20 },
                          ],
                        },
                      })
                    }
                  >
                    Initialiser un squelette Semestre / UE / Matière
                  </button>
                }
              />
            ) : (
              <div className="stack" style={{ gap: 6 }}>
                {renderTree(null, 0)}
              </div>
            )}
          </Card>
        </div>

        <aside className="split__side">
          {/* Academic Objectives */}
          <Card title="Objectifs académiques" subtitle="Calcule automatiquement la note nécessaire sur les épreuves restantes.">
            {(objectives.data ?? []).length === 0 ? (
              <p className="small muted">Aucun objectif défini pour ce cursus.</p>
            ) : (
              <ul className="list">
                {(objectives.data ?? []).map((obj) => {
                  const node = nodes.find((n) => n.id === obj.node_id);
                  const req = reqByNodeId.get(obj.node_id);
                  const badge = req ? FEASIBILITY_BADGE[req.feasibility] : null;
                  return (
                    <li key={obj.id} style={{ alignItems: "flex-start" }}>
                      <div className="stack" style={{ gap: 2, flex: 1 }}>
                        <strong className="small">
                          {node?.title ?? obj.node_key} · viser {fmtDec(obj.target, 1)}/20
                        </strong>
                        {req && badge && (
                          <span className="xsmall">
                            <span className={`pill${badge.tone === "neutral" ? "" : ` status--${badge.tone}`}`}>
                              {badge.label}
                            </span>{" "}
                            {req.feasibility === "feasible" && req.required_mean && (
                              <span>Note nécessaire : <strong>{fmtDec(req.required_mean)}/20</strong></span>
                            )}
                            {req.feasibility === "impossible" && (
                              <span>Max atteignable : <strong>{fmtDec(req.bound_max)}/20</strong></span>
                            )}
                          </span>
                        )}
                      </div>
                      <button
                        type="button"
                        className="btn btn--small btn--ghost btn--danger"
                        aria-label={`Supprimer l'objectif ${node?.title ?? obj.node_key}`}
                        disabled={deleteObjective.isPending}
                        onClick={() => deleteObjective.mutate(obj.id)}
                      >
                        <Icon name="trash" size={14} />
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}

            {nodes.length > 0 && (
              <form
                className="stack"
                style={{ borderTop: "1px solid var(--grid)", paddingTop: 10 }}
                onSubmit={(e) => {
                  e.preventDefault();
                  const targetNum = parseMark(objTarget);
                  const targetNode = objNodeId || root?.id;
                  if (targetNum === null || targetNum < 0 || targetNum > 20 || !targetNode) return;
                  createObjective.mutate({ program_id: program.id, node_id: targetNode, target: targetNum });
                }}
              >
                <label className="field">
                  <span>Cible</span>
                  <select className="select" value={objNodeId || root?.id || ""} onChange={(e) => setObjNodeId(e.target.value)}>
                    {nodes
                      .filter((n) => !n.is_leaf || n.kind === "subject")
                      .map((n) => (
                        <option key={n.id} value={n.id}>
                          [{NODE_KIND_LABEL[n.kind]}] {n.title}
                        </option>
                      ))}
                  </select>
                </label>
                <div className="row">
                  <label className="field" style={{ flex: 1 }}>
                    <span>Objectif (/20)</span>
                    <input className="input" type="number" min={0} max={20} step={0.5} value={objTarget} onChange={(e) => setObjTarget(e.target.value)} />
                  </label>
                  <button type="submit" className="btn btn--primary" style={{ alignSelf: "flex-end" }} disabled={createObjective.isPending}>
                    Ajouter
                  </button>
                </div>
              </form>
            )}
          </Card>

          {/* Saved What-If Scenarios */}
          <Card title="Scénarios simulés" subtitle="Enregistrez des hypothèses sans modifier vos notes réelles.">
            {(scenarios.data ?? []).length === 0 ? (
              <p className="small muted">Aucun scénario sauvegardé.</p>
            ) : (
              <ul className="list">
                {(scenarios.data ?? []).map((sc) => (
                  <li key={sc.id}>
                    <div className="stack" style={{ gap: 2, flex: 1 }}>
                      <strong className="small">{sc.name}</strong>
                      <span className="xsmall muted">
                        {sc.values.length} hypothèse(s){sc.stale ? " · données modifiées" : ""}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="btn btn--small"
                      onClick={() => {
                        const loaded: Record<string, string> = {};
                        for (const v of sc.values) loaded[v.node_id] = String(v.value);
                        setSimDrafts(loaded);
                        if (sc.values.length > 0) {
                          simulateMutation.mutate({
                            program_id: program.id,
                            values: sc.values.map((v) => ({ node_id: v.node_id, value: Number(v.value) })),
                          });
                        }
                      }}
                    >
                      Appliquer
                    </button>
                    <button
                      type="button"
                      className="btn btn--small btn--ghost btn--danger"
                      aria-label={`Supprimer le scénario ${sc.name}`}
                      onClick={() => deleteScenario.mutate(sc.id)}
                    >
                      <Icon name="trash" size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <form
              className="row"
              style={{ borderTop: "1px solid var(--grid)", paddingTop: 10 }}
              onSubmit={(e) => {
                e.preventDefault();
                if (!scenarioName.trim() || simValues.length === 0) return;
                createScenario.mutate(
                  {
                    program_id: program.id,
                    name: scenarioName.trim(),
                    values: simValues.map((v) => ({ node_id: v.node_id, value: v.value })),
                  },
                  { onSuccess: () => setScenarioName("") },
                );
              }}
            >
              <input
                className="input"
                style={{ flex: 1 }}
                placeholder="Nom du scénario"
                aria-label="Nom du scénario"
                value={scenarioName}
                onChange={(e) => setScenarioName(e.target.value)}
              />
              <button
                type="submit"
                className="btn btn--small"
                disabled={!scenarioName.trim() || simValues.length === 0 || createScenario.isPending}
              >
                Sauvegarder
              </button>
            </form>
          </Card>
        </aside>
      </div>

      {addingNodeParent && curriculum.data && (
        <AddNodeDialog
          parent={addingNodeParent}
          nodes={nodes}
          revision={curriculum.data.revision}
          programId={program.id}
          onClose={() => setAddingNodeParent(null)}
        />
      )}
    </div>
  );
}

function CreateProgramDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (p: GradeProgram) => void }) {
  const create = useCreateProgram();
  const [title, setTitle] = useState("ENSAI 2026-2027");
  const [year, setYear] = useState("2026-2027");
  const [cohort, setCohort] = useState<"1A" | "2A" | "3A" | "">("2A");

  return (
    <Dialog title="Nouveau cursus académique" onClose={onClose}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(
            {
              title: title.trim(),
              academic_year: year.trim() || null,
              cohort: cohort || null,
            },
            { onSuccess: onCreated },
          );
        }}
      >
        <label className="field">
          <span>Intitulé</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} />
        </label>
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label className="field">
            <span>Année (AAAA-AAAA)</span>
            <input className="input" value={year} onChange={(e) => setYear(e.target.value)} pattern="\d{4}-\d{4}" />
          </label>
          <label className="field">
            <span>Promo</span>
            <select className="select" value={cohort} onChange={(e) => setCohort(e.target.value as typeof cohort)}>
              <option value="">—</option>
              <option value="1A">1A</option>
              <option value="2A">2A</option>
              <option value="3A">3A</option>
            </select>
          </label>
        </div>
        {create.error && <div className="status status--critical small">{errorMessage(create.error)}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Annuler</button>
          <button type="submit" className="btn btn--primary" disabled={create.isPending || !title.trim()}>Créer</button>
        </div>
      </form>
    </Dialog>
  );
}

function AddNodeDialog({
  parent,
  nodes,
  revision,
  programId,
  onClose,
}: {
  parent: NodeOut;
  nodes: NodeOut[];
  revision: number;
  programId: string;
  onClose: () => void;
}) {
  const put = usePutCurriculum();
  const nextKind: NodeOut["kind"] =
    parent.kind === "year" ? "semester" : parent.kind === "semester" ? "unit" : parent.kind === "unit" ? "subject" : "assessment";
  const [kind, setKind] = useState<NodeOut["kind"]>(nextKind);
  const [title, setTitle] = useState("");
  const [weight, setWeight] = useState("1");
  const [scaleMax, setScaleMax] = useState("20");

  return (
    <Dialog title={`Ajouter dans « ${parent.title} »`} onClose={onClose}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          const w = parseMark(weight);
          const sm = parseMark(scaleMax);
          if (!title.trim() || w === null || w <= 0 || sm === null || sm <= 0) return;
          const slug = `${kind}-${Date.now().toString(36)}`;
          const nextNodes = [
            ...nodes.map<NodeIn>((n) => ({
              key: n.key,
              parent_key: n.parent_key,
              kind: n.kind,
              title: n.title,
              weight: n.weight ?? null,
              weight_provenance: n.weight_provenance === "official" || n.weight_provenance === "unknown" ? n.weight_provenance : "manual",
              ects: n.ects,
              weight_mode: (n.weight_mode === "fraction" ? "fraction" : "coefficient") as "coefficient" | "fraction",
              scale_min: Number(n.scale_min),
              scale_max: Number(n.scale_max),
              subject_id: n.subject_id,
              event_id: n.event_id,
            })),
            {
              key: slug,
              parent_key: parent.key,
              kind,
              title: title.trim(),
              weight: w,
              weight_mode: "coefficient" as const,
              weight_provenance: "manual" as const,
              scale_min: 0,
              scale_max: sm,
            },
          ];
          put.mutate({ programId, body: { expected_revision: revision, nodes: nextNodes } }, { onSuccess: onClose });
        }}
      >
        <label className="field">
          <span>Niveau</span>
          <select className="select" value={kind} onChange={(e) => setKind(e.target.value as NodeOut["kind"])}>
            <option value="semester">Semestre</option>
            <option value="unit">UE</option>
            <option value="subject">Matière</option>
            <option value="assessment">Évaluation</option>
          </select>
        </label>
        <label className="field">
          <span>Intitulé</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} />
        </label>
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label className="field">
            <span>Coefficient</span>
            <input className="input" inputMode="decimal" value={weight} onChange={(e) => setWeight(e.target.value)} required />
          </label>
          <label className="field">
            <span>Barème max</span>
            <input className="input" inputMode="decimal" value={scaleMax} onChange={(e) => setScaleMax(e.target.value)} required />
          </label>
        </div>
        {put.error && <div className="status status--critical small">{errorMessage(put.error)}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Annuler</button>
          <button type="submit" className="btn btn--primary" disabled={put.isPending}>Ajouter</button>
        </div>
      </form>
    </Dialog>
  );
}

/* ========================================================================== */
/*                   MODE 2 : PAMPLEMOUSSE SYNCED RESULTS                     */
/* ========================================================================== */

function PamplemousseView({
  resultsQuery,
}: {
  resultsQuery: { data: Results | undefined; isPending: boolean; error: unknown; refetch: () => unknown };
}) {
  const [sims, setSims] = useState<Simulations>(() => loadJson(SIM_KEY, {}));
  const [customCoefs, setCustomCoefs] = useState<CustomCoefficients>(() => loadJson(COEF_KEY, {}));
  const [semKey, setSemKey] = useState<string | null>(null);
  const [targetStr, setTargetStr] = useState("12");

  useEffect(() => {
    localStorage.setItem(SIM_KEY, JSON.stringify(sims));
  }, [sims]);

  useEffect(() => {
    localStorage.setItem(COEF_KEY, JSON.stringify(customCoefs));
  }, [customCoefs]);

  const semesters = useMemo(() => resultsQuery.data?.semesters ?? [], [resultsQuery.data]);
  const sem = semesters.find((s) => s.key === semKey) ?? semesters.find((s) => s.units.length) ?? semesters[0];
  const setSim = (key: string, value: string) => setSims((all) => ({ ...all, [key]: value }));
  const setCoef = (key: string, value: string) => setCustomCoefs((all) => ({ ...all, [key]: value }));

  if (resultsQuery.isPending) return <div className="page"><Loading lines={6} /></div>;
  if (resultsQuery.error) {
    return (
      <div className="page">
        {sourcesUnavailable(resultsQuery.error) ? (
          <Card>
            <EmptyState title="Relevé Pamplemousse indisponible" action={<SyncButton />}>
              Connectez Pamplemousse dans Profil › Sources, puis synchronisez.
            </EmptyState>
          </Card>
        ) : (
          <ErrorPanel error={resultsQuery.error} onRetry={() => void resultsQuery.refetch()} />
        )}
      </div>
    );
  }
  if (!sem) {
    return (
      <div className="page">
        <Card>
          <EmptyState title="Aucun résultat synchronisé depuis Pamplemousse" action={<SyncButton />}>
            Connectez Pamplemousse dans Profil › Sources puis synchronisez, ou utilisez l'onglet « Calculateur & Objectifs ».
          </EmptyState>
        </Card>
      </div>
    );
  }

  const semAvg = semesterAverage(sem, sims, customCoefs);
  const targetVal = parseMark(targetStr);
  const targetReq = targetVal !== null && targetVal >= 0 && targetVal <= 20 ? requiredForTarget(semAvg, targetVal) : null;

  return (
    <div>
      <ResultsHero
        semester={sem}
        semesters={semesters}
        onSemester={setSemKey}
        sims={sims}
        customCoefs={customCoefs}
        general={resultsQuery.data!.general_average}
        year={resultsQuery.data!.academic_year}
        generalMaximum={resultsQuery.data!.general_average_maximum}
      />
      <div className="results__body">
        <div className="results__tools">
          <div className="row" style={{ gap: 10 }}>
            <label className="grade-inline-field">
              <span className="small strong">Objectif semestre (/20) :</span>
              <input
                className="input ue__input"
                style={{ width: 68 }}
                inputMode="decimal"
                value={targetStr}
                onChange={(e) => setTargetStr(e.target.value)}
              />
            </label>
            {targetReq && (
              <span
                className={`pill${
                  targetReq.status === "impossible"
                    ? " status--critical"
                    : targetReq.status === "achieved" || targetReq.status === "guaranteed"
                      ? " status--good"
                      : targetReq.status === "unknown"
                        ? " status--warning"
                        : ""
                }`}
              >
                {targetReq.status === "feasible" && `Note nécessaire : ${fmt(targetReq.requiredMark)} /20`}
                {targetReq.status === "impossible" && `Objectif impossible (max ${fmt(targetReq.maxReachable)} /20)`}
                {targetReq.status === "achieved" && "Objectif atteint"}
                {targetReq.status === "guaranteed" && "Objectif garanti"}
                {targetReq.status === "unknown" && "Des coefficients ou barèmes manquent pour calculer la note nécessaire"}
              </span>
            )}
          </div>
          <div className="row">
            <button
              type="button"
              className="btn btn--small"
              disabled={!Object.keys(sims).length && !Object.keys(customCoefs).length}
              onClick={() => {
                setSims({});
                setCustomCoefs({});
              }}
            >
              Effacer simulations & coefs perso
            </button>
          </div>
        </div>
        {semAvg.unknownCoefCount > 0 && (
          <div style={{ marginBottom: 14 }}>
            <StatusLine tone="warning" label={`${semAvg.unknownCoefCount} coefficient(s) manquant(s)`}>
              Saisissez le coefficient personnalisé dans la colonne Coef (les crédits ECTS ne sont jamais utilisés comme coefficient).
            </StatusLine>
          </div>
        )}
        {sem.units.map((u) => (
          <UnitCard key={u.key} unit={u} sims={sims} customCoefs={customCoefs} onSim={setSim} onCoef={setCoef} />
        ))}
      </div>
    </div>
  );
}

function ResultsHero({
  semester,
  semesters,
  onSemester,
  sims,
  customCoefs,
  general,
  generalMaximum,
  year,
}: {
  semester: ResultsSemester;
  semesters: ResultsSemester[];
  onSemester: (key: string) => void;
  sims: Simulations;
  customCoefs: CustomCoefficients;
  general: number | null;
  generalMaximum?: number | null;
  year: string | null;
}) {
  const computed = semesterAverage(semester, sims, customCoefs);
  const showOfficial = semester.official_average !== null && !computed.simulated;
  const value = showOfficial ? semester.official_average : computed.value;
  const maximum = showOfficial ? semester.official_average_maximum : 20;

  return (
    <header className="results__hero">
      <div className="results__wrap">
        <div className="results__top">
          <div>
            <h2 style={{ fontSize: "clamp(24px, 3vw, 34px)", color: "#fff", margin: "0 0 4px" }}>
              Ma moyenne · {semester.title}
            </h2>
            <p>
              {year ? `${year} · ` : ""}Pamplemousse{general !== null ? ` · moyenne générale officielle ${fmt(general)}${generalMaximum ? ` / ${generalMaximum}` : " (barème non publié)"}` : ""}
            </p>
          </div>
          {semesters.length > 1 && (
            <div className="results__sems" role="group" aria-label="Semestre">
              {semesters.map((s) => (
                <button key={s.key} type="button" aria-pressed={s.key === semester.key} onClick={() => onSemester(s.key)}>
                  {s.title}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="results__grid">
          <div>
            <div className="results__big">
              {fmt(value)}
              {value !== null && <small>{maximum ? ` / ${maximum}` : " · barème non publié"}</small>}
            </div>
            <div className="results__sub">
              {value === null
                ? "Aucune note pour l'instant."
                : showOfficial
                  ? "Moyenne officielle Pamplemousse"
                  : `Sur ${fmtCoef(computed.markedCoef)} coef. notés / ${fmtCoef(computed.totalCoef)}${computed.simulated ? " · simulée (provisoire)" : computed.markedCoef < computed.totalCoef ? " · provisoire" : ""}`}
            </div>
          </div>
          <div className="results__bars">
            {semester.units.map((u) => {
              const uAvg = unitAverage(u, sims, customCoefs);
              const official = u.official_average !== null && !uAvg.simulated;
              const avg = official ? u.official_average : uAvg.value;
              const unitMaximum = official ? u.official_average_maximum : 20;
              return (
                <div key={u.key} className="results__bar" title={`${u.title} : ${fmt(avg)}`}>
                  <div>
                    <div className="results__label">{u.title}</div>
                    <div className="results__track" role="img" aria-label={`${u.title} · ${fmt(avg)}${unitMaximum ? ` sur ${unitMaximum}` : " · barème non publié"}`}>
                      <div className="results__fill" style={{ width: `${avg === null || !unitMaximum ? 0 : Math.min(100, (avg / unitMaximum) * 100)}%` }} />
                      {unitMaximum === 20 && <div className="results__ten" />}
                    </div>
                  </div>
                  <span className="results__v">{fmt(avg)}</span>
                </div>
              );
            })}

          </div>
        </div>
      </div>
    </header>
  );
}

function UnitCard({
  unit,
  sims,
  customCoefs,
  onSim,
  onCoef,
}: {
  unit: ResultsUnit;
  sims: Simulations;
  customCoefs: CustomCoefficients;
  onSim: (key: string, v: string) => void;
  onCoef: (key: string, v: string) => void;
}) {
  const avg = unitAverage(unit, sims, customCoefs);
  const showOfficial = unit.official_average !== null && !avg.simulated;
  const value = showOfficial ? unit.official_average : avg.value;

  return (
    <section className="ue">
      <div className="ue__head">
        <h2>{unit.title}</h2>
        <span className="small muted">
          {unit.unassigned ? "Hors moyenne du semestre" : `Coefficient UE ${fmtCoef(effectiveCoefficient(unit.coefficient, unit.key, customCoefs).value)}`}
          {avg.value !== null && avg.markedCoef < avg.totalCoef && ` · ${fmtCoef(avg.markedCoef)} noté`}
          {avg.unknownCoefCount > 0 && ` · ${avg.unknownCoefCount} coef inconnu(s)`}
        </span>
        {!unit.unassigned && unit.coefficient == null && (
          <input className="input ue__input" inputMode="decimal" placeholder="Coef. UE" aria-label={`Coefficient personnalisé pour ${unit.title}`} value={customCoefs[unit.key] ?? ""} onChange={(event) => onCoef(unit.key, event.target.value)} />
        )}
        <span className="ue__avg">
          {fmt(value)}
          {showOfficial && <span className="pill">officielle{unit.official_average_maximum ? ` / ${unit.official_average_maximum}` : " · barème non publié"}</span>}
          {avg.simulated && <span className="pill status--warning">simulée</span>}
        </span>
      </div>
      <div className="ue__row ue__row--head" aria-hidden="true">
        <span>Matière</span>
        <span>Coef</span>
        <span>Note /20</span>
        <span />
      </div>
      {unit.subjects.map((s) => (
        <SubjectRow key={s.key} subject={s} sims={sims} customCoefs={customCoefs} onSim={onSim} onCoef={onCoef} />
      ))}
    </section>
  );
}

function assessmentRows(items: ResultsAssessment[], depth = 0): { assessment: ResultsAssessment; depth: number }[] {
  return items.flatMap((assessment) => [{ assessment, depth }, ...assessmentRows(assessment.children ?? [], depth + 1)]);
}

function SubjectRow({
  subject,
  sims,
  customCoefs,
  onSim,
  onCoef,
}: {
  subject: ResultsSubject;
  sims: Simulations;
  customCoefs: CustomCoefficients;
  onSim: (key: string, v: string) => void;
  onCoef: (key: string, v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const { mark, official, simulated } = subjectMark(subject, sims, customCoefs);
  const coefInfo = effectiveCoefficient(subject.coefficient, subject.key, customCoefs);
  const hasDetail = subject.assessments.length > 0;
  const hasAnyAssessmentGrade = subject.assessments.some((a) => a.grade !== null);
  const computedFromParts = !official && hasAnyAssessmentGrade && sims[subject.key] === undefined;
  const invalid = (v: string, max = 20) => v !== "" && (parseMark(v) === null || parseMark(v)! < 0 || parseMark(v)! > max);

  return (
    <>
      <div className="ue__row">
        <span className="ue__name">
          {subject.title}
          {subject.code && <span className="xsmall muted"> {subject.code}</span>}
          {subject.ects !== null && <span className="xsmall muted"> · {fmtCoef(subject.ects)} ECTS</span>}
        </span>
        {coefInfo.source === "official" ? (
          <span className="ue__num" title="Coefficient officiel Pamplemousse">
            {fmtCoef(coefInfo.value)}
          </span>
        ) : (
          <input
            className={`input ue__input${coefInfo.source === "custom" ? " ue__input--sim" : ""}`}
            inputMode="decimal"
            placeholder="?"
            title={coefInfo.source === "custom" ? "Coefficient personnalisé" : "Coefficient officiel inconnu — saisissez-le"}
            aria-label={`Coefficient personnalisé pour ${subject.title}`}
            value={customCoefs[subject.key] ?? ""}
            onChange={(e) => onCoef(subject.key, e.target.value)}
          />
        )}
        {official || computedFromParts ? (
          <span
            className={`ue__mark${official ? " ue__mark--official" : " ue__mark--computed"}`}
            title={official ? "Note officielle Pamplemousse" : "Calculée à partir des épreuves"}
          >
            {official ? `${fmt(subject.official_grade)}${subject.official_grade_maximum ? ` / ${subject.official_grade_maximum}` : " · barème non publié"}` : fmt(mark)}
          </span>
        ) : (
          <input
            className={`input ue__input${simulated ? " ue__input--sim" : ""}${invalid(sims[subject.key] ?? "") ? " ue__input--bad" : ""}`}
            inputMode="decimal"
            placeholder="–"
            aria-label={`Note simulée pour ${subject.title}`}
            value={sims[subject.key] ?? ""}
            onChange={(e) => onSim(subject.key, e.target.value)}
          />
        )}
        <span>
          {hasDetail && (
            <button type="button" className={`btn btn--small${open ? " btn--on" : ""}`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
              {open ? "Détail ▾" : "Détailler"}
            </button>
          )}
        </span>
      </div>
      {open && hasDetail && (
        <div className="ue__detail">
          <div className="ue__sub ue__row--head" aria-hidden="true">
            <span>Évaluation</span>
            <span>Note</span>
            <span>Sur</span>
            <span>Coef</span>
          </div>
          {assessmentRows(subject.assessments).map(({ assessment: a, depth }) => {
            const { mark: am } = assessmentMark(a, sims);
            const aCoef = effectiveCoefficient(a.coefficient ?? a.weight, a.key, customCoefs);
            return (
              <div key={a.key} className="ue__sub">
                <span style={{ paddingInlineStart: depth * 14 }}>{a.title}</span>
                {a.grade !== null ? (
                  <span className="ue__mark ue__mark--official">{fmt(a.grade)}</span>
                ) : a.maximum === null ? <span className="muted">Barème non publié</span> : (
                  <input
                    className={`input ue__input${am !== null ? " ue__input--sim" : ""}${invalid(sims[a.key] ?? "", a.maximum) ? " ue__input--bad" : ""}`}
                    inputMode="decimal"
                    placeholder="–"
                    aria-label={`Note simulée pour ${a.title}`}
                    value={sims[a.key] ?? ""}
                    onChange={(e) => onSim(a.key, e.target.value)}
                  />
                )}
                <span className="ue__num">{fmtCoef(a.maximum)}</span>
                {aCoef.source === "official" ? (
                  <span className="ue__num">{fmtCoef(aCoef.value)}</span>
                ) : (
                  <input
                    className={`input ue__input${aCoef.source === "custom" ? " ue__input--sim" : ""}`}
                    inputMode="decimal"
                    placeholder="?"
                    aria-label={`Coefficient personnalisé pour ${a.title}`}
                    value={customCoefs[a.key] ?? ""}
                    onChange={(e) => onCoef(a.key, e.target.value)}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

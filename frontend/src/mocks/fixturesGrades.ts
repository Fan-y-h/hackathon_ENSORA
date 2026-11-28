// Synthetic academic program (fictitious coefficients, not official ENSAI rules).
import type { AcademicNode, AcademicProgram, GradeRecord, Objective } from "@/api/typesGrades";

import { IDS, type createFixtures } from "./fixtures";

export function createGradeFixtures(base: ReturnType<typeof createFixtures>) {
  const created = new Date(Date.now() - 20 * 86_400_000).toISOString();
  const record = { created_at: created, updated_at: created, revision: 1 };
  const examEvent = (title: string) => base.events.find((e) => e.title === title)?.id ?? null;

  const programId = "d1000000-0000-4000-8000-000000000001";
  const programs: AcademicProgram[] = [
    { ...record, id: programId, academic_year: "2026-2027", cohort: "2A", specialization: null, title: "2A — Semestre 1 (exemple)", semester: 1 },
  ];

  let n = 0;
  const id = () => `d2000000-0000-4000-8000-${String(++n).padStart(12, "0")}`;
  const nodes: AcademicNode[] = [];
  const add = (node: Omit<AcademicNode, "id" | "weight_mode" | "subject_id" | "event_id"> & Partial<AcademicNode>): string => {
    const nodeId = id();
    nodes.push({ id: nodeId, weight_mode: "coefficient", subject_id: null, event_id: null, ...node });
    return nodeId;
  };

  const semester = add({ parent_id: null, kind: "semester", title: "Semestre 1", weight: "1" });
  const ueStats = add({ parent_id: semester, kind: "unit", title: "UE Statistique", weight: "5" });
  const ueInfo = add({ parent_id: semester, kind: "unit", title: "UE Informatique", weight: "4" });

  const stats = add({ parent_id: ueStats, kind: "subject", title: "Statistique inférentielle", weight: "3", subject_id: IDS.statsSubject });
  const statsCc = add({ parent_id: stats, kind: "assessment", title: "Contrôle continu", weight: "0.4", weight_mode: "fraction" });
  const statsExam = add({ parent_id: stats, kind: "assessment", title: "Partiel", weight: "0.6", weight_mode: "fraction", event_id: examEvent("Partiel Statistique inférentielle") });

  const proba = add({ parent_id: ueStats, kind: "subject", title: "Probabilités avancées", weight: "2", subject_id: IDS.probaSubject });
  const probaCtrl = add({ parent_id: proba, kind: "assessment", title: "Contrôle", weight: "0.3", weight_mode: "fraction", event_id: examEvent("Contrôle Probabilités") });
  add({ parent_id: proba, kind: "assessment", title: "Examen final", weight: "0.7", weight_mode: "fraction" });

  const algo = add({ parent_id: ueInfo, kind: "subject", title: "Algorithmique et programmation", weight: "3", subject_id: IDS.algoSubject });
  const algoTp = add({ parent_id: algo, kind: "assessment", title: "TP notés", weight: "0.3", weight_mode: "fraction" });
  add({ parent_id: algo, kind: "assessment", title: "Projet", weight: "0.3", weight_mode: "fraction" });
  add({ parent_id: algo, kind: "assessment", title: "Examen", weight: "0.4", weight_mode: "fraction" });

  void statsExam;
  void probaCtrl;

  const mark = (nodeId: string, value: string): GradeRecord => ({ ...record, id: crypto.randomUUID(), node_id: nodeId, kind: "actual", value, provenance: "manual" });
  const records: GradeRecord[] = [mark(statsCc, "11.50"), mark(algoTp, "15.00")];

  const objectives: Objective[] = [{ ...record, id: "d3000000-0000-4000-8000-000000000001", node_id: semester, target_kind: "numeric", target_value: "13.00", active: true }];

  return { programs, nodes, curriculumRevision: 1, records, objectives };
}

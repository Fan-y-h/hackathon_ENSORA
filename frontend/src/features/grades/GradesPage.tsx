import { useState } from "react";

import { useGradePrograms, useGradeSummary } from "../../api/queries";
import { Card, EmptyState, PageHeader, QueryState } from "../../components/ui";

/** Decimal values arrive as strings (no float rounding); display only, never recomputed here. */
const fmt = (v: string | null | undefined, digits = 2) =>
  v === null || v === undefined ? "—" : Number(v).toFixed(digits).replace(".", ",");
const pct = (v: string | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(Number(v) * 100)} %`);

const KIND_INDENT: Record<string, number> = { program: 0, year: 0, semester: 0, unit: 1, subject: 2, assessment_group: 3, assessment: 3 };

export function GradesPage() {
  const programs = useGradePrograms();
  const [programId, setProgramId] = useState("");
  const current = programId || programs.data?.[0]?.id;
  const summary = useGradeSummary(current);

  return (
    <div className="page">
      <PageHeader
        eyebrow="Résultats & objectifs"
        title="Mes moyennes"
        actions={
          programs.data && programs.data.length > 1 ? (
            <label className="field" style={{ minWidth: 240 }}>
              <span className="sr-only">Programme</span>
              <select className="select" value={current} onChange={(e) => setProgramId(e.target.value)}>
                {programs.data.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.title}
                  </option>
                ))}
              </select>
            </label>
          ) : undefined
        }
      />
      <Card
        subtitle="Moyenne provisoire = notes déjà connues, pondérées par leur couverture. Une note manquante n'est jamais comptée comme 0. Statut officiel : inconnu tant que les règles ne sont pas vérifiées."
      >
        <QueryState query={programs}>
          {(list) =>
            list.length === 0 ? (
              <EmptyState title="Aucun programme configuré" />
            ) : (
              <QueryState query={summary} lines={6}>
                {(s) => (
                  <div className="table-wrap">
                    <table className="table">
                      <caption className="sr-only">Moyennes par semestre, UE, matière et épreuve</caption>
                      <thead>
                        <tr>
                          <th scope="col">Élément</th>
                          <th scope="col" className="num">Provisoire</th>
                          <th scope="col" className="num">Couverture</th>
                          <th scope="col" className="num">Fourchette possible</th>
                          <th scope="col">Statut officiel</th>
                        </tr>
                      </thead>
                      <tbody>
                        {s.nodes.map((n) => (
                          <tr key={n.node_id}>
                            <th scope="row" style={{ paddingLeft: 8 + (KIND_INDENT[n.kind] ?? 0) * 18, fontWeight: n.kind === "assessment" ? 400 : 600, textAlign: "left" }}>
                              {n.title}
                            </th>
                            <td className="num">{fmt(n.provisional)}</td>
                            <td className="num">{pct(n.coverage)}</td>
                            <td className="num">
                              {fmt(n.bound_min, 1)} – {fmt(n.bound_max, 1)}
                            </td>
                            <td className="small muted">{n.official_status === "unknown" ? "inconnu" : n.official_status}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </QueryState>
            )
          }
        </QueryState>
      </Card>
      <p className="xsmall muted">
        Simulateur, objectifs et notes requises : disponibles dans l'API (/grades/simulate, /grades/required), écran détaillé à venir.
      </p>
    </div>
  );
}

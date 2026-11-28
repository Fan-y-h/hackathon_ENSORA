import { Plus, Trash2 } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import { ApiError } from "@/api/client";
import { useDeleteMark, useSaveMark } from "@/api/hooksGrades";
import type { AcademicNode, GradeRecord, NodeSummary, SimulationResult } from "@/api/typesGrades";
import { cn } from "@/lib/cn";

/** "13.50" -> "13,50" (French display only). */
export function frNumber(value: string | null): string {
  return value === null ? "—" : value.replace(".", ",");
}

/** Accepts "12,5" or "12.5" typed by the student and returns "12.5" for the API. */
export function toDecimal(input: string): string {
  return input.trim().replace(",", ".");
}

function MarkCell({ node, record }: { node: AcademicNode; record?: GradeRecord }) {
  const save = useSaveMark();
  const remove = useDeleteMark();
  const [value, setValue] = useState(record ? frNumber(record.value) : "");
  const initial = record ? frNumber(record.value) : "";

  function commit() {
    const typed = value.trim();
    if (typed === initial || (typed === "" && !record)) return;
    // Emptying the field removes the mark (missing again, never 0).
    if (typed === "" && record) {
      remove.mutate(record.id, { onError: () => setValue(initial) });
      return;
    }
    save.mutate({ record, nodeId: node.id, value: toDecimal(typed) }, { onError: () => setValue(initial) });
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") event.currentTarget.blur();
    if (event.key === "Escape") setValue(initial);
  }

  const error = save.error ?? remove.error;
  const message = error instanceof ApiError ? error.message : error?.message;
  return (
    <div>
      <input
        aria-label={`Note de « ${node.title} » sur 20 (vider le champ pour effacer)`}
        inputMode="decimal"
        value={value}
        placeholder="—"
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={onKeyDown}
        aria-invalid={message ? true : undefined}
        className={cn("focus-ring h-8 w-20 rounded-md border bg-surface px-2 text-right text-sm tabular-nums", message ? "border-danger" : "border-line")}
      />
      {message && <p className="mt-1 text-xs text-danger">{message}</p>}
    </div>
  );
}

interface GradeTreeProps {
  nodes: AcademicNode[];
  records: GradeRecord[];
  summaries: NodeSummary[];
  /** Curriculum edit mode: titles, weights, add/remove assessments (saved together). */
  editing: boolean;
  onNodeChange: (nodeId: string, changes: Partial<AcademicNode>) => void;
  onAdd: (parentId: string) => void;
  onRemove: (nodeId: string) => void;
  simulating: boolean;
  hypotheses: Record<string, string>;
  onHypothesis: (nodeId: string, value: string) => void;
  simulation: SimulationResult | null;
}

const KIND_STYLE: Record<string, string> = { semester: "font-semibold", unit: "font-semibold", subject: "font-medium", assessment_group: "", assessment: "text-muted" };

/** Curriculum as an accessible table: one row per node, indented by depth. */
export function GradeTree({ nodes, records, summaries, editing, onNodeChange, onAdd, onRemove, simulating, hypotheses, onHypothesis, simulation }: GradeTreeProps) {
  const children = (parentId: string | null) => nodes.filter((n) => n.parent_id === parentId);
  const rows: Array<{ node: AcademicNode; depth: number }> = [];
  const walk = (parentId: string | null, depth: number) => {
    for (const node of children(parentId)) {
      rows.push({ node, depth });
      walk(node.id, depth + 1);
    }
  };
  walk(null, 0);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <caption className="sr-only">Programme, notes et moyennes provisoires</caption>
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <th scope="col" className="py-2 pr-3 font-medium">Élément</th>
            <th scope="col" className="px-3 py-2 font-medium">Poids</th>
            <th scope="col" className="px-3 py-2 font-medium">Note / 20</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Moyenne provisoire</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Déjà noté</th>
            {simulation && <th scope="col" className="px-3 py-2 text-right font-medium">Projection</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ node, depth }) => {
            const leaf = children(node.id).length === 0;
            const summary = summaries.find((s) => s.node_id === node.id);
            const record = records.find((r) => r.node_id === node.id && r.kind === "actual");
            const projected = simulation?.nodes.find((n) => n.node_id === node.id);
            const changed = projected && projected.projected !== projected.baseline;
            return (
              <tr key={node.id} className="border-b border-line/60 align-top">
                <th scope="row" className={cn("py-2 pr-3 text-left font-normal", KIND_STYLE[node.kind])} style={{ paddingLeft: `${depth * 1.25}rem` }}>
                  {editing && node.parent_id !== null ? (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <input
                        aria-label={`Nom de « ${node.title} »`}
                        value={node.title}
                        onChange={(e) => onNodeChange(node.id, { title: e.target.value })}
                        className="focus-ring h-8 min-w-0 flex-1 rounded-md border border-line bg-surface px-2 text-sm"
                      />
                      {(node.kind === "subject" || node.kind === "assessment_group") && (
                        <button type="button" onClick={() => onAdd(node.id)} className="focus-ring rounded p-1 text-accent hover:bg-accent-soft" aria-label={`Ajouter une épreuve à « ${node.title} »`} title="Ajouter une épreuve">
                          <Plus className="size-4" aria-hidden="true" />
                        </button>
                      )}
                      {node.kind === "assessment" && (
                        <button
                          type="button"
                          onClick={() => onRemove(node.id)}
                          disabled={record !== undefined}
                          className="focus-ring rounded p-1 text-muted hover:text-danger disabled:cursor-not-allowed disabled:opacity-40"
                          aria-label={`Supprimer l'épreuve « ${node.title} »`}
                          title={record ? "Effacez d'abord la note de cette épreuve" : "Supprimer l'épreuve"}
                        >
                          <Trash2 className="size-4" aria-hidden="true" />
                        </button>
                      )}
                    </div>
                  ) : (
                    node.title
                  )}
                </th>
                <td className="px-3 py-2 tabular-nums">
                  {node.parent_id === null ? (
                    "—"
                  ) : editing ? (
                    <input
                      aria-label={`Poids de « ${node.title} »${node.weight_mode === "fraction" ? " (fraction, total 1)" : " (coefficient)"}`}
                      inputMode="decimal"
                      value={node.weight}
                      onChange={(e) => onNodeChange(node.id, { weight: toDecimal(e.target.value) })}
                      className="focus-ring h-8 w-20 rounded-md border border-line bg-surface px-2 text-right"
                    />
                  ) : node.weight_mode === "fraction" ? (
                    `${Math.round(Number(node.weight) * 1000) / 10} %`
                  ) : (
                    `coef. ${frNumber(node.weight)}`
                  )}
                </td>
                <td className="px-3 py-2">
                  {leaf && !simulating && !editing && <MarkCell key={record?.revision ?? "new"} node={node} record={record} />}
                  {leaf && simulating &&
                    (record ? (
                      <span className="tabular-nums">{frNumber(record.value)}</span>
                    ) : (
                      <input
                        aria-label={`Note hypothétique pour « ${node.title} »`}
                        inputMode="decimal"
                        placeholder="hypothèse"
                        value={hypotheses[node.id] ?? ""}
                        onChange={(e) => onHypothesis(node.id, e.target.value)}
                        className="focus-ring h-8 w-24 rounded-md border border-dashed border-accent bg-accent-soft/30 px-2 text-right text-sm"
                      />
                    ))}
                </td>
                <td className="px-3 py-2 text-right font-medium tabular-nums">{frNumber(summary?.provisional ?? null)}</td>
                <td className="px-3 py-2 text-right text-muted tabular-nums">{summary ? `${Math.round(Number(summary.actual_coverage) * 100)} %` : "—"}</td>
                {simulation && <td className={cn("px-3 py-2 text-right tabular-nums", changed ? "font-semibold text-accent" : "text-muted")}>{frNumber(projected?.projected ?? null)}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Decodes the planner's `reason` string ("CARDS_DUE;ACADEMIC_SIGNAL(...)") into French labels. */
const REASONS: Record<string, string> = {
  CARDS_DUE: "cartes à réviser",
  TASK_REMAINING_WORK: "travail restant sur une tâche",
  ASSESSMENT_SCOPE: "programme d'une évaluation",
  ASSESSMENT_CONCEPT_UNKNOWN: "notion jamais évaluée",
  ASSESSMENT_CONCEPT_LEARNING: "notion en cours d'apprentissage",
  ASSESSMENT_CONCEPT_WEAK: "notion insuffisamment maîtrisée",
  ASSESSMENT_CONCEPT_ACQUIRED: "rappel d'une notion acquise",
  ASSESSMENT_CONCEPT_CONSOLIDATE: "notion à consolider",
  REVIEW_DUE: "répétition espacée due",
  WEAK_CONCEPT: "notion fragile",
  ACADEMIC_SIGNAL: "objectif de note",
  DIAGNOSTIC: "diagnostic après une note",
  CONSOLIDATION: "erreurs récurrentes",
  PREREQUISITE: "prérequis à revoir d'abord",
};

export function reasonLabels(reason: string | null | undefined): string[] {
  if (!reason) return [];
  const labels = reason
    .split(";")
    .map((part) => part.trim().split("(")[0])
    .filter(Boolean)
    .map((code) => REASONS[code] ?? code.toLowerCase().replace(/_/g, " "));
  return [...new Set(labels)];
}

import type { ActivityType, SessionStatus } from "@/api/typesPlanning";
import type { Tone } from "@/components/ui";

export const ACTIVITY_LABELS: Record<ActivityType, string> = {
  learn: "Cours",
  active_recall: "Rappel actif",
  practice: "Exercices",
  review: "Révision",
  task: "Tâche",
  mock_exam: "Examen blanc",
};

export const STATUS_LABELS: Record<SessionStatus, { text: string; tone: Tone }> = {
  planned: { text: "Prévue", tone: "neutral" },
  in_progress: { text: "En cours", tone: "accent" },
  completed: { text: "Terminée", tone: "success" },
  partial: { text: "Partielle", tone: "warning" },
  skipped: { text: "Sautée", tone: "neutral" },
  cancelled: { text: "Annulée", tone: "neutral" },
};

/** Groups items by local day (yyyy-mm-dd), keeping time order. */
export function groupByDay<T extends { start_at: string }>(items: T[]): Array<[string, T[]]> {
  const groups = new Map<string, T[]>();
  for (const item of [...items].sort((a, b) => a.start_at.localeCompare(b.start_at))) {
    const d = new Date(item.start_at);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return [...groups];
}

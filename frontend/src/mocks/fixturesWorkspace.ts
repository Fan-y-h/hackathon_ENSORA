// Demo data for planning, focus, notes, notifications and settings. Synthetic only.
import type { Assessment, FocusSession, Plan } from "@/api/typesPlanning";
import type { AppNotification, ErrorEntry, Note, ProviderInfo, Settings } from "@/api/typesWorkspace";

import { IDS, type createFixtures } from "./fixtures";

export function createWorkspaceFixtures(base: ReturnType<typeof createFixtures>) {
  const now = Date.now();
  const iso = (offsetDays: number) => new Date(now + offsetDays * 86_400_000).toISOString();
  const record = { created_at: iso(-15), updated_at: iso(-15), revision: 1 };
  const conceptsOf = (subjectId: string) => {
    const chapterIds = new Set(base.chapters.filter((c) => c.subject_id === subjectId).map((c) => c.id));
    return base.concepts.filter((c) => chapterIds.has(c.chapter_id)).map((c) => c.id);
  };

  // Assessments linked to exam events. The ambiguous Pamplemousse one stays "proposed".
  const assessments: Assessment[] = base.events
    .filter((e) => e.type === "exam" && e.subject_id)
    .map((e, i) => ({
      ...record,
      id: `e1000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`,
      event_id: e.id,
      subject_id: e.subject_id as string,
      title: e.title,
      deadline_at: e.start_at,
      format: "written" as const,
      duration_min: 120,
      scope_status: e.confirmation_required ? ("proposed" as const) : ("confirmed" as const),
      concept_ids: conceptsOf(e.subject_id as string),
    }));

  const settings: Settings = {
    ...record,
    id: "e2000000-0000-4000-8000-000000000001",
    preferences: {
      work_windows: [1, 2, 3, 4, 5].map((day) => ({ day, start: "17:30", end: "20:30" })).concat([6, 7].map((day) => ({ day, start: "10:00", end: "17:00" }))),
      daily_max_min: 120,
      weekly_max_min: 840,
      work_method: "pomodoro_25_5",
      min_session_min: 25,
      max_session_min: 55,
      preferred_formats: ["revision_sheet", "flashcards", "quiz"],
      subject_levels: {},
      subject_languages: {},
      starting_difficulty: "progressive",
      download_folder: null,
    },
    notification_channels: { in_app: true, email: false },
    provider_policy: "local_only",
    preferred_model: "auto",
  };

  const providers: ProviderInfo[] = [
    { id: "mock", label: "Démonstration (sans IA)", available: true, status_note: "Réponses fixes, aucun modèle appelé", external: false },
    { id: "ollama", label: "Modèle local (Ollama)", available: false, status_note: "Non installé sur cette machine", external: false },
    { id: "openai", label: "OpenAI", available: false, status_note: "Clé API non configurée", external: true },
    { id: "anthropic", label: "Anthropic Claude", available: false, status_note: "Clé API non configurée", external: true },
    { id: "gemini", label: "Google Gemini", available: false, status_note: "Clé API non configurée", external: true },
    { id: "mistral", label: "Mistral", available: false, status_note: "Clé API non configurée", external: true },
  ];

  const notes: Note[] = [
    {
      ...record,
      id: "e3000000-0000-4000-8000-000000000001",
      subject_id: IDS.statsSubject,
      title: "Tests : ce que je confonds",
      markdown:
        "## Niveau et puissance\n\n- Le **niveau** $\\alpha$ borne l'erreur de première espèce.\n- La **puissance** vaut $1 - \\beta$.\n\n> Astuce : relire le TD 4, exercice 2.",
      citations: [],
    },
  ];

  const errors: ErrorEntry[] = [
    {
      id: "e4000000-0000-4000-8000-000000000001",
      concept_id: base.concepts.find((c) => c.label === "Intervalle pour une moyenne")?.id ?? null,
      subject_id: IDS.statsSubject,
      category: "method",
      description: "Utilisation de la loi normale au lieu de la loi de Student (variance inconnue).",
      remediation: "Refaire l'exercice 1 du TD 3.",
      occurred_at: iso(-3),
      resolved_at: null,
    },
  ];

  const notifications: AppNotification[] = [
    { id: "e5000000-0000-4000-8000-000000000001", event_type: "review_due", title: "4 flashcards à réviser", body: "Paquet « Statistique inférentielle — notions clés ».", target_path: "/flashcards", created_at: iso(-0.1), read_at: null },
    { id: "e5000000-0000-4000-8000-000000000002", event_type: "timetable_change", title: "Nouvel examen détecté", body: "« Contrôle Probabilités » doit être confirmé dans l'agenda.", target_path: "/agenda", created_at: iso(-0.5), read_at: null },
    { id: "e5000000-0000-4000-8000-000000000003", event_type: "new_document", title: "Nouveau document", body: "« TD 4 — Tests » a été ajouté depuis Moodle.", target_path: "/library", created_at: iso(-2), read_at: iso(-1.5) },
  ];

  // Work log of past days, for analytics (minutes of actual focus work).
  const localDay = (offset: number) => {
    const d = new Date(now + offset * 86_400_000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };
  const workLog = Array.from({ length: 14 }, (_, i) => ({ date: localDay(-(14 - i)), minutes: [45, 0, 90, 60, 30, 0, 120, 75, 40, 0, 95, 60, 30, 50][i] }));

  return {
    assessments,
    settings,
    providers,
    notes,
    errors,
    notifications,
    workLog,
    plan: null as Plan | null,
    previews: [] as Array<import("@/api/typesPlanning").PlanPreview>,
    focusSessions: [] as FocusSession[],
    taskProposals: [] as Array<import("@/api/typesPlanning").TaskProposal>,
    tokensUsed: 0,
  };
}

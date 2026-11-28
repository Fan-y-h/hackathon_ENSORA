// Synthetic demo data (no real student, grade or course content).
// Shapes follow src/api/types.ts exactly. Dates are computed from "now" so the
// demo always shows the current week, whatever day the jury opens it.
import type {
  CalendarEvent,
  Chapter,
  Concept,
  Connection,
  DocumentSummary,
  Mastery,
  MasteryDimension,
  Profile,
  ResourceType,
  SourceKind,
  Subject,
  Task,
} from "@/api/types";

const NOW = new Date();
const CREATED = new Date(NOW.getTime() - 30 * 86_400_000).toISOString();

/**
 * Date `schoolDays` weekdays after today (0 = today), at a local hour, as ISO UTC.
 * Skipping weekends keeps the demo realistic: no class on a Saturday.
 */
function at(schoolDays: number, hour: number, minute = 0): string {
  const date = new Date(NOW);
  let remaining = schoolDays;
  while (remaining > 0) {
    date.setDate(date.getDate() + 1);
    const weekday = date.getDay();
    if (weekday !== 0 && weekday !== 6) remaining -= 1;
  }
  date.setHours(hour, minute, 0, 0);
  return date.toISOString();
}

function minutesAgo(minutes: number): string {
  return new Date(NOW.getTime() - minutes * 60_000).toISOString();
}

const base = { created_at: CREATED, updated_at: CREATED, revision: 1 };

// Fixed ids so links stay stable between reloads.
export const IDS = {
  user: "00000000-0000-4000-8000-000000000001",
  statsSubject: "10000000-0000-4000-8000-000000000001",
  probaSubject: "10000000-0000-4000-8000-000000000002",
  algoSubject: "10000000-0000-4000-8000-000000000003",
};

export function createFixtures() {
  const profile: Profile = {
    ...base,
    id: "20000000-0000-4000-8000-000000000001",
    user_id: IDS.user,
    display_name: "Étudiant démo",
    cohort: "2A",
    track: null,
    academic_year: "2026-2027",
    locale: "fr",
    timezone: "Europe/Paris",
    goals: ["Valider le semestre avec 13 de moyenne"],
    semester: 1,
    onboarding_completed_at: null,
  };

  const connections: Connection[] = [
    { ...base, id: "30000000-0000-4000-8000-000000000001", source_kind: "pamplemousse", status: "connected", last_success_at: minutesAgo(12), is_fixture: true },
    { ...base, id: "30000000-0000-4000-8000-000000000002", source_kind: "moodle", status: "stale", last_success_at: minutesAgo(60 * 26), is_fixture: true },
    { ...base, id: "30000000-0000-4000-8000-000000000003", source_kind: "ensora_drive", status: "disconnected", last_success_at: null, is_fixture: true },
  ];

  const subjects: Subject[] = [
    { ...base, id: IDS.statsSubject, canonical_code: "STAT-INF", title: "Statistique inférentielle", cohort: "2A", track: null, academic_year: "2026-2027", languages: ["fr"], default_language: "fr" },
    { ...base, id: IDS.probaSubject, canonical_code: "PROBA-2", title: "Probabilités avancées", cohort: "2A", track: null, academic_year: "2026-2027", languages: ["fr", "en"], default_language: "fr" },
    { ...base, id: IDS.algoSubject, canonical_code: "ALGO", title: "Algorithmique et programmation", cohort: "2A", track: null, academic_year: "2026-2027", languages: ["en"], default_language: "en" },
  ];

  let eventNumber = 0;
  const event = (fields: Omit<CalendarEvent, keyof typeof base | "id" | "room" | "teacher" | "group" | "confirmation_required"> & Partial<CalendarEvent>): CalendarEvent => ({
    ...base,
    id: `40000000-0000-4000-8000-${String(++eventNumber).padStart(12, "0")}`,
    room: null,
    teacher: null,
    group: null,
    confirmation_required: false,
    ...fields,
  });

  const events: CalendarEvent[] = [
    event({ subject_id: IDS.statsSubject, title: "Statistique inférentielle — cours", type: "class", start_at: at(0, 8, 30), end_at: at(0, 10, 0), source: "pamplemousse", room: "Amphi 1" }),
    event({ subject_id: IDS.statsSubject, title: "Statistique inférentielle — TD", type: "td", start_at: at(0, 10, 15), end_at: at(0, 11, 45), source: "pamplemousse", room: "Salle 104", group: "Groupe B" }),
    event({ subject_id: IDS.algoSubject, title: "Algorithmique — TP", type: "tp", start_at: at(1, 13, 30), end_at: at(1, 16, 30), source: "pamplemousse", room: "Salle info 2", group: "Groupe B" }),
    event({ subject_id: IDS.probaSubject, title: "Probabilités avancées — cours", type: "class", start_at: at(2, 8, 30), end_at: at(2, 10, 0), source: "pamplemousse", room: "Amphi 2" }),
    event({ subject_id: IDS.algoSubject, title: "Projet algorithmique — rendu", type: "deadline", start_at: at(4, 23, 59), end_at: at(4, 23, 59), source: "moodle" }),
    // Ambiguous: a red Pamplemousse event is only a hint of an exam, the student must confirm.
    event({ subject_id: IDS.probaSubject, title: "Contrôle Probabilités", type: "exam", start_at: at(6, 8, 30), end_at: at(6, 10, 30), source: "pamplemousse", room: "Amphi 1", confirmation_required: true }),
    event({ subject_id: IDS.statsSubject, title: "Partiel Statistique inférentielle", type: "exam", start_at: at(10, 13, 30), end_at: at(10, 15, 30), source: "pamplemousse", room: "Amphi 1" }),
    event({ subject_id: null, title: "Association sportive", type: "personal", start_at: at(1, 18, 0), end_at: at(1, 20, 0), source: "manual" }),
    event({ subject_id: null, title: "Rendez-vous médical", type: "personal", start_at: at(2, 17, 30), end_at: at(2, 18, 30), source: "google_calendar" }),
  ];

  let taskNumber = 0;
  const task = (fields: Pick<Task, "title" | "priority"> & Partial<Task>): Task => ({
    ...base,
    id: `50000000-0000-4000-8000-${String(++taskNumber).padStart(12, "0")}`,
    parent_task_id: null,
    subject_id: null,
    description: null,
    deadline_at: null,
    estimated_min: null,
    remaining_min: null,
    status: "todo",
    locked: false,
    ...fields,
  });

  const tasks: Task[] = [
    task({ title: "Terminer le projet d'algorithmique", subject_id: IDS.algoSubject, priority: 1, deadline_at: at(4, 23, 59), estimated_min: 360, remaining_min: 240, status: "in_progress" }),
    task({ title: "Refaire le TD 4 de statistique", subject_id: IDS.statsSubject, priority: 2, estimated_min: 90, remaining_min: 90 }),
    task({ title: "Fiche : lois usuelles", subject_id: IDS.probaSubject, priority: 2, deadline_at: at(5, 18, 0), estimated_min: 45, remaining_min: 45 }),
    task({ title: "Relire le cours sur les tests", subject_id: IDS.statsSubject, priority: 3, estimated_min: 60, remaining_min: 0, status: "done" }),
  ];

  // --- Curriculum: chapters and concepts (synthetic, generic statistics/CS topics) ---
  const chapters: Chapter[] = [];
  const concepts: Concept[] = [];
  const curriculum: Record<string, Array<[string, string[]]>> = {
    [IDS.statsSubject]: [
      ["Estimation ponctuelle", ["Biais et risque quadratique", "Maximum de vraisemblance"]],
      ["Intervalles de confiance", ["Intervalle pour une moyenne", "Méthode du pivot"]],
      ["Tests d'hypothèses", ["Erreurs de première et seconde espèce", "Test de Student", "p-valeur"]],
    ],
    [IDS.probaSubject]: [
      ["Convergences", ["Convergence en loi", "Loi des grands nombres", "Théorème central limite"]],
      ["Vecteurs gaussiens", ["Matrice de covariance", "Indépendance et décorrélation"]],
    ],
    [IDS.algoSubject]: [
      ["Complexité", ["Notation grand O", "Complexité des tris"]],
      ["Structures de données", ["Piles et files", "Arbres binaires de recherche"]],
      ["Graphes", ["Parcours en largeur", "Algorithme de Dijkstra"]],
    ],
  };
  let chapterNumber = 0;
  let conceptNumber = 0;
  for (const [subjectId, chapterList] of Object.entries(curriculum)) {
    chapterList.forEach(([title, labels], index) => {
      const chapterId = `60000000-0000-4000-8000-${String(++chapterNumber).padStart(12, "0")}`;
      chapters.push({ ...base, id: chapterId, subject_id: subjectId, title, ordinal: index + 1, active: true });
      for (const label of labels) {
        const n = ++conceptNumber;
        concepts.push({
          ...base,
          id: `70000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
          chapter_id: chapterId,
          label,
          description: null,
          difficulty: (n % 3) + 1,
          importance: ((n + 1) % 3) + 1,
          active: true,
        });
      }
    });
  }

  // --- Mastery: some concepts assessed, others still unknown (no record at all) ---
  const conceptId = (n: number) => `70000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
  const mastery: Mastery[] = [];
  const assess = (n: number, dimension: MasteryDimension, score: number, evidence: number, reviewedDaysAgo: number, dueInDays: number) =>
    mastery.push({
      concept_id: conceptId(n),
      dimension,
      score,
      evidence_count: evidence,
      confidence: Math.min(1, evidence / 5),
      last_review_at: new Date(NOW.getTime() - reviewedDaysAgo * 86_400_000).toISOString(),
      next_due_at: new Date(NOW.getTime() + dueInDays * 86_400_000).toISOString(),
    });
  assess(1, "theoretical_understanding", 0.82, 6, 3, 4);
  assess(2, "mathematical_reasoning", 0.58, 4, 5, 1);
  assess(3, "practical_application", 0.35, 3, 8, -1);
  assess(5, "theoretical_understanding", 0.71, 1, 2, 2); // a single answer: low evidence
  assess(6, "practical_application", 0.44, 5, 6, 0);
  assess(8, "theoretical_understanding", 0.9, 7, 1, 9);
  assess(10, "mathematical_reasoning", 0.62, 3, 4, 2);
  assess(13, "implementation", 0.76, 5, 2, 6);
  assess(15, "implementation", 0.48, 2, 7, 1);

  // --- Documents (titles only, no real course content) ---
  const documents: DocumentSummary[] = [];
  let documentNumber = 0;
  const doc = (subjectId: string, chapterIndex: number | null, title: string, resource_type: ResourceType, source: SourceKind, year: number | null = null) => {
    const chapter = chapterIndex === null ? null : chapters.filter((c) => c.subject_id === subjectId)[chapterIndex];
    documents.push({
      ...base,
      id: `80000000-0000-4000-8000-${String(++documentNumber).padStart(12, "0")}`,
      subject_id: subjectId,
      chapter_id: chapter?.id ?? null,
      title,
      resource_type,
      source,
      year,
      remote_modified_at: CREATED,
      active_version_id: `90000000-0000-4000-8000-${String(documentNumber).padStart(12, "0")}`,
    });
  };
  doc(IDS.statsSubject, 0, "Chapitre 1 — Estimation ponctuelle (diapositives)", "course", "moodle");
  doc(IDS.statsSubject, 1, "Chapitre 2 — Intervalles de confiance (diapositives)", "course", "moodle");
  doc(IDS.statsSubject, 2, "Chapitre 3 — Tests d'hypothèses (diapositives)", "course", "moodle");
  doc(IDS.statsSubject, 1, "TD 3 — Intervalles de confiance", "td", "moodle");
  doc(IDS.statsSubject, 2, "TD 4 — Tests", "td", "moodle");
  doc(IDS.statsSubject, null, "Partiel 2024", "exam", "ensora_drive", 2024);
  doc(IDS.statsSubject, null, "Partiel 2024 — corrigé", "solution", "ensora_drive", 2024);
  doc(IDS.statsSubject, null, "Fiche de synthèse — lois usuelles", "sheet", "ensora_drive");
  doc(IDS.probaSubject, 0, "Polycopié — Convergences", "course", "moodle");
  doc(IDS.probaSubject, 1, "Polycopié — Vecteurs gaussiens", "course", "moodle");
  doc(IDS.probaSubject, 0, "TD 2 — Théorème central limite", "td", "moodle");
  doc(IDS.probaSubject, null, "Contrôle 2023", "exam", "ensora_drive", 2023);
  doc(IDS.algoSubject, 0, "Cours 1 — Complexité", "course", "moodle");
  doc(IDS.algoSubject, 2, "Cours 5 — Graphes", "course", "moodle");
  doc(IDS.algoSubject, 1, "TP 3 — Arbres binaires de recherche", "tp", "moodle");
  doc(IDS.algoSubject, null, "Sujet du projet", "assignment", "moodle");
  doc(IDS.algoSubject, 2, "Notebook — parcours de graphes", "notebook", "ensora_drive");

  return { profile, connections, subjects, events, tasks, chapters, concepts, mastery, documents };
}

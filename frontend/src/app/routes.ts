// Single source of truth for every screen of the app.
// Both the sidebar menu and the router are built from this list,
// so adding a screen here makes it appear everywhere at once.
import {
  Bell,
  BookOpen,
  CalendarClock,
  CalendarDays,
  CircleHelp,
  FileText,
  GraduationCap,
  Layers,
  LayoutDashboard,
  Library,
  ListChecks,
  MessageSquare,
  NotebookPen,
  Rocket,
  Settings,
  Timer,
  TrendingUp,
  UserRound,
  Users,
  WandSparkles,
  type LucideIcon,
} from "lucide-react";

/** P0 = needed for the jury demo, P1 = after P0, P2 = bonus, later = out of hackathon scope. */
export type Priority = "P0" | "P1" | "P2" | "later";

export type NavGroupId = "organisation" | "learning" | "tracking" | "configuration" | "future";

export interface AppRoute {
  path: string;
  label: string;
  description: string;
  icon: LucideIcon;
  /** null = reachable by link only, not shown in the sidebar. */
  group: NavGroupId | null;
  priority: Priority;
}

export const NAV_GROUPS: { id: NavGroupId; label: string }[] = [
  { id: "organisation", label: "Organisation" },
  { id: "learning", label: "Apprentissage et IA" },
  { id: "tracking", label: "Productivité et suivi" },
  { id: "configuration", label: "Configuration" },
  { id: "future", label: "Version future" },
];

// Routes and descriptions follow docs/FRONTEND_UX_UI.md (information architecture).
export const APP_ROUTES: AppRoute[] = [
  // Organisation
  {
    path: "/",
    label: "Tableau de bord",
    description: "Journée, prochain partiel, objectifs de travail, notions fragiles, tâches et état de la synchronisation.",
    icon: LayoutDashboard,
    group: "organisation",
    priority: "P0",
  },
  {
    path: "/subjects",
    label: "Mes matières",
    description: "Matières classées par UE, avec chapitres, notions, ressources, notes, supports générés et résultats.",
    icon: BookOpen,
    group: "organisation",
    priority: "P0",
  },
  {
    path: "/library",
    label: "Bibliothèque",
    description: "Recherche par mots-clés ou par sens, filtres, aperçu des documents, version de la source, citations et import.",
    icon: Library,
    group: "organisation",
    priority: "P0",
  },
  {
    path: "/agenda",
    label: "Agenda",
    description: "Cours, échéances, examens et événements personnels ; confirmation des évaluations ambiguës.",
    icon: CalendarDays,
    group: "organisation",
    priority: "P0",
  },
  {
    path: "/planner",
    label: "Smart Planner",
    description: "Programme des partiels, séances proposées, alertes, aperçu des changements, acceptation et verrouillage.",
    icon: CalendarClock,
    group: "organisation",
    priority: "P0",
  },
  {
    path: "/tasks",
    label: "Mes tâches",
    description: "Listes du jour et de la semaine, calendrier, Kanban, découpage des projets et travail restant.",
    icon: ListChecks,
    group: "organisation",
    priority: "P1",
  },

  // Learning and AI
  {
    path: "/tutor",
    label: "AI Tutor",
    description: "Chat par matière ou global, choix des documents, citations ouvrables et enregistrement dans les notes.",
    icon: MessageSquare,
    group: "learning",
    priority: "P0",
  },
  {
    path: "/studio",
    label: "Learning Studio",
    description: "Choix du type de support, configuration de la génération, suivi, versions enregistrées et éditeur.",
    icon: WandSparkles,
    group: "learning",
    priority: "P0",
  },
  {
    path: "/flashcards",
    label: "Flashcards",
    description: "Paquets, cartes à revoir, révélation de la réponse et quatre boutons d'évaluation.",
    icon: Layers,
    group: "learning",
    priority: "P0",
  },
  {
    path: "/quiz",
    label: "Quiz",
    description: "Questions diagnostiques ou adaptatives, correction, score et notions concernées.",
    icon: CircleHelp,
    group: "learning",
    priority: "P0",
  },
  {
    path: "/exams",
    label: "Exam Trainer",
    description: "Annales et corrigés, examens blancs, mode examen chronométré et correction après remise.",
    icon: FileText,
    group: "learning",
    priority: "P1",
  },
  {
    path: "/notes",
    label: "Mes notes",
    description: "Cahiers par matière, éditeur Markdown et LaTeX, extraits de documents et carnet d'erreurs.",
    icon: NotebookPen,
    group: "learning",
    priority: "P1",
  },

  // Productivity and tracking
  {
    path: "/focus",
    label: "Focus Room",
    description: "Minuteur plein écran, objectif de séance, ressources, ambiance sonore, notes et auto-évaluation.",
    icon: Timer,
    group: "tracking",
    priority: "P1",
  },
  {
    path: "/analytics",
    label: "Learning Analytics",
    description: "Temps de travail, respect du planning, résultats, maîtrise, carte des révisions et préparation.",
    icon: TrendingUp,
    group: "tracking",
    priority: "P1",
  },
  {
    path: "/grades",
    label: "Résultats et objectifs",
    description: "UE, matières, coefficients, notes, moyennes provisoires, objectifs, notes nécessaires et simulations.",
    icon: GraduationCap,
    group: "tracking",
    priority: "P0",
  },

  // Configuration
  {
    path: "/profile",
    label: "Mon profil",
    description: "Année, filière, groupe, matières, objectifs, disponibilités, limites de travail et préférences.",
    icon: UserRound,
    group: "configuration",
    priority: "P0",
  },
  {
    path: "/settings",
    label: "Paramètres",
    description: "Sources, modèles IA, budgets, stockage local, sauvegarde, export, suppression et thème.",
    icon: Settings,
    group: "configuration",
    priority: "P1",
  },
  {
    path: "/notifications",
    label: "Notifications",
    description: "Changements des sources, échéances, révisions dues, canaux de notification et lecture.",
    icon: Bell,
    group: "configuration",
    priority: "P1",
  },
  {
    path: "/onboarding",
    label: "Première connexion",
    description: "Profil, connexion ou import des sources, préférences et quiz diagnostiques.",
    icon: Rocket,
    // Not in the menu: opened from "Refaire la configuration" in Mon profil / Paramètres.
    group: null,
    priority: "P0",
  },

  // Future version
  {
    path: "/community",
    label: "Community",
    description: "Partage et import de supports entre étudiants. Reporté à une version future par l'équipe.",
    icon: Users,
    group: "future",
    priority: "later",
  },
];

/** Detail screen of one subject: reached from "Mes matières", not from the sidebar. */
export const SUBJECT_DETAIL_ROUTE: AppRoute = {
  path: "/subjects/:subjectId",
  label: "Détail d'une matière",
  description: "Onglets : programme, bibliothèque, notions, notes, supports générés et résultats.",
  icon: BookOpen,
  group: null,
  priority: "P0",
};

export function findRoute(path: string): AppRoute | undefined {
  return APP_ROUTES.find((route) => route.path === path);
}

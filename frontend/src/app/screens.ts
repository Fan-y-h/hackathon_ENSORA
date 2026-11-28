// Code of each built screen, keyed by its path in routes.ts. Loaded only when the screen
// is opened (smaller first download). A path without an entry here shows the "planned"
// placeholder (e.g. Community). src/app/screens.test.ts checks that keys and routes match.
import type { ComponentType } from "react";

/** What React Router's `lazy` expects. */
export type Loader = () => Promise<{ Component: ComponentType }>;

export const lazy =
  <M>(load: () => Promise<M>, pick: (m: M) => ComponentType): Loader =>
  () =>
    load().then((m) => ({ Component: pick(m) }));

export const SCREENS: Record<string, Loader> = {
  "/subjects": lazy(() => import("@/features/subjects/SubjectsPage"), (m) => m.SubjectsPage),
  "/subjects/:subjectId": lazy(() => import("@/features/subjects/SubjectDetailPage"), (m) => m.SubjectDetailPage),
  "/library": lazy(() => import("@/features/library/LibraryPage"), (m) => m.LibraryPage),
  "/agenda": lazy(() => import("@/features/agenda/AgendaPage"), (m) => m.AgendaPage),
  "/planner": lazy(() => import("@/features/planner/PlannerPage"), (m) => m.PlannerPage),
  "/tasks": lazy(() => import("@/features/tasks/TasksPage"), (m) => m.TasksPage),
  "/tutor": lazy(() => import("@/features/tutor/TutorPage"), (m) => m.TutorPage),
  "/studio": lazy(() => import("@/features/studio/StudioPage"), (m) => m.StudioPage),
  "/flashcards": lazy(() => import("@/features/flashcards/FlashcardsPage"), (m) => m.FlashcardsPage),
  "/quiz": lazy(() => import("@/features/quiz/QuizPage"), (m) => m.QuizPage),
  "/exams": lazy(() => import("@/features/exams/ExamsPage"), (m) => m.ExamsPage),
  "/notes": lazy(() => import("@/features/notes/NotesPage"), (m) => m.NotesPage),
  "/focus": lazy(() => import("@/features/focus/FocusPage"), (m) => m.FocusPage),
  "/analytics": lazy(() => import("@/features/analytics/AnalyticsPage"), (m) => m.AnalyticsPage),
  "/grades": lazy(() => import("@/features/grades/GradesPage"), (m) => m.GradesPage),
  "/profile": lazy(() => import("@/features/profile/ProfilePage"), (m) => m.ProfilePage),
  "/settings": lazy(() => import("@/features/settings/SettingsPage"), (m) => m.SettingsPage),
  "/notifications": lazy(() => import("@/features/notifications/NotificationsPage"), (m) => m.NotificationsPage),
};

/** Screens outside the menu layout, routed separately in router.tsx. */
export const STANDALONE_SCREENS = {
  "/login": lazy(() => import("@/features/auth/LoginPage"), (m) => m.LoginPage),
  "/onboarding": lazy(() => import("@/features/onboarding/OnboardingPage"), (m) => m.OnboardingPage),
};

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import { ApiError } from "../api/client";
import { AnalyticsPage } from "../features/analytics/AnalyticsPage";
import { StudioPage, SubjectPage, SubjectsPage, TutorPage } from "../features/ai/StudyTools";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { FocusPage } from "../features/focus/FocusPage";
import { GradesPage } from "../features/grades/GradesPage";
import { FlashcardsPage } from "../features/learning/FlashcardsPage";
import { QuizPage } from "../features/learning/QuizPage";
import { LibraryPage } from "../features/library/LibraryPage";
import { NotificationsPage } from "../features/notifications/NotificationsPage";
import { PlannedPage } from "../features/planned/PlannedPage";
import { PlannerPage } from "../features/planner/PlannerPage";
import { OnboardingPage } from "../features/profile/OnboardingPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { TasksPage } from "../features/tasks/TasksPage";
import { NotesPage } from "../features/workspace/NotesPage";
import { AppShell } from "./AppShell";
import { PreferencesProvider } from "./preferences";
import { SubjectsProvider } from "./subjects";

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: true,
        // Retry transient failures only, never validation/conflict errors.
        retry: (count, error) => count < 2 && (!(error instanceof ApiError) || error.isNetwork || error.status >= 500),
      },
    },
  });
}

const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "planner", element: <PlannerPage /> },
      { path: "agenda", element: <PlannerPage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "flashcards", element: <FlashcardsPage /> },
      { path: "quiz", element: <QuizPage /> },
      { path: "notes", element: <NotesPage /> },
      { path: "focus", element: <FocusPage /> },
      { path: "analytics", element: <AnalyticsPage /> },
      { path: "grades", element: <GradesPage /> },
      { path: "notifications", element: <NotificationsPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "onboarding", element: <OnboardingPage /> },
      { path: "profile", element: <SettingsPage /> },
      { path: "subjects", element: <SubjectsPage /> },
      { path: "subjects/:subjectId", element: <SubjectPage /> },
      { path: "library", element: <LibraryPage /> },
      { path: "tutor", element: <TutorPage /> },
      { path: "studio", element: <StudioPage /> },
      { path: "exams", element: <QuizPage exams /> },
      { path: "*", element: <PlannedPage module="notFound" /> },
    ],
  },
]);

export function App({ queryClient }: { queryClient: QueryClient }) {
  return (
    <QueryClientProvider client={queryClient}>
      <PreferencesProvider>
        <SubjectsProvider>
          <RouterProvider router={router} />
        </SubjectsProvider>
      </PreferencesProvider>
    </QueryClientProvider>
  );
}

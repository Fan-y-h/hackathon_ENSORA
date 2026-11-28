import { createBrowserRouter, type RouteObject } from "react-router";

import { AppShell } from "@/app/layouts/AppShell";
import { OnboardingLayout } from "@/app/layouts/OnboardingLayout";
import { APP_ROUTES, SUBJECT_DETAIL_ROUTE, type AppRoute } from "@/app/routes";
import { SCREENS, STANDALONE_SCREENS, lazy } from "@/app/screens";
import { NotFoundPage } from "@/components/NotFoundPage";
import { PlaceholderPage } from "@/components/PlaceholderPage";
import { RouteError } from "@/components/RouteError";
import { LoadingState } from "@/components/ui";
import { DashboardPage } from "@/features/dashboard/DashboardPage";

/**
 * Shown while the code of the first page opened is still downloading
 * (without it the screen could stay empty for a moment on a slow connection).
 */
const firstLoad = (
  <div className="grid min-h-dvh place-items-center bg-canvas">
    <LoadingState label="Chargement d'Ensora+…" />
  </div>
);

function toChildRoute(route: AppRoute): RouteObject {
  // Child paths are relative to the parent "/", so "/library" becomes "library".
  const where = route.path === "/" ? { index: true as const } : { path: route.path.slice(1) };
  // The dashboard is the first screen after login: loaded with the app, not on demand.
  if (route.path === "/") return { ...where, element: <DashboardPage /> };
  const loader = SCREENS[route.path];
  return loader ? { ...where, lazy: loader } : { ...where, element: <PlaceholderPage route={route} /> };
}

// Onboarding is shown without the sidebar, on its own route below.
const shellRoutes = APP_ROUTES.filter((route) => route.path !== "/onboarding");

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    errorElement: <RouteError />,
    hydrateFallbackElement: firstLoad,
    children: [
      {
        // A crashing page shows its error inside the layout: the menu stays usable.
        errorElement: <RouteError />,
        children: [
          ...shellRoutes.map(toChildRoute),
          toChildRoute(SUBJECT_DETAIL_ROUTE),
          // Developer-only component gallery. import.meta.env.DEV is false in every build,
          // so this page and its code never reach the deployed site.
          ...(import.meta.env.DEV ? [{ path: "dev/ui", lazy: lazy(() => import("@/components/dev/UiGallery"), (m) => m.UiGallery) }] : []),
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
  {
    path: "/login",
    errorElement: <RouteError />,
    hydrateFallbackElement: firstLoad,
    lazy: STANDALONE_SCREENS["/login"],
  },
  {
    path: "/onboarding",
    element: <OnboardingLayout />,
    errorElement: <RouteError />,
    hydrateFallbackElement: firstLoad,
    children: [{ index: true, lazy: STANDALONE_SCREENS["/onboarding"] }],
  },
]);

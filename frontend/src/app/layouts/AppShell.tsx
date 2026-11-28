import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router";

import { useSession } from "@/api/hooksWorkspace";
import { USE_MOCKS } from "@/app/config";
import { Sidebar } from "@/app/layouts/Sidebar";
import { Topbar } from "@/app/layouts/Topbar";
import { usePageAnnouncer } from "@/app/usePageAnnouncer";
import { DemoBanner } from "@/components/DemoBanner";
import { LoadingState } from "@/components/ui";

/** Main layout: sidebar on the left, top bar, then the current page (<Outlet />). */
export function AppShell() {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  const session = useSession();
  // Tab title + focus on the new page's heading after each navigation (accessibility).
  usePageAnnouncer();

  // On a phone, close the menu as soon as the student opens another page.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  // Signed-out students go to the login screen, and come back here afterwards.
  if (session.isPending) return <LoadingState label="Ouverture de votre espace…" />;
  if (!session.data?.authenticated) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  }

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
      <a
        href="#contenu"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:text-ink focus:shadow"
      >
        Aller au contenu
      </a>

      <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />

      <div className="flex min-w-0 flex-col">
        <Topbar onMenuClick={() => setMenuOpen(true)} />
        <main id="contenu" className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6">
          {USE_MOCKS && <DemoBanner />}
          <Outlet />
        </main>
      </div>
    </div>
  );
}

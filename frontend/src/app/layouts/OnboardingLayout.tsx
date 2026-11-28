import { useEffect } from "react";
import { Navigate, Outlet } from "react-router";

import { useSession } from "@/api/hooksWorkspace";
import { USE_MOCKS } from "@/app/config";
import { setPageTitle } from "@/app/usePageAnnouncer";
import { DemoBanner } from "@/components/DemoBanner";
import { EnsaiLogo } from "@/components/EnsaiLogo";
import { EnsoraLogo } from "@/components/EnsoraLogo";
import { LoadingState } from "@/components/ui";

/** Simple centred layout without sidebar, used for the first-time setup. */
export function OnboardingLayout() {
  const session = useSession();

  useEffect(() => {
    setPageTitle("Configurer mon espace");
  }, []);

  // Wait for the session before showing the setup: a signed-out student must not trigger
  // refused requests (and a full page reload) before being sent to the login screen.
  if (session.isPending) return <LoadingState label="Ouverture de votre espace…" />;
  if (!session.data?.authenticated) return <Navigate to={`/login?next=${encodeURIComponent("/onboarding")}`} replace />;

  return (
    <div className="min-h-dvh">
      <header className="flex items-center gap-2.5 border-b border-line bg-surface px-4 py-3 sm:px-6">
        <EnsoraLogo className="w-32 p-1.5 ring-1 ring-line" />
        <EnsaiLogo className="ml-auto w-20 ring-1 ring-line" />
      </header>
      <main className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6">
        {USE_MOCKS && <DemoBanner />}
        <Outlet />
      </main>
    </div>
  );
}

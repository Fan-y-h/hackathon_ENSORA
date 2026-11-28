import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import { queryClient } from "@/api/queryClient";
import { router } from "@/app/router";
import "@/styles/index.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Element #root not found in index.html");
}

/**
 * Shown instead of the app when demo mode is on but the demo backend could not start
 * (e.g. its file failed to download). Without it, every screen would fail with a vague
 * "server error". Plain markup on purpose: it must work even if the rest is broken.
 */
function DemoStartFailed() {
  return (
    <main role="alert" style={{ maxWidth: "32rem", margin: "15vh auto", padding: "0 1rem", fontFamily: "system-ui, sans-serif", lineHeight: 1.5 }}>
      <h1 style={{ fontSize: "1.25rem", fontWeight: 600 }}>Le mode démonstration n'a pas pu démarrer</h1>
      <p>Les données de démonstration ne se sont pas chargées, souvent à cause d'une connexion interrompue. Rechargez la page pour réessayer.</p>
      <button type="button" onClick={() => window.location.reload()} style={{ marginTop: "1rem", padding: "0.5rem 1rem", borderRadius: "0.5rem", border: "1px solid currentColor", background: "transparent", color: "inherit", cursor: "pointer" }}>
        Recharger la page
      </button>
    </main>
  );
}

async function start(root: HTMLElement) {
  // Demo mode: start the fake backend BEFORE the first request.
  // Same rule as USE_MOCKS in src/app/config.ts: keep both in sync. It is written inline here
  // (not imported) so that Vite drops the whole demo backend from a build with mocks off.
  if (import.meta.env.VITE_USE_MOCKS !== "false") {
    try {
      const { startMockBackend } = await import("@/mocks/browser");
      await startMockBackend();
    } catch (error) {
      console.error("Demo backend failed to start", error);
      createRoot(root).render(<DemoStartFailed />);
      return;
    }
  }

  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}

void start(rootElement);

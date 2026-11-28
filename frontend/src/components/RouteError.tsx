import { isRouteErrorResponse, Link, useRouteError } from "react-router";

import { buttonClasses, ErrorPanel } from "@/components/ui";

/**
 * Shown when a screen crashes or fails to load (e.g. a new version was deployed).
 * The rest of the app (sidebar) keeps working when it is used inside the shell.
 */
export function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error && /dynamically imported module|Failed to fetch/i.test(error.message)
      ? "Cette page n'a pas pu être chargée. Une nouvelle version est peut-être disponible : rechargez la page."
      : "Un problème inattendu est survenu sur cette page.";

  return (
    <section className="mx-auto flex max-w-xl flex-col gap-4 p-6">
      <ErrorPanel title="Page indisponible" message={message} onRetry={() => window.location.reload()} />
      <Link to="/" className={buttonClasses("secondary", "sm", "self-start")}>
        Retour au tableau de bord
      </Link>
    </section>
  );
}

import { useEffect, useRef } from "react";
import { matchPath, useLocation } from "react-router";

import { APP_ROUTES, SUBJECT_DETAIL_ROUTE } from "@/app/routes";

const SITE = "ENSORA+";

/** Sets the browser tab title, e.g. "Agenda · ENSORA+". */
export function setPageTitle(label: string | null) {
  document.title = label ? `${label} · ${SITE}` : SITE;
}

function routeLabel(pathname: string): string | null {
  const route = [...APP_ROUTES, SUBJECT_DETAIL_ROUTE].find((r) => matchPath({ path: r.path, end: true }, pathname));
  return route?.label ?? null;
}

/**
 * After each page change (path only, not ?tab=... changes):
 * - the tab title names the page (WCAG 2.4.2), using the page's <h1> once it is drawn
 *   (e.g. the subject name), else the route label;
 * - keyboard focus moves to that <h1>, so keyboard and screen reader users land on the new
 *   page instead of staying in the menu. Not done on the very first load.
 */
export function usePageAnnouncer() {
  const { pathname } = useLocation();
  const firstLoad = useRef(true);

  useEffect(() => {
    setPageTitle(routeLabel(pathname));
    const moveFocus = !firstLoad.current;
    firstLoad.current = false;

    // Pages are loaded on demand: wait (up to 5 s) for their <h1 id="page-title">.
    let tries = 0;
    const timer = window.setInterval(() => {
      const heading = document.getElementById("page-title");
      if (heading) {
        window.clearInterval(timer);
        const text = heading.textContent?.trim();
        if (text) setPageTitle(text);
        if (moveFocus) {
          heading.setAttribute("tabindex", "-1");
          heading.focus({ preventScroll: true });
        }
      } else if (++tries > 50) {
        window.clearInterval(timer);
      }
    }, 100);
    return () => window.clearInterval(timer);
  }, [pathname]);
}

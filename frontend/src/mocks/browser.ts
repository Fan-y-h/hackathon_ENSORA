// Starts the fake backend inside the page.
//
// Why not MSW's Service Worker? Browsers stop idle service workers after a few seconds;
// MSW then forgets which tabs it mocks and lets requests through to the (absent) real
// server. Seen in testing: mocks silently stopped after ~6 s. Every API call of the app
// goes through window.fetch, so we answer them right here with the same MSW handlers.
import { getResponse } from "msw";

import type { ApiErrorBody } from "@/api/types";
import { API_BASE_URL } from "@/app/config";

import { db, persistDb } from "./core";
import { handlers } from "./handlers";

/** Routes reachable without being signed in. */
const PUBLIC = ["/auth/", "/health", "/demo/"];

function unauthorized(): Response {
  const body: ApiErrorBody = { error: { code: "unauthenticated", message: "Connectez-vous pour continuer." } };
  return new Response(JSON.stringify(body), { status: 401, headers: { "Content-Type": "application/json" } });
}

export async function startMockBackend(): Promise<void> {
  const realFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    const { origin, pathname } = new URL(request.url);
    // Only our API is mocked; scripts, fonts and images go to the network normally.
    if (origin === window.location.origin && pathname.startsWith(API_BASE_URL)) {
      const route = pathname.slice(API_BASE_URL.length);
      if (!db.session.authenticated && !PUBLIC.some((p) => route.startsWith(p))) return unauthorized();
      const response = await getResponse(handlers, request);
      // Keep the demo data across reloads: save after every change.
      if (request.method !== "GET") persistDb();
      if (response) return response;
    }
    return realFetch(input, init);
  };

  // Clean up the Service Worker that older versions of this demo registered.
  const registrations = await navigator.serviceWorker?.getRegistrations().catch(() => []);
  await Promise.all((registrations ?? []).filter((r) => r.active?.scriptURL.endsWith("/mockServiceWorker.js")).map((r) => r.unregister()));
}

import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";

/** Session expired or never opened: send the student to the login screen, then back here. */
function onAuthError(error: unknown) {
  if (error instanceof ApiError && error.status === 401 && !window.location.pathname.startsWith("/login")) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.assign(`/login?next=${next}`);
  }
}

/** Shared cache of server data for the whole app (TanStack Query). */
export const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onAuthError }),
  mutationCache: new MutationCache({ onError: onAuthError }),
  defaultOptions: {
    queries: {
      // Data stays "fresh" for 30 s: switching pages does not refetch everything.
      staleTime: 30_000,
      // Retry network/5xx errors once; 4xx errors (401, 403, 404, 422...) will not fix themselves.
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
        return failureCount < 1;
      },
      refetchOnWindowFocus: false,
    },
  },
});

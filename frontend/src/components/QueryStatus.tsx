import type { UseQueryResult } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { ErrorPanel, LoadingState } from "@/components/ui";

interface QueryStatusProps {
  query: Pick<UseQueryResult<unknown, Error>, "isPending" | "error" | "refetch" | "fetchStatus">;
  loadingLabel?: string;
}

/**
 * Loading and error states for one API query, so every card handles them the same way.
 * Renders nothing once data is there: the caller then shows its content (or its empty state).
 */
export function QueryStatus({ query, loadingLabel }: QueryStatusProps) {
  if (query.error) {
    const requestId = query.error instanceof ApiError ? query.error.requestId : undefined;
    return <ErrorPanel message={query.error.message} requestId={requestId} onRetry={() => void query.refetch()} />;
  }
  // A disabled query (e.g. nothing selected yet) is "pending" but idle: it is not loading.
  if (query.isPending && query.fetchStatus !== "idle") return <LoadingState label={loadingLabel} />;
  return null;
}

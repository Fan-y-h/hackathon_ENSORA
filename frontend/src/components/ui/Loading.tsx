import { LoaderCircle } from "lucide-react";

import { cn } from "@/lib/cn";

/** Spinner with a visible text, announced politely to screen readers. */
export function LoadingState({ label = "Chargement…" }: { label?: string }) {
  return (
    <p role="status" className="flex items-center gap-2 py-6 text-sm text-muted">
      <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
      {label}
    </p>
  );
}

/** Grey placeholder block while content loads. Set width and height with className. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-md bg-surface-2", className)} />;
}

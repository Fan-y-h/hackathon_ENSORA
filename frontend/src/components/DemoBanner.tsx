import { FlaskConical } from "lucide-react";

/** Required by docs/MVP.md: demo data must always be visibly labelled. */
export function DemoBanner() {
  return (
    <p
      role="note"
      className="mb-6 flex items-start gap-2.5 rounded-lg bg-warning-soft px-3.5 py-2.5 text-sm text-warning"
    >
      <FlaskConical className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>
        <strong className="font-semibold">Mode démonstration.</strong> Les données affichées sont fictives.
      </span>
    </p>
  );
}

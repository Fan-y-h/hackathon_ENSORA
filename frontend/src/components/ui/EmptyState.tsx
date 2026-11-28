import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  description?: ReactNode;
  icon?: ReactNode;
  /** Usually one button telling the student what to do next. */
  action?: ReactNode;
}

/** Shown when a list is empty: explains why and offers the next step. */
export function EmptyState({ title, description, icon, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-line bg-surface px-6 py-10 text-center">
      <span className="mb-3 grid size-11 place-items-center rounded-full bg-surface-2 text-muted">
        {icon ?? <Inbox className="size-5" aria-hidden="true" />}
      </span>
      <p className="font-semibold">{title}</p>
      {description && <p className="mt-1 max-w-md text-sm text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

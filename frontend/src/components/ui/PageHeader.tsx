import type { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
}

/** Top of every screen: exactly one <h1> per page. */
export function PageHeader({ title, description, icon, actions }: PageHeaderProps) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 items-start gap-3">
        {icon && (
          <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-surface text-accent ring-1 ring-line">
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <h1 id="page-title" className="text-2xl font-semibold tracking-tight">
            {title}
          </h1>
          {description && <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

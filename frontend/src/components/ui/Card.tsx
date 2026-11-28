import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** Plain bordered surface. Keep cards calm: no stacked shadows or gradients. */
export function Card({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("rounded-xl border border-line bg-surface p-5", className)} {...props} />;
}

interface CardHeaderProps {
  title: ReactNode;
  /** Short line under the title. */
  description?: ReactNode;
  /** Buttons or links aligned on the right. */
  actions?: ReactNode;
}

export function CardHeader({ title, description, actions }: CardHeaderProps) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-base font-semibold">{title}</h2>
        {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
    </div>
  );
}

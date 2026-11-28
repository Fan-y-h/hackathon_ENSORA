import { Construction } from "lucide-react";

import type { AppRoute } from "@/app/routes";
import { EmptyState, PageHeader } from "@/components/ui";

interface PlaceholderPageProps {
  route: AppRoute;
}

/**
 * Temporary page shown until the real screen is built.
 * Only user-facing text here: priorities, build steps and specification
 * references stay in the code (routes.ts), never on screen.
 */
export function PlaceholderPage({ route }: PlaceholderPageProps) {
  const Icon = route.icon;
  const isLater = route.priority === "later";

  return (
    <section aria-labelledby="page-title">
      <PageHeader title={route.label} icon={<Icon className="size-5" aria-hidden="true" />} />
      <div className="max-w-2xl">
        <EmptyState
          icon={<Construction className="size-5" aria-hidden="true" />}
          title={isLater ? "Prévu dans une version future" : "Bientôt disponible"}
          description={route.description}
        />
      </div>
    </section>
  );
}

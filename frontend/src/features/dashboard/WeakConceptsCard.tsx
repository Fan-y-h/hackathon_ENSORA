import { Sparkles } from "lucide-react";
import { Link } from "react-router";

import { useAnalytics } from "@/api/hooksPlanning";
import { QueryStatus } from "@/components/QueryStatus";
import { Card, CardHeader, EmptyState, MasteryIndicator, buttonClasses } from "@/components/ui";

/** The 3 weakest assessed concepts, with a shortcut to practice. Unassessed ones are not "weak". */
export function WeakConceptsCard() {
  const analytics = useAnalytics("week");
  const weak = analytics.data?.weak_concepts.slice(0, 3);

  return (
    <Card>
      <CardHeader
        title="Notions fragiles"
        actions={
          <Link to="/analytics" className={buttonClasses("ghost", "sm")}>
            Détail
          </Link>
        }
      />
      <QueryStatus query={analytics} />
      {weak?.length === 0 && <EmptyState icon={<Sparkles className="size-5" aria-hidden="true" />} title="Rien de fragile pour l'instant" description="Faites un quiz pour mesurer les notions pas encore évaluées." />}
      {weak && weak.length > 0 && (
        <ul className="flex flex-col gap-3">
          {weak.map((c) => (
            <li key={c.concept_id}>
              <Link to={`/subjects/${c.subject_id}`} className="focus-ring mb-1 block rounded text-sm font-medium hover:underline">
                {c.label}
              </Link>
              <MasteryIndicator value={c.score} label={c.label} />
            </li>
          ))}
        </ul>
      )}
      {weak && weak.length > 0 && (
        <Link to="/quiz" className={buttonClasses("secondary", "sm", "mt-4")}>
          S'entraîner
        </Link>
      )}
    </Card>
  );
}

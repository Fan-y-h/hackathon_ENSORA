import { BadgeCheck } from "lucide-react";

import { useProfile } from "@/api/hooks";
import { QueryStatus } from "@/components/QueryStatus";

const COHORT_LABEL = { "1A": "1re année (1A)", "2A": "2e année (2A)", "3A": "3e année (3A)" } as const;

/**
 * Identity and schooling as read from the ENSAI account (Pamplemousse / Moodle).
 * Never typed by the student: a mistake must be fixed at the source (scolarité).
 */
export function EnsaiIdentity() {
  const profile = useProfile();
  if (!profile.data) return <QueryStatus query={profile} />;
  const p = profile.data;
  const rows: Array<[string, string]> = [
    ["Nom", p.display_name],
    ["Année d'études", COHORT_LABEL[p.cohort]],
    ["Filière", p.cohort === "3A" ? (p.track ?? "Non renseignée") : "Uniquement en 3A"],
    ["Semestre en cours", `Semestre ${p.semester}`],
    ["Année universitaire", p.academic_year],
  ];
  return (
    <div className="flex flex-col gap-3">
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted">{label}</dt>
            <dd className="font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="flex items-start gap-2 text-xs text-muted">
        <BadgeCheck className="mt-0.5 size-4 shrink-0 text-success" aria-hidden="true" />
        Récupéré de votre compte ENSAI. Une information est fausse ? Elle se corrige auprès de la scolarité, puis se met à jour ici à la prochaine synchronisation.
      </p>
    </div>
  );
}

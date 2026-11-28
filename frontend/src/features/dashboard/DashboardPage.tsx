import { Target } from "lucide-react";
import { Link } from "react-router";

import { useProfile } from "@/api/hooks";
import { QueryStatus } from "@/components/QueryStatus";
import { Card, CardHeader, EmptyState, PageHeader, buttonClasses } from "@/components/ui";
import { formatDay } from "@/lib/format";

import { DeadlinesCard } from "./DeadlinesCard";
import { SourcesCard } from "./SourcesCard";
import { TasksCard } from "./TasksCard";
import { TodayCard } from "./TodayCard";
import { WeakConceptsCard } from "./WeakConceptsCard";

function GoalsCard() {
  const profile = useProfile();
  const goals = profile.data?.goals;

  return (
    <Card>
      <CardHeader
        title="Mes objectifs"
        actions={
          <Link to="/profile" className={buttonClasses("ghost", "sm")}>
            Modifier
          </Link>
        }
      />
      <QueryStatus query={profile} />
      {goals?.length === 0 && (
        <EmptyState
          icon={<Target className="size-5" aria-hidden="true" />}
          title="Aucun objectif"
          description="Fixez un objectif dans votre profil pour que le planning en tienne compte."
        />
      )}
      {goals && goals.length > 0 && (
        <ul className="flex flex-col gap-2 text-sm">
          {goals.map((goal) => (
            <li key={goal} className="flex items-start gap-2">
              <Target className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />
              {goal}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** Home screen: today's day, what is coming, what to do next and whether data is up to date. */
export function DashboardPage() {
  const profile = useProfile();
  const firstName = profile.data?.display_name;
  const today = formatDay(new Date());

  return (
    <section aria-labelledby="page-title">
      <PageHeader
        title={firstName ? `Bonjour, ${firstName}` : "Bonjour"}
        description={today.charAt(0).toUpperCase() + today.slice(1)}
      />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex flex-col gap-6">
          <TodayCard />
          <TasksCard />
        </div>
        <div className="flex flex-col gap-6">
          <DeadlinesCard />
          <WeakConceptsCard />
          <GoalsCard />
          <SourcesCard />
        </div>
      </div>
    </section>
  );
}

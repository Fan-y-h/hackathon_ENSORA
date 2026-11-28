import { Bell, CheckCheck } from "lucide-react";
import { Link } from "react-router";

import { useMarkNotification, useNotifications } from "@/api/hooksWorkspace";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, Card, EmptyState, PageHeader } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";

/** In-app alerts: new documents, timetable changes, deadlines, reviews due, plan changes. */
export function NotificationsPage() {
  const notifications = useNotifications();
  const mark = useMarkNotification();
  const unread = notifications.data?.filter((n) => !n.read_at) ?? [];

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="Notifications"
        icon={<Bell className="size-5" aria-hidden="true" />}
        description={notifications.data ? `${unread.length} non lue${unread.length > 1 ? "s" : ""}` : undefined}
        actions={
          unread.length > 0 && (
            <Button variant="secondary" onClick={() => unread.forEach((n) => mark.mutate({ id: n.id, read: true }))}>
              <CheckCheck className="size-4" aria-hidden="true" />
              Tout marquer comme lu
            </Button>
          )
        }
      />
      <QueryStatus query={notifications} />
      {notifications.data?.length === 0 && <EmptyState icon={<Bell className="size-5" aria-hidden="true" />} title="Aucune notification" />}
      <ul className="flex flex-col gap-2">
        {notifications.data?.map((n) => (
          <li key={n.id}>
            <Card className={cn("flex flex-wrap items-start gap-3 p-4", !n.read_at && "border-accent/60")}>
              <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", n.read_at ? "bg-transparent" : "bg-accent")} aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="font-medium">
                  {n.title}
                  {!n.read_at && <span className="sr-only"> (non lue)</span>}
                </p>
                <p className="text-sm text-muted">{n.body}</p>
                <p className="mt-1 text-xs text-muted">{formatRelative(n.created_at)}</p>
              </div>
              <div className="flex gap-2">
                {n.target_path && (
                  <Link to={n.target_path} onClick={() => !n.read_at && mark.mutate({ id: n.id, read: true })} className="text-sm font-medium text-accent hover:underline">
                    Voir
                  </Link>
                )}
                <Button size="sm" variant="ghost" onClick={() => mark.mutate({ id: n.id, read: !n.read_at })}>
                  {n.read_at ? "Marquer non lue" : "Marquer lue"}
                </Button>
              </div>
            </Card>
          </li>
        ))}
      </ul>
    </section>
  );
}

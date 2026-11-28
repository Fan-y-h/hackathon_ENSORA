import { useMarkAllNotifications, useMarkNotification, useNotifications } from "../../api/queries";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Card, EmptyState, PageHeader, QueryState } from "../../components/ui";
import { dayKey, formatClock, formatLongDate } from "../../lib/time";

const TYPE_LABEL: Record<string, string> = {
  review_due: "Répétitions",
  deadline_soon: "Échéance",
  task_overdue: "Retard",
  session_soon: "Séance",
  plan_outdated: "Planning",
  new_assessment: "Évaluation",
  timetable_change: "Emploi du temps",
  grade_recorded: "Note",
  academic_risk: "Risque académique",
};

export function NotificationsPage() {
  const notifications = useNotifications();
  const mark = useMarkNotification();
  const markAll = useMarkAllNotifications();
  const { timezone } = useSubjects();

  return (
    <div className="page" style={{ maxWidth: 900 }}>
      <PageHeader
        title="Notifications"
        actions={
          <button type="button" className="btn" disabled={!notifications.data?.unread_count || markAll.isPending} onClick={() => markAll.mutate()}>
            Tout lire
          </button>
        }
      />
      <Card>
        <QueryState query={notifications}>
          {(data) =>
            data.items.length === 0 ? (
              <EmptyState title="Rien de nouveau" />
            ) : (
              <ul className="list">
                {data.items.map((n) => (
                  <li key={n.id} style={{ opacity: n.read ? 0.7 : 1 }}>
                    <span style={{ color: n.read ? "var(--text-2)" : "var(--accent)" }}>
                      <Icon name="bell" size={18} />
                    </span>
                    <div className="stack" style={{ gap: 2, flex: 1 }}>
                      <span className="row" style={{ gap: 8 }}>
                        <span className="pill">{TYPE_LABEL[n.type] ?? n.type}</span>
                        <strong className="small">{n.title}</strong>
                        {!n.read && <span className="sr-only">(non lue)</span>}
                      </span>
                      {n.body && <span className="small muted">{n.body}</span>}
                      <span className="xsmall muted">
                        {formatLongDate(dayKey(n.created_at, timezone))} · {formatClock(n.created_at, timezone)}
                      </span>
                    </div>
                    <button type="button" className="btn btn--small" onClick={() => mark.mutate({ id: n.id, read: !n.read })}>
                      {n.read ? "Marquer non lue" : "Marquer lue"}
                    </button>
                  </li>
                ))}
              </ul>
            )
          }
        </QueryState>
      </Card>
    </div>
  );
}

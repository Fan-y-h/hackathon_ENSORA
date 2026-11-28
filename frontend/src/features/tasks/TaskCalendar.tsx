import frLocale from "@fullcalendar/core/locales/fr";
import dayGridPlugin from "@fullcalendar/daygrid";
import listPlugin from "@fullcalendar/list";
import FullCalendar from "@fullcalendar/react";
import { useState } from "react";

import { useUpdateTask } from "@/api/hooks";
import type { Task } from "@/api/types";
import { ConfirmDialog } from "@/components/ui";

const isSmallScreen = () => typeof window !== "undefined" && window.matchMedia("(max-width: 640px)").matches;

/** Tasks placed on their deadline (month grid, or a list on phones). Clicking one marks it done. */
export function TaskCalendar({ tasks }: { tasks: Task[] }) {
  const update = useUpdateTask();
  const [selected, setSelected] = useState<Task | null>(null);
  const dated = tasks.filter((t) => t.deadline_at && t.status !== "cancelled");

  return (
    <div className="ensora-calendar rounded-xl border border-line bg-surface p-3 sm:p-4">
      <FullCalendar
        plugins={[dayGridPlugin, listPlugin]}
        locale={frLocale}
        initialView={isSmallScreen() ? "listMonth" : "dayGridMonth"}
        headerToolbar={{ left: "prev,next today", center: "title", right: "dayGridMonth,listMonth" }}
        buttonText={{ prev: "‹ Précédent", next: "Suivant ›", today: "Aujourd'hui", month: "Mois", list: "Liste" }}
        buttonIcons={false}
        firstDay={1}
        height="auto"
        noEventsText="Aucune tâche avec échéance sur cette période"
        events={dated.map((t) => ({
          id: t.id,
          title: t.status === "done" ? `✓ ${t.title}` : t.title,
          start: (t.deadline_at as string).slice(0, 10),
          allDay: true,
          classNames: [t.status === "done" ? "ev-other" : t.priority === 1 ? "ev-exam" : "ev-deadline"],
          extendedProps: { task: t },
        }))}
        eventClick={(arg) => {
          const task = arg.event.extendedProps.task as Task;
          if (task.status !== "done") setSelected(task);
        }}
      />
      <ConfirmDialog
        open={selected !== null}
        title="Marquer cette tâche comme terminée ?"
        description={selected?.title}
        confirmLabel="Terminée"
        loading={update.isPending}
        onConfirm={() => selected && update.mutate({ id: selected.id, status: "done", remaining_min: 0, expected_revision: selected.revision }, { onSuccess: () => setSelected(null) })}
        onCancel={() => setSelected(null)}
      />
    </div>
  );
}

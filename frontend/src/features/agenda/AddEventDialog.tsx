import { useState, type FormEvent } from "react";

import { ApiError } from "@/api/client";
import { useCreateEvent } from "@/api/hooks";
import { Button, Dialog, TextField } from "@/components/ui";

interface AddEventDialogProps {
  open: boolean;
  onClose: () => void;
}

function todayInputValue(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** Adds a personal event: sport, job, appointment... The planner will not schedule work there. */
export function AddEventDialog({ open, onClose }: AddEventDialogProps) {
  const createEvent = useCreateEvent();
  const [title, setTitle] = useState("");
  const [date, setDate] = useState(todayInputValue);
  const [start, setStart] = useState("18:00");
  const [end, setEnd] = useState("20:00");

  // The server validates; we show its message next to the field it names.
  const apiError = createEvent.error instanceof ApiError ? createEvent.error : null;
  const field = (apiError?.details as { field?: string } | undefined)?.field;
  const errorFor = (name: string) => (field === name ? apiError?.message : undefined);

  function close() {
    createEvent.reset();
    setTitle("");
    onClose();
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    createEvent.mutate(
      {
        type: "personal",
        title,
        // Local date + time typed by the student -> ISO UTC for the API.
        start_at: new Date(`${date}T${start}`).toISOString(),
        end_at: new Date(`${date}T${end}`).toISOString(),
      },
      { onSuccess: close },
    );
  }

  return (
    <Dialog open={open} title="Ajouter une indisponibilité" onClose={close}>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <p className="text-sm text-muted">Le planning de révision ne placera aucune session sur ce créneau.</p>
        <TextField label="Intitulé" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Ex. : entraînement de handball" required error={errorFor("title")} />
        <TextField label="Date" type="date" value={date} onChange={(e) => setDate(e.target.value)} required error={errorFor("start_at")} />
        <div className="grid grid-cols-2 gap-4">
          <TextField label="Début" type="time" value={start} onChange={(e) => setStart(e.target.value)} required />
          <TextField label="Fin" type="time" value={end} onChange={(e) => setEnd(e.target.value)} required error={errorFor("end_at")} />
        </div>
        {apiError && !field && <p className="text-sm text-danger">{apiError.message}</p>}
        <div className="mt-2 flex justify-end gap-2">
          <Button variant="secondary" onClick={close}>
            Annuler
          </Button>
          <Button type="submit" loading={createEvent.isPending}>
            Ajouter
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

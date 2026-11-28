import { CalendarCheck, PlugZap } from "lucide-react";
import { useState } from "react";

import { useAvailabilitySuggestions, useConnect } from "@/api/hooksWorkspace";
import type { Preferences, WorkMethod, WorkWindow } from "@/api/typesWorkspace";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, ErrorPanel } from "@/components/ui";
import { cn } from "@/lib/cn";
import { formatMinutes } from "@/lib/format";
import { WORK_METHODS, weeklyFromDaily } from "@/lib/workMethods";

const DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];

export type AvailabilityValue = Pick<Preferences, "work_windows" | "daily_max_min" | "weekly_max_min" | "work_method" | "min_session_min" | "max_session_min">;

const same = (a: WorkWindow, b: WorkWindow) => a.day === b.day && a.start === b.start && a.end === b.end;

/** Recomputes the deduced fields (weekly limit, session lengths) from what the student chose. */
export function normalizeAvailability(value: AvailabilityValue): AvailabilityValue {
  const method = WORK_METHODS[value.work_method];
  const days = new Set(value.work_windows.map((w) => w.day)).size;
  return { ...value, weekly_max_min: weeklyFromDaily(value.daily_max_min, days), min_session_min: method.minSession, max_session_min: method.maxSession };
}

interface AvailabilityFieldsProps {
  value: AvailabilityValue;
  onChange: (value: AvailabilityValue) => void;
}

/**
 * Free slots are deduced from Pamplemousse (classes) and Google Agenda (personal events):
 * the student only keeps or removes them, sets one daily limit and picks a Pomodoro method.
 */
export function AvailabilityFields({ value, onChange }: AvailabilityFieldsProps) {
  const connect = useConnect();
  const [connecting, setConnecting] = useState(false);
  const suggestions = useAvailabilitySuggestions(true);
  const update = (changes: Partial<AvailabilityValue>) => onChange(normalizeAvailability({ ...value, ...changes }));

  const missing = suggestions.data?.missing ?? [];
  const candidates = [...value.work_windows, ...(suggestions.data?.windows ?? []).filter((s) => !value.work_windows.some((w) => same(w, s)))].sort(
    (a, b) => a.day - b.day || a.start.localeCompare(b.start),
  );
  const days = new Set(value.work_windows.map((w) => w.day)).size;

  async function connectBoth() {
    setConnecting(true);
    try {
      for (const kind of missing) await connect.mutateAsync(kind);
      const fresh = await suggestions.refetch();
      if (fresh.data) update({ work_windows: fresh.data.windows });
    } finally {
      setConnecting(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="slots-title" className="flex flex-col gap-3">
        <h3 id="slots-title" className="font-semibold">
          Mes créneaux libres
        </h3>
        <p className="text-sm text-muted">
          Ensora+ lit vos cours dans Pamplemousse et vos rendez-vous dans Google Agenda, et en déduit quand vous êtes libre. Décochez les créneaux où vous ne voulez pas réviser.
        </p>
        {missing.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 rounded-lg bg-accent-soft/50 p-3 text-sm">
            <span className="flex-1">{missing.length === 2 ? "Connectez Pamplemousse et Google Agenda" : missing[0] === "pamplemousse" ? "Connectez Pamplemousse" : "Connectez Google Agenda"} pour déduire vos disponibilités.</span>
            <Button size="sm" loading={connecting} onClick={() => void connectBoth()}>
              <PlugZap className="size-4" aria-hidden="true" />
              {missing.length === 2 ? "Connecter les deux" : "Connecter"}
            </Button>
          </div>
        )}
        {connect.error && <ErrorPanel message={connect.error.message} />}
        <QueryStatus query={suggestions} loadingLabel="Lecture de votre emploi du temps…" />
        {suggestions.data && value.work_windows.length === 0 && suggestions.data.windows.length > 0 && (
          <Button size="sm" variant="secondary" className="self-start" onClick={() => update({ work_windows: suggestions.data.windows })}>
            <CalendarCheck className="size-4" aria-hidden="true" />
            Utiliser tous les créneaux libres
          </Button>
        )}
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {DAYS.map((label, index) => {
            const day = index + 1;
            const items = candidates.filter((w) => w.day === day);
            return (
              <fieldset key={label} className="rounded-lg border border-line p-3">
                <legend className="px-1 text-sm font-medium">{label}</legend>
                {items.length === 0 && <p className="text-xs text-muted">Pas de créneau libre</p>}
                {items.map((w) => {
                  const checked = value.work_windows.some((x) => same(x, w));
                  return (
                    <label key={`${w.start}-${w.end}`} className="flex items-center gap-2 py-0.5 text-sm">
                      <input
                        type="checkbox"
                        className="focus-ring size-4 accent-(--accent)"
                        checked={checked}
                        onChange={(e) => update({ work_windows: e.target.checked ? [...value.work_windows, w] : value.work_windows.filter((x) => !same(x, w)) })}
                      />
                      <span className="tabular-nums">
                        {w.start} – {w.end}
                      </span>
                    </label>
                  );
                })}
              </fieldset>
            );
          })}
        </div>
      </section>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 font-semibold">Ma méthode de travail</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {(Object.keys(WORK_METHODS) as WorkMethod[]).map((id) => (
            <label
              key={id}
              className={cn(
                "flex cursor-pointer flex-col gap-0.5 rounded-lg border p-3 text-sm has-focus-visible:outline-2 has-focus-visible:outline-accent",
                value.work_method === id ? "border-accent bg-accent-soft/50" : "border-line",
              )}
            >
              <input type="radio" name="work-method" className="sr-only" checked={value.work_method === id} onChange={() => update({ work_method: id })} />
              <span className="font-medium">{WORK_METHODS[id].label}</span>
              <span className="text-xs text-muted">{WORK_METHODS[id].hint}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="daily-limit" className="font-semibold">
            Révision maximale par jour
          </label>
          <div className="flex items-center gap-2">
            <input
              id="daily-limit"
              type="number"
              min={0.5}
              max={12}
              step={0.5}
              value={value.daily_max_min / 60}
              onChange={(e) => update({ daily_max_min: Math.round(Math.max(0.5, Math.min(12, Number(e.target.value) || 0.5)) * 60) })}
              aria-describedby="weekly-deduced"
              className="focus-ring h-10 w-24 rounded-lg border border-line bg-surface px-3 text-sm"
            />
            <span className="text-sm text-muted">heures</span>
          </div>
        </div>
        <p id="weekly-deduced" className="pb-2 text-sm text-muted">
          Soit au plus {formatMinutes(weeklyFromDaily(value.daily_max_min, days))} par semaine ({days} jour{days > 1 ? "s" : ""} avec des créneaux).
        </p>
      </div>
    </div>
  );
}

import { createContext, useContext, useMemo, type ReactNode } from "react";

import { useEvents, usePlan, useProfile, useTasks } from "../api/queries";
import { useSourceSubjects } from "../api/sources";
import { deriveSubjectNames, resolveSubjectColor, subjectAcronym } from "../lib/subjects";
import { addDays, dayKey, dayRange, DEFAULT_TZ, mondayOf } from "../lib/time";
import { usePreferences } from "./preferences";

interface SubjectsCtx {
  ids: string[];
  name: (id: string | null | undefined) => string;
  acronym: (id: string | null | undefined) => string;
  color: (id: string | null | undefined) => string | undefined;
  timezone: string;
}

const SubjectsContext = createContext<SubjectsCtx | null>(null);

/** Collects the subjects present in the agenda, tasks and plan (no catalog API yet). */
export function SubjectsProvider({ children }: { children: ReactNode }) {
  const { data: profile } = useProfile();
  const tz = profile?.timezone ?? DEFAULT_TZ;
  const monday = mondayOf(dayKey(new Date(), tz));
  const range = dayRange(addDays(monday, -7), addDays(monday, 35), tz);
  const events = useEvents(range.start, range.end);
  const tasks = useTasks();
  const plan = usePlan();
  const prefs = usePreferences();
  const catalog = useSourceSubjects();

  const value = useMemo<SubjectsCtx>(() => {
    const items: { subject_id?: string | null; title: string }[] = [
      ...(events.data?.items ?? []),
      ...(tasks.data?.items ?? []),
      ...(plan.data?.sessions ?? []),
    ];
    const ids: string[] = [];
    for (const item of items) if (item.subject_id && !ids.includes(item.subject_id)) ids.push(item.subject_id);
    ids.sort();
    // Official names from the synced Pamplemousse catalog first, agenda titles as fallback.
    const names = { ...deriveSubjectNames(events.data?.items ?? []), ...catalog.data };
    const ctx = { overrides: prefs.subjectColors, accessible: prefs.accessible, dark: prefs.resolvedDark };
    return {
      ids,
      timezone: tz,
      name: (id) => (id ? (names[id] ?? subjectAcronym(id)) : "Sans matière"),
      acronym: (id) => subjectAcronym(id),
      color: (id) => (id ? resolveSubjectColor(id, ids, ctx) : undefined),
    };
  }, [events.data, tasks.data, plan.data, catalog.data, prefs.subjectColors, prefs.accessible, prefs.resolvedDark, tz]);

  return <SubjectsContext.Provider value={value}>{children}</SubjectsContext.Provider>;
}

export function useSubjects(): SubjectsCtx {
  const ctx = useContext(SubjectsContext);
  if (!ctx) throw new Error("useSubjects must be used inside SubjectsProvider");
  return ctx;
}

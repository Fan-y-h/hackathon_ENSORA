import { createContext, useContext, useMemo, type ReactNode } from "react";

import { useCatalogSubjects, useDecks, useEvents, usePlan, useProfile, useQuizzes, useTasks } from "../api/queries";
import { useResults, useSourceSubjects } from "../api/sources";
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

/** Collects subjects from the catalog, Pamplemousse results, agenda, tasks, decks, quizzes and plan. */
export function SubjectsProvider({ children }: { children: ReactNode }) {
  const { data: profile } = useProfile();
  const tz = profile?.timezone ?? DEFAULT_TZ;
  const today = dayKey(new Date(), tz);
  const range = useMemo(() => {
    const monday = mondayOf(today);
    return dayRange(addDays(monday, -7), addDays(monday, 35), tz);
  }, [today, tz]);

  const events = useEvents(range.start, range.end);
  const tasks = useTasks();
  const plan = usePlan();
  const decks = useDecks();
  const quizzes = useQuizzes();
  const prefs = usePreferences();
  const catalog = useCatalogSubjects();
  const sourceCatalog = useSourceSubjects();
  const results = useResults();

  const value = useMemo<SubjectsCtx>(() => {
    const idSet = new Set<string>();
    const pushId = (id: string | null | undefined) => {
      if (id && id.trim()) idSet.add(id.trim());
    };

    for (const item of events.data?.items ?? []) pushId(item.subject_id);
    for (const item of tasks.data?.items ?? []) pushId(item.subject_id);
    for (const item of plan.data?.sessions ?? []) pushId(item.subject_id);
    for (const item of decks.data ?? []) pushId(item.subject_id);
    for (const item of quizzes.data ?? []) pushId(item.subject_id);
    for (const item of catalog.data?.items ?? []) pushId(item.id);

    const names: Record<string, string> = { ...deriveSubjectNames(events.data?.items ?? []) };

    // Pamplemousse synced results titles (by code and key).
    for (const sem of results.data?.semesters ?? []) {
      for (const ue of sem.units) {
        for (const s of ue.subjects) {
          if (s.code) {
            names[s.code] = s.title;
            names[s.code.toUpperCase()] = s.title;
          }
          if (s.key) names[s.key] = s.title;
        }
      }
    }

    // Official names from synced P1 catalog.
    for (const [k, v] of Object.entries(sourceCatalog.data ?? {})) {
      names[k] = v;
      names[k.toUpperCase()] = v;
    }

    // Unified catalog endpoint (Agent 00) takes top precedence when mounted.
    for (const item of catalog.data?.items ?? []) {
      if (item.title) {
        names[item.id] = item.title;
        if (item.code) names[item.code.toUpperCase()] = item.title;
      }
    }

    const ids = [...idSet].sort();
    const ctx = { overrides: prefs.subjectColors, accessible: prefs.accessible, dark: prefs.resolvedDark };
    return {
      ids,
      timezone: tz,
      name: (id) => {
        if (!id) return "Sans matière";
        return names[id] ?? names[id.toUpperCase()] ?? names[id.toLowerCase()] ?? subjectAcronym(id);
      },
      acronym: (id) => subjectAcronym(id),
      color: (id) => (id ? resolveSubjectColor(id, ids, ctx) : undefined),
    };
  }, [
    events.data,
    tasks.data,
    plan.data,
    decks.data,
    quizzes.data,
    catalog.data,
    sourceCatalog.data,
    results.data,
    prefs.subjectColors,
    prefs.accessible,
    prefs.resolvedDark,
    tz,
  ]);

  return <SubjectsContext.Provider value={value}>{children}</SubjectsContext.Provider>;
}

export function useSubjects(): SubjectsCtx {
  const ctx = useContext(SubjectsContext);
  if (!ctx) throw new Error("useSubjects must be used inside SubjectsProvider");
  return ctx;
}

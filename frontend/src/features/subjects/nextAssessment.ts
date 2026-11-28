import type { CalendarEvent, Uuid } from "@/api/types";

/**
 * First upcoming exam or presentation of a subject, from an already loaded,
 * time-sorted event list. Display helper only, no business rule.
 */
export function nextAssessment(events: CalendarEvent[] | undefined, subjectId: Uuid): CalendarEvent | undefined {
  return events?.find((e) => e.subject_id === subjectId && (e.type === "exam" || e.type === "presentation"));
}

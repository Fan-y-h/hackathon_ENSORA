import type { DocumentFilters, SearchParams } from "./types";

// Cache keys in one place, so a mutation can invalidate exactly what it changed.
export const queryKeys = {
  health: ["health"] as const,
  profile: ["profile"] as const,
  connections: ["connections"] as const,
  subjects: ["subjects"] as const,
  subject: (id: string) => ["subjects", id] as const,
  chapters: (subjectId: string) => ["subjects", subjectId, "chapters"] as const,
  concepts: (subjectId: string) => ["subjects", subjectId, "concepts"] as const,
  documents: (filters: DocumentFilters) => ["documents", "list", filters] as const,
  allDocuments: ["documents"] as const,
  document: (id: string) => ["documents", "one", id] as const,
  documentContent: (id: string, versionId: string) => ["documents", "content", id, versionId] as const,
  search: (params: SearchParams) => ["search", params] as const,
  mastery: (filters: { subject_id?: string }) => ["mastery", filters] as const,
  events: (range: { from: string; to: string }) => ["events", range] as const,
  allEvents: ["events"] as const,
  tasks: ["tasks"] as const,
  job: (id: string) => ["jobs", id] as const,
};

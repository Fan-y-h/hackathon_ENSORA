// Hooks: settings, providers, connections, notes, error notebook, notifications, account.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "./client";
import { queryKeys } from "./queryKeys";
import type { Connection, Job, Page, SourceKind, Uuid } from "./types";
import type { AppNotification, AuthSession, AvailabilitySuggestion, ErrorEntry, LoginResult, Note, NoteInput, Settings } from "./typesWorkspace";

export const workspaceKeys = {
  settings: ["settings"] as const,
  notes: ["notes"] as const,
  errors: ["errors"] as const,
  notifications: ["notifications"] as const,
};

export function useSettings() {
  return useQuery({ queryKey: workspaceKeys.settings, queryFn: () => apiFetch<Settings>("/settings") });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (changes: Partial<Omit<Settings, "id" | "created_at" | "updated_at" | "revision">> & { expected_revision: number }) =>
      apiFetch<Settings>("/settings", { method: "PATCH", body: changes }),
    onSuccess: (settings) => queryClient.setQueryData(workspaceKeys.settings, settings),
  });
}



export function useConnect() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (source_kind: SourceKind) => apiFetch<Connection>("/connections", { method: "POST", body: { source_kind } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.connections }),
  });
}

export function useDisconnect() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: Uuid) => apiFetch<void>(`/connections/${connectionId}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.connections }),
  });
}

// --- Notes ---------------------------------------------------------------------------

export function useNotes() {
  return useQuery({ queryKey: workspaceKeys.notes, queryFn: () => apiFetch<Page<Note>>("/notes"), select: (p) => p.items });
}

export function useCreateNote() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: NoteInput) => apiFetch<Note>("/notes", { method: "POST", body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: workspaceKeys.notes }),
  });
}

export function useUpdateNote() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<NoteInput> & { id: Uuid; expected_revision: number }) => apiFetch<Note>(`/notes/${id}`, { method: "PATCH", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: workspaceKeys.notes }),
  });
}

export function useDeleteNote() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: Uuid) => apiFetch<void>(`/notes/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: workspaceKeys.notes }),
  });
}

export function useErrors() {
  return useQuery({ queryKey: workspaceKeys.errors, queryFn: () => apiFetch<Page<ErrorEntry>>("/errors"), select: (p) => p.items });
}

export function useResolveError() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, resolved }: { id: Uuid; resolved: boolean }) => apiFetch<ErrorEntry>(`/errors/${id}`, { method: "PATCH", body: { resolved } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: workspaceKeys.errors }),
  });
}

// --- Notifications ---------------------------------------------------------------------

export function useNotifications() {
  return useQuery({ queryKey: workspaceKeys.notifications, queryFn: () => apiFetch<Page<AppNotification>>("/notifications"), select: (p) => p.items });
}

export function useMarkNotification() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, read }: { id: Uuid; read: boolean }) => apiFetch<AppNotification>(`/notifications/${id}`, { method: "PATCH", body: { read } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: workspaceKeys.notifications }),
  });
}

// --- Account -----------------------------------------------------------------------------

export function useExportAccount() {
  return useMutation({ mutationFn: () => apiFetch<Job>("/account/export", { method: "POST" }) });
}

export function useDeleteAccount() {
  return useMutation({ mutationFn: () => apiFetch<void>("/account", { method: "DELETE" }) });
}

// --- Session ------------------------------------------------------------------------------

export const sessionKey = ["auth", "session"] as const;

/** ASSUMPTION: GET /auth/session tells whether the browser session is signed in. */
export function useSession() {
  return useQuery({ queryKey: sessionKey, queryFn: () => apiFetch<AuthSession>("/auth/session"), staleTime: 60_000 });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { username: string; password: string } | { demo: true }) => apiFetch<LoginResult>("/auth/login", { method: "POST", body: input }),
    onSuccess: ({ sync_job: _job, ...session }) => {
      // Everything cached before belongs to "nobody": start fresh.
      queryClient.clear();
      queryClient.setQueryData(sessionKey, session);
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<void>("/auth/logout", { method: "POST" }),
    onSuccess: () => {
      queryClient.clear();
      queryClient.setQueryData(sessionKey, { authenticated: false, demo: false });
    },
  });
}

/** Mock only: puts the demo data back to its initial state. */
export function useResetDemo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<void>("/demo/reset", { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries(),
  });
}

export function useAvailabilitySuggestions(enabled: boolean) {
  return useQuery({
    queryKey: ["availability", "suggestions"],
    queryFn: () => apiFetch<AvailabilitySuggestion>("/availability/suggestions"),
    enabled,
  });
}

/** Asks for a restructured version of a note (a Job); nothing changes until the student applies it. */
export function useRestructureNote() {
  return useMutation({ mutationFn: (noteId: Uuid) => apiFetch<Job>(`/notes/${noteId}/restructure`, { method: "POST" }) });
}

export function useNoteProposal(proposalId: Uuid | null) {
  return useQuery({
    queryKey: ["notes", "proposal", proposalId ?? "none"],
    queryFn: () => apiFetch<{ id: Uuid; note_id: Uuid; markdown: string }>(`/notes/proposals/${proposalId}`),
    enabled: proposalId !== null,
  });
}

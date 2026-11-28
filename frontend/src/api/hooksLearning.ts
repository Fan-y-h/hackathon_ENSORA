// Hooks: AI tutor, Learning Studio, flashcards, quizzes and exam attempts.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, newIdempotencyKey } from "./client";
import type { Job, Page, Uuid } from "./types";
import type {
  Artifact,
  ArtifactDetail,
  ArtifactVersion,
  Attempt,
  AttemptResponseValue,
  AttemptResult,
  Conversation,
  Deck,
  Flashcard,
  GenerationSpec,
  Quiz,
  ReviewRating,
  ReviewResult,
  TutorMessage,
  TutorRequest,
  TutorResponse,
} from "./typesLearning";

export const learningKeys = {
  conversations: ["conversations"] as const,
  messages: (id: Uuid) => ["conversations", id, "messages"] as const,
  artifacts: ["artifacts"] as const,
  artifact: (id: Uuid) => ["artifacts", "one", id] as const,
  versions: (id: Uuid) => ["artifacts", "versions", id] as const,
  decks: ["decks"] as const,
  cards: (deckId: Uuid) => ["decks", deckId, "cards"] as const,
  due: (deckId: Uuid | null) => ["reviews", "due", deckId] as const,
  quizzes: ["quizzes"] as const,
  attempt: (id: Uuid) => ["attempts", id] as const,
  result: (id: Uuid) => ["attempts", id, "result"] as const,
};

/** Anything that changes mastery must refresh these views. */
const MASTERY_VIEWS = [["mastery"], ["analytics"], ["errors"]] as const;

// --- Tutor -------------------------------------------------------------------------

export function useConversations() {
  return useQuery({ queryKey: learningKeys.conversations, queryFn: () => apiFetch<Page<Conversation>>("/conversations"), select: (p) => p.items });
}

export function useMessages(conversationId: Uuid | null) {
  return useQuery({
    queryKey: learningKeys.messages(conversationId ?? "none"),
    queryFn: () => apiFetch<Page<TutorMessage>>(`/conversations/${conversationId}/messages`),
    select: (p) => p.items,
    enabled: conversationId !== null,
  });
}

export function useCreateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { subject_id?: Uuid | null; title?: string }) => apiFetch<Conversation>("/conversations", { method: "POST", body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: learningKeys.conversations }),
  });
}

export function useAskTutor(conversationId: Uuid | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: TutorRequest & { conversationId?: Uuid }) =>
      apiFetch<TutorResponse>(`/conversations/${request.conversationId ?? conversationId}/messages`, { method: "POST", body: request }),
    onSuccess: (_data, request) => {
      void queryClient.invalidateQueries({ queryKey: learningKeys.messages(request.conversationId ?? conversationId ?? "none") });
      void queryClient.invalidateQueries({ queryKey: learningKeys.conversations });
    },
  });
}

// --- Studio ------------------------------------------------------------------------

export function useArtifacts() {
  return useQuery({ queryKey: learningKeys.artifacts, queryFn: () => apiFetch<Page<Artifact>>("/artifacts"), select: (p) => p.items });
}

export function useArtifact(id: Uuid | null) {
  return useQuery({ queryKey: learningKeys.artifact(id ?? "none"), queryFn: () => apiFetch<ArtifactDetail>(`/artifacts/${id}`), enabled: id !== null });
}

export function useArtifactVersions(id: Uuid | null) {
  return useQuery({
    queryKey: learningKeys.versions(id ?? "none"),
    queryFn: () => apiFetch<Page<ArtifactVersion>>(`/artifacts/${id}/versions`),
    select: (p) => p.items,
    enabled: id !== null,
  });
}

/** Either a cached artifact (200) or a Job to follow (202). */
export type GenerateOutcome = { kind: "cached"; artifact: Artifact } | { kind: "job"; job: Job };

export function useGenerateArtifact() {
  return useMutation({
    mutationFn: async (spec: GenerationSpec): Promise<GenerateOutcome> => {
      const data = await apiFetch<Artifact | Job>("/artifacts/generate", { method: "POST", body: spec, idempotencyKey: newIdempotencyKey() });
      return "kind" in data ? { kind: "job", job: data } : { kind: "cached", artifact: data };
    },
  });
}

export function useUpdateArtifact() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: Uuid; expected_revision: number; content_markdown?: string; active_version_id?: Uuid }) =>
      apiFetch<ArtifactDetail>(`/artifacts/${id}`, { method: "PATCH", body }),
    onSuccess: (detail) => {
      queryClient.setQueryData(learningKeys.artifact(detail.artifact.id), detail);
      void queryClient.invalidateQueries({ queryKey: learningKeys.versions(detail.artifact.id) });
      void queryClient.invalidateQueries({ queryKey: learningKeys.artifacts });
    },
  });
}

// --- Flashcards --------------------------------------------------------------------

export function useDecks() {
  return useQuery({ queryKey: learningKeys.decks, queryFn: () => apiFetch<Page<Deck>>("/decks"), select: (p) => p.items });
}

export function useCards(deckId: Uuid | null) {
  return useQuery({ queryKey: learningKeys.cards(deckId ?? "none"), queryFn: () => apiFetch<Page<Flashcard>>(`/decks/${deckId}/cards`), select: (p) => p.items, enabled: deckId !== null });
}

export function useDueCards(deckId: Uuid | null, enabled = true) {
  return useQuery({
    queryKey: learningKeys.due(deckId),
    queryFn: () => apiFetch<Page<Flashcard>>("/reviews/due", { query: { deck_id: deckId } }),
    select: (p) => p.items,
    enabled,
    // The review session works on a snapshot: do not reshuffle cards while reviewing,
    // and forget it once the session is closed (next session = fresh list).
    staleTime: Infinity,
    gcTime: 0,
  });
}

export function useCreateDeck() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { subject_id: Uuid; title: string }) => apiFetch<Deck>("/decks", { method: "POST", body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: learningKeys.decks }),
  });
}

export function useCreateCard(deckId: Uuid) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { front: string; back: string; concept_id?: Uuid | null }) => apiFetch<Flashcard>(`/decks/${deckId}/cards`, { method: "POST", body: input }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: learningKeys.cards(deckId) });
      void queryClient.invalidateQueries({ queryKey: learningKeys.decks });
    },
  });
}

export function useDeleteCard(deckId: Uuid) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (cardId: Uuid) => apiFetch<void>(`/cards/${cardId}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: learningKeys.cards(deckId) });
      void queryClient.invalidateQueries({ queryKey: learningKeys.decks });
    },
  });
}

export function useReviewCard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ card, rating }: { card: Flashcard; rating: ReviewRating }) =>
      apiFetch<ReviewResult>(`/cards/${card.id}/review`, {
        method: "POST",
        body: { rating, expected_revision: card.revision, reviewed_at: new Date().toISOString() },
        idempotencyKey: `${card.id}:${card.revision}`,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: learningKeys.decks });
      for (const key of MASTERY_VIEWS) void queryClient.invalidateQueries({ queryKey: key });
    },
  });
}

// --- Quizzes and attempts ---------------------------------------------------------------

export function useQuizzes() {
  return useQuery({ queryKey: learningKeys.quizzes, queryFn: () => apiFetch<Page<Quiz>>("/quizzes"), select: (p) => p.items });
}

export function useCreateMockExam() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { subject_id: Uuid; duration_min: number }) => apiFetch<Quiz>("/quizzes", { method: "POST", body: { ...input, type: "mock_exam" } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: learningKeys.quizzes }),
  });
}

export function useStartAttempt() {
  return useMutation({ mutationFn: (quizId: Uuid) => apiFetch<Attempt>(`/quizzes/${quizId}/attempts`, { method: "POST", body: {} }) });
}

export function useAttempt(attemptId: Uuid | null) {
  return useQuery({ queryKey: learningKeys.attempt(attemptId ?? "none"), queryFn: () => apiFetch<Attempt>(`/attempts/${attemptId}`), enabled: attemptId !== null });
}

export function useSaveResponses(attemptId: Uuid) {
  return useMutation({
    mutationFn: (responses: Record<Uuid, AttemptResponseValue>) => apiFetch<Attempt>(`/attempts/${attemptId}/responses`, { method: "PATCH", body: { responses } }),
  });
}

export function useSubmitAttempt(attemptId: Uuid) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<AttemptResult>(`/attempts/${attemptId}/submit`, { method: "POST", idempotencyKey: `submit:${attemptId}` }),
    onSuccess: (result) => {
      queryClient.setQueryData(learningKeys.result(attemptId), result);
      void queryClient.invalidateQueries({ queryKey: learningKeys.attempt(attemptId) });
      for (const key of MASTERY_VIEWS) void queryClient.invalidateQueries({ queryKey: key });
    },
  });
}

export function useAttemptResult(attemptId: Uuid | null, enabled: boolean) {
  return useQuery({
    queryKey: learningKeys.result(attemptId ?? "none"),
    queryFn: () => apiFetch<AttemptResult>(`/attempts/${attemptId}/result`),
    enabled: attemptId !== null && enabled,
  });
}

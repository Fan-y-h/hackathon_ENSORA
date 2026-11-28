// Fake backend: AI tutor, Learning Studio, flashcards, quizzes and exam attempts.
// No AI is called: answers and artifacts are assembled from the demo passages and
// always labelled provider "mock".
import { http, HttpResponse } from "msw";

import type { Citation, Uuid } from "@/api/types";
import type {
  Artifact,
  ArtifactDetail,
  ArtifactVersion,
  AttemptItemResult,
  AttemptResponseValue,
  AttemptResult,
  EvidenceStatus,
  Flashcard,
  GenerationSpec,
  MasteryUpdate,
  ReviewRating,
  TutorMessage,
  TutorRequest,
  TutorResponse,
} from "@/api/typesLearning";

import { api, apiError, bump, citationsOf, conceptsOfSubject, createJob, db, latency, newRecord, page, recordEvidence, searchCitations } from "./core";
import type { AttemptRecord, QuestionDef } from "./fixturesLearning";

const MOCK_MODEL = { provider: "mock", model: "demo-fixture" };
const DEMO_NOTE = "\n\n---\n*Mode démonstration : cette réponse assemble des passages de vos sources, sans modèle d'IA.*";

// --- Tutor -------------------------------------------------------------------------

function tutorAnswer(request: TutorRequest): { markdown: string; citations: Citation[]; status: EvidenceStatus } {
  let candidates: Array<{ citation: Citation; score: number }>;
  if (request.citation_ids?.length) {
    const chosen = db.documents.flatMap(citationsOf).filter((c) => request.citation_ids?.includes(c.citation_id));
    candidates = chosen.map((citation) => ({ citation, score: 3 }));
  } else {
    const subjectDocs = new Set(db.documents.filter((d) => !request.subject_id || d.subject_id === request.subject_id).map((d) => d.id));
    candidates = searchCitations(request.question, (c) =>
      request.document_ids?.length ? request.document_ids.includes(c.document_id) : subjectDocs.has(c.document_id),
    );
  }
  // Keep only the best-matching passages: a weak match next to a strong one is noise.
  const best = candidates[0]?.score ?? 0;
  const top = candidates.filter((c) => c.score >= best - 1 && c.score > 0).slice(0, 3);
  if (top.length === 0) {
    return {
      markdown:
        "Je ne trouve pas d'élément sur ce point dans les sources sélectionnées, je préfère donc ne pas inventer de réponse.\n\n" +
        "- Élargissez la sélection (toute la matière ou tous les documents) ;\n- ou importez le cours correspondant dans la Bibliothèque." +
        DEMO_NOTE,
      citations: [],
      status: "insufficient",
    };
  }
  const refs = top.map((hit, i) => `${hit.citation.excerpt} [${i + 1}]`);
  const intro: Record<TutorRequest["mode"], string> = {
    explain: "Voici ce que disent vos sources sur ce point :",
    derivation: "Les étapes s'appuient sur ces éléments de vos sources :",
    code: "Les passages utiles pour ce code sont :",
    correction: "Pour corriger, comparez votre démarche à ces passages :",
    socratic: "Avant de donner la réponse, une question : que retenez-vous de ces passages ?",
    exam_prep: "Points à maîtriser pour l'examen, d'après vos sources :",
    memorize: "À retenir (formulation des sources) :",
  };
  const status: EvidenceStatus = request.citation_ids?.length || top[0].score >= 2 ? "grounded" : "partial";
  const caveat = status === "partial" ? "\n\n> Les sources ne couvrent qu'en partie votre question : vérifiez dans le cours complet." : "";
  return { markdown: `${intro[request.mode]}\n\n${refs.map((r) => `- ${r}`).join("\n")}${caveat}${DEMO_NOTE}`, citations: top.map((h) => h.citation), status };
}

// --- Studio ------------------------------------------------------------------------

const FORMULAS: Record<string, string> = {
  "Biais et risque quadratique": "$$R(\\hat\\theta) = \\operatorname{Var}(\\hat\\theta) + b(\\hat\\theta)^2$$",
  "Intervalle pour une moyenne": "$$\\bar X_n \\pm t_{n-1,\\,1-\\alpha/2}\\,\\frac{S_n}{\\sqrt n}$$",
  "Théorème central limite": "$$\\sqrt n\\,\\frac{\\bar X_n - \\mu}{\\sigma} \\xrightarrow{\\mathcal L} \\mathcal N(0,1)$$",
  "Notation grand O": "$$f(n) = O(g(n)) \\iff \\exists c, n_0,\\ \\forall n \\ge n_0,\\ f(n) \\le c\\,g(n)$$",
};

function bestCitation(label: string, subjectId: Uuid): Citation | undefined {
  const docs = new Set(db.documents.filter((d) => d.subject_id === subjectId).map((d) => d.id));
  return searchCitations(label, (c) => docs.has(c.document_id))[0]?.citation;
}

function scopeConcepts(spec: GenerationSpec) {
  const all = conceptsOfSubject(spec.subject_id);
  return spec.concept_ids.length ? all.filter((c) => spec.concept_ids.includes(c.id)) : all;
}

function buildSheet(spec: GenerationSpec, subjectTitle: string): { markdown: string; citations: Citation[] } {
  const citations: Citation[] = [];
  const cite = (c: Citation | undefined) => {
    if (!c) return "";
    let index = citations.findIndex((x) => x.citation_id === c.citation_id);
    if (index === -1) index = citations.push(c) - 1;
    return ` [${index + 1}]`;
  };
  const concepts = scopeConcepts(spec);
  const lines = [`# Fiche de révision — ${subjectTitle}`, ""];
  for (const chapter of db.chapters.filter((ch) => ch.subject_id === spec.subject_id)) {
    const inChapter = concepts.filter((c) => c.chapter_id === chapter.id);
    if (inChapter.length === 0) continue;
    lines.push(`## ${chapter.title}`, "");
    for (const concept of inChapter) {
      const citation = bestCitation(concept.label, spec.subject_id);
      lines.push(`### ${concept.label}`, "");
      lines.push(citation ? `${citation.excerpt}${cite(citation)}` : "_Aucun passage trouvé dans vos sources pour cette notion._", "");
      if (FORMULAS[concept.label]) lines.push(FORMULAS[concept.label], "");
    }
  }
  if (spec.detail_level !== "summary") {
    lines.push("## Pièges fréquents", "", "- Vérifier les hypothèses avant d'appliquer un résultat.", "- Distinguer ce qui est connu (données) de ce qui est estimé.", "");
  }
  if (spec.user_instructions.trim()) lines.push(`> Consigne prise en compte : ${spec.user_instructions.trim()}`, "");
  lines.push("*Fiche générée en mode démonstration, sans modèle d'IA, à partir de vos sources.*");
  return { markdown: lines.join("\n"), citations };
}

function createArtifact(spec: GenerationSpec, title: string, content: string | null, citations: Citation[], linkedId: Uuid | null): Artifact {
  const record = newRecord();
  const version: ArtifactVersion = {
    id: crypto.randomUUID(),
    artifact_id: record.id,
    parent_version_id: null,
    content_markdown: content,
    citations,
    ...MOCK_MODEL,
    edited_by_user: false,
    created_at: record.created_at,
  };
  const artifact: Artifact = { ...record, type: spec.type, subject_id: spec.subject_id, title, status: "ready", active_version_id: version.id, stale: false, linked_id: linkedId };
  db.artifacts.unshift(artifact);
  db.artifactVersions.push(version);
  specCache.set(artifact.id, cacheKey(spec));
  return artifact;
}

const specCache = new Map<Uuid, string>();
const cacheKey = (spec: GenerationSpec) =>
  JSON.stringify([spec.type, spec.subject_id, [...spec.concept_ids].sort(), spec.objective, spec.difficulty, spec.detail_level, spec.duration_min, spec.user_instructions.trim()]);

function generate(spec: GenerationSpec): Artifact {
  const subject = db.subjects.find((s) => s.id === spec.subject_id);
  const subjectTitle = subject?.title ?? "Matière";
  const concepts = scopeConcepts(spec);

  if (spec.type === "flashcards") {
    const deck = { ...newRecord(), subject_id: spec.subject_id, title: `Flashcards — ${subjectTitle}`, card_count: 0, due_count: 0 };
    db.decks.push(deck);
    for (const concept of concepts) {
      const citation = bestCitation(concept.label, spec.subject_id);
      if (!citation) continue;
      db.cards.push({ ...newRecord(), deck_id: deck.id, concept_id: concept.id, front: `Que savez-vous de : ${concept.label} ?`, back: citation.excerpt, citations: [citation], due_at: new Date().toISOString(), interval_days: 0 });
    }
    return createArtifact(spec, deck.title, null, [], deck.id);
  }

  if (spec.type === "quiz") {
    const quiz = { ...newRecord(), subject_id: spec.subject_id, title: `Quiz — ${subjectTitle}`, type: "practice" as const, question_count: 0, duration_min: null };
    const labels = concepts.map((c) => c.label);
    for (const [i, concept] of concepts.entries()) {
      const citation = bestCitation(concept.label, spec.subject_id);
      if (!citation) continue;
      const others = labels.filter((l) => l !== concept.label).slice(i % 2, (i % 2) + 2);
      const options = [concept.label, ...others].sort().map((label, j) => ({ id: String.fromCharCode(97 + j), label }));
      db.questions.push({
        id: crypto.randomUUID(),
        quiz_id: quiz.id,
        type: "single",
        prompt: `À quelle notion correspond ce passage ? « ${citation.excerpt} »`,
        options,
        answer: options.find((o) => o.label === concept.label)?.id ?? "a",
        explanation: `Ce passage décrit « ${concept.label} ».`,
        points: 1,
        concept_ids: [concept.id],
        citations: [citation],
      });
      quiz.question_count += 1;
    }
    db.quizzes.unshift(quiz);
    return createArtifact(spec, quiz.title, null, [], quiz.id);
  }

  const sheet = buildSheet(spec, subjectTitle);
  return createArtifact(spec, `Fiche — ${subjectTitle}`, sheet.markdown, sheet.citations, null);
}

function artifactDetail(artifact: Artifact): ArtifactDetail {
  const version = db.artifactVersions.find((v) => v.id === artifact.active_version_id) as ArtifactVersion;
  return { artifact, version };
}

// --- Flashcards review heuristic (docs/ADAPTIVE_LEARNING.md) ------------------------

function nextInterval(card: Flashcard, rating: ReviewRating): number {
  const previous = Math.max(card.interval_days, 1);
  if (rating === "again") return 1;
  if (rating === "hard") return Math.max(1, Math.round(previous * 1.2));
  if (rating === "good") return Math.max(2, Math.round(previous * 2));
  return Math.max(3, Math.round(previous * 3));
}
const RATING_SCORE: Record<ReviewRating, number> = { again: 0, hard: 0.5, good: 0.8, easy: 1 };

function deckWithCounts<T extends { id: Uuid }>(deck: T) {
  const cards = db.cards.filter((c) => c.deck_id === deck.id);
  const now = new Date().toISOString();
  return { ...deck, card_count: cards.length, due_count: cards.filter((c) => c.due_at !== null && c.due_at <= now).length };
}

// --- Quiz grading ------------------------------------------------------------------

function publicQuestion(q: QuestionDef) {
  return { id: q.id, type: q.type, prompt: q.prompt, options: q.options, points: q.points };
}

function normalizeAnswer(value: string): string {
  return value.normalize("NFD").replace(/[̀-ͯ]/g, "").trim().toLowerCase();
}

function gradeItem(q: QuestionDef, response: AttemptResponseValue | undefined): AttemptItemResult {
  const expectedText = Array.isArray(q.answer)
    ? q.answer.map((id) => q.options?.find((o) => o.id === id)?.label ?? id).join(" ; ")
    : (q.options?.find((o) => o.id === q.answer)?.label ?? q.answer);
  let correct: boolean | null;
  if (response === undefined || (Array.isArray(response) && response.length === 0) || response === "") correct = false;
  else if (q.type === "open") correct = null;
  else if (Array.isArray(q.answer)) correct = Array.isArray(response) && response.length === q.answer.length && q.answer.every((a) => response.includes(a));
  else correct = normalizeAnswer(String(response)) === normalizeAnswer(q.answer);
  return {
    question_id: q.id,
    prompt: q.prompt,
    response: response ?? null,
    expected: expectedText,
    correct,
    score: correct === null ? null : correct ? q.points : 0,
    points: q.points,
    explanation: q.explanation,
    citations: q.citations,
    concept_ids: q.concept_ids,
  };
}

const results = new Map<Uuid, AttemptResult>();

function expireIfNeeded(attempt: AttemptRecord) {
  if (attempt.status === "active" && attempt.expires_at && attempt.expires_at <= new Date().toISOString()) attempt.status = "expired";
}

function submitAttempt(attempt: AttemptRecord): AttemptResult {
  const existing = results.get(attempt.id);
  if (existing) return existing; // idempotent: a second submit returns the same result
  const questions = db.questions.filter((q) => q.quiz_id === attempt.quiz_id);
  const items = questions.map((q) => gradeItem(q, attempt.responses[q.id]));
  const updates: MasteryUpdate[] = [];
  for (const item of items) {
    if (item.correct === null) continue; // open answers are not auto-graded: no evidence
    for (const conceptId of item.concept_ids) updates.push(recordEvidence(conceptId, "theoretical_understanding", item.correct ? 1 : 0, 1, item.correct ? 4 : 1));
    if (!item.correct) {
      const subjectId = db.quizzes.find((q) => q.id === attempt.quiz_id)?.subject_id ?? null;
      db.errors.unshift({
        id: crypto.randomUUID(),
        concept_id: item.concept_ids[0] ?? null,
        subject_id: subjectId,
        category: "reasoning",
        description: `Erreur sur : « ${item.prompt} »`,
        remediation: item.explanation,
        occurred_at: new Date().toISOString(),
        resolved_at: null,
      });
    }
  }
  const result: AttemptResult = {
    attempt_id: attempt.id,
    quiz_id: attempt.quiz_id,
    score: items.reduce((sum, i) => sum + (i.score ?? 0), 0),
    max_score: items.reduce((sum, i) => sum + i.points, 0),
    items,
    mastery_updates: updates,
  };
  results.set(attempt.id, result);
  attempt.status = "graded";
  return result;
}

function publicAttempt(attempt: AttemptRecord) {
  const { result_id: _resultId, ...rest } = attempt;
  return rest;
}

export const learningHandlers = [
  // --- Tutor ---
  http.get(api("/conversations"), async () => {
    await latency();
    return HttpResponse.json(page([...db.conversations].sort((a, b) => b.updated_at.localeCompare(a.updated_at))));
  }),

  http.post(api("/conversations"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as { subject_id?: string | null; title?: string };
    const conversation = { ...newRecord(), subject_id: input.subject_id ?? null, title: input.title?.trim() || "Nouvelle conversation" };
    db.conversations.push(conversation);
    return HttpResponse.json(conversation, { status: 201 });
  }),

  http.get(api("/conversations/:id/messages"), async ({ params }) => {
    await latency();
    if (!db.conversations.some((c) => c.id === params.id)) return apiError(404, "not_found", "Conversation introuvable.");
    return HttpResponse.json(page(db.messages.filter((m) => m.conversation_id === params.id)));
  }),

  http.post(api("/conversations/:id/messages"), async ({ params, request }) => {
    await latency();
    const index = db.conversations.findIndex((c) => c.id === params.id);
    if (index === -1) return apiError(404, "not_found", "Conversation introuvable.");
    const input = (await request.json()) as TutorRequest;
    if (!input.question?.trim()) return apiError(422, "validation_error", "Posez une question.", { field: "question" });
    const now = new Date().toISOString();
    const userMessage: TutorMessage = { id: crypto.randomUUID(), conversation_id: String(params.id), role: "user", content_markdown: input.question.trim(), citations: [], evidence_status: null, provider: null, model: null, occurred_at: now };
    const answer = tutorAnswer(input);
    const reply: TutorMessage = { id: crypto.randomUUID(), conversation_id: String(params.id), role: "assistant", content_markdown: answer.markdown, citations: answer.citations, evidence_status: answer.status, ...MOCK_MODEL, occurred_at: now };
    db.messages.push(userMessage, reply);
    const conversation = db.conversations[index];
    db.conversations[index] = bump(conversation, conversation.title === "Nouvelle conversation" ? { title: input.question.trim().slice(0, 60) } : {});
    const response: TutorResponse = { message_id: reply.id, answer_markdown: answer.markdown, citations: answer.citations, evidence_status: answer.status, ...MOCK_MODEL, usage: null };
    return HttpResponse.json(response);
  }),

  // --- Studio ---
  http.post(api("/artifacts/generate"), async ({ request }) => {
    await latency();
    const spec = (await request.json()) as GenerationSpec;
    if (!db.subjects.some((s) => s.id === spec.subject_id)) return apiError(422, "validation_error", "Choisissez une matière.", { field: "subject_id" });
    if (!["revision_sheet", "flashcards", "quiz"].includes(spec.type)) {
      return apiError(501, "not_implemented", "Ce type de support n'est pas encore disponible en mode démonstration.");
    }
    // Same request already generated: reuse it (cache), unless a new version is requested.
    if (!spec.force_regenerate) {
      const cached = db.artifacts.find((a) => specCache.get(a.id) === cacheKey(spec));
      if (cached) return HttpResponse.json(cached);
    }
    const job = createJob("generation", () => generate(spec).id);
    return HttpResponse.json(job, { status: 202 });
  }),

  http.get(api("/artifacts"), async ({ request }) => {
    await latency();
    const params = new URL(request.url).searchParams;
    const subjectId = params.get("subject_id");
    const type = params.get("type");
    return HttpResponse.json(page(db.artifacts.filter((a) => (!subjectId || a.subject_id === subjectId) && (!type || a.type === type))));
  }),

  http.get(api("/artifacts/:id"), async ({ params }) => {
    await latency();
    const artifact = db.artifacts.find((a) => a.id === params.id);
    return artifact ? HttpResponse.json(artifactDetail(artifact)) : apiError(404, "not_found", "Support introuvable.");
  }),

  http.get(api("/artifacts/:id/versions"), async ({ params }) => {
    await latency();
    const versions = db.artifactVersions.filter((v) => v.artifact_id === params.id).sort((a, b) => b.created_at.localeCompare(a.created_at));
    return HttpResponse.json(page(versions));
  }),

  // An edit never overwrites: it creates a new version marked edited_by_user.
  http.patch(api("/artifacts/:id"), async ({ params, request }) => {
    await latency();
    const index = db.artifacts.findIndex((a) => a.id === params.id);
    if (index === -1) return apiError(404, "not_found", "Support introuvable.");
    const input = (await request.json()) as { content_markdown?: string; active_version_id?: string; expected_revision: number };
    const artifact = db.artifacts[index];
    if (input.expected_revision !== artifact.revision) return apiError(409, "stale_revision", "Ce support a été modifié entre-temps. Rechargez-le.");
    if (input.active_version_id) {
      if (!db.artifactVersions.some((v) => v.id === input.active_version_id && v.artifact_id === artifact.id)) return apiError(422, "validation_error", "Version inconnue.");
      db.artifacts[index] = bump(artifact, { active_version_id: input.active_version_id });
      return HttpResponse.json(artifactDetail(db.artifacts[index]));
    }
    const current = db.artifactVersions.find((v) => v.id === artifact.active_version_id) as ArtifactVersion;
    const version: ArtifactVersion = { ...current, id: crypto.randomUUID(), parent_version_id: current.id, content_markdown: input.content_markdown ?? current.content_markdown, edited_by_user: true, created_at: new Date().toISOString() };
    db.artifactVersions.push(version);
    db.artifacts[index] = bump(artifact, { active_version_id: version.id });
    return HttpResponse.json(artifactDetail(db.artifacts[index]));
  }),

  // --- Flashcards ---
  http.get(api("/decks"), async () => {
    await latency();
    return HttpResponse.json(page(db.decks.map(deckWithCounts)));
  }),

  http.post(api("/decks"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as { subject_id?: string; title?: string };
    if (!input.title?.trim()) return apiError(422, "validation_error", "Le titre est obligatoire.", { field: "title" });
    if (!db.subjects.some((s) => s.id === input.subject_id)) return apiError(422, "validation_error", "Choisissez une matière.", { field: "subject_id" });
    const deck = { ...newRecord(), subject_id: input.subject_id as string, title: input.title.trim(), card_count: 0, due_count: 0 };
    db.decks.push(deck);
    return HttpResponse.json(deck, { status: 201 });
  }),

  http.get(api("/decks/:id/cards"), async ({ params }) => {
    await latency();
    if (!db.decks.some((d) => d.id === params.id)) return apiError(404, "not_found", "Paquet introuvable.");
    return HttpResponse.json(page(db.cards.filter((c) => c.deck_id === params.id)));
  }),

  http.post(api("/decks/:id/cards"), async ({ params, request }) => {
    await latency();
    if (!db.decks.some((d) => d.id === params.id)) return apiError(404, "not_found", "Paquet introuvable.");
    const input = (await request.json()) as { front?: string; back?: string; concept_id?: string | null };
    if (!input.front?.trim() || !input.back?.trim()) return apiError(422, "validation_error", "Recto et verso sont obligatoires.", { field: input.front?.trim() ? "back" : "front" });
    const card: Flashcard = { ...newRecord(), deck_id: String(params.id), concept_id: input.concept_id ?? null, front: input.front.trim(), back: input.back.trim(), citations: [], due_at: new Date().toISOString(), interval_days: 0 };
    db.cards.push(card);
    return HttpResponse.json(card, { status: 201 });
  }),

  http.delete(api("/cards/:id"), async ({ params }) => {
    await latency();
    const before = db.cards.length;
    db.cards = db.cards.filter((c) => c.id !== params.id);
    return db.cards.length < before ? new HttpResponse(null, { status: 204 }) : apiError(404, "not_found", "Carte introuvable.");
  }),

  http.get(api("/reviews/due"), async ({ request }) => {
    await latency();
    const deckId = new URL(request.url).searchParams.get("deck_id");
    const now = new Date().toISOString();
    return HttpResponse.json(page(db.cards.filter((c) => (!deckId || c.deck_id === deckId) && c.due_at !== null && c.due_at <= now)));
  }),

  http.post(api("/cards/:id/review"), async ({ params, request }) => {
    await latency();
    const index = db.cards.findIndex((c) => c.id === params.id);
    if (index === -1) return apiError(404, "not_found", "Carte introuvable.");
    const input = (await request.json()) as { rating?: ReviewRating; expected_revision?: number };
    if (!input.rating || !(input.rating in RATING_SCORE)) return apiError(422, "validation_error", "Note inconnue.", { field: "rating" });
    const card = db.cards[index];
    if (input.expected_revision !== card.revision) return apiError(409, "stale_revision", "Cette carte a déjà été révisée.");
    const interval = nextInterval(card, input.rating);
    const nextDue = new Date(Date.now() + interval * 86_400_000).toISOString();
    db.cards[index] = bump(card, { interval_days: interval, due_at: nextDue });
    const updates = card.concept_id ? [recordEvidence(card.concept_id, "memory", RATING_SCORE[input.rating], 1, interval)] : [];
    return HttpResponse.json({ card_id: card.id, next_due_at: nextDue, interval_days: interval, mastery_updates: updates });
  }),

  // --- Quizzes and attempts ---
  http.get(api("/quizzes"), async ({ request }) => {
    await latency();
    const type = new URL(request.url).searchParams.get("type");
    return HttpResponse.json(page(db.quizzes.filter((q) => !type || q.type === type)));
  }),

  // Creates a mock exam from the subject's question bank.
  http.post(api("/quizzes"), async ({ request }) => {
    await latency();
    const input = (await request.json()) as { subject_id?: string; type?: string; duration_min?: number };
    const subject = db.subjects.find((s) => s.id === input.subject_id);
    if (!subject) return apiError(422, "validation_error", "Choisissez une matière.", { field: "subject_id" });
    if (input.type !== "mock_exam") return apiError(422, "validation_error", "Seuls les examens blancs se créent ici.", { field: "type" });
    const duration = Number(input.duration_min);
    if (!Number.isInteger(duration) || duration < 5 || duration > 240) return apiError(422, "validation_error", "Durée entre 5 et 240 minutes.", { field: "duration_min" });
    const bank = db.questions.filter((q) => db.quizzes.find((z) => z.id === q.quiz_id)?.subject_id === subject.id);
    if (bank.length === 0) return apiError(422, "no_questions", "Pas encore de questions pour cette matière : générez d'abord un quiz dans Learning Studio.");
    const quiz = { ...newRecord(), subject_id: subject.id, title: `Examen blanc — ${subject.title}`, type: "mock_exam" as const, question_count: bank.length, duration_min: duration };
    db.quizzes.unshift(quiz);
    for (const q of bank) db.questions.push({ ...q, id: crypto.randomUUID(), quiz_id: quiz.id });
    return HttpResponse.json(quiz, { status: 201 });
  }),

  http.post(api("/quizzes/:id/attempts"), async ({ params }) => {
    await latency();
    const quiz = db.quizzes.find((q) => q.id === params.id);
    if (!quiz) return apiError(404, "not_found", "Quiz introuvable.");
    const now = new Date();
    const attempt: AttemptRecord = {
      id: crypto.randomUUID(),
      quiz_id: quiz.id,
      mode: quiz.type === "mock_exam" ? "exam" : "practice",
      status: "active",
      started_at: now.toISOString(),
      expires_at: quiz.duration_min ? new Date(now.getTime() + quiz.duration_min * 60_000).toISOString() : null,
      // The answer key is never part of an active attempt.
      questions: db.questions.filter((q) => q.quiz_id === quiz.id).map(publicQuestion),
      responses: {},
      result_id: null,
    };
    db.attempts.push(attempt);
    return HttpResponse.json(publicAttempt(attempt), { status: 201 });
  }),

  http.get(api("/attempts/:id"), async ({ params }) => {
    await latency();
    const attempt = db.attempts.find((a) => a.id === params.id);
    if (!attempt) return apiError(404, "not_found", "Tentative introuvable.");
    expireIfNeeded(attempt);
    return HttpResponse.json(publicAttempt(attempt));
  }),

  http.patch(api("/attempts/:id/responses"), async ({ params, request }) => {
    await latency();
    const attempt = db.attempts.find((a) => a.id === params.id);
    if (!attempt) return apiError(404, "not_found", "Tentative introuvable.");
    expireIfNeeded(attempt);
    if (attempt.status !== "active") return apiError(409, "attempt_closed", "Le temps est écoulé ou la copie a déjà été rendue.");
    const input = (await request.json()) as { responses?: Record<string, AttemptResponseValue> };
    attempt.responses = { ...attempt.responses, ...(input.responses ?? {}) };
    return HttpResponse.json(publicAttempt(attempt));
  }),

  http.post(api("/attempts/:id/submit"), async ({ params }) => {
    await latency();
    const attempt = db.attempts.find((a) => a.id === params.id);
    if (!attempt) return apiError(404, "not_found", "Tentative introuvable.");
    expireIfNeeded(attempt);
    return HttpResponse.json(submitAttempt(attempt));
  }),

  http.get(api("/attempts/:id/result"), async ({ params }) => {
    await latency();
    const attempt = db.attempts.find((a) => a.id === params.id);
    if (!attempt) return apiError(404, "not_found", "Tentative introuvable.");
    expireIfNeeded(attempt);
    if (attempt.status === "active") return apiError(403, "attempt_active", "La correction n'est disponible qu'après avoir rendu la copie.");
    return HttpResponse.json(submitAttempt(attempt));
  }),
];

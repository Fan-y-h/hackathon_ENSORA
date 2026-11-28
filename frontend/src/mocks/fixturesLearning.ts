// Demo data for the tutor, studio, flashcards and quizzes. Synthetic content only.
import type { Citation, Uuid } from "@/api/types";
import type { Artifact, ArtifactVersion, Attempt, Conversation, Deck, Flashcard, QuestionType, Quiz, TutorMessage } from "@/api/typesLearning";

import { IDS, type createFixtures } from "./fixtures";

/** Private question definition: the answer never leaves the mock server while an attempt is active. */
export interface QuestionDef {
  id: Uuid;
  quiz_id: Uuid;
  type: QuestionType;
  prompt: string;
  options: Array<{ id: string; label: string }> | null;
  answer: string | string[];
  explanation: string;
  points: number;
  concept_ids: Uuid[];
  citations: Citation[];
}

/** Grading memory of an attempt (responses + result once submitted). */
export interface AttemptRecord extends Attempt {
  result_id: Uuid | null;
}

export function createLearningFixtures(base: ReturnType<typeof createFixtures>) {
  const now = new Date();
  const iso = (offsetDays: number) => new Date(now.getTime() + offsetDays * 86_400_000).toISOString();
  const record = { created_at: iso(-10), updated_at: iso(-10), revision: 1 };
  const conceptByLabel = (label: string) => base.concepts.find((c) => c.label === label)?.id ?? null;

  // --- Flashcards: one deck in statistics, some cards due today -----------------
  const deckId = "b1000000-0000-4000-8000-000000000001";
  const cardDefs: Array<[string, string, string]> = [
    ["Qu'est-ce que le biais d'un estimateur ?", "La différence entre son espérance et la vraie valeur du paramètre.", "Biais et risque quadratique"],
    ["Décomposition du risque quadratique ?", "Variance de l'estimateur + carré du biais.", "Biais et risque quadratique"],
    ["Qu'est-ce qu'une quantité pivotale ?", "Une fonction des données et du paramètre dont la loi ne dépend pas du paramètre.", "Méthode du pivot"],
    ["Définition de la p-valeur ?", "Probabilité, sous H0, d'observer une statistique au moins aussi extrême que celle obtenue.", "p-valeur"],
    ["Erreur de première espèce ?", "Rejeter H0 alors qu'elle est vraie ; sa probabilité maximale est le niveau du test.", "Erreurs de première et seconde espèce"],
    ["Quelle loi pour une moyenne à variance inconnue (échantillon gaussien) ?", "La loi de Student à n − 1 degrés de liberté.", "Intervalle pour une moyenne"],
  ];
  const cards: Flashcard[] = cardDefs.map(([front, back, concept], i) => ({
    ...record,
    id: `b2000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`,
    deck_id: deckId,
    concept_id: conceptByLabel(concept),
    front,
    back,
    citations: [],
    // 4 cards due now, 2 later.
    due_at: i < 4 ? iso(-1) : iso(3),
    interval_days: i < 4 ? 1 : 4,
  }));
  const decks: Deck[] = [{ ...record, id: deckId, subject_id: IDS.statsSubject, title: "Statistique inférentielle — notions clés", card_count: cards.length, due_count: 4 }];

  // --- Quiz: a diagnostic in statistics -------------------------------------------
  const quizId = "b3000000-0000-4000-8000-000000000001";
  const q = (n: number) => `b4000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
  const questions: QuestionDef[] = [
    {
      id: q(1),
      quiz_id: quizId,
      type: "single",
      prompt: "Le risque quadratique d'un estimateur est égal à :",
      options: [
        { id: "a", label: "Sa variance seule" },
        { id: "b", label: "Sa variance plus le carré de son biais" },
        { id: "c", label: "Le carré de son biais seul" },
      ],
      answer: "b",
      explanation: "Le risque quadratique se décompose en variance + biais².",
      points: 1,
      concept_ids: [conceptByLabel("Biais et risque quadratique")].filter(Boolean) as Uuid[],
      citations: [],
    },
    {
      id: q(2),
      quiz_id: quizId,
      type: "true_false",
      prompt: "Une p-valeur de 0,03 conduit à rejeter H0 au niveau 5 %.",
      options: [
        { id: "true", label: "Vrai" },
        { id: "false", label: "Faux" },
      ],
      answer: "true",
      explanation: "On rejette H0 lorsque la p-valeur est inférieure au niveau choisi : 0,03 < 0,05.",
      points: 1,
      concept_ids: [conceptByLabel("p-valeur")].filter(Boolean) as Uuid[],
      citations: [],
    },
    {
      id: q(3),
      quiz_id: quizId,
      type: "multiple",
      prompt: "Parmi ces affirmations sur les erreurs d'un test, lesquelles sont vraies ?",
      options: [
        { id: "a", label: "L'erreur de première espèce consiste à rejeter H0 alors qu'elle est vraie" },
        { id: "b", label: "La puissance vaut un moins le risque de seconde espèce" },
        { id: "c", label: "Le niveau du test est la probabilité de l'erreur de seconde espèce" },
      ],
      answer: ["a", "b"],
      explanation: "Le niveau borne l'erreur de première espèce ; la puissance vaut 1 − β.",
      points: 2,
      concept_ids: [conceptByLabel("Erreurs de première et seconde espèce")].filter(Boolean) as Uuid[],
      citations: [],
    },
    {
      id: q(4),
      quiz_id: quizId,
      type: "fill",
      prompt: "Pour une moyenne d'échantillon gaussien à variance inconnue, on utilise la loi de … (un mot).",
      options: null,
      answer: "student",
      explanation: "La statistique studentisée suit une loi de Student à n − 1 degrés de liberté.",
      points: 1,
      concept_ids: [conceptByLabel("Intervalle pour une moyenne")].filter(Boolean) as Uuid[],
      citations: [],
    },
    {
      id: q(5),
      quiz_id: quizId,
      type: "open",
      prompt: "Expliquez en deux phrases le principe de la méthode du pivot.",
      options: null,
      answer: "Trouver une fonction des données et du paramètre dont la loi ne dépend pas du paramètre, puis l'inverser pour obtenir un intervalle.",
      explanation: "Réponse ouverte : comparez votre réponse à la réponse attendue.",
      points: 2,
      concept_ids: [conceptByLabel("Méthode du pivot")].filter(Boolean) as Uuid[],
      citations: [],
    },
  ];
  const quizzes: Quiz[] = [
    { ...record, id: quizId, subject_id: IDS.statsSubject, title: "Diagnostic — Statistique inférentielle", type: "diagnostic", question_count: questions.length, duration_min: null },
  ];

  return {
    conversations: [] as Conversation[],
    messages: [] as TutorMessage[],
    artifacts: [] as Artifact[],
    artifactVersions: [] as ArtifactVersion[],
    decks,
    cards,
    quizzes,
    questions,
    attempts: [] as AttemptRecord[],
  };
}

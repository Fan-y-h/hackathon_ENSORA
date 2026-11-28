import { useState } from "react";
import { Link } from "react-router-dom";

import { errorMessage } from "../../api/client";
import {
  useArtifacts, useCatalogSubjects, useConversationMessages, useConversations,
  useGenerateArtifact, useTutorAnswer,
} from "../../api/queries";
import type { ArtifactType, CitationOut } from "../../api/types";
import { Card, PageHeader } from "../../components/ui";

function Citations({ items }: { items: CitationOut[] }) {
  if (!items.length) return null;
  return (
    <ul>
      {items.map((item) => (
        <li key={item.citation_id}>
          {item.content_url
            ? <a href={item.content_url} target="_blank" rel="noreferrer">{item.title}</a>
            : item.title}
          {item.locator.page != null && <span> · p. {item.locator.page}</span>}
          <blockquote>{item.excerpt}</blockquote>
        </li>
      ))}
    </ul>
  );
}

export function TutorPage() {
  const [question, setQuestion] = useState("");
  const [conversation, setConversation] = useState("");
  const conversations = useConversations();
  const messages = useConversationMessages(conversation || undefined);
  const answer = useTutorAnswer();
  const error = answer.error ?? messages.error ?? conversations.error;
  return (
    <div className="stack">
      <PageHeader title="AI Tutor" />
      <Card>
        <label>
          Conversation
          <select value={conversation} onChange={(event) => setConversation(event.target.value)}>
            <option value="">Nouvelle conversation</option>
            {conversations.data?.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
          </select>
        </label>
        <form className="stack" onSubmit={(event) => {
          event.preventDefault();
          if (!question.trim()) return;
          answer.mutate({ question: question.trim(), conversation_id: conversation || null }, {
            onSuccess: (result) => {
              setConversation(result.conversation_id);
              setQuestion("");
            },
          });
        }}>
          <label>Votre question<textarea value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={5000} required /></label>
          <button className="btn btn--primary" disabled={answer.isPending}>Envoyer</button>
        </form>
        {error && <p role="alert">{errorMessage(error)}</p>}
      </Card>
      {messages.data?.map((item) => (
        <Card key={item.id} title={item.role === "user" ? "Vous" : "Tuteur"}
          subtitle={item.is_fallback ? "Réponse extractive locale" : item.model}>
          <p style={{ whiteSpace: "pre-wrap" }}>{item.content_markdown}</p>
          <Citations items={item.citations ?? []} />
        </Card>
      ))}
    </div>
  );
}

export function StudioPage() {
  const [topic, setTopic] = useState("");
  const [type, setType] = useState<ArtifactType>("revision_sheet");
  const artifacts = useArtifacts();
  const generate = useGenerateArtifact();
  const error = generate.error ?? artifacts.error;
  return (
    <div className="stack">
      <PageHeader title="Learning Studio" />
      <Card>
        <form className="stack" onSubmit={(event) => {
          event.preventDefault();
          if (topic.trim()) generate.mutate({ type, topic: topic.trim() });
        }}>
          <label>Sujet<input value={topic} onChange={(event) => setTopic(event.target.value)} maxLength={300} required /></label>
          <label>
            Support
            <select value={type} onChange={(event) => setType(event.target.value as ArtifactType)}>
              <option value="revision_sheet">Fiche de révision</option>
              <option value="flashcards">Flashcards</option>
              <option value="quiz">Quiz</option>
            </select>
          </label>
          <button className="btn btn--primary" disabled={generate.isPending}>Générer</button>
        </form>
        {error && <p role="alert">{errorMessage(error)}</p>}
      </Card>
      {artifacts.data?.map((item) => (
        <Card key={item.id} title={item.title} subtitle={item.is_fallback ? "Support extractif local" : item.model}>
          <p style={{ whiteSpace: "pre-wrap" }}>{item.content_markdown}</p>
          <Citations items={item.citations} />
          {item.linked_deck_id && <Link to="/flashcards">Ouvrir les flashcards</Link>}
          {item.linked_quiz_id && <Link to="/quiz">Ouvrir les quiz</Link>}
        </Card>
      ))}
    </div>
  );
}

export function SubjectsPage() {
  const subjects = useCatalogSubjects();
  return (
    <div className="stack">
      <PageHeader title="Mes matières" />
      {subjects.isPending && <p role="status">Chargement…</p>}
      {subjects.error && <p role="alert">{errorMessage(subjects.error)}</p>}
      {subjects.data?.items.length === 0 && <p>Aucune matière enregistrée.</p>}
      {subjects.data?.items.map((item) => (
        <Card key={item.id} title={item.title} subtitle={item.code}>
          <p>{item.document_count} documents · {item.assessment_count} évaluations</p>
        </Card>
      ))}
    </div>
  );
}

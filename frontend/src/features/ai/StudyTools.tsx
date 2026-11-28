import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { errorMessage } from "../../api/client";
import {
  useAiPreferences, useAiProviders, useArtifacts, useCatalogSubject, useCatalogSubjects, useConversationMessages, useConversations,
  useGenerateArtifact, useTutorAnswer,
} from "../../api/queries";
import type { ArtifactType, CitationOut } from "../../api/types";
import { useResults, useSourceDocuments } from "../../api/sources";
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
          {item.locator.heading && <span> · {item.locator.heading}</span>}
          <blockquote>{item.excerpt}</blockquote>
          <Link to={`/library?${new URLSearchParams({ document: item.document_id })}`}>Voir dans la bibliothèque</Link>
        </li>
      ))}
    </ul>
  );
}

export function TutorPage() {
  const [params] = useSearchParams();
  const [subject, setSubject] = useState(params.get("subject") ?? "");
  const [question, setQuestion] = useState("");
  const [conversation, setConversation] = useState(params.get("conversation") ?? "");
  const [documentIds, setDocumentIds] = useState(() => params.getAll("document"));
  const [model, setModel] = useState("");
  const [documentQuery, setDocumentQuery] = useState("");
  const catalog = useCatalogSubjects();
  const subjectDetail = useCatalogSubject(subject || undefined);
  const documents = useSourceDocuments(!subject);
  const providers = useAiProviders();
  const preferences = useAiPreferences();
  const conversations = useConversations();
  const messages = useConversationMessages(conversation || undefined);
  const answer = useTutorAnswer();

  const choices = subject
    ? subjectDetail.data?.documents.filter((item) => item.active_version_id).map(({ id, title }) => ({ id, title })) ?? []
    : documents.data?.items.filter((item) => item.active_version_id).map((item) => ({ id: item.id, title: item.metadata_json.title })) ?? [];
  const activeDocuments = documentIds.filter((id) => choices.some((doc) => doc.id === id));
  const displayedDocuments = choices.filter((doc) => doc.title.toLocaleLowerCase("fr").includes(documentQuery.trim().toLocaleLowerCase("fr")) || activeDocuments.includes(doc.id));
  const models = (providers.data?.items ?? [])
    .filter((provider) => provider.available && (provider.local || preferences.data?.cloud_data_consent))
    .flatMap((provider) => provider.models.map((entry) => ({ ...entry, id: JSON.stringify([entry.provider_id, entry.model_id]) })));
  const selectedModel = models.find((entry) => entry.id === model);
  const documentSource = subject ? subjectDetail : documents;
  const error = answer.error ?? messages.error ?? conversations.error;
  const pendingSelection = documentIds.length > 0 && (!documentSource.isSuccess || activeDocuments.length !== documentIds.length);
  const changingConversation = Boolean(conversation && conversations.isPending);

  return (
    <div className="page">
      <PageHeader title="AI Tutor" />
      <Card>
        <form className="stack" onSubmit={(event) => {
          event.preventDefault();
          if (!question.trim() || pendingSelection || changingConversation) return;
          answer.mutate({
            question: question.trim(),
            subject_id: subject || null,
            conversation_id: conversation || null,
            document_ids: activeDocuments,
            ...(selectedModel ? { provider_override: selectedModel.provider_id, model_override: selectedModel.model_id } : {}),
          }, {
            onSuccess: (result) => {
              setConversation(result.conversation_id);
              setQuestion("");
            },
          });
        }}>
          <div className="form-grid">
            <label className="field"><span>Conversation</span>
              <select className="select" value={conversation} disabled={answer.isPending || conversations.isPending} onChange={(event) => {
                const id = event.target.value;
                setConversation(id);
                setSubject(conversations.data?.find((entry) => entry.id === id)?.subject_id ?? "");
                setDocumentIds([]);
                answer.reset();
              }}>
                <option value="">Nouvelle conversation</option>
                {conversations.data?.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
              </select>
            </label>
            <label className="field"><span>Matière</span>
              <select className="select" value={subject} disabled={answer.isPending} onChange={(event) => {
                setSubject(event.target.value);
                setConversation("");
                setDocumentIds([]);
                setDocumentQuery("");
                answer.reset();
              }}>
                <option value="">Toutes les matières autorisées</option>
                {subject && !catalog.data?.items.some((item) => item.id === subject) && <option value={subject}>{subjectDetail.data?.title ?? subject}</option>}
                {catalog.data?.items.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
              </select>
            </label>
            <label className="field"><span>Modèle</span>
              <select className="select" value={selectedModel?.id ?? ""} disabled={answer.isPending} onChange={(event) => setModel(event.target.value)}>
                <option value="">Sélection automatique</option>
                {models.map((entry) => <option key={entry.id} value={entry.id}>{entry.model_id} · {entry.provider_id}</option>)}
              </select>
            </label>
          </div>
          {providers.isSuccess && !models.length && <p className="small muted">Aucun modèle autorisé et disponible. Le Tuteur peut utiliser des extraits locaux si ce repli est activé.</p>}
          <details open={documentIds.length > 0 || undefined}>
            <summary>Documents de référence {documentIds.length > 0 && `(${documentIds.length})`}</summary>
            {documentSource.isPending && <p role="status">Chargement des documents…</p>}
            {documentSource.error && <p role="alert">{errorMessage(documentSource.error)}</p>}
            {documentSource.isSuccess && !choices.length && <p>Aucun document disponible dans ce périmètre. <Link to="/library">Ouvrir la bibliothèque</Link></p>}
            {!!choices.length && <label className="field"><span>Filtrer les documents</span><input className="input" type="search" value={documentQuery} onChange={(event) => setDocumentQuery(event.target.value)} /></label>}
            {displayedDocuments.slice(0, 50).map((doc) => (
              <label key={doc.id} className="checkbox">
                <input type="checkbox" checked={documentIds.includes(doc.id)}
                  disabled={answer.isPending || (documentIds.length >= 50 && !documentIds.includes(doc.id))}
                  onChange={(event) => setDocumentIds((current) => event.target.checked ? [...current, doc.id] : current.filter((id) => id !== doc.id))} />
                {doc.title}
              </label>
            ))}
            {displayedDocuments.length > 50 && <p className="small muted">Affinez le filtre pour afficher les autres documents.</p>}
            {pendingSelection && documentSource.isSuccess && <p role="alert">Une ressource sélectionnée n’est plus accessible dans ce périmètre.</p>}
            {!!documentIds.length && <button className="btn btn--small" type="button" disabled={answer.isPending} onClick={() => setDocumentIds([])}>Effacer la sélection</button>}
          </details>
          <label className="field"><span>Votre question</span><textarea className="input" value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={5000} required /></label>
          <button className="btn btn--primary" disabled={answer.isPending || pendingSelection || changingConversation || !question.trim()}>{answer.isPending ? "Réponse en cours…" : "Envoyer"}</button>
        </form>
        {error && <p role="alert">{errorMessage(error)}</p>}
      </Card>
      {conversation && messages.isPending && <p role="status">Chargement de l’historique…</p>}
      {messages.data?.map((item) => (
        <Card key={item.id} title={item.role === "user" ? "Vous" : "Tuteur"}
          subtitle={item.is_fallback ? "Réponse extractive locale" : [item.provider, item.model].filter(Boolean).join(" · ")}>
          {item.evidence_status === "insufficient" && <p className="small muted">Sources insuffisantes dans le périmètre sélectionné.</p>}
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
  const results = useResults();
  const groups = (results.data?.semesters ?? []).flatMap((semester) =>
    semester.units.map((unit) => ({ ...unit, semester: semester.title })),
  );
  const catalog = subjects.data?.items ?? [];
  const listed = new Set<string>();
  return (
    <div className="page">
      <PageHeader title="Mes matières" />
      {subjects.isPending && <p role="status">Chargement…</p>}
      {subjects.error && <p role="alert">{errorMessage(subjects.error)}</p>}
      {subjects.isSuccess && !catalog.length && !groups.length && (
        <p>Aucune matière enregistrée. <Link to="/settings">Connecter et synchroniser Pamplemousse</Link></p>
      )}
      {groups.map((group) => (
        <Card key={group.key} title={group.title} subtitle={group.semester}>
          <ul className="list">
            {group.subjects.map((subject) => {
              const item = catalog.find((candidate) => candidate.id === subject.key || Boolean(subject.code && candidate.code?.toUpperCase() === subject.code.toUpperCase()));
              if (item) listed.add(item.id);
              return (
                <li key={subject.key}>
                  <div className="stack">
                    {item ? <Link to={`/subjects/${encodeURIComponent(item.id)}`}>{subject.title}</Link> : <strong>{subject.title}</strong>}
                    <span className="small muted">{subject.code && `${subject.code} · `}Coefficient {subject.coefficient ?? "inconnu"}{subject.ects !== null && ` · ${subject.ects} ECTS`}</span>
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      ))}
      {catalog.some((item) => !listed.has(item.id)) && (
        <Card title={groups.length ? "Autres matières du catalogue" : "Catalogue"}>
          <ul className="list">
            {catalog.filter((item) => !listed.has(item.id)).map((item) => (
              <li key={item.id}><Link to={`/subjects/${encodeURIComponent(item.id)}`}>{item.title}</Link> <span className="small muted">{item.document_count} documents</span></li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

export function SubjectPage() {
  const { subjectId } = useParams();
  const subject = useCatalogSubject(subjectId);
  const results = useResults();
  const item = subject.data;
  const academic = results.data?.semesters.flatMap((semester) => semester.units.flatMap((unit) => unit.subjects))
    .find((row) => row.key === item?.id || Boolean(row.code && row.code.toUpperCase() === item?.code?.toUpperCase()));
  const search = new URLSearchParams({ subject: subjectId ?? "" });
  return (
    <div className="page">
      <Link to="/subjects">Toutes les matières</Link>
      <PageHeader title={item?.title ?? "Matière"} actions={item && <Link className="btn btn--primary" to={`/tutor?${search}`}>Interroger le Tuteur</Link>} />
      {subject.isPending && <p role="status">Chargement…</p>}
      {subject.error && <p role="alert">{errorMessage(subject.error)}</p>}
      {item && <>
        {academic && <Card title="Informations académiques" subtitle={academic.code}>
          <p>Coefficient {academic.coefficient ?? "inconnu"}{academic.ects !== null && ` · ${academic.ects} ECTS`}</p>
          {academic.official_grade !== null && <p>Note officielle {academic.official_grade}{academic.official_grade_maximum ? ` / ${academic.official_grade_maximum}` : " · barème non publié"}</p>}
          <Link to="/grades">Voir les résultats et objectifs</Link>
        </Card>}
        <Card title="Ressources">
          {item.documents.length ? <ul className="list">{item.documents.map((doc) => (
            <li key={doc.id}><Link to={`/library?${new URLSearchParams({ document: doc.id })}`}>{doc.title}</Link> <span className="small muted">{doc.resource_type}</span></li>
          ))}</ul> : <p>Aucun document rattaché.</p>}
        </Card>
        <Card title="Évaluations et révision">
          {item.assessments.length ? <ul className="list">{item.assessments.map((assessment) => (
            <li key={assessment.id}>{assessment.title} · {new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeZone: "Europe/Paris" }).format(new Date(assessment.deadline_at))}</li>
          ))}</ul> : <p>Aucune évaluation dans l’agenda.</p>}
          <p>{item.quizzes.length} quiz · {item.decks.length} jeux de flashcards</p>
          <div className="row"><Link to="/planner">Ouvrir l’agenda</Link><Link to="/quiz">Quiz</Link><Link to="/flashcards">Flashcards</Link></div>
        </Card>
        {item.artifacts.length > 0 && <Card title="Supports pédagogiques">
          <ul className="list">{item.artifacts.map((artifact) => <li key={artifact.id}>{artifact.title}</li>)}</ul>
          <Link to="/studio">Ouvrir le Learning Studio</Link>
        </Card>}
      </>}
    </div>
  );
}

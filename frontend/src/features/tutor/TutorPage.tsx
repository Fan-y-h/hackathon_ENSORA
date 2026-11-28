import { MessageSquare, NotebookPen, Plus, Send, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { useCitation, useDocument, useSubjects } from "@/api/hooks";
import { useAskTutor, useConversations, useCreateConversation, useMessages } from "@/api/hooksLearning";
import { useCreateNote } from "@/api/hooksWorkspace";
import type { TutorMessage, TutorMode } from "@/api/typesLearning";
import { CitationList, EvidenceBadge, DemoLabel } from "@/components/Citations";
import { Markdown } from "@/components/Markdown";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, EmptyState, ErrorPanel, PageHeader, SelectField } from "@/components/ui";
import { locatorLabel } from "@/lib/citations";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";

const MODES: Array<{ value: TutorMode; label: string }> = [
  { value: "explain", label: "Explication progressive" },
  { value: "derivation", label: "Démonstration pas à pas" },
  { value: "code", label: "Explication de code" },
  { value: "correction", label: "Correction détaillée" },
  { value: "socratic", label: "Indices (méthode socratique)" },
  { value: "exam_prep", label: "Préparation d'examen" },
  { value: "memorize", label: "Mémorisation" },
];

const SUGGESTIONS = ["Qu'est-ce qu'une p-valeur ?", "Explique la méthode du pivot", "Quelle différence entre loi des grands nombres et TCL ?"];

function AssistantMessage({ message, question }: { message: TutorMessage; question: string }) {
  const createNote = useCreateNote();
  return (
    <article className="rounded-xl border border-line bg-surface p-4" aria-label="Réponse du tuteur">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {message.evidence_status && <EvidenceBadge status={message.evidence_status} />}
        <DemoLabel provider={message.provider} />
      </div>
      <Markdown>{message.content_markdown}</Markdown>
      <CitationList citations={message.citations} />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="ghost"
          loading={createNote.isPending}
          disabled={createNote.isSuccess}
          onClick={() => createNote.mutate({ title: question.slice(0, 80), markdown: message.content_markdown, citations: message.citations })}
        >
          <NotebookPen className="size-4" aria-hidden="true" />
          {createNote.isSuccess ? "Enregistrée dans Mes notes" : "Enregistrer dans mes notes"}
        </Button>
        {createNote.isSuccess && (
          <Link to="/notes" className="text-sm text-accent hover:underline">
            Ouvrir
          </Link>
        )}
        {createNote.error && <span className="text-sm text-danger">{createNote.error.message}</span>}
      </div>
    </article>
  );
}

/** Chat grounded in the student's sources: every answer shows its citations and evidence level. */
export function TutorPage() {
  const [params, setParams] = useSearchParams();
  const conversationId = params.get("c");
  const documentId = params.get("document");
  const citationId = params.get("cite");

  const subjects = useSubjects();
  const conversations = useConversations();
  const messages = useMessages(conversationId);
  const createConversation = useCreateConversation();
  const ask = useAskTutor(conversationId);
  const citation = useCitation(citationId);
  const document = useDocument(documentId);

  const [subjectId, setSubjectId] = useState("");
  const [mode, setMode] = useState<TutorMode>("explain");
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // Scope from a library link: the document's subject is the natural default.
  useEffect(() => {
    if (document.data?.subject_id && !subjectId) setSubjectId(document.data.subject_id);
  }, [document.data?.subject_id, subjectId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.data?.length, pending]);

  function setParam(key: string, value: string | null) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
  }

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || ask.isPending) return;
    setPending(trimmed);
    setQuestion("");
    let id = conversationId;
    try {
      if (!id) {
        const created = await createConversation.mutateAsync({ subject_id: subjectId || null });
        id = created.id;
        setParam("c", id);
      }
      await ask.mutateAsync({
        conversationId: id,
        question: trimmed,
        subject_id: subjectId || null,
        document_ids: documentId ? [documentId] : undefined,
        citation_ids: citationId ? [citationId] : undefined,
        mode,
        locale: "fr",
        model: "auto",
      });
    } catch {
      setQuestion(trimmed); // keep the text so the student can retry
    } finally {
      setPending(null);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(question);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      void send(question);
    }
  }

  const list = messages.data ?? [];
  const busy = ask.isPending || createConversation.isPending;

  return (
    <section aria-labelledby="page-title" className="flex flex-col gap-5">
      <PageHeader
        title="AI Tutor"
        icon={<MessageSquare className="size-5" aria-hidden="true" />}
        description="Posez vos questions sur vos cours : chaque réponse cite les passages utilisés et signale quand les sources ne suffisent pas."
      />

      <div className="grid gap-5 lg:grid-cols-[240px_minmax(0,1fr)]">
        <nav aria-label="Conversations" className="flex flex-col gap-2">
          <Button variant="secondary" size="sm" onClick={() => setParam("c", null)}>
            <Plus className="size-4" aria-hidden="true" />
            Nouvelle conversation
          </Button>
          <QueryStatus query={conversations} />
          <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto lg:max-h-[60vh]">
            {conversations.data?.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => setParam("c", c.id)}
                  aria-current={c.id === conversationId || undefined}
                  className={cn("focus-ring w-full rounded-md px-2.5 py-2 text-left text-sm", c.id === conversationId ? "bg-accent-soft text-ink" : "hover:bg-surface-2")}
                >
                  <span className="block truncate font-medium">{c.title}</span>
                  <span className="text-xs text-muted">{formatRelative(c.updated_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex min-w-0 flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <SelectField
              label="Matière"
              value={subjectId}
              onChange={(e) => setSubjectId(e.target.value)}
              options={[{ value: "", label: "Toutes mes matières" }, ...(subjects.data ?? []).map((s) => ({ value: s.id, label: s.title }))]}
            />
            <SelectField label="Mode" value={mode} onChange={(e) => setMode(e.target.value as TutorMode)} options={MODES} />
          </div>

          {(documentId || citationId) && (
            <div className="flex flex-wrap items-start gap-3 rounded-lg border border-accent/40 bg-accent-soft/40 p-3 text-sm">
              <div className="min-w-0 flex-1">
                <p className="font-medium">{citationId ? "Réponses limitées au passage sélectionné" : "Réponses limitées au document sélectionné"}</p>
                {citation.data && (
                  <p className="mt-1 text-muted">
                    {citation.data.title} · {locatorLabel(citation.data.locator)} — « {citation.data.excerpt} »
                  </p>
                )}
                {!citationId && document.data && <p className="mt-1 text-muted">{document.data.title}</p>}
              </div>
              <button
                type="button"
                onClick={() => {
                  setParam("cite", null);
                  setParam("document", null);
                }}
                className="focus-ring rounded p-1 text-muted hover:text-ink"
                aria-label="Retirer cette sélection"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            </div>
          )}

          <div className="flex min-h-64 flex-col gap-3" aria-live="polite">
            {conversationId && <QueryStatus query={messages} />}
            {/* A disabled query (no conversation yet) stays "pending": only wait for a real one. */}
            {list.length === 0 && !pending && (!conversationId || !messages.isPending) && (
              <EmptyState
                icon={<MessageSquare className="size-5" aria-hidden="true" />}
                title="Posez votre première question"
                description="Le tuteur répond uniquement à partir de vos documents et indique ses sources."
                action={
                  <div className="flex flex-wrap justify-center gap-2">
                    {SUGGESTIONS.map((s) => (
                      <Button key={s} size="sm" variant="secondary" onClick={() => void send(s)}>
                        {s}
                      </Button>
                    ))}
                  </div>
                }
              />
            )}
            {list.map((message, index) =>
              message.role === "user" ? (
                <p key={message.id} className="ml-auto max-w-[85%] rounded-xl bg-accent px-4 py-2.5 text-sm text-accent-ink">
                  {message.content_markdown}
                </p>
              ) : (
                <AssistantMessage key={message.id} message={message} question={list[index - 1]?.content_markdown ?? "Réponse du tuteur"} />
              ),
            )}
            {pending && (
              <>
                <p className="ml-auto max-w-[85%] rounded-xl bg-accent px-4 py-2.5 text-sm text-accent-ink">{pending}</p>
                <p role="status" className="text-sm text-muted">
                  Le tuteur cherche dans vos sources…
                </p>
              </>
            )}
            {(ask.error || createConversation.error) && <ErrorPanel message={(ask.error ?? createConversation.error)?.message ?? ""} onRetry={() => void send(question)} />}
            <div ref={endRef} />
          </div>

          <form onSubmit={onSubmit} className="sticky bottom-0 flex flex-col gap-2 rounded-xl border border-line bg-surface p-3">
            <label htmlFor="tutor-question" className="sr-only">
              Votre question
            </label>
            <textarea
              id="tutor-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={onKeyDown}
              rows={2}
              placeholder="Votre question… (Ctrl + Entrée pour envoyer)"
              className="focus-ring w-full resize-y rounded-lg border border-line bg-surface px-3 py-2 text-sm placeholder:text-muted"
            />
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs text-muted">Les formules s'écrivent en LaTeX : $\alpha$, $$\bar X_n$$.</p>
              <Button type="submit" loading={busy} disabled={!question.trim()}>
                <Send className="size-4" aria-hidden="true" />
                Envoyer
              </Button>
            </div>
          </form>
        </div>
      </div>
    </section>
  );
}

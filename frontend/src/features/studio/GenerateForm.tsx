import { FileText, Layers, ListChecks, Lock } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";

import { useChapters, useConcepts, useSubjects } from "@/api/hooks";
import type { ArtifactType, DetailLevel, Difficulty, GenerationSpec, StudyObjective } from "@/api/typesLearning";
import { Button, Card, CardHeader, SelectField, TextField } from "@/components/ui";
import { cn } from "@/lib/cn";

const TYPES: Array<{ id: ArtifactType; label: string; icon: ReactNode; available: boolean }> = [
  { id: "revision_sheet", label: "Fiche de révision", icon: <FileText className="size-5" aria-hidden="true" />, available: true },
  { id: "flashcards", label: "Flashcards", icon: <Layers className="size-5" aria-hidden="true" />, available: true },
  { id: "quiz", label: "Quiz", icon: <ListChecks className="size-5" aria-hidden="true" />, available: true },
  { id: "exercises", label: "Exercices corrigés", icon: <Lock className="size-5" aria-hidden="true" />, available: false },
  { id: "mind_map", label: "Carte mentale", icon: <Lock className="size-5" aria-hidden="true" />, available: false },
  { id: "podcast", label: "Podcast", icon: <Lock className="size-5" aria-hidden="true" />, available: false },
];

const OBJECTIVES: Array<{ value: StudyObjective; label: string }> = [
  { value: "discover", label: "Découvrir" },
  { value: "understand", label: "Comprendre" },
  { value: "memorize", label: "Mémoriser" },
  { value: "deepen", label: "Approfondir" },
  { value: "practice", label: "S'entraîner" },
];

interface GenerateFormProps {
  busy: boolean;
  onGenerate: (spec: GenerationSpec) => void;
}

/** Everything the generation contract needs: type, scope, objective, level, duration, instructions. */
export function GenerateForm({ busy, onGenerate }: GenerateFormProps) {
  const subjects = useSubjects();
  const [type, setType] = useState<ArtifactType>("revision_sheet");
  const [subjectId, setSubjectId] = useState("");
  const [chapterIds, setChapterIds] = useState<string[]>([]);
  const [objective, setObjective] = useState<StudyObjective>("understand");
  const [difficulty, setDifficulty] = useState<Difficulty>("intermediate");
  const [detail, setDetail] = useState<DetailLevel>("standard");
  const [duration, setDuration] = useState("20");
  const [instructions, setInstructions] = useState("");
  const chapters = useChapters(subjectId);
  const concepts = useConcepts(subjectId);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!subjectId) return;
    const conceptIds = chapterIds.length ? (concepts.data ?? []).filter((c) => chapterIds.includes(c.chapter_id)).map((c) => c.id) : [];
    onGenerate({
      type,
      subject_id: subjectId,
      document_ids: [],
      concept_ids: conceptIds,
      objective,
      locale: "fr",
      difficulty,
      duration_min: Number(duration) || 20,
      detail_level: detail,
      model: "auto",
      user_instructions: instructions,
      force_regenerate: false,
    });
  }

  return (
    <Card>
      <CardHeader title="Créer un support" description="Le contenu est construit à partir de vos documents ; rien n'est généré à l'avance." />
      <form onSubmit={submit} className="flex flex-col gap-4">
        <fieldset>
          <legend className="mb-2 text-sm font-medium">Type de support</legend>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {TYPES.map((option) => (
              <label
                key={option.id}
                className={cn(
                  "flex items-center gap-2 rounded-lg border p-3 text-sm has-focus-visible:outline-2 has-focus-visible:outline-accent",
                  !option.available && "cursor-not-allowed opacity-55",
                  option.available && "cursor-pointer",
                  type === option.id ? "border-accent bg-accent-soft/50 font-medium" : "border-line",
                )}
              >
                <input type="radio" name="artifact-type" className="sr-only" value={option.id} checked={type === option.id} disabled={!option.available} onChange={() => setType(option.id)} />
                {option.icon}
                <span>
                  {option.label}
                  {!option.available && <span className="block text-xs text-muted">Bientôt</span>}
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <SelectField
          label="Matière"
          required
          value={subjectId}
          onChange={(e) => {
            setSubjectId(e.target.value);
            setChapterIds([]);
          }}
          options={[{ value: "", label: "Choisir une matière" }, ...(subjects.data ?? []).map((s) => ({ value: s.id, label: s.title }))]}
        />

        {subjectId && chapters.data && chapters.data.length > 0 && (
          <fieldset>
            <legend className="mb-2 text-sm font-medium">
              Chapitres <span className="font-normal text-muted">(aucun coché = toute la matière)</span>
            </legend>
            <div className="flex flex-col gap-1.5">
              {chapters.data.map((chapter) => (
                <label key={chapter.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="focus-ring size-4 accent-(--accent)"
                    checked={chapterIds.includes(chapter.id)}
                    onChange={(e) => setChapterIds((ids) => (e.target.checked ? [...ids, chapter.id] : ids.filter((id) => id !== chapter.id)))}
                  />
                  {chapter.ordinal}. {chapter.title}
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <SelectField label="Objectif" value={objective} onChange={(e) => setObjective(e.target.value as StudyObjective)} options={OBJECTIVES} />
          <SelectField
            label="Difficulté"
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value as Difficulty)}
            options={[
              { value: "beginner", label: "Débutant" },
              { value: "intermediate", label: "Intermédiaire" },
              { value: "advanced", label: "Avancé" },
            ]}
          />
          <SelectField
            label="Niveau de détail"
            value={detail}
            onChange={(e) => setDetail(e.target.value as DetailLevel)}
            options={[
              { value: "summary", label: "Synthétique" },
              { value: "standard", label: "Standard" },
              { value: "detailed", label: "Détaillé" },
            ]}
          />
          <TextField label="Durée visée (min)" type="number" min={5} max={180} value={duration} onChange={(e) => setDuration(e.target.value)} />
        </div>
        <TextField label="Consignes (facultatif)" value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="Ex. : insister sur les formules" />

        <div className="flex justify-end">
          <Button type="submit" loading={busy} disabled={!subjectId}>
            Générer
          </Button>
        </div>
      </form>
    </Card>
  );
}

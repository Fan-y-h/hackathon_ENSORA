import { useQueryClient } from "@tanstack/react-query";
import { Check, FlaskConical, FolderOpen, PlugZap } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { apiFetch } from "@/api/client";
import { useConnections, useProfile, useSubjects, useUpdateProfile } from "@/api/hooks";
import { gradeKeys, useCurriculum, usePrograms } from "@/api/hooksGrades";
import { useConnect, useSettings, useUpdateSettings } from "@/api/hooksWorkspace";
import type { SourceKind } from "@/api/types";
import { ENSAI_SOURCES, type PreferredFormat, type SelfLevel } from "@/api/typesWorkspace";
import { QueryStatus } from "@/components/QueryStatus";
import { Button, Card, ErrorPanel, SelectField, SourceStatus } from "@/components/ui";
import { AvailabilityFields, normalizeAvailability, type AvailabilityValue } from "@/features/profile/AvailabilityFields";
import { EnsaiIdentity } from "@/features/profile/EnsaiIdentity";
import { cn } from "@/lib/cn";
import { SOURCE_LABELS } from "@/lib/labels";
import { canPickFolder, pickFolder } from "@/lib/localFolder";

const STEPS = ["Ma scolarité", "Connexions", "Matières et UE", "Objectifs et préférences", "Disponibilités"];
const SOURCES: SourceKind[] = ["moodle", "pamplemousse", "ensora_drive", "google_calendar", "personal_drive"];
const SOURCE_HINTS: Partial<Record<SourceKind, string>> = {
  moodle: "Cours, TD, TP, devoirs et échéances",
  pamplemousse: "Emploi du temps, salles, groupe de TD, coefficients",
  ensora_drive: "Annales, corrigés et fiches des promos",
  google_calendar: "Vos rendez-vous personnels",
  personal_drive: "Vos propres documents",
};

export const FORMATS: Array<{ id: PreferredFormat; label: string }> = [
  { id: "revision_sheet", label: "Fiches" },
  { id: "flashcards", label: "Flashcards" },
  { id: "quiz", label: "Quiz" },
  { id: "exercises", label: "Exercices" },
  { id: "mind_map", label: "Cartes mentales" },
  { id: "video", label: "Vidéos" },
  { id: "podcast", label: "Podcasts" },
  { id: "animation", label: "Animations" },
  { id: "infographic", label: "Infographies" },
];

export const LEVELS: Array<{ value: SelfLevel; label: string }> = [
  { value: "beginner", label: "Débutant" },
  { value: "fragile", label: "Fragile" },
  { value: "average", label: "Moyen" },
  { value: "comfortable", label: "À l'aise" },
  { value: "very_comfortable", label: "Très à l'aise" },
];

const LANGUAGE_LABEL = { fr: "Français", en: "Anglais" } as const;

/** Display-only weighted average of the targets, to show what each UE aims at. */
function weighted(items: Array<{ weight: number; target: number }>): number {
  const total = items.reduce((s, i) => s + i.weight, 0);
  return total ? items.reduce((s, i) => s + i.weight * i.target, 0) / total : 10;
}

/**
 * First-run setup, opened from Mon profil / Paramètres. Everything stays editable later.
 * Nothing is saved before "Terminer", except source connections (they are immediate).
 */
export function OnboardingPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);
  const profile = useProfile();
  const settings = useSettings();
  const subjects = useSubjects();
  const connections = useConnections();
  const connect = useConnect();
  const programs = usePrograms();
  const curriculum = useCurriculum(programs.data?.[0]?.id);
  const updateProfile = useUpdateProfile();
  const updateSettings = useUpdateSettings();

  // Step 2
  const [folder, setFolder] = useState<string | null>(null);
  // Step 4
  const [formats, setFormats] = useState<PreferredFormat[]>([]);
  const [goal, setGoal] = useState<"validate" | "custom">("validate");
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [levels, setLevels] = useState<Record<string, SelfLevel>>({});
  const [languages, setLanguages] = useState<Record<string, "fr" | "en">>({});
  const [difficulty, setDifficulty] = useState<"progressive" | "diagnostic">("progressive");
  // Step 5
  const [availability, setAvailability] = useState<AvailabilityValue | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    const p = settings.data?.preferences;
    if (!p) return;
    setFormats(p.preferred_formats);
    setLevels(p.subject_levels);
    setLanguages(p.subject_languages);
    setDifficulty(p.starting_difficulty);
    setFolder(p.download_folder);
    setAvailability(normalizeAvailability(p));
  }, [settings.data]);

  const nodes = curriculum.data?.nodes ?? [];
  const units = nodes.filter((n) => n.kind === "unit");
  const subjectNodes = (unitId: string) => nodes.filter((n) => n.parent_id === unitId && n.kind === "subject");
  const targetOf = (subjectId: string) => (goal === "validate" ? 10 : Math.max(10, Number((targets[subjectId] ?? "10").replace(",", ".")) || 10));
  const bilingual = subjects.data?.filter((s) => s.languages.length > 1) ?? [];

  async function finish() {
    if (!profile.data || !settings.data || !availability) return;
    setSaving(true);
    setSaveError(null);
    try {
      await updateProfile.mutateAsync({ onboarding_completed_at: new Date().toISOString(), expected_revision: profile.data.revision });
      await updateSettings.mutateAsync({
        preferences: { ...settings.data.preferences, ...normalizeAvailability(availability), preferred_formats: formats, subject_levels: levels, subject_languages: languages, starting_difficulty: difficulty, download_folder: folder },
        expected_revision: settings.data.revision,
      });
      // Goals: every UE at 10 by default ("valider toutes les UE"), plus custom subject targets above 10.
      for (const unit of units) await apiFetch("/grades/objectives", { method: "POST", body: { node_id: unit.id, target_value: "10.00" } });
      if (goal === "custom") {
        for (const node of nodes.filter((n) => n.kind === "subject" && n.subject_id)) {
          const target = targetOf(node.subject_id as string);
          if (target > 10) await apiFetch("/grades/objectives", { method: "POST", body: { node_id: node.id, target_value: target.toFixed(2) } });
        }
      }
      await queryClient.invalidateQueries({ queryKey: gradeKeys.all });
      void navigate(difficulty === "diagnostic" ? "/quiz" : "/", { replace: true });
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Enregistrement impossible.");
    } finally {
      setSaving(false);
    }
  }

  const loading = [profile, settings].find((q) => q.isPending || q.error);

  return (
    <section aria-labelledby="onboarding-title" className="flex flex-col gap-6">
      <div>
        <h1 id="onboarding-title" className="text-2xl font-semibold tracking-tight">
          Configurer mon espace
        </h1>
        <p className="mt-1 text-sm text-muted">Quelques réglages pour un planning et des supports adaptés. Tout reste modifiable ensuite dans Mon profil.</p>
      </div>

      <ol className="flex gap-1.5 overflow-x-auto pb-1" aria-label="Étapes">
        {STEPS.map((label, index) => (
          <li key={label} className="min-w-28 flex-1">
            <button
              type="button"
              onClick={() => setStep(index)}
              aria-current={index === step ? "step" : undefined}
              className={cn(
                "focus-ring w-full border-t-[3px] pt-2 text-left text-xs",
                index === step ? "border-accent font-semibold text-ink" : index < step ? "border-accent/50 text-ink" : "border-line text-muted",
              )}
            >
              <span className="block tabular-nums">{index < step ? <Check className="inline size-3" aria-hidden="true" /> : `${index + 1}.`}</span>
              {label}
            </button>
          </li>
        ))}
      </ol>

      {loading && <QueryStatus query={loading} />}

      {!loading && (
        <Card className="flex flex-col gap-5">
          {step === 0 && (
            <>
              <h2 className="text-lg font-semibold">Ma scolarité</h2>
              <p className="text-sm text-muted">Tout est récupéré de votre compte ENSAI : rien à saisir.</p>
              <EnsaiIdentity />
              <p className="text-sm text-muted">Votre groupe de TD est lu dans Pamplemousse, cours par cours.</p>
            </>
          )}

          {step === 1 && (
            <>
              <h2 className="text-lg font-semibold">Mes sources</h2>
              <p className="text-sm text-muted">Les plateformes de l'ENSAI sont lues avec votre compte, sans jamais être modifiées. Ajoutez si vous le souhaitez vos sources personnelles.</p>
              <ul className="flex flex-col divide-y divide-line">
                {SOURCES.map((kind) => {
                  const connection = connections.data?.find((c) => c.source_kind === kind);
                  const connected = connection && connection.status !== "disconnected";
                  return (
                    <li key={kind} className="flex flex-wrap items-center justify-between gap-3 py-3">
                      <div>
                        <SourceStatus source={SOURCE_LABELS[kind]} state={connection?.status ?? "disconnected"} lastSyncAt={connection?.last_success_at ?? undefined} />
                        <p className="text-xs text-muted">{SOURCE_HINTS[kind]}</p>
                      </div>
                      {(ENSAI_SOURCES as readonly string[]).includes(kind) ? (
                        <span className="text-xs text-muted">Via votre compte ENSAI</span>
                      ) : (
                        !connected && (
                          <Button size="sm" variant="secondary" loading={connect.isPending && connect.variables === kind} onClick={() => connect.mutate(kind)}>
                            <PlugZap className="size-4" aria-hidden="true" />
                            Connecter
                          </Button>
                        )
                      )}
                    </li>
                  );
                })}
              </ul>
              {connect.error && <ErrorPanel message={connect.error.message} />}

              <div className="rounded-lg border border-line p-4">
                <h3 className="font-semibold">Où enregistrer les fichiers sur mon ordinateur ?</h3>
                <p className="mt-1 text-sm text-muted">Les cours et annales que vous téléchargez depuis Ensora+ seront rangés dans ce dossier.</p>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <span className="inline-flex items-center gap-2 rounded-md bg-surface-2 px-3 py-1.5 text-sm">
                    <FolderOpen className="size-4 text-muted" aria-hidden="true" />
                    {folder ?? "Dossier Téléchargements du navigateur"}
                  </span>
                  {canPickFolder() ? (
                    <Button size="sm" variant="secondary" onClick={() => void pickFolder().then((n) => n && setFolder(n))}>
                      Choisir un dossier
                    </Button>
                  ) : (
                    <span className="text-xs text-muted">Votre navigateur ne permet pas de choisir un dossier : les fichiers iront dans Téléchargements.</span>
                  )}
                </div>
              </div>
              <p className="flex items-start gap-2 rounded-lg bg-warning-soft px-3 py-2 text-sm">
                <FlaskConical className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
                En mode démonstration, les connexions utilisent des données fictives, clairement signalées.
              </p>
            </>
          )}

          {step === 2 && (
            <>
              <h2 className="text-lg font-semibold">Mes matières, classées par UE</h2>
              <p className="text-sm text-muted">Matières et coefficients récupérés de Pamplemousse. Les priorités du planning en sont déduites.</p>
              <QueryStatus query={curriculum} />
              <div className="grid gap-4 sm:grid-cols-2">
                {units.map((unit) => (
                  <div key={unit.id} className="rounded-lg border border-line p-4">
                    <p className="mb-2 flex justify-between gap-2 font-semibold">
                      {unit.title} <span className="text-xs font-normal text-muted">coef. {unit.weight.replace(".", ",")}</span>
                    </p>
                    <ul className="flex flex-col gap-1 text-sm">
                      {subjectNodes(unit.id).map((s) => (
                        <li key={s.id} className="flex justify-between gap-2">
                          {s.title} <span className="text-muted tabular-nums">coef. {s.weight.replace(".", ",")}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
              <p className="text-sm text-muted">Une matière manque ? Importez son cours dans la Bibliothèque : elle sera ajoutée automatiquement.</p>
            </>
          )}

          {step === 3 && (
            <>
              <h2 className="text-lg font-semibold">Objectifs et préférences</h2>

              <fieldset>
                <legend className="mb-1 font-semibold">Formats que je préfère</legend>
                <p className="mb-2 text-sm text-muted">Utilisés pour toutes les matières et tous les supports.</p>
                <div className="flex flex-wrap gap-2">
                  {FORMATS.map((f) => {
                    const on = formats.includes(f.id);
                    return (
                      <button key={f.id} type="button" aria-pressed={on} onClick={() => setFormats((list) => (on ? list.filter((x) => x !== f.id) : [...list, f.id]))} className={cn("focus-ring rounded-full border px-3 py-1 text-sm", on ? "border-accent bg-accent text-accent-ink" : "border-line text-muted hover:text-ink")}>
                        {f.label}
                      </button>
                    );
                  })}
                </div>
              </fieldset>

              <fieldset>
                <legend className="mb-2 font-semibold">Mon objectif</legend>
                <div className="flex flex-wrap gap-2">
                  {(
                    [
                      ["validate", "Valider toutes les UE (10 de moyenne)"],
                      ["custom", "Viser plus haut dans certaines matières"],
                    ] as const
                  ).map(([id, label]) => (
                    <label key={id} className={cn("cursor-pointer rounded-lg border px-3 py-2 text-sm has-focus-visible:outline-2 has-focus-visible:outline-accent", goal === id ? "border-accent bg-accent-soft/50 font-medium" : "border-line")}>
                      <input type="radio" name="goal" className="sr-only" checked={goal === id} onChange={() => setGoal(id)} />
                      {label}
                    </label>
                  ))}
                </div>
              </fieldset>

              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-sm">
                  <caption className="sr-only">Niveau actuel et note visée par matière</caption>
                  <thead>
                    <tr className="border-b border-line text-left text-xs text-muted">
                      <th scope="col" className="py-2 pr-3 font-medium">Matière</th>
                      <th scope="col" className="px-3 py-2 font-medium">Coef.</th>
                      <th scope="col" className="px-3 py-2 font-medium">Niveau actuel</th>
                      <th scope="col" className="px-3 py-2 font-medium">Note visée / 20</th>
                    </tr>
                  </thead>
                  <tbody>
                    {units.map((unit) => {
                      const rows = subjectNodes(unit.id);
                      const avg = weighted(rows.map((r) => ({ weight: Number(r.weight), target: targetOf(r.subject_id ?? "") })));
                      return [
                        <tr key={unit.id} className="border-b border-line bg-surface-2/60">
                          <th scope="rowgroup" colSpan={3} className="py-2 pr-3 text-left font-semibold">
                            {unit.title}
                          </th>
                          <td className="px-3 py-2 text-xs text-muted">Moyenne visée : {avg.toFixed(1).replace(".", ",")}</td>
                        </tr>,
                        ...rows.map((row) => {
                          const id = row.subject_id ?? row.id;
                          return (
                            <tr key={row.id} className="border-b border-line/60">
                              <th scope="row" className="py-2 pr-3 text-left font-normal">{row.title}</th>
                              <td className="px-3 py-2 tabular-nums">{row.weight.replace(".", ",")}</td>
                              <td className="px-3 py-2">
                                <select aria-label={`Niveau actuel en ${row.title}`} value={levels[id] ?? "average"} onChange={(e) => setLevels((l) => ({ ...l, [id]: e.target.value as SelfLevel }))} className="focus-ring h-9 rounded-md border border-line bg-surface px-2">
                                  {LEVELS.map((l) => (
                                    <option key={l.value} value={l.value}>
                                      {l.label}
                                    </option>
                                  ))}
                                </select>
                              </td>
                              <td className="px-3 py-2">
                                <input
                                  aria-label={`Note visée en ${row.title}`}
                                  type="number"
                                  min={10}
                                  max={20}
                                  step={0.5}
                                  disabled={goal === "validate"}
                                  value={goal === "validate" ? 10 : (targets[id] ?? "10")}
                                  onChange={(e) => setTargets((t) => ({ ...t, [id]: e.target.value }))}
                                  className="focus-ring h-9 w-20 rounded-md border border-line bg-surface px-2 text-right disabled:text-muted"
                                />
                              </td>
                            </tr>
                          );
                        }),
                      ];
                    })}
                  </tbody>
                </table>
              </div>

              <fieldset>
                <legend className="mb-1 font-semibold">Langue des contenus</legend>
                <p className="mb-2 text-sm text-muted">Les supports sont faits dans la langue du cours publié sur Moodle.</p>
                {bilingual.length === 0 ? (
                  <p className="text-sm">Aucun cours n'existe en deux langues : la langue d'origine de chaque cours est utilisée.</p>
                ) : (
                  <div className="flex flex-col gap-2">
                    <p className="text-sm">Ces cours existent en deux versions, choisissez la vôtre :</p>
                    {bilingual.map((s) => (
                      <div key={s.id} className="max-w-sm">
                        <SelectField
                          label={s.title}
                          value={languages[s.id] ?? s.default_language}
                          onChange={(e) => setLanguages((l) => ({ ...l, [s.id]: e.target.value as "fr" | "en" }))}
                          options={s.languages.map((lang) => ({ value: lang, label: `${LANGUAGE_LABEL[lang]}${lang === s.default_language ? " (langue du professeur)" : ""}` }))}
                        />
                      </div>
                    ))}
                  </div>
                )}
              </fieldset>

              <fieldset>
                <legend className="mb-2 font-semibold">Niveau de difficulté pour commencer</legend>
                <div className="flex flex-col gap-2 text-sm">
                  {(
                    [
                      ["progressive", "Progressif depuis les bases", "Le niveau monte au fil de vos réponses."],
                      ["diagnostic", "Un petit quiz révélateur", "Quelques questions pour partir directement du bon niveau."],
                    ] as const
                  ).map(([id, label, hint]) => (
                    <label key={id} className={cn("flex cursor-pointer gap-2.5 rounded-lg border p-3 has-focus-visible:outline-2 has-focus-visible:outline-accent", difficulty === id ? "border-accent bg-accent-soft/40" : "border-line")}>
                      <input type="radio" name="difficulty" checked={difficulty === id} onChange={() => setDifficulty(id)} className="mt-1 accent-(--accent)" />
                      <span>
                        <span className="font-medium">{label}</span>
                        <span className="block text-muted">{hint}</span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
            </>
          )}

          {step === 4 && (
            <>
              <h2 className="text-lg font-semibold">Mes disponibilités</h2>
              {availability && <AvailabilityFields value={availability} onChange={setAvailability} />}
            </>
          )}

          {saveError && <ErrorPanel message={saveError} />}
          <div className="flex items-center justify-between gap-2 border-t border-line pt-4">
            <Button variant="secondary" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
              Précédent
            </Button>
            <span className="text-xs text-muted tabular-nums">
              {step + 1} / {STEPS.length}
            </span>
            {step < STEPS.length - 1 ? (
              <Button onClick={() => setStep((s) => s + 1)}>Suivant</Button>
            ) : (
              <Button loading={saving} onClick={() => void finish()}>
                Terminer
              </Button>
            )}
          </div>
        </Card>
      )}
    </section>
  );
}

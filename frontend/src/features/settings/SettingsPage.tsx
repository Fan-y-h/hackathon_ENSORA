import { useEffect, useState } from "react";

import { ApiError, errorMessage } from "../../api/client";
import {
  useAiPreferences,
  useAiProviders,
  useDeleteAccount,
  useExportAccount,
  useProfile,
  useRefreshAiProviders,
  useUpdateAiPreferences,
  useUpdateProfile,
} from "../../api/queries";
import type { Profile } from "../../api/types";
import { usePreferences, type ThemeChoice } from "../../app/preferences";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Card, ConfirmDialog, Loading, PageHeader, QueryState, SubjectBadge } from "../../components/ui";
import { colorByKey, confusableWith, resolveSubjectColorKey, STANDARD_PALETTE } from "../../lib/subjects";
import { StudentStatusPicker } from "../profile/StudentStatusPicker";
import { SourcesCard } from "../sources/SourcesCard";
import "./settings.css";

export function SettingsPage() {
  const profile = useProfile();
  return (
    <div className="page">
      <PageHeader title="Profil & Paramètres" />
      <Card title="Catégorie d'élève">
        <StudentStatusPicker />
      </Card>
      <div className="grid-auto" style={{ ["--min" as string]: "380px" }}>
        <QueryState query={profile}>{(p) => <ProfileForm profile={p} />}</QueryState>
        <DisplayCard />
        <SourcesCard />
        <AiPreferencesCard />
      </div>
      <SubjectColors />
      <DataManagementCard />
    </div>
  );
}

function ProfileForm({ profile }: { profile: Profile }) {
  const update = useUpdateProfile();
  const [form, setForm] = useState(() => toForm(profile));

  // Keep form synchronized when profile updates externally (e.g. StudentStatusPicker bumps revision).
  useEffect(() => {
    setForm(toForm(profile));
  }, [profile]);

  const set = <K extends keyof ReturnType<typeof toForm>>(k: K, v: ReturnType<typeof toForm>[K]) =>
    setForm((f) => ({ ...f, [k]: v }));
  const conflict = update.error instanceof ApiError && update.error.isConflict;

  return (
    <Card title="Profil & Limites de travail">
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          update.mutate({
            expected_revision: profile.revision,
            display_name: form.display_name.trim() || undefined,
            cohort: (form.cohort || null) as Profile["cohort"],
            track: form.track.trim() || null,
            academic_year: form.academic_year.trim() || null,
            timezone: form.timezone.trim() || "Europe/Paris",
            preferences: {
              daily_max_min: form.daily_max_min,
              weekly_max_min: form.weekly_max_min,
              min_session_min: form.min_session_min,
              max_session_min: form.max_session_min,
              planner_algorithm: form.planner_algorithm,
              repetition_algorithm: form.repetition_algorithm,
            },
          });
        }}
      >
        <div className="form-grid">
          <label className="field">
            <span>Nom affiché</span>
            <input className="input" value={form.display_name} onChange={(e) => set("display_name", e.target.value)} maxLength={120} />
          </label>
          <label className="field">
            <span>Année</span>
            <select className="select" value={form.cohort} onChange={(e) => set("cohort", e.target.value)}>
              <option value="">—</option>
              <option value="1A">1A</option>
              <option value="2A">2A</option>
              <option value="3A">3A</option>
            </select>
          </label>
          <label className="field">
            <span>Filière</span>
            <input className="input" value={form.track} onChange={(e) => set("track", e.target.value)} maxLength={120} />
          </label>
          <label className="field">
            <span>Année universitaire</span>
            <input className="input" placeholder="2026-2027" value={form.academic_year} onChange={(e) => set("academic_year", e.target.value)} maxLength={9} />
          </label>
          <label className="field">
            <span>Fuseau horaire</span>
            <input className="input" value={form.timezone} onChange={(e) => set("timezone", e.target.value)} maxLength={64} />
          </label>
        </div>
        <div className="form-grid">
          {(
            [
              ["daily_max_min", "Max par jour (min)", 30, 720],
              ["weekly_max_min", "Max par semaine (min)", 60, 4200],
              ["min_session_min", "Séance min. (min)", 10, 120],
              ["max_session_min", "Séance max. (min)", 20, 240],
            ] as const
          ).map(([k, label, min, max]) => (
            <label key={k} className="field">
              <span>{label}</span>
              <input className="input" type="number" min={min} max={max} step={5} value={form[k]} onChange={(e) => set(k, Number(e.target.value))} />
            </label>
          ))}
          <label className="field">
            <span>Moteur Smart Planner</span>
            <select
              className="select"
              value={form.planner_algorithm}
              onChange={(e) => set("planner_algorithm", e.target.value as "greedy" | "cpsat")}
            >
              <option value="greedy">Rapide (heuristique)</option>
              <option value="cpsat">Optimisé (CP-SAT si disponible)</option>
            </select>
          </label>
          <label className="field">
            <span>Répétition espacée</span>
            <select
              className="select"
              value={form.repetition_algorithm}
              onChange={(e) => set("repetition_algorithm", e.target.value as "heuristic" | "fsrs")}
            >
              <option value="fsrs">FSRS adaptatif</option>
              <option value="heuristic">Intervalles classiques</option>
            </select>
          </label>
        </div>
        {update.error && (
          <div className="status status--critical" role="alert">
            {conflict ? "Profil modifié ailleurs : rechargez la page." : errorMessage(update.error)}
          </div>
        )}
        {update.isSuccess && (
          <div className="status status--good" role="status">
            <Icon name="check" /> Enregistré. Le planning signalera s'il doit être recalculé.
          </div>
        )}
        <button type="submit" className="btn btn--primary" disabled={update.isPending}>
          {update.isPending ? "Enregistrement…" : "Enregistrer"}
        </button>
      </form>
    </Card>
  );
}

function toForm(profile: Profile) {
  return {
    display_name: profile.display_name ?? "",
    cohort: profile.cohort ?? "",
    track: profile.track ?? "",
    academic_year: profile.academic_year ?? "",
    timezone: profile.timezone ?? "Europe/Paris",
    daily_max_min: profile.preferences.daily_max_min,
    weekly_max_min: profile.preferences.weekly_max_min,
    min_session_min: profile.preferences.min_session_min,
    max_session_min: profile.preferences.max_session_min,
    planner_algorithm: profile.preferences.planner_algorithm,
    repetition_algorithm: profile.preferences.repetition_algorithm,
  };
}

function DisplayCard() {
  const prefs = usePreferences();
  const themes: { id: ThemeChoice; label: string }[] = [
    { id: "light", label: "Clair" },
    { id: "dark", label: "Sombre" },
    { id: "system", label: "Système" },
  ];
  return (
    <Card title="Affichage & Accessibilité">
      <div className="field">
        <span>Thème</span>
        <div className="segmented" role="group" aria-label="Thème">
          {themes.map((t) => (
            <button key={t.id} type="button" aria-pressed={prefs.theme === t.id} onClick={() => prefs.update({ theme: t.id })}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={prefs.accessible} onChange={(e) => prefs.update({ accessible: e.target.checked })} />
        Mode accessible : contours renforcés, palette Paul Tol lisible par les personnes daltoniennes, évaluations en bloc plein
      </label>
      <label className="checkbox">
        <input type="checkbox" checked={prefs.alwaysShowAcronyms} onChange={(e) => prefs.update({ alwaysShowAcronyms: e.target.checked })} />
        Toujours afficher les sigles des matières (recommandé)
      </label>
    </Card>
  );
}

function AiPreferencesCard() {
  const providers = useAiProviders();
  const preferences = useAiPreferences();
  const refresh = useRefreshAiProviders();
  const update = useUpdateAiPreferences();

  const unavailable =
    (providers.error instanceof ApiError && providers.error.isUnavailable) ||
    (preferences.error instanceof ApiError && preferences.error.isUnavailable);

  return (
    <Card
      title="Fournisseurs IA & Confidentialité"
      subtitle="Local-first par défaut. Aucun document n'est envoyé à un fournisseur distant sans votre consentement."
    >
      {providers.isPending || preferences.isPending ? (
        <Loading lines={3} />
      ) : unavailable ? (
        <p className="small muted">
          Le routeur IA (<code>/api/v1/ai/*</code>) n'est pas encore monté sur ce serveur. Il sera activé dès l'intégration du module IA/RAG.
        </p>
      ) : (
        <div className="stack">
          {providers.data && (
            <ul className="list">
              {providers.data.items.map((p) => (
                <li key={p.provider_id} style={{ padding: "6px 0", justifyContent: "space-between" }}>
                  <span className="small">
                    <strong>{p.provider_id}</strong> · {p.local ? "Local" : "Distant"}
                  </span>
                  <span className={`pill${p.available ? " status--good" : ""}`}>
                    {p.available ? "Disponible" : p.error_code ?? "Indisponible"}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {preferences.data && (
            <>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={preferences.data.enabled}
                  disabled={update.isPending}
                  onChange={(e) => update.mutate({ enabled: e.target.checked })}
                />
                Activer l'assistant IA
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={preferences.data.cloud_data_consent}
                  disabled={update.isPending}
                  onChange={(e) => update.mutate({ cloud_data_consent: e.target.checked })}
                />
                Autoriser l'envoi vers un fournisseur cloud configuré (opt-in)
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={preferences.data.allow_extractive_fallback}
                  disabled={update.isPending}
                  onChange={(e) => update.mutate({ allow_extractive_fallback: e.target.checked })}
                />
                Autoriser le mode extractif local transparent si aucun LLM n'est disponible
              </label>
            </>
          )}
          {(refresh.error || update.error) && (
            <div className="status status--critical small">{errorMessage(refresh.error ?? update.error)}</div>
          )}
          <div className="row">
            <button type="button" className="btn btn--small" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
              <Icon name="refresh" size={14} /> Actualiser les fournisseurs
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}

function SubjectColors() {
  const prefs = usePreferences();
  const subjects = useSubjects();
  const [draft, setDraft] = useState<Record<string, string>>(prefs.subjectColors);
  const [savedNotice, setSavedNotice] = useState(false);

  useEffect(() => {
    setDraft(prefs.subjectColors);
  }, [prefs.subjectColors]);

  // Always resolve by canonical key (works identically in light, dark, and accessible modes).
  const keyOf = (id: string) => resolveSubjectColorKey(id, subjects.ids, draft);
  const chosen = subjects.ids.map(keyOf);

  return (
    <section id="couleurs" className="card">
      <div className="card__head">
        <div className="stack" style={{ gap: 2 }}>
          <h2 className="card__title">Couleurs de mes matières</h2>
          <p className="card__subtitle">
            10 teintes d'intensité voisine. La couleur n'est qu'un repère : le sigle reste toujours affiché.
            {prefs.accessible && " En mode accessible, la palette haute lisibilité est activée."}
          </p>
        </div>
        <div className="row">
          <button
            type="button"
            className="btn"
            onClick={() => {
              prefs.update({ subjectColors: {} });
              setSavedNotice(true);
            }}
          >
            Rétablir les couleurs par défaut
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => {
              prefs.update({ subjectColors: draft });
              setSavedNotice(true);
            }}
          >
            Enregistrer
          </button>
        </div>
      </div>
      {savedNotice && (
        <div className="status status--good small" role="status">
          <Icon name="check" size={14} /> Couleurs enregistrées sur cet appareil.
        </div>
      )}
      {subjects.ids.length === 0 ? (
        <p className="small muted">Aucune matière détectée dans l'agenda ou les tâches pour l'instant.</p>
      ) : (
        subjects.ids.map((id) => {
          const key = keyOf(id);
          const color = colorByKey(key);
          const swatchColor = prefs.resolvedDark ? color.dark : prefs.accessible ? color.accessible : color.hex;
          const clash = confusableWith(
            key,
            chosen.filter((_, i) => subjects.ids[i] !== id),
          );
          return (
            <fieldset key={id} className="subject-color">
              <legend className="row" style={{ gap: 8 }}>
                <span className="subject-color__bar" style={{ background: swatchColor }} aria-hidden />
                <SubjectBadge acronym={subjects.acronym(id)} color={swatchColor} />
                <strong>{subjects.name(id)}</strong>
                <span className="small muted">· {color.name}</span>
              </legend>
              <div className="subject-color__swatches" role="radiogroup" aria-label={`Couleur de ${subjects.name(id)}`}>
                {STANDARD_PALETTE.map((c) => {
                  const bg = prefs.resolvedDark ? c.dark : c.hex;
                  return (
                    <label key={c.key} className="subject-color__swatch" style={{ background: bg }} title={c.name}>
                      <input
                        type="radio"
                        name={`color-${id}`}
                        className="sr-only"
                        checked={key === c.key}
                        onChange={() => {
                          setSavedNotice(false);
                          setDraft((d) => ({ ...d, [id]: c.key }));
                        }}
                      />
                      <span className="sr-only">{c.name}</span>
                      {key === c.key && <Icon name="check" size={16} strokeWidth={3} />}
                    </label>
                  );
                })}
              </div>
              {clash && (
                <div className="status status--warning small">
                  <Icon name="warning" />
                  {color.name} et {colorByKey(clash).name} peuvent se confondre pour certaines personnes daltoniennes. Le sigle reste affiché.
                </div>
              )}
            </fieldset>
          );
        })
      )}
    </section>
  );
}

function DataManagementCard() {
  const exporter = useExportAccount();
  const deleter = useDeleteAccount();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [exportedAt, setExportedAt] = useState<string | null>(null);

  const handleExport = () => {
    exporter.mutate(undefined, {
      onSuccess: (payload) => {
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `ensora-export-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        setExportedAt(new Date().toLocaleTimeString("fr-FR"));
      },
    });
  };

  return (
    <Card
      title="Mes données personnelles (local-first)"
      subtitle="Exportez l'intégralité de vos données (profil, planning, notes, résultats, apprentissage) ou supprimez votre espace local."
    >
      <div className="row">
        <button type="button" className="btn" disabled={exporter.isPending} onClick={handleExport}>
          {exporter.isPending ? "Préparation de l'export…" : "Exporter mes données (JSON)"}
        </button>
        <button type="button" className="btn btn--danger" onClick={() => setConfirmDelete(true)}>
          <Icon name="trash" size={14} /> Supprimer mes données
        </button>
      </div>
      {exportedAt && (
        <div className="status status--good small" role="status">
          <Icon name="check" size={14} /> Export téléchargé à {exportedAt}.
        </div>
      )}
      {exporter.error && <div className="status status--critical small">{errorMessage(exporter.error)}</div>}
      {deleter.isSuccess && (
        <div className="status status--warning small" role="status">
          Vos données locales ont été supprimées.
        </div>
      )}

      {confirmDelete && (
        <ConfirmDialog
          title="Supprimer définitivement vos données ?"
          message="Cette action efface votre profil, agenda local, tâches, notes, flashcards, quiz et notes académiques locales. Vos plateformes ENSAI d'origine ne sont pas affectées."
          confirmLabel="Supprimer définitivement"
          danger
          requireText="DELETE MY DATA"
          pending={deleter.isPending}
          error={deleter.error}
          onClose={() => setConfirmDelete(false)}
          onConfirm={() =>
            deleter.mutate("DELETE MY DATA", {
              onSuccess: () => setConfirmDelete(false),
            })
          }
        />
      )}
    </Card>
  );
}

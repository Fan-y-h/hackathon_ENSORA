import { useEffect, useState } from "react";

import { ApiError, errorMessage } from "../../api/client";
import { useProfile, useUpdateProfile } from "../../api/queries";
import type { Profile } from "../../api/types";
import { usePreferences, type ThemeChoice } from "../../app/preferences";
import { useSubjects } from "../../app/subjects";
import { Icon } from "../../components/Icon";
import { Card, PageHeader, QueryState, SubjectBadge } from "../../components/ui";
import { StudentStatusPicker } from "../profile/StudentStatusPicker";
import { SourcesCard } from "../sources/SourcesCard";
import { colorByKey, confusableWith, resolveSubjectColor, STANDARD_PALETTE } from "../../lib/subjects";
import "./settings.css";

export function SettingsPage() {
  const profile = useProfile();
  return (
    <div className="page">
      <PageHeader title="Profil" />
      <Card title="Catégorie d'élève">
        <StudentStatusPicker />
      </Card>
      <div className="grid-auto" style={{ ["--min" as string]: "380px" }}>
        <QueryState query={profile}>{(p) => <ProfileForm profile={p} />}</QueryState>
        <DisplayCard />
        <SourcesCard />
      </div>
      <SubjectColors />
    </div>
  );
}

function ProfileForm({ profile }: { profile: Profile }) {
  const update = useUpdateProfile();
  const prefs = profile.preferences;
  const [form, setForm] = useState({
    display_name: profile.display_name ?? "",
    cohort: profile.cohort ?? "",
    track: profile.track ?? "",
    daily_max_min: prefs.daily_max_min,
    weekly_max_min: prefs.weekly_max_min,
    min_session_min: prefs.min_session_min,
    max_session_min: prefs.max_session_min,
  });
  const set = (k: keyof typeof form, v: string | number) => setForm((f) => ({ ...f, [k]: v }));
  const conflict = update.error instanceof ApiError && update.error.isConflict;

  return (
    <Card title="Limites de travail">
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          update.mutate({
            expected_revision: profile.revision,
            display_name: form.display_name || null,
            cohort: (form.cohort || null) as Profile["cohort"],
            track: form.track || null,
            preferences: {
              daily_max_min: form.daily_max_min,
              weekly_max_min: form.weekly_max_min,
              min_session_min: form.min_session_min,
              max_session_min: form.max_session_min,
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
        </div>
        {update.error && (
          <div className="status status--critical">{conflict ? "Profil modifié ailleurs : rechargez la page." : errorMessage(update.error)}</div>
        )}
        {update.isSuccess && <div className="status status--good"><Icon name="check" /> Enregistré. Le planning signalera s'il doit être recalculé.</div>}
        <button type="submit" className="btn btn--primary" disabled={update.isPending}>
          Enregistrer
        </button>
      </form>
    </Card>
  );
}

function DisplayCard() {
  const prefs = usePreferences();
  const themes: { id: ThemeChoice; label: string }[] = [
    { id: "light", label: "Clair" },
    { id: "dark", label: "Sombre" },
    { id: "system", label: "Système" },
  ];
  return (
    <Card title="Affichage">
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

function SubjectColors() {
  const prefs = usePreferences();
  const subjects = useSubjects();
  const [draft, setDraft] = useState<Record<string, string>>(prefs.subjectColors);
  useEffect(() => setDraft(prefs.subjectColors), [prefs.subjectColors]);

  const ctx = { overrides: draft, accessible: false, dark: prefs.resolvedDark };
  const keyOf = (id: string) => draft[id] ?? STANDARD_PALETTE.find((c) => c.hex === resolveSubjectColor(id, subjects.ids, ctx))?.key ?? "bleu";
  const chosen = subjects.ids.map(keyOf);

  return (
    <section id="couleurs" className="card">
      <div className="card__head">
        <div className="stack" style={{ gap: 2 }}>
          <h2 className="card__title">Couleurs de mes matières</h2>
          <p className="card__subtitle">
            10 teintes d'intensité voisine. La couleur n'est qu'un repère : le sigle reste toujours affiché.
            {prefs.accessible && " En mode accessible, la palette Paul Tol est imposée."}
          </p>
        </div>
        <div className="row">
          <button type="button" className="btn" onClick={() => prefs.update({ subjectColors: {} })}>
            Rétablir les couleurs par défaut
          </button>
          <button type="button" className="btn btn--primary" onClick={() => prefs.update({ subjectColors: draft })}>
            Enregistrer
          </button>
        </div>
      </div>
      {subjects.ids.length === 0 ? (
        <p className="small muted">Aucune matière détectée dans l'agenda ou les tâches pour l'instant.</p>
      ) : (
        subjects.ids.map((id) => {
          const key = keyOf(id);
          const clash = confusableWith(key, chosen.filter((_, i) => subjects.ids[i] !== id));
          return (
            <fieldset key={id} className="subject-color">
              <legend className="row" style={{ gap: 8 }}>
                <span className="subject-color__bar" style={{ background: colorByKey(key).hex }} aria-hidden />
                <SubjectBadge acronym={subjects.acronym(id)} color={colorByKey(key).hex} />
                <strong>{subjects.name(id)}</strong>
                <span className="small muted">· {colorByKey(key).name}</span>
              </legend>
              <div className="subject-color__swatches" role="radiogroup" aria-label={`Couleur de ${subjects.name(id)}`}>
                {STANDARD_PALETTE.map((c) => (
                  <label key={c.key} className="subject-color__swatch" style={{ background: c.hex }} title={c.name}>
                    <input
                      type="radio"
                      name={`color-${id}`}
                      className="sr-only"
                      checked={key === c.key}
                      onChange={() => setDraft((d) => ({ ...d, [id]: c.key }))}
                    />
                    <span className="sr-only">{c.name}</span>
                    {key === c.key && <Icon name="check" size={16} strokeWidth={3} />}
                  </label>
                ))}
              </div>
              {clash && (
                <div className="status status--warning small">
                  <Icon name="warning" />
                  {colorByKey(key).name} et {colorByKey(clash).name} se confondent pour certaines personnes daltoniennes. Le sigle reste affiché.
                </div>
              )}
            </fieldset>
          );
        })
      )}
    </section>
  );
}

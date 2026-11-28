import { useState } from "react";
import { Link } from "react-router-dom";

import { errorMessage } from "../../api/client";
import { useProfile, useUpdateProfile } from "../../api/queries";
import type { Profile, TimeWindow } from "../../api/types";
import { Card, PageHeader, QueryState } from "../../components/ui";
import { SourcesCard } from "../sources/SourcesCard";

const weekdays = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];

function academicYearToday() {
  const now = new Date();
  const start = now.getFullYear() - Number(now.getMonth() < 8);
  return `${start}-${start + 1}`;
}

export function OnboardingPage() {
  const profile = useProfile();
  return (
    <div className="page">
      <PageHeader title="Configurer mon espace ENSAI" />
      <QueryState query={profile}>
        {(value) => <OnboardingForm key={value.user_id} profile={value} />}
      </QueryState>
    </div>
  );
}

function OnboardingForm({ profile }: { profile: Profile }) {
  const update = useUpdateProfile();
  const [showSources, setShowSources] = useState(profile.onboarding_complete && Boolean(profile.academic_year));
  const [name, setName] = useState(profile.cohort ? profile.display_name : "");
  const [cohort, setCohort] = useState<Profile["cohort"]>(profile.cohort);
  const [track, setTrack] = useState(profile.track ?? "");
  const [studentStatus, setStudentStatus] = useState<Profile["student_status"]>(profile.student_status);
  const [year, setYear] = useState(profile.academic_year ?? academicYearToday());
  const [windows, setWindows] = useState<TimeWindow[]>(profile.preferences.work_windows);
  const [goal, setGoal] = useState("");
  const [error, setError] = useState("");
  const changeWindow = (index: number, patch: Partial<TimeWindow>) =>
    setWindows((current) => current.map((window, i) => i === index ? { ...window, ...patch } : window));

  if (showSources) return (
    <>
      {!profile.onboarding_complete ? null : <Card title="Connecter mes plateformes">
        <ol>
          <li>Autorisez la lecture de Pamplemousse, connectez-vous dans la fenêtre ENSAI et cliquez sur Vérifier, puis Synchroniser.</li>
          <li>Après la récupération du programme, connectez Moodle et choisissez une matière à synchroniser.</li>
          <li>Connectez Drive Ensora et Google Agenda si vous souhaitez les ajouter à votre espace.</li>
        </ol>
        <p className="small muted">Vous pourrez reprendre ces étapes dans Paramètres.</p>
      </Card>}
      <SourcesCard />
      <div className="row">
        <button className="btn" type="button" onClick={() => setShowSources(false)}>Modifier mon profil</button>
        <Link className="btn btn--primary" to="/">Ouvrir le tableau de bord</Link>
      </div>
    </>
  );

  return (
    <Card title="Identité et préférences de travail">
      <form className="stack" onSubmit={(event) => {
        event.preventDefault();
        if (!name.trim() || !cohort || !studentStatus || (cohort === "3A" && !track.trim())) {
          setError("Renseignez votre identité, votre statut et votre filière en 3A.");
          return;
        }
        if (!/^\d{4}-\d{4}$/.test(year) || Number(year.slice(5)) !== Number(year.slice(0, 4)) + 1) {
          setError("Saisissez deux années consécutives au format AAAA-AAAA.");
          return;
        }
        if (!windows.length || windows.some(({ start, end }) => !start || !end || start >= end)) {
          setError("Ajoutez au moins un créneau de travail dont la fin suit le début.");
          return;
        }
        setError("");
        update.mutate({
          expected_revision: profile.revision,
          display_name: name.trim(),
          cohort,
          track: track.trim() || null,
          student_status: studentStatus,
          academic_year: year,
          preferences: { work_windows: windows },
          ...(goal.trim() ? { goals: [...profile.goals, { label: goal.trim(), kind: "custom", active: true }] } : {}),
        }, { onSuccess: () => { setGoal(""); setShowSources(true); } });
      }}>
        <p className="muted">Le profil est enregistré sur cet appareil. Les plateformes ENSAI demandent leur propre connexion.</p>
        <div className="form-grid">
          <label className="field"><span>Nom affiché</span><input className="input" value={name} onChange={(event) => setName(event.target.value)} maxLength={120} required /></label>
          <label className="field"><span>Année d’études</span><select className="select" value={cohort ?? ""} onChange={(event) => setCohort(event.target.value as Profile["cohort"])} required>
            <option value="">Choisir</option><option>1A</option><option>2A</option><option>3A</option>
          </select></label>
          <label className="field"><span>Filière</span><input className="input" value={track} onChange={(event) => setTrack(event.target.value)} maxLength={120} required={cohort === "3A"} /></label>
          <label className="field"><span>Statut étudiant</span><select className="select" value={studentStatus ?? ""} onChange={(event) => setStudentStatus(event.target.value as Profile["student_status"])} required>
            <option value="">Choisir</option><option value="engineer">Élève ingénieur·e</option><option value="civil_servant">Élève fonctionnaire</option>
          </select></label>
          <label className="field"><span>Année universitaire</span><input className="input" value={year} onChange={(event) => setYear(event.target.value)} maxLength={9} required /></label>
        </div>
        <fieldset className="stack">
          <legend>Créneaux de révision hebdomadaires</legend>
          {windows.map((window, index) => (
            <div className="row" key={index}>
              <label className="field"><span>Jour</span><select className="select" value={window.weekday} onChange={(event) => changeWindow(index, { weekday: Number(event.target.value) })}>
                {weekdays.map((day, dayIndex) => <option key={day} value={dayIndex}>{day}</option>)}
              </select></label>
              <label className="field"><span>Début</span><input className="input" type="time" value={window.start} onChange={(event) => changeWindow(index, { start: event.target.value })} required /></label>
              <label className="field"><span>Fin</span><input className="input" type="time" value={window.end} onChange={(event) => changeWindow(index, { end: event.target.value })} required /></label>
              <button className="btn" type="button" onClick={() => setWindows((current) => current.filter((_, i) => i !== index))}>Retirer le créneau {index + 1}</button>
            </div>
          ))}
          <button className="btn" type="button" disabled={windows.length >= 70} onClick={() => setWindows((current) => [...current, { weekday: 0, start: "18:00", end: "20:00" }])}>Ajouter un créneau</button>
        </fieldset>
        <label className="field"><span>Objectif académique personnel (facultatif)</span><input className="input" value={goal} onChange={(event) => setGoal(event.target.value)} maxLength={200} /></label>
        {(error || update.error) && <p className="status status--critical" role="alert">{error || errorMessage(update.error)}</p>}
        <button className="btn btn--primary" type="submit" disabled={update.isPending}>{update.isPending ? "Enregistrement…" : "Enregistrer et configurer les sources"}</button>
      </form>
    </Card>
  );
}

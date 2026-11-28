import { useEffect, useMemo, useState } from "react";

import { sourcesUnavailable, useResults, type ResultsSemester, type ResultsSubject, type ResultsUnit } from "../../api/sources";
import { Card, EmptyState, ErrorPanel, Loading } from "../../components/ui";
import { SyncButton } from "../sources/SyncButton";
import { assessmentMark, fmt, fmtCoef, parseMark, semesterAverage, subjectMark, unitAverage, type Simulations } from "./average";
import "./results.css";

const SIM_KEY = "ensora.results.simulations.v1";

function loadSims(): Simulations {
  try {
    return JSON.parse(localStorage.getItem(SIM_KEY) ?? "{}") as Simulations;
  } catch {
    return {};
  }
}

/**
 * "Mes résultats": UEs, subjects, coefficients and marks synced from Pamplemousse.
 * Official marks are read-only; empty marks can be simulated (stored on this device only).
 */
export function GradesPage() {
  const results = useResults();
  const [sims, setSims] = useState<Simulations>(loadSims);
  const [semKey, setSemKey] = useState<string | null>(null);

  useEffect(() => {
    localStorage.setItem(SIM_KEY, JSON.stringify(sims));
  }, [sims]);

  const semesters = useMemo(() => results.data?.semesters ?? [], [results.data]);
  const sem = semesters.find((s) => s.key === semKey) ?? semesters.find((s) => s.units.length) ?? semesters[0];
  const setSim = (key: string, value: string) => setSims((all) => ({ ...all, [key]: value }));

  if (results.isPending) return <div className="page"><Loading lines={6} /></div>;
  if (results.error) {
    return (
      <div className="page">
        {sourcesUnavailable(results.error) ? (
          <Card>
            <EmptyState title="Résultats indisponibles" action={<SyncButton />}>
              Connectez Pamplemousse dans Profil › Sources, puis synchronisez.
            </EmptyState>
          </Card>
        ) : (
          <ErrorPanel error={results.error} onRetry={() => void results.refetch()} />
        )}
      </div>
    );
  }
  if (!sem) {
    return (
      <div className="page">
        <Card>
          <EmptyState title="Aucun résultat synchronisé" action={<SyncButton />} />
        </Card>
      </div>
    );
  }

  return (
    <div className="results">
      <ResultsHero
        semester={sem}
        semesters={semesters}
        onSemester={setSemKey}
        sims={sims}
        general={results.data!.general_average}
        year={results.data!.academic_year}
      />
      <div className="results__body">
        <div className="results__tools">
          <p className="small muted">Une matière sans note n'est pas comptée. Les cases vides acceptent une note simulée.</p>
          <button type="button" className="btn btn--small" disabled={!Object.keys(sims).length} onClick={() => setSims({})}>
            Effacer les simulations
          </button>
        </div>
        {sem.units.map((u) => (
          <UnitCard key={u.key} unit={u} sims={sims} onSim={setSim} />
        ))}
      </div>
    </div>
  );
}

function ResultsHero({
  semester,
  semesters,
  onSemester,
  sims,
  general,
  year,
}: {
  semester: ResultsSemester;
  semesters: ResultsSemester[];
  onSemester: (key: string) => void;
  sims: Simulations;
  general: number | null;
  year: string | null;
}) {
  const computed = semesterAverage(semester, sims);
  // The official average wins unless the student is simulating missing marks.
  const showOfficial = semester.official_average !== null && !computed.simulated;
  const value = showOfficial ? semester.official_average : computed.value;

  return (
    <header className="results__hero">
      <div className="results__wrap">
        <div className="results__top">
          <div>
            <h1>Ma moyenne · {semester.title}</h1>
            <p>
              {year ? `${year} · ` : ""}Pamplemousse{general !== null ? ` · moyenne générale ${fmt(general)}` : ""}
            </p>
          </div>
          {semesters.length > 1 && (
            <div className="results__sems" role="group" aria-label="Semestre">
              {semesters.map((s) => (
                <button key={s.key} type="button" aria-pressed={s.key === semester.key} onClick={() => onSemester(s.key)}>
                  {s.title}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="results__grid">
          <div>
            <div className="results__big">
              {fmt(value)}
              <small> /20</small>
            </div>
            <div className="results__sub">
              {value === null
                ? "Aucune note pour l'instant."
                : showOfficial
                  ? "Moyenne officielle"
                  : `Sur ${fmtCoef(computed.markedCoef)} coef. notés / ${fmtCoef(computed.totalCoef)}${computed.markedCoef < computed.totalCoef || computed.simulated ? " · provisoire" : ""}`}
            </div>
          </div>
          <div className="results__bars">
            {semester.units.map((u) => {
              const avg = u.official_average ?? unitAverage(u, sims).value;
              return (
                <div key={u.key} className="results__bar" title={`${u.title} : ${fmt(avg)}`}>
                  <div>
                    <div className="results__label">{u.title}</div>
                    <div className="results__track" role="img" aria-label={`${u.title} : ${fmt(avg)} sur 20`}>
                      <div className="results__fill" style={{ width: `${avg === null ? 0 : Math.min(100, (avg / 20) * 100)}%` }} />
                      <div className="results__ten" />
                    </div>
                  </div>
                  <span className="results__v">{fmt(avg)}</span>
                </div>
              );
            })}
            <div className="results__legend">Le trait jaune marque 10/20.</div>
          </div>
        </div>
      </div>
    </header>
  );
}

function UnitCard({ unit, sims, onSim }: { unit: ResultsUnit; sims: Simulations; onSim: (key: string, v: string) => void }) {
  const avg = unitAverage(unit, sims);
  const value = unit.official_average ?? avg.value;
  return (
    <section className="ue">
      <div className="ue__head">
        <h2>{unit.title}</h2>
        <span className="small muted">
          coef {fmtCoef(avg.totalCoef)}
          {avg.value !== null && avg.markedCoef < avg.totalCoef && ` · ${fmtCoef(avg.markedCoef)} noté`}
        </span>
        <span className="ue__avg">
          {fmt(value)}
          {unit.official_average !== null && <span className="pill">officielle</span>}
        </span>
      </div>
      <div className="ue__row ue__row--head" aria-hidden="true">
        <span>Matière</span>
        <span>Coef</span>
        <span>Note /20</span>
        <span />
      </div>
      {unit.subjects.map((s) => (
        <SubjectRow key={s.key} subject={s} sims={sims} onSim={onSim} />
      ))}
    </section>
  );
}

function SubjectRow({ subject, sims, onSim }: { subject: ResultsSubject; sims: Simulations; onSim: (key: string, v: string) => void }) {
  const [open, setOpen] = useState(false);
  const { mark, official, simulated } = subjectMark(subject, sims);
  const hasDetail = subject.assessments.length > 0;
  const computedFromParts = !official && hasDetail && sims[subject.key] === undefined;
  const invalid = (v: string) => v !== "" && (parseMark(v) === null || parseMark(v)! < 0 || parseMark(v)! > 20);

  return (
    <>
      <div className="ue__row">
        <span className="ue__name">
          {subject.title}
          {subject.code && <span className="xsmall muted"> {subject.code}</span>}
        </span>
        <span className="ue__num">{fmtCoef(subject.coefficient)}</span>
        {official || computedFromParts ? (
          <span className={`ue__mark${official ? " ue__mark--official" : " ue__mark--computed"}`} title={official ? "Note officielle Pamplemousse" : "Calculée à partir des épreuves"}>
            {fmt(mark)}
          </span>
        ) : (
          <input
            className={`input ue__input${simulated ? " ue__input--sim" : ""}${invalid(sims[subject.key] ?? "") ? " ue__input--bad" : ""}`}
            inputMode="decimal"
            placeholder="–"
            aria-label={`Note simulée pour ${subject.title}`}
            value={sims[subject.key] ?? ""}
            onChange={(e) => onSim(subject.key, e.target.value)}
          />
        )}
        <span>
          {hasDetail && (
            <button type="button" className={`btn btn--small${open ? " btn--on" : ""}`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
              {open ? "Détail ▾" : "Détailler"}
            </button>
          )}
        </span>
      </div>
      {open && hasDetail && (
        <div className="ue__detail">
          <div className="ue__sub ue__row--head" aria-hidden="true">
            <span>Évaluation</span>
            <span>Note</span>
            <span>Sur</span>
            <span>Coef</span>
          </div>
          {subject.assessments.map((a) => {
            const { mark: am } = assessmentMark(a, sims);
            return (
              <div key={a.key} className="ue__sub">
                <span>{a.title}</span>
                {a.grade !== null ? (
                  <span className="ue__mark ue__mark--official">{fmt(a.grade)}</span>
                ) : (
                  <input
                    className={`input ue__input${am !== null ? " ue__input--sim" : ""}`}
                    inputMode="decimal"
                    placeholder="–"
                    aria-label={`Note simulée pour ${a.title}`}
                    value={sims[a.key] ?? ""}
                    onChange={(e) => onSim(a.key, e.target.value)}
                  />
                )}
                <span className="ue__num">{fmtCoef(a.maximum)}</span>
                <span className="ue__num">{fmtCoef(a.coefficient ?? a.weight)}</span>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

import { describe, expect, it } from "vitest";

import type { ResultsSemester, ResultsSubject } from "../src/api/sources";
import {
  customDetailMarks,
  semesterAverage,
  subjectMark,
  unitAverage,
  withCustomDetails,
  type CustomDetails,
} from "../src/features/grades/average";

const subject = (over: Partial<ResultsSubject>): ResultsSubject => ({
  key: "s",
  code: null,
  title: "Matière",
  coefficient: 1,
  ects: null,
  official_grade: null,
  assessments: [],
  ...over,
});

// UE2-01 of the student's sheet: Data Visualisation (1), Régression (4, QCM/CC/Exam), Séries temporelles (3).
const reg = subject({
  key: "reg",
  title: "Régression linéaire et généralisée",
  coefficient: 4,
  assessments: [
    { key: "qcm", title: "QCM", coefficient: 0.25, weight: null, grade: 3.2, maximum: 5 },
    { key: "cc", title: "Contrôle", coefficient: 0.25, weight: null, grade: 11.28, maximum: 20 },
    { key: "exam", title: "Examen", coefficient: 0.5, weight: null, grade: null, maximum: 20 },
  ],
});
const sem: ResultsSemester = {
  key: "s1",
  title: "S1",
  official_average: null,
  units: [
    {
      key: "ue1",
      coefficient: 8,
      title: "UE2-01",
      official_average: null,
      subjects: [subject({ key: "dv", coefficient: 1 }), reg, subject({ key: "st", coefficient: 3 })],
    },
    { key: "ue2", title: "UE2-02", coefficient: 2.5, official_average: 15.6, official_average_maximum: 20, subjects: [subject({ key: "as", coefficient: 2.5, official_grade: 15.6, official_grade_maximum: 20 })] },
  ],
};

describe("results averages", () => {
  it("computes a subject mark from its marked assessments, normalised to /20", () => {
    // (0.25 × 12.8 + 0.25 × 11.28) / 0.5 = 12.04
    expect(subjectMark(reg, {}).mark).toBeCloseTo(12.04, 2);
  });

  it("does not count subjects without a mark (never as 0)", () => {
    const avg = unitAverage(sem.units[0], {});
    expect(avg.value).toBeCloseTo(12.04, 2);
    expect(avg.markedCoef).toBe(4);
    expect(avg.totalCoef).toBe(8);
  });

  it("uses official marks and lets simulations fill only the gaps", () => {
    const sims = { dv: "16", exam: "14" };
    expect(subjectMark(reg, sims).simulated).toBe(true);
    // Régression = (3.2 + 2.82 + 7) / 1 = 13.02 ; UE = (16×1 + 13.02×4) / 5
    expect(unitAverage(sem.units[0], sims).value).toBeCloseTo((16 + 13.02 * 4) / 5, 2);
    expect(subjectMark(sem.units[1].subjects[0], { as: "2" }).mark).toBe(15.6);
  });

  it("excludes official zero coefficients and preserves unknown scales", () => {
    const unit = {
      ...sem.units[0],
      subjects: [
        subject({ key: "zero", coefficient: 0, official_grade: 20, official_grade_maximum: 20 }),
        subject({ key: "unknown", coefficient: 4, official_grade: 12, official_grade_maximum: null }),
        reg,
      ],
    };
    const avg = unitAverage(unit, {}, { zero: "100" });
    expect(avg.value).toBeCloseTo(12.04, 2);
    expect(avg.markedCoef).toBe(4);
    expect(avg.totalCoef).toBe(8);
  });

  it("propagates coverage through nested assessment groups", () => {
    const nested = subject({
      assessments: [
        { key: "final", title: "Examen", coefficient: 0.5, weight: null, grade: 10, maximum: 20 },
        { key: "cc", title: "Contrôle continu", coefficient: 0.5, weight: null, grade: null, maximum: null, children: [
          { key: "quiz", title: "Quiz", coefficient: 0.5, weight: null, grade: 4, maximum: 5 },
          { key: "midterm", title: "Partiel", coefficient: 0.5, weight: null, grade: null, maximum: 20 },
        ] },
      ],
    });
    expect(subjectMark(nested, {}).mark).toBeCloseTo(12);
  });

  it("weights UE averages using official coefficients and coverage", () => {
    const avg = semesterAverage(sem, {});
    expect(avg.value).toBeCloseTo((12.04 * 4 + 15.6 * 2.5) / 6.5, 2);
  });
});

describe("personal subdivisions (Détailler)", () => {
  // Régression is not on the Pamplemousse bulletin yet: the student types QCM 3,2/5 and Contrôle 11,28/20.
  const bare: ResultsSemester = {
    key: "s1",
    title: "S1",
    official_average: null,
    units: [
      {
        key: "ue1",
        coefficient: 5,
        title: "UE2-01",
        official_average: null,
        subjects: [subject({ key: "reg", coefficient: 4 }), subject({ key: "dv", coefficient: 1, official_grade: 16, official_grade_maximum: 20 })],
      },
    ],
  };
  const details: CustomDetails = {
    reg: [
      { id: "a", title: "QCM (moyenne)", note: "3,2", max: "5", coef: "0,25" },
      { id: "b", title: "Contrôle intermédiaire", note: "11,28", max: "20", coef: "0,25" },
      { id: "c", title: "Examen", note: "", max: "20", coef: "0,5" },
    ],
    dv: [{ id: "x", title: "Ignorée", note: "2", max: "20", coef: "1" }],
  };
  const tree = withCustomDetails(bare, details);
  const marks = customDetailMarks(details);

  it("computes a provisional subject mark from the typed parts, like the student's sheet", () => {
    const reg = subjectMark(tree.units[0].subjects[0], marks);
    expect(reg.mark).toBeCloseTo(12.04, 2);
    expect(reg.simulated).toBe(true);
    expect(reg.official).toBe(false);
  });

  it("never lets personal parts replace an official mark", () => {
    expect(subjectMark(tree.units[0].subjects[1], marks).mark).toBe(16);
  });

  it("feeds the UE average and ignores parts without a coefficient", () => {
    expect(unitAverage(tree.units[0], marks).value).toBeCloseTo((12.04 * 4 + 16) / 5, 2);
    const noCoef = withCustomDetails(bare, { reg: [{ id: "z", title: "Sans coef", note: "18", max: "20", coef: "" }] });
    expect(subjectMark(noCoef.units[0].subjects[0], customDetailMarks({ reg: [{ id: "z", title: "", note: "18", max: "20", coef: "" }] })).mark).toBeNull();
  });
});

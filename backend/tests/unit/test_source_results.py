"""Results tree built from synthetic Pamplemousse records (shape of the P1 connector output)."""

from app.modules.grades.source_results import build_results

Y = "pamplemousse:year:2026_2027"
S1 = "pamplemousse:sem:2026_2027:s1"
UE = "pamplemousse:ue:2026_2027:s1:ue2_01"
REG = "pamplemousse:subject:2026_2027:2asta02:aa"
DATAVIZ = "pamplemousse:subject:2026_2027:2ainf12:bb"
EXAM = "pamplemousse:notes:assessment:2026_2027:ex"

RECORDS = {
    Y: {"kind": "academic_year", "title": "2026-2027", "parent_key": None},
    S1: {"kind": "semester", "title": "S1", "parent_key": Y},
    UE: {"kind": "teaching_unit", "title": "UE2-01 : Modélisation statistique", "parent_key": S1},
    REG: {"kind": "subject", "title": "Régression linéaire et généralisée", "code": "2ASTA02", "ects": "4", "parent_key": UE},
    DATAVIZ: {"kind": "subject", "title": "Data Visualisation", "code": "2AINF12", "ects": "1", "parent_key": UE},
    REG + ":coef": {"kind": "coefficient", "weight": "4", "level": "subject", "parent_key": REG},
    REG + ":coef-empty": {"kind": "coefficient", "weight": None, "level": "subject", "parent_key": REG},
    EXAM: {"kind": "assessment", "title": "- EXAMEN", "parent_key": REG},
    EXAM + ":coef": {"kind": "coefficient", "weight": "0.5", "level": "assessment", "parent_key": EXAM},
    EXAM + ":grade": {"kind": "grade", "value": "11.28", "maximum": "20", "grade_kind": "assessment", "parent_key": EXAM},
    REG + ":final": {"kind": "grade", "value": "12.40", "grade_kind": "official_aggregate", "parent_key": REG},
    S1 + ":avg": {"kind": "grade", "value": "14.50", "grade_kind": "official_aggregate", "parent_key": S1},
    Y + ":avg": {"kind": "grade", "value": "14.50", "grade_kind": "official_aggregate", "parent_key": Y},
}


def test_tree_follows_semester_unit_subject_assessment():
    results = build_results(RECORDS)
    assert results.academic_year == "2026-2027" and results.general_average == 14.5
    (sem,) = results.semesters
    assert sem.title == "S1" and sem.official_average == 14.5
    (unit,) = sem.units
    assert [s.title for s in unit.subjects] == ["Data Visualisation", "Régression linéaire et généralisée"]
    reg = unit.subjects[1]
    assert reg.code == "2ASTA02" and reg.coefficient == 4.0 and reg.official_grade == 12.4
    (exam,) = reg.assessments
    assert exam.title == "Examen" and exam.grade == 11.28 and exam.coefficient == 0.5 and exam.maximum == 20


def test_missing_values_stay_unknown_not_zero():
    dataviz = build_results(RECORDS).semesters[0].units[0].subjects[0]
    assert dataviz.coefficient is None  # no coefficient record: unknown, never 0
    assert dataviz.official_grade is None and dataviz.assessments == []
    assert build_results(RECORDS).semesters[0].units[0].official_average is None

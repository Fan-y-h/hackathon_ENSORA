"""P2 pure-domain tests: T33 calibration/readiness, T47 forecast/allocation, T21 decomposition,
R12 prerequisite ordering in the scheduler."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.modules.grades import forecast
from app.modules.grades.domain import NodeSpec, build_tree
from app.modules.learning import calibration as cal
from app.modules.learning.service import find_cycle
from app.modules.planner.decompose import decompose
from app.modules.planner.domain import Interval
from app.modules.planner.scheduling import Limits, Need, check_invariants, schedule


def test_derived_dimensions_need_delay_and_independence():
    assert cal.derived_dimensions("theory", False, None) == []
    assert cal.derived_dimensions("theory", False, 0.5) == []
    assert cal.derived_dimensions("theory", False, 2) == ["retention"]
    assert cal.derived_dimensions("application", False, 3) == ["retention", "autonomy"]
    assert cal.derived_dimensions("application", True, 3) == []  # assisted: neither


def test_calibration_brier_and_bins():
    perfect = cal.calibration([(1.0, 1.0), (0.0, 0.0)] * 15)
    assert perfect.brier == 0 and perfect.brier_skill == 1 and perfect.sufficient
    naive = cal.calibration([(0.5, 1.0), (0.5, 0.0)])
    assert naive.brier == 0.25 and naive.sufficient is False and naive.bins[0]["range"] == [0.4, 0.6]
    assert cal.calibration([]).samples == 0


def test_readiness_unknown_lowers_coverage_not_score():
    r = cal.readiness([cal.ConceptState("a", 0.8, 4), cal.ConceptState("b", None, 0)])
    assert r["estimate"] == 0.8 and r["coverage"] == 0.5 and r["low"] < 0.4 < r["high"]
    assert cal.readiness([cal.ConceptState("a", None, 0)])["estimate"] is None
    assert cal.exam_frequency([{"a", "b"}, {"a"}]) == {"a": 1.0, "b": 0.5}


def test_forecast_is_deterministic_bounded_and_shrunk():
    tree = build_tree([NodeSpec("ue", None, "unit", "UE"), NodeSpec("A", "ue", "subject", "A", D(4)),
                       NodeSpec("B", "ue", "subject", "B", D(3))])
    model = forecast.fit([13.0])
    assert model.n_marks == 1 and model.calibrated is False and 12 < model.mean < 13
    f1 = forecast.forecast_node(tree, {"A": D(13)}, "ue", D(12), model, 2000, seed=1)
    f2 = forecast.forecast_node(tree, {"A": D(13)}, "ue", D(12), model, 2000, seed=1)
    assert f1 == f2 and 0 < f1.probability < 1
    assert f1.deterministic_min <= f1.p10 <= f1.p50 <= f1.p90 <= f1.deterministic_max
    done = forecast.forecast_node(tree, {"A": D(13), "B": D(10)}, "ue", D(12), model)
    assert done.probability == 0 and done.p50 == pytest.approx(11.7142857, abs=1e-4)
    assert forecast.fit([10.0] * 8).calibrated is True


def test_allocation_concave_and_budget_exact():
    alloc = forecast.allocate({"a": 2.0, "b": 1.0, "c": 0.0}, 300)
    assert sum(alloc.values()) == 300 and alloc["c"] == 0 and alloc["a"] == 240 and alloc["b"] == 60
    base = forecast.proportional({"a": 2.0, "b": 1.0}, 300)
    assert base == {"a": 200, "b": 100}
    assert forecast.allocate({"a": 1.0, "b": 1.0}, 100, floor_min=60)["a"] == 50  # floor dropped if impossible


def test_decompose_structured_and_unstructured_text():
    text = """Projet ML
1. Explorer les données et nettoyer le jeu
2. Entraîner trois modèles
   avec validation croisée
3. Rédiger le rapport final à rendre sur Moodle"""
    steps = decompose(text, total_min=240)
    assert [s.title[:8] for s in steps] == ["Explorer", "Entraîne", "Rédiger "]
    assert steps[1].detail == "avec validation croisée" and steps[2].deliverable and not steps[0].deliverable
    assert 230 <= sum(s.estimated_min for s in steps) <= 250
    free = decompose("Lire le chapitre. Faire les exercices du TD. Préparer une fiche.")
    assert len(free) == 3 and all(s.estimated_min >= 30 for s in free)


def test_prerequisite_cycle_detection():
    assert find_cycle({("a", "b"), ("b", "c")}) is None
    assert find_cycle({("a", "b"), ("b", "c"), ("c", "a")}) is not None


NOW = datetime(2026, 12, 1, 8, 0, tzinfo=UTC)


def test_scheduler_places_prerequisites_first_even_if_lower_priority():
    free = [Interval(NOW + timedelta(hours=h), NOW + timedelta(hours=h + 2)) for h in (1, 25, 49)]
    limits = Limits("Europe/Paris", 240, 1200, 25, 90)
    base = dict(kind="assessment", title="x", activity_type="practice", earliest=None,
                deadline=NOW + timedelta(days=4))
    needs = [Need(key="dep", remaining_min=60, importance=1.0, after=("pre",), **base),
             Need(key="pre", remaining_min=60, importance=0.1, **base)]
    res = schedule(needs, free, [], limits, NOW, Interval(NOW, NOW + timedelta(days=7)))
    assert check_invariants(res, needs, free, [], limits, NOW) == []
    order = [p.need_key for p in res.placed]
    assert order.index("pre") < order.index("dep")
    blocked = [Need(key="dep", remaining_min=60, after=("pre",), **base),
               Need(key="pre", remaining_min=600, splittable=False, **base)]
    res = schedule(blocked, free, [], limits, NOW, Interval(NOW, NOW + timedelta(days=7)))
    assert {u.need_key: u.reason for u in res.unscheduled} == {"pre": "EXCEEDS_MAX_SESSION",
                                                              "dep": "PREREQUISITE_NOT_SCHEDULED"}

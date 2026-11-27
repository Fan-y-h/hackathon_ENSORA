"""P1 pure-domain tests: validation rules (T44) and FSRS (T26)."""

from decimal import Decimal as D

import pytest

from app.modules.grades.domain import NodeSpec, Objective, academic_signals, build_tree
from app.modules.grades.validation_rules import (
    Rule,
    RuleError,
    evaluate,
    subject_risk,
    validate_rules,
)
from app.modules.learning import domain as heuristic
from app.modules.learning import fsrs


def ue():
    return build_tree([
        NodeSpec("ue", None, "unit", "UE"),
        NodeSpec("A", "ue", "subject", "A", D(1), subject_id="a"),
        NodeSpec("B", "ue", "subject", "B", D(1), subject_id="b"),
    ])


RULES = [Rule("compensation", "ue", D(10)), Rule("pass_threshold", "A", D(10)),
         Rule("pass_threshold", "B", D(10)), Rule("eliminatory_minimum", "B", D(6))]


def test_compensation_validates_a_failed_subject():
    ev = evaluate(ue(), {"A": D(14), "B": D(8)}, RULES)
    assert ev["B"].computed_status == "validated_by_compensation"
    assert ev["A"].computed_status == "validated" and ev["ue"].computed_status == "validated"


def test_eliminatory_minimum_blocks_compensation():
    ev = evaluate(ue(), {"A": D(16), "B": D(5)}, RULES)
    assert ev["B"].computed_status == "not_validated" and "ELIMINATED" in ev["B"].reason_codes
    assert ev["ue"].computed_status == "not_validated"  # group mean 10.5 but B is eliminated


def test_pending_outlook_and_risk():
    ev = evaluate(ue(), {"A": D(8)}, RULES)
    assert ev["A"].computed_status == "pending"  # below threshold, but compensation still possible
    assert "BELOW_THRESHOLD" in ev["A"].reason_codes
    assert ev["A"].outlook == "possible" and "COMPENSATION_STILL_POSSIBLE" in ev["A"].reason_codes
    assert ev["B"].computed_status == "pending" and ev["B"].risk in ("medium", "high", "low")
    assert ev["ue"].outlook == "n/a"
    assert subject_risk(ue(), ev, "B") is not None


def test_guaranteed_and_impossible_thresholds():
    tree = build_tree([NodeSpec("s", None, "subject", "S"), NodeSpec("x", "s", "assessment", "X"),
                       NodeSpec("y", "s", "assessment", "Y")])
    assert evaluate(tree, {"x": D(20)}, [Rule("pass_threshold", "s", D(10))])["s"].outlook == "guaranteed"
    assert evaluate(tree, {"x": D(0)}, [Rule("pass_threshold", "s", D(11))])["s"].outlook == "impossible"


def test_invalid_rules():
    with pytest.raises(RuleError) as exc:
        validate_rules(ue(), [Rule("compensation", "A", D(10)), Rule("pass_threshold", "zz", D(10)),
                              Rule("pass_threshold", "ue", D(25)), Rule("bogus", "ue", D(1))])
    codes = {e["code"] for e in exc.value.errors}
    assert {"compensation_on_leaf", "unknown_node", "out_of_scale", "unknown_rule_type"} <= codes


def test_verified_risk_enters_academic_signal_only_when_given():
    tree, marks = ue(), {"A": D(13)}
    plain = {s.node_key: s for s in academic_signals(tree, marks, [])}
    assert plain["B"].score is None  # no objective, no verified risk → baseline
    risky = {s.node_key: s for s in academic_signals(tree, marks, [], {"B": D(1)})}
    assert risky["B"].score == 1 and "VERIFIED_RULE_RISK" in risky["B"].reason_codes
    both = {s.node_key: s for s in academic_signals(tree, marks, [Objective("o", "ue", D(12))], {"B": D(0)})}
    assert D(0) < both["B"].score < 1


# ---- FSRS ---------------------------------------------------------------------------------
def test_fsrs_retention_and_intervals():
    assert fsrs.retrievability(10, 10) == pytest.approx(0.9)
    assert fsrs.interval_days(10, 0.9) == 10
    assert fsrs.interval_days(10, 0.8) > fsrs.interval_days(10, 0.95)
    s = fsrs.init_state("good")
    assert s.stability == pytest.approx(3.7145) and 1 <= s.difficulty <= 10


def test_fsrs_success_grows_and_failure_shrinks_stability():
    s = fsrs.init_state("good")
    good = fsrs.next_state(s, "good", elapsed_days=4)
    again = fsrs.next_state(good, "again", elapsed_days=10)
    easy = fsrs.next_state(s, "easy", elapsed_days=4)
    hard = fsrs.next_state(s, "hard", elapsed_days=4)
    assert good.stability > s.stability and again.stability < good.stability
    assert easy.stability > good.stability > hard.stability
    assert again.difficulty > good.difficulty  # failure makes the card harder


def test_fsrs_vs_heuristic_baseline_sequence():
    """Both schedulers grow intervals on repeated 'good'; FSRS is the documented alternative."""
    state, h_prev, f_int = fsrs.init_state("good"), 0, []
    h_prev = heuristic.next_interval("good", h_prev)
    for _ in range(3):
        f = fsrs.interval_days(state.stability, 0.9)
        f_int.append(f)
        state = fsrs.next_state(state, "good", elapsed_days=f)
        h_prev = heuristic.next_interval("good", h_prev)
    assert f_int == sorted(f_int) and f_int[-1] > f_int[0]
    assert h_prev == 16

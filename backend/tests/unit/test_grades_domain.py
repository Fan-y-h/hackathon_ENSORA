"""Golden tests from docs/TESTING.md § Academic and docs/GRADE_CALCULATOR_DESIGN.md § 4."""

from decimal import Decimal as D

import pytest

from app.modules.grades.domain import (
    NodeSpec,
    Objective,
    TreeError,
    academic_signals,
    build_tree,
    leaf_weights,
    needed_per_leaf,
    q,
    required,
    summarize,
)


def ml_tree():
    specs = [
        NodeSpec("ml", None, "subject", "Machine Learning"),
        NodeSpec("final", "ml", "assessment", "Final", D("0.5")),
        NodeSpec("cc", "ml", "assessment_group", "CC", D("0.5"), weight_mode="fraction"),
        NodeSpec("mid", "cc", "assessment", "Midterm", D("0.5")),
        NodeSpec("quizzes", "cc", "assessment_group", "QCM", D("0.5"), weight_mode="fraction"),
    ]
    specs += [NodeSpec(f"qcm{i}", "quizzes", "assessment", f"QCM {i}", D("0.125")) for i in range(1, 9)]
    return build_tree(specs)


def ue_tree():
    return build_tree(
        [
            NodeSpec("ue", None, "unit", "UE"),
            NodeSpec("A", "ue", "subject", "A", D(4), subject_id="subj-a"),
            NodeSpec("B", "ue", "subject", "B", D(3), subject_id="subj-b"),
        ]
    )


def test_nested_weights_qcm_is_3_125_percent():
    w = leaf_weights(ml_tree(), "ml")
    assert all(w[f"qcm{i}"] == D("0.03125") for i in range(1, 9))
    assert w["final"] == D("0.5") and w["mid"] == D("0.25")
    assert sum(w.values()) == 1


def test_weighted_mean_and_required_grade():
    tree = ue_tree()
    s = summarize(tree, {"A": D(13), "B": D(10)}, "ue")
    assert q(s.final) == D("11.7142857")
    r = required(tree, {"A": D(13)}, Objective("o", "ue", D(12)))
    assert q(r.required_mean) == D("10.6666667") and r.feasibility == "feasible"


def test_missing_is_not_zero():
    tree = ue_tree()
    missing = summarize(tree, {"A": D(13)}, "ue")
    assert missing.provisional == 13 and q(missing.coverage) == q(D(4) / D(7))
    assert missing.final is None
    zero = summarize(tree, {"A": D(13), "B": D(0)}, "ue")
    assert q(zero.final) == q(D(52) / D(7))


def test_subdivided_subject_example_b():
    tree = ml_tree()
    marks = {"mid": D(11), "qcm1": D(14), "qcm2": D(16), "qcm3": D(12), "qcm4": D(18)}
    s = summarize(tree, marks, "ml")
    assert s.coverage == D("0.375") and s.earned == D("4.625")
    assert q(s.provisional) == D("12.3333333")
    r = required(tree, marks, Objective("o", "ml", D(12)))
    assert q(r.required_mean) == D("11.8")
    with_assumption = required(
        tree, marks, Objective("o", "ml", D(12)), {f"qcm{i}": D(15) for i in range(5, 9)}
    )
    assert q(with_assumption.required_mean) == D("11")
    assert "ASSUMPTIONS_APPLIED" in with_assumption.reason_codes


def test_feasibility_edge_cases():
    tree = ue_tree()
    assert required(tree, {"A": D(20)}, Objective("o", "ue", D(5))).feasibility == "guaranteed"
    assert required(tree, {"A": D(0)}, Objective("o", "ue", D(19))).feasibility == "impossible"
    done = required(tree, {"A": D(13), "B": D(10)}, Objective("o", "ue", D(11)))
    assert done.feasibility == "achieved" and done.required_mean is None
    failed = required(tree, {"A": D(13), "B": D(10)}, Objective("o", "ue", D(12)))
    assert failed.feasibility == "impossible" and "NO_REMAINING_WEIGHT" in failed.reason_codes


def test_scales_are_converted_to_twenty():
    tree = build_tree(
        [
            NodeSpec("s", None, "subject", "S"),
            NodeSpec("a", "s", "assessment", "A", scale_max=D(10)),
            NodeSpec("b", "s", "assessment", "B", scale_max=D(100)),
        ]
    )
    assert summarize(tree, {"a": D(5), "b": D(75)}, "s").final == D("12.5")


def test_multiple_objectives_needed_per_leaf_uses_max():
    tree = build_tree(
        [
            NodeSpec("sem", None, "semester", "S5"),
            NodeSpec("ue1", "sem", "unit", "UE1", D(5)),
            NodeSpec("ue2", "sem", "unit", "UE2", D(4)),
            NodeSpec("x", "ue1", "subject", "X"),
            NodeSpec("y", "ue2", "subject", "Y"),
        ]
    )
    actual: dict = {}
    reqs = [
        required(tree, actual, Objective("u1", "ue1", D(12))),
        required(tree, actual, Objective("u2", "ue2", D(12))),
        required(tree, actual, Objective("sem", "sem", D(13))),
    ]
    needs = {n.leaf_key: n for n in needed_per_leaf(tree, actual, reqs)}
    assert needs["x"].needed == 13 and needs["x"].objective_ids == ("sem",)


@pytest.mark.parametrize(
    "specs, code",
    [
        ([NodeSpec("a", None, "unit", "A"), NodeSpec("b", None, "unit", "B")], "root_count"),
        ([NodeSpec("a", None, "unit", "A"), NodeSpec("b", "zz", "subject", "B")], "missing_parent"),
        ([NodeSpec("a", None, "unit", "A"), NodeSpec("a", None, "unit", "A2")], "duplicate_key"),
        ([NodeSpec("a", None, "unit", "A"), NodeSpec("b", "a", "subject", "B", D(-1))], "non_positive_weight"),
        (
            [
                NodeSpec("a", None, "unit", "A", weight_mode="fraction"),
                NodeSpec("b", "a", "subject", "B", D("0.5")),
                NodeSpec("c", "a", "subject", "C", D("0.4")),
            ],
            "fraction_sum",
        ),
        (
            [
                NodeSpec("r", None, "unit", "R"),
                NodeSpec("a", "b", "subject", "A"),
                NodeSpec("b", "a", "subject", "B"),
            ],
            "cycle_or_orphan",
        ),
        ([NodeSpec("a", None, "unit", "A", scale_min=D(20), scale_max=D(10))], "invalid_scale"),
    ],
)
def test_invalid_trees_are_rejected(specs, code):
    with pytest.raises(TreeError) as exc:
        build_tree(specs)
    assert code in {e["code"] for e in exc.value.errors}


def test_academic_signals_require_objective_and_rank_gap():
    tree = ue_tree()
    no_goal = academic_signals(tree, {"A": D(13)}, [])
    assert all(s.score is None and "NO_OBJECTIVE" in s.reason_codes for s in no_goal)
    sig = {s.node_key: s for s in academic_signals(tree, {"A": D(13)}, [Objective("o", "ue", D(12))])}
    assert sig["A"].score == 0 and "FULLY_GRADED" in sig["A"].reason_codes
    assert sig["B"].score == 1 and sig["B"].subject_id == "subj-b"
    assert q(sig["B"].required_mean) == D("10.6666667")

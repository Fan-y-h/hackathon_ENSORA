"""Versioned validation / compensation / eliminatory rules (R40, T44) — pure Decimal logic.

Rules are data supplied by the student (or later an authorized source) with provenance and a
verification status. They are evaluated deterministically under the weighted-average model;
``official_status`` is only reported when the rule set is *verified*, otherwise ``unknown``
(GRADE_TRACKER §4: unsupported or unverified rules never silently pass).

Supported rule types:
* ``pass_threshold``   — node validated when its mean ≥ threshold.
* ``eliminatory_minimum`` — node mean < minimum ⇒ not validated and cannot be compensated.
* ``compensation``     — inside group G, a child below its own threshold is validated by
  compensation when G's mean ≥ G threshold and no child of G is eliminated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.modules.grades.domain import FRACTION_TOLERANCE, CurriculumTree, summarize

RULE_TYPES = ("pass_threshold", "eliminatory_minimum", "compensation")


@dataclass(frozen=True)
class Rule:
    type: str
    node_key: str
    value: Decimal  # threshold or minimum, /20


class RuleError(ValueError):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("invalid rules")
        self.errors = errors


def validate_rules(tree: CurriculumTree, rules: Sequence[Rule]) -> None:
    errors = []
    seen: set[tuple[str, str]] = set()
    for i, r in enumerate(rules):
        if r.type not in RULE_TYPES:
            errors.append({"index": str(i), "code": "unknown_rule_type", "message": f"type must be one of {RULE_TYPES}"})
        if r.node_key not in tree.nodes:
            errors.append({"index": str(i), "code": "unknown_node", "message": f"unknown node {r.node_key!r}"})
        elif r.type == "compensation" and tree.is_leaf(r.node_key):
            errors.append({"index": str(i), "code": "compensation_on_leaf", "message": "compensation needs a group"})
        if not Decimal(0) <= r.value <= Decimal(20):
            errors.append({"index": str(i), "code": "out_of_scale", "message": "value must be within [0, 20]"})
        if (r.type, r.node_key) in seen:
            errors.append({"index": str(i), "code": "duplicate_rule", "message": "one rule per type and node"})
        seen.add((r.type, r.node_key))
    if errors:
        raise RuleError(errors)


@dataclass(frozen=True)
class NodeEvaluation:
    node_key: str
    computed_status: str  # validated | validated_by_compensation | not_validated | pending
    outlook: str  # guaranteed | possible | impossible | decided | n/a
    risk: str  # none | low | medium | high
    mean: Decimal | None
    bound_min: Decimal
    bound_max: Decimal
    reason_codes: tuple[str, ...] = field(default_factory=tuple)


def evaluate(
    tree: CurriculumTree, actual: Mapping[str, Decimal], rules: Sequence[Rule]
) -> dict[str, NodeEvaluation]:
    """Evaluate every node that carries a rule (and the children of compensation groups)."""
    by_node: dict[str, dict[str, Decimal]] = {}
    for r in rules:
        by_node.setdefault(r.node_key, {})[r.type] = r.value
    comp_children = {c: g for g, t in by_node.items() if "compensation" in t for c in tree.children[g]}
    targets = set(by_node) | set(comp_children)

    def raw(key: str) -> NodeEvaluation:
        s = summarize(tree, actual, key)
        decided = s.remaining_weight <= FRACTION_TOLERANCE
        mean = s.final if decided else s.provisional
        reasons: list[str] = []
        rules_here = by_node.get(key, {})
        threshold = rules_here.get("pass_threshold")
        minimum = rules_here.get("eliminatory_minimum")
        status, outlook, risk = "pending", "n/a", "none"
        if minimum is not None:
            if decided and s.final is not None and s.final < minimum:
                return NodeEvaluation(key, "not_validated", "decided", "high", mean, s.bound_min, s.bound_max,
                                      ("ELIMINATED",))
            if not decided and s.bound_max < minimum:
                reasons.append("ELIMINATION_CERTAIN")
                risk = "high"
            elif not decided and s.bound_min < minimum:
                reasons.append("ELIMINATION_POSSIBLE")
                risk = "high" if (s.provisional is not None and s.provisional < minimum) else "medium"
        if threshold is not None:
            if decided and s.final is not None:
                status = "validated" if s.final >= threshold else "not_validated"
                outlook = "decided"
                if status == "not_validated":
                    reasons.append("BELOW_THRESHOLD")
                    risk = "high"
            else:
                if s.bound_min >= threshold:
                    outlook = "guaranteed"
                elif s.bound_max < threshold:
                    outlook, risk = "impossible", "high"
                    reasons.append("THRESHOLD_UNREACHABLE")
                else:
                    outlook = "possible"
                    below = s.provisional is not None and s.provisional < threshold
                    risk = max(risk, "high" if below else "low", key=_RISK_ORDER.index)
                    reasons.append("CURRENTLY_BELOW_THRESHOLD" if below else "THRESHOLD_NOT_SECURED")
        elif minimum is not None and decided:
            status, outlook = "validated", "decided"  # only an eliminatory rule, and it is met
        return NodeEvaluation(key, status, outlook, risk, mean, s.bound_min, s.bound_max, tuple(reasons))

    evals = {k: raw(k) for k in targets}

    # Compensation: a failed child is rescued by its group unless any sibling is eliminated.
    for group, kinds in by_node.items():
        if "compensation" not in kinds:
            continue
        g_threshold = kinds["compensation"]
        g = summarize(tree, actual, group)
        g_decided = g.remaining_weight <= FRACTION_TOLERANCE
        children = tree.children[group]
        eliminated = [c for c in children if "ELIMINATED" in evals[c].reason_codes]
        for c in children:
            e = evals[c]
            if e.computed_status != "not_validated" or "ELIMINATED" in e.reason_codes:
                continue
            if eliminated:
                evals[c] = _with(e, reasons=(*e.reason_codes, "COMPENSATION_BLOCKED_BY_ELIMINATORY"))
            elif g_decided and g.final is not None and g.final >= g_threshold:
                evals[c] = _with(e, status="validated_by_compensation", risk="low",
                                 reasons=(*e.reason_codes, "VALIDATED_BY_COMPENSATION"))
            elif not g_decided:
                evals[c] = _with(e, status="pending", outlook="possible",
                                 reasons=(*e.reason_codes, "COMPENSATION_STILL_POSSIBLE"))
        if group not in by_node or "pass_threshold" not in by_node[group]:
            ge = evals.get(group)
            if ge is not None and ge.computed_status == "pending" and g_decided and g.final is not None:
                ok = g.final >= g_threshold and not eliminated
                evals[group] = _with(ge, status="validated" if ok else "not_validated", outlook="decided",
                                     risk="none" if ok else "high",
                                     reasons=(*ge.reason_codes, *(() if ok else ("GROUP_BELOW_COMPENSATION",))))
    return evals


_RISK_ORDER = ["none", "low", "medium", "high"]
RISK_SCORE = {"none": Decimal(0), "low": Decimal("0.25"), "medium": Decimal("0.5"), "high": Decimal(1)}


def _with(e: NodeEvaluation, status=None, outlook=None, risk=None, reasons=None) -> NodeEvaluation:
    return NodeEvaluation(
        e.node_key,
        status or e.computed_status,
        outlook or e.outlook,
        risk or e.risk,
        e.mean,
        e.bound_min,
        e.bound_max,
        reasons if reasons is not None else e.reason_codes,
    )


def subject_risk(tree: CurriculumTree, evals: Mapping[str, NodeEvaluation], subject_key: str) -> Decimal | None:
    """Highest risk among the subject itself, its descendants and its ancestors with rules."""
    related = {subject_key, *tree.ancestors(subject_key)}
    stack = [subject_key]
    while stack:
        k = stack.pop()
        related.add(k)
        stack.extend(tree.children.get(k, []))
    risks = [RISK_SCORE[evals[k].risk] for k in related if k in evals]
    return max(risks) if risks else None

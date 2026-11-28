"""Deterministic grade arithmetic (docs/GRADE_CALCULATOR_DESIGN.md, docs/GRADE_TRACKER.md).

Pure Python + exact rational arithmetic (``Fraction`` internally, ``Decimal`` at boundaries).
A missing mark is absence (not 0); 0 is a real mark; an unjustified absence counts as 0 and is
explicitly flagged; a dispensed/excused leaf is excluded from the denominator so its weight is
redistributed across remaining siblings; an unknown coefficient (``weight=None``) is NEVER
silently replaced by 0 or 1. Aggregation is always from leaves with coverage, never by averaging
child means. All aggregated values are on a /20 scale.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from fractions import Fraction

ZERO, ONE, TWENTY = Decimal(0), Decimal(1), Decimal(20)
F_ZERO, F_ONE, F_TWENTY = Fraction(0), Fraction(1), Fraction(20)
QUANT = Decimal("0.0000001")
FRACTION_TOLERANCE = Decimal("1e-9")
F_TOLERANCE = Fraction(1, 10**9)
MAX_DEPTH = 8
KINDS = ("year", "semester", "unit", "subject", "assessment_group", "assessment")
WEIGHT_MODES = ("coefficient", "fraction")
WEIGHT_PROVENANCES = ("official", "manual", "unknown")

# Academic signal blend (GRADE_TRACKER §5 recommendation, uncalibrated assumption).
IMPACT_WEIGHT = Decimal("0.35")
GAP_WEIGHT = Decimal("0.35")
RISK_WEIGHT = Decimal("0.30")


def q(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(QUANT)


def _to_frac(d: Decimal) -> Fraction:
    return Fraction(d)


def _to_dec(f: Fraction) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 34
        return Decimal(f.numerator) / Decimal(f.denominator)


@dataclass(frozen=True)
class NodeSpec:
    key: str
    parent_key: str | None
    kind: str
    title: str
    weight: Decimal | None = ONE
    weight_mode: str = "coefficient"  # how THIS node's children weights are interpreted
    scale_min: Decimal = ZERO
    scale_max: Decimal = TWENTY
    subject_id: str | None = None
    event_id: str | None = None
    ects: Decimal | None = None
    weight_provenance: str = "manual"  # official | manual | unknown


class TreeError(ValueError):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("invalid curriculum")
        self.errors = errors


@dataclass
class CurriculumTree:
    nodes: dict[str, NodeSpec]
    children: dict[str, list[str]]
    root: str

    def is_leaf(self, key: str) -> bool:
        return not self.children.get(key)

    def leaves_under(self, key: str) -> list[str]:
        if self.is_leaf(key):
            return [key]
        out: list[str] = []
        for c in self.children[key]:
            out.extend(self.leaves_under(c))
        return out

    def ancestors(self, key: str) -> list[str]:
        out, cur = [], self.nodes[key].parent_key
        while cur is not None:
            out.append(cur)
            cur = self.nodes[cur].parent_key
        return out

    def walk(self) -> list[str]:
        order, stack = [], [self.root]
        while stack:
            k = stack.pop()
            order.append(k)
            stack.extend(reversed(self.children.get(k, [])))
        return order


def build_tree(specs: Sequence[NodeSpec]) -> CurriculumTree:
    errors: list[dict[str, str]] = []

    def err(key: str | None, code: str, msg: str) -> None:
        errors.append({"key": key or "", "code": code, "message": msg})

    nodes: dict[str, NodeSpec] = {}
    for s in specs:
        if s.key in nodes:
            err(s.key, "duplicate_key", "duplicate node key")
        nodes[s.key] = s
        if s.kind not in KINDS:
            err(s.key, "invalid_kind", f"kind must be one of {KINDS}")
        if s.weight_mode not in WEIGHT_MODES:
            err(s.key, "invalid_weight_mode", "weight_mode must be coefficient or fraction")
        if s.parent_key is not None and s.weight is not None and s.weight < 0:
            err(s.key, "non_positive_weight", "weights must be non-negative")
        if s.ects is not None and s.ects < 0:
            err(s.key, "invalid_ects", "ects must be >= 0")
        if s.scale_min >= s.scale_max:
            err(s.key, "invalid_scale", "scale_min must be < scale_max")
    roots = [s.key for s in specs if s.parent_key is None]
    if len(roots) != 1:
        err(None, "root_count", f"exactly one root required, found {len(roots)}")
    children: dict[str, list[str]] = {k: [] for k in nodes}
    for s in specs:
        if s.parent_key is not None:
            if s.parent_key not in nodes:
                err(s.key, "missing_parent", f"unknown parent {s.parent_key!r}")
            else:
                children[s.parent_key].append(s.key)
    if errors:
        raise TreeError(errors)

    # Cycles / orphans: everything must be reachable from the root, depth bounded.
    root = roots[0]
    seen: set[str] = set()
    stack = [(root, 0)]
    while stack:
        key, depth = stack.pop()
        if key in seen:
            err(key, "cycle", "cycle detected")
            continue
        seen.add(key)
        if depth > MAX_DEPTH:
            err(key, "too_deep", f"maximum depth is {MAX_DEPTH}")
        stack.extend((c, depth + 1) for c in children[key])
    for key in nodes:
        if key not in seen:
            err(key, "cycle_or_orphan", "node not reachable from the root")

    for key, kids in children.items():
        if kids and nodes[key].weight_mode == "fraction":
            known_weights = [nodes[c].weight for c in kids if nodes[c].weight is not None]
            if len(known_weights) == len(kids):
                total = sum((_to_frac(w) for w in known_weights), F_ZERO)
                if abs(total - F_ONE) > F_TOLERANCE:
                    err(key, "fraction_sum", f"child fractions sum to {_to_dec(total)}, expected 1")
            else:
                partial = sum((_to_frac(w) for w in known_weights), F_ZERO)
                if partial >= F_ONE:
                    err(key, "fraction_sum", f"known child fractions already sum to {_to_dec(partial)} >= 1")
    if errors:
        raise TreeError(errors)
    return CurriculumTree(nodes=nodes, children=children, root=root)


def _is_fully_dispensed(tree: CurriculumTree, key: str, dispensed: AbstractSet[str]) -> bool:
    if not dispensed:
        return False
    leaves = tree.leaves_under(key)
    return bool(leaves) and all(leaf in dispensed for leaf in leaves)


def leaf_weights_exact(
    tree: CurriculumTree,
    key: str,
    dispensed: AbstractSet[str] = frozenset(),
) -> tuple[dict[str, Fraction], list[str]]:
    """Exact rational leaf weights under ``key``, plus any descendant keys with unknown weight.

    Dispensed leaves are excluded from sibling denominators so their weight is redistributed
    proportionally across remaining non-dispensed siblings. Unknown weights (``weight=None``)
    are NEVER replaced by 0 or 1; when present, the affected parent cannot normalize weights.
    """
    out: dict[str, Fraction] = {}
    unknown: list[str] = []

    def visit(k: str, acc: Fraction) -> None:
        kids = tree.children.get(k, [])
        if not kids:
            if k not in dispensed:
                out[k] = acc
            return
        active_kids = [c for c in kids if tree.nodes[c].weight != ZERO and not _is_fully_dispensed(tree, c, dispensed)]
        if not active_kids:
            return
        missing = [c for c in active_kids if tree.nodes[c].weight is None]
        if missing:
            unknown.extend(missing)
            # Still walk descendants to report every unknown coefficient in the subtree.
            for c in active_kids:
                _collect_unknown(c)
            return
        total = sum((_to_frac(tree.nodes[c].weight) for c in active_kids), F_ZERO)  # type: ignore[arg-type]
        for c in active_kids:
            visit(c, acc * _to_frac(tree.nodes[c].weight) / total)  # type: ignore[arg-type]

    def _collect_unknown(k: str) -> None:
        kids = tree.children.get(k, [])
        active_kids = [c for c in kids if tree.nodes[c].weight != ZERO and not _is_fully_dispensed(tree, c, dispensed)]
        for c in active_kids:
            if tree.nodes[c].weight is None and c not in unknown:
                unknown.append(c)
            _collect_unknown(c)

    visit(key, F_ONE)
    if unknown:
        return {}, list(dict.fromkeys(unknown))
    return out, []


def leaf_weights(
    tree: CurriculumTree,
    key: str,
    dispensed: AbstractSet[str] = frozenset(),
) -> dict[str, Decimal]:
    """a_i = product of normalized sibling weights from ``key`` down to leaf i (Σ a_i = 1)."""
    exact, _ = leaf_weights_exact(tree, key, dispensed)
    return {k: _to_dec(v) for k, v in exact.items()}


def _to_twenty_frac(spec: NodeSpec, value: Decimal) -> Fraction:
    return (_to_frac(value) - _to_frac(spec.scale_min)) / (
        _to_frac(spec.scale_max) - _to_frac(spec.scale_min)
    ) * F_TWENTY


def to_twenty(spec: NodeSpec, value: Decimal) -> Decimal:
    return _to_dec(_to_twenty_frac(spec, value))


def check_mark(spec: NodeSpec, value: Decimal) -> str | None:
    if not (spec.scale_min <= value <= spec.scale_max):
        return f"mark must be within [{spec.scale_min}, {spec.scale_max}]"
    return None


@dataclass(frozen=True)
class NodeSummary:
    key: str
    coverage: Decimal
    earned: Decimal
    provisional: Decimal | None
    final: Decimal | None
    remaining_weight: Decimal
    bound_min: Decimal
    bound_max: Decimal
    forecast: Decimal | None
    forecast_coverage: Decimal
    weight_complete: bool = True
    unknown_weight_keys: tuple[str, ...] = ()


def summarize(
    tree: CurriculumTree,
    actual: Mapping[str, Decimal],
    node_key: str,
    estimated: Mapping[str, Decimal] | None = None,
    dispensed: AbstractSet[str] = frozenset(),
) -> NodeSummary:
    """Marks are on each leaf's own scale; results are /20, computed with exact rationals."""
    estimated = estimated or {}
    weights, unknown = leaf_weights_exact(tree, node_key, dispensed)
    if unknown:
        return NodeSummary(
            key=node_key,
            coverage=ZERO,
            earned=ZERO,
            provisional=None,
            final=None,
            remaining_weight=ONE,
            bound_min=ZERO,
            bound_max=TWENTY,
            forecast=None,
            forecast_coverage=ZERO,
            weight_complete=False,
            unknown_weight_keys=tuple(unknown),
        )
    if not weights:
        # All leaves under this node are dispensed.
        return NodeSummary(
            key=node_key,
            coverage=ONE,
            earned=ZERO,
            provisional=None,
            final=None,
            remaining_weight=ZERO,
            bound_min=ZERO,
            bound_max=ZERO,
            forecast=None,
            forecast_coverage=ONE,
            weight_complete=True,
            unknown_weight_keys=(),
        )
    coverage = earned = f_cov = f_earned = F_ZERO
    for leaf, a in weights.items():
        spec = tree.nodes[leaf]
        if leaf in actual:
            x = _to_twenty_frac(spec, actual[leaf])
            coverage += a
            earned += a * x
            f_cov += a
            f_earned += a * x
        elif leaf in estimated:
            f_cov += a
            f_earned += a * _to_twenty_frac(spec, estimated[leaf])
    remaining = F_ONE - coverage
    full = abs(remaining) <= F_TOLERANCE
    rem_eff = F_ZERO if full else remaining
    return NodeSummary(
        key=node_key,
        coverage=_to_dec(coverage),
        earned=_to_dec(earned),
        provisional=_to_dec(earned / coverage) if coverage > 0 else None,
        final=_to_dec(earned) if full else None,
        remaining_weight=_to_dec(rem_eff),
        bound_min=_to_dec(earned),
        bound_max=_to_dec(earned + F_TWENTY * rem_eff),
        forecast=_to_dec(f_earned) if abs(F_ONE - f_cov) <= F_TOLERANCE else None,
        forecast_coverage=_to_dec(f_cov),
        weight_complete=True,
        unknown_weight_keys=(),
    )


@dataclass(frozen=True)
class Objective:
    id: str
    node_key: str
    target: Decimal  # /20


@dataclass(frozen=True)
class Requirement:
    objective_id: str
    node_key: str
    target: Decimal
    required_mean: Decimal | None  # unclamped, /20, over the still-free leaves
    feasibility: str  # feasible | guaranteed | achieved | impossible | unknown
    bound_min: Decimal
    bound_max: Decimal
    remaining_weight: Decimal
    reason_codes: tuple[str, ...] = ()


def required(
    tree: CurriculumTree,
    actual: Mapping[str, Decimal],
    objective: Objective,
    assumptions: Mapping[str, Decimal] | None = None,
    dispensed: AbstractSet[str] = frozenset(),
) -> Requirement:
    """r = (T − S) / Σ_R a_i, exact rational arithmetic with assumptions treated as known."""
    assumptions = assumptions or {}
    reasons: list[str] = []
    weights, unknown = leaf_weights_exact(tree, objective.node_key, dispensed)
    if unknown:
        return Requirement(
            objective_id=objective.id,
            node_key=objective.node_key,
            target=objective.target,
            required_mean=None,
            feasibility="unknown",
            bound_min=ZERO,
            bound_max=TWENTY,
            remaining_weight=ONE,
            reason_codes=("MISSING_COEFFICIENT",),
        )
    if not weights:
        return Requirement(
            objective_id=objective.id,
            node_key=objective.node_key,
            target=objective.target,
            required_mean=None,
            feasibility="unknown",
            bound_min=ZERO,
            bound_max=ZERO,
            remaining_weight=ZERO,
            reason_codes=("ALL_DISPENSED",),
        )
    target_f = _to_frac(objective.target)
    earned = remaining = F_ZERO
    for leaf, a in weights.items():
        spec = tree.nodes[leaf]
        if leaf in actual:
            earned += a * _to_twenty_frac(spec, actual[leaf])
        elif leaf in assumptions:
            earned += a * _to_twenty_frac(spec, assumptions[leaf])
            if "ASSUMPTIONS_APPLIED" not in reasons:
                reasons.append("ASSUMPTIONS_APPLIED")
        else:
            remaining += a
    if remaining <= F_TOLERANCE:
        feas = "achieved" if earned >= target_f else "impossible"
        reasons.append("NO_REMAINING_WEIGHT")
        earned_d = _to_dec(earned)
        return Requirement(
            objective.id, objective.node_key, objective.target, None, feas, earned_d, earned_d, ZERO, tuple(reasons)
        )
    r = (target_f - earned) / remaining
    if r <= F_ZERO:
        feas = "guaranteed"
    elif r > F_TWENTY:
        feas = "impossible"
        reasons.append("ABOVE_SCALE_MAXIMUM")
    else:
        feas = "feasible"
    return Requirement(
        objective_id=objective.id,
        node_key=objective.node_key,
        target=objective.target,
        required_mean=_to_dec(r),
        feasibility=feas,
        bound_min=_to_dec(earned),
        bound_max=_to_dec(earned + F_TWENTY * remaining),
        remaining_weight=_to_dec(remaining),
        reason_codes=tuple(reasons),
    )


@dataclass(frozen=True)
class LeafNeed:
    leaf_key: str
    needed: Decimal  # sufficient uniform plan: max r over the objectives above this leaf
    objective_ids: tuple[str, ...]


def needed_per_leaf(
    tree: CurriculumTree,
    actual: Mapping[str, Decimal],
    requirements: Sequence[Requirement],
    assumptions: Mapping[str, Decimal] | None = None,
    dispensed: AbstractSet[str] = frozenset(),
) -> list[LeafNeed]:
    assumptions = assumptions or {}
    per_leaf: dict[str, tuple[Decimal, list[str]]] = {}
    for req in requirements:
        if req.required_mean is None:
            continue
        for leaf in tree.leaves_under(req.node_key):
            if leaf in actual or leaf in assumptions or leaf in dispensed:
                continue
            cur = per_leaf.get(leaf)
            if cur is None or req.required_mean > cur[0]:
                per_leaf[leaf] = (req.required_mean, [req.objective_id])
            elif req.required_mean == cur[0]:
                cur[1].append(req.objective_id)
    return [LeafNeed(k, max(ZERO, v[0]), tuple(v[1])) for k, v in sorted(per_leaf.items())]


@dataclass(frozen=True)
class AcademicSignal:
    node_key: str
    subject_id: str | None
    effective_weight: Decimal
    remaining_weight: Decimal
    coverage: Decimal
    provisional: Decimal | None
    target: Decimal | None
    required_mean: Decimal | None
    feasibility: str
    score: Decimal | None  # None = insufficient inputs → planner keeps baseline priority
    reason_codes: tuple[str, ...] = field(default_factory=tuple)


def academic_signals(
    tree: CurriculumTree,
    actual: Mapping[str, Decimal],
    objectives: Sequence[Objective],
    verified_risk: Mapping[str, Decimal] | None = None,
    dispensed: AbstractSet[str] = frozenset(),
) -> list[AcademicSignal]:
    """One signal per subject node: impact of remaining weight + normalized target gap
    (+ verified rule risk when a *verified* rule set exists; unknown risk is omitted)."""
    verified_risk = verified_risk or {}
    root_weights_f, root_unknown = leaf_weights_exact(tree, tree.root, dispensed)
    root_weights = {k: _to_dec(v) for k, v in root_weights_f.items()}
    subjects = [k for k in tree.walk() if tree.nodes[k].kind == "subject"]
    by_node: dict[str, list[Objective]] = {}
    for o in objectives:
        by_node.setdefault(o.node_key, []).append(o)
    raw = []
    for s in subjects:
        leaves = [leaf for leaf in tree.leaves_under(s) if leaf not in dispensed]
        eff = sum((root_weights[leaf] for leaf in leaves if leaf in root_weights), ZERO)
        rem = sum((root_weights[leaf] for leaf in leaves if leaf not in actual and leaf in root_weights), ZERO)
        summ = summarize(tree, actual, s, dispensed=dispensed)
        applicable = [o for k in [s, *tree.ancestors(s)] for o in by_node.get(k, [])]
        reqs = [required(tree, actual, o, dispensed=dispensed) for o in applicable]
        reqs_r = [r for r in reqs if r.required_mean is not None]
        worst = max(reqs_r, key=lambda r: r.required_mean) if reqs_r else None  # type: ignore[arg-type, return-value]
        reasons: list[str] = []
        if not summ.weight_complete or root_unknown:
            reasons.append("MISSING_COEFFICIENT")
        if not applicable:
            reasons.append("NO_OBJECTIVE")
        if summ.weight_complete and summ.remaining_weight <= FRACTION_TOLERANCE:
            reasons.append("FULLY_GRADED")
        if worst is not None and worst.feasibility == "impossible":
            reasons.append("TARGET_IMPOSSIBLE")
        if worst is not None and worst.feasibility == "guaranteed":
            reasons.append("TARGET_GUARANTEED")
        gap = None
        if worst is not None and worst.required_mean is not None:
            if worst.feasibility == "guaranteed":
                gap = ZERO
            elif summ.provisional is not None:
                gap = max(ZERO, worst.required_mean - summ.provisional)
            else:
                gap = max(ZERO, worst.required_mean)
        # If root had unknown coefficients elsewhere, fall back to subject's own remaining_weight for local signal.
        local_rem = rem if not root_unknown else (summ.remaining_weight if summ.weight_complete else ZERO)
        raw.append((s, eff, local_rem, summ, worst, gap, reasons))

    max_rem = max((r[2] for r in raw), default=ZERO)
    max_gap = max((r[5] for r in raw if r[5] is not None), default=ZERO)
    out = []
    for s, eff, rem, summ, worst, gap, reasons in raw:
        score = None
        risk = verified_risk.get(s)
        if risk is not None:
            reasons.append("VERIFIED_RULE_RISK")
        if (worst is not None or risk is not None) and rem > FRACTION_TOLERANCE:
            ni = rem / max_rem if max_rem > 0 else ZERO
            num, den = IMPACT_WEIGHT * ni, IMPACT_WEIGHT
            if worst is not None:
                ng = min(ONE, gap / max_gap) if gap is not None and max_gap > 0 else ZERO
                num, den = num + GAP_WEIGHT * ng, den + GAP_WEIGHT
            if risk is not None:
                num, den = num + RISK_WEIGHT * risk, den + RISK_WEIGHT
            score = num / den
        elif worst is not None or risk is not None:
            score = ZERO
        out.append(
            AcademicSignal(
                node_key=s,
                subject_id=tree.nodes[s].subject_id,
                effective_weight=eff,
                remaining_weight=rem,
                coverage=summ.coverage,
                provisional=summ.provisional,
                target=worst.target if worst else None,
                required_mean=worst.required_mean if worst else None,
                feasibility=worst.feasibility if worst else "unknown",
                score=score,
                reason_codes=tuple(dict.fromkeys(reasons)),
            )
        )
    return out

"""Deterministic grade arithmetic (docs/GRADE_CALCULATOR_DESIGN.md, docs/GRADE_TRACKER.md).

Pure Python + Decimal. A missing mark is absence (not 0); 0 is a real mark. Aggregation is
always from leaves with coverage, never by averaging child means. All aggregated values are
on a /20 scale. Official validation rules are not modelled here (status stays ``unknown``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, localcontext

ZERO, ONE, TWENTY = Decimal(0), Decimal(1), Decimal(20)
QUANT = Decimal("0.0000001")
FRACTION_TOLERANCE = Decimal("1e-9")
MAX_DEPTH = 8
KINDS = ("year", "semester", "unit", "subject", "assessment_group", "assessment")
WEIGHT_MODES = ("coefficient", "fraction")

# Academic signal blend (GRADE_TRACKER §5 recommendation, uncalibrated assumption).
IMPACT_WEIGHT = Decimal("0.35")
GAP_WEIGHT = Decimal("0.35")
RISK_WEIGHT = Decimal("0.30")


def q(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(QUANT)


@dataclass(frozen=True)
class NodeSpec:
    key: str
    parent_key: str | None
    kind: str
    title: str
    weight: Decimal = ONE
    weight_mode: str = "coefficient"  # how THIS node's children weights are interpreted
    scale_min: Decimal = ZERO
    scale_max: Decimal = TWENTY
    subject_id: str | None = None
    event_id: str | None = None


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
        if s.parent_key is not None and s.weight <= 0:
            err(s.key, "non_positive_weight", "weights must be > 0")
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
            total = sum((nodes[c].weight for c in kids), ZERO)
            if abs(total - ONE) > FRACTION_TOLERANCE:
                err(key, "fraction_sum", f"child fractions sum to {total}, expected 1")
    if errors:
        raise TreeError(errors)
    return CurriculumTree(nodes=nodes, children=children, root=root)


def leaf_weights(tree: CurriculumTree, key: str) -> dict[str, Decimal]:
    """a_i = product of normalized sibling weights from ``key`` down to leaf i (Σ a_i = 1)."""
    out: dict[str, Decimal] = {}

    def visit(k: str, acc: Decimal) -> None:
        kids = tree.children.get(k, [])
        if not kids:
            out[k] = acc
            return
        total = sum((tree.nodes[c].weight for c in kids), ZERO)
        for c in kids:
            visit(c, acc * tree.nodes[c].weight / total)

    with localcontext() as ctx:
        ctx.prec = 34
        visit(key, ONE)
    return out


def to_twenty(spec: NodeSpec, value: Decimal) -> Decimal:
    return (value - spec.scale_min) / (spec.scale_max - spec.scale_min) * TWENTY


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


def summarize(
    tree: CurriculumTree,
    actual: Mapping[str, Decimal],
    node_key: str,
    estimated: Mapping[str, Decimal] | None = None,
) -> NodeSummary:
    """Marks are on each leaf's own scale; results are /20."""
    estimated = estimated or {}
    with localcontext() as ctx:
        ctx.prec = 34
        weights = leaf_weights(tree, node_key)
        coverage = earned = f_cov = f_earned = ZERO
        for leaf, a in weights.items():
            spec = tree.nodes[leaf]
            if leaf in actual:
                x = to_twenty(spec, actual[leaf])
                coverage += a
                earned += a * x
                f_cov += a
                f_earned += a * x
            elif leaf in estimated:
                f_cov += a
                f_earned += a * to_twenty(spec, estimated[leaf])
        remaining = ONE - coverage
        full = abs(remaining) <= FRACTION_TOLERANCE
        return NodeSummary(
            key=node_key,
            coverage=coverage,
            earned=earned,
            provisional=earned / coverage if coverage > 0 else None,
            final=earned if full else None,
            remaining_weight=ZERO if full else remaining,
            bound_min=earned,
            bound_max=earned + TWENTY * (ZERO if full else remaining),
            forecast=f_earned if abs(ONE - f_cov) <= FRACTION_TOLERANCE else None,
            forecast_coverage=f_cov,
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
) -> Requirement:
    """r = (T − S) / Σ_R a_i, with explicit assumptions treated as known leaves."""
    assumptions = assumptions or {}
    reasons: list[str] = []
    with localcontext() as ctx:
        ctx.prec = 34
        weights = leaf_weights(tree, objective.node_key)
        earned = remaining = ZERO
        for leaf, a in weights.items():
            spec = tree.nodes[leaf]
            if leaf in actual:
                earned += a * to_twenty(spec, actual[leaf])
            elif leaf in assumptions:
                earned += a * to_twenty(spec, assumptions[leaf])
                if "ASSUMPTIONS_APPLIED" not in reasons:
                    reasons.append("ASSUMPTIONS_APPLIED")
            else:
                remaining += a
        if remaining <= FRACTION_TOLERANCE:
            feas = "achieved" if earned >= objective.target else "impossible"
            reasons.append("NO_REMAINING_WEIGHT")
            return Requirement(
                objective.id, objective.node_key, objective.target, None, feas, earned, earned, ZERO, tuple(reasons)
            )
        r = (objective.target - earned) / remaining
        if r <= ZERO:
            feas = "guaranteed"
        elif r > TWENTY:
            feas = "impossible"
            reasons.append("ABOVE_SCALE_MAXIMUM")
        else:
            feas = "feasible"
        return Requirement(
            objective_id=objective.id,
            node_key=objective.node_key,
            target=objective.target,
            required_mean=r,
            feasibility=feas,
            bound_min=earned,
            bound_max=earned + TWENTY * remaining,
            remaining_weight=remaining,
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
) -> list[LeafNeed]:
    assumptions = assumptions or {}
    per_leaf: dict[str, tuple[Decimal, list[str]]] = {}
    for req in requirements:
        if req.required_mean is None:
            continue
        for leaf in tree.leaves_under(req.node_key):
            if leaf in actual or leaf in assumptions:
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
) -> list[AcademicSignal]:
    """One signal per subject node: impact of remaining weight + normalized target gap
    (+ verified rule risk when a *verified* rule set exists; unknown risk is omitted)."""
    verified_risk = verified_risk or {}
    root_weights = leaf_weights(tree, tree.root)
    subjects = [k for k in tree.walk() if tree.nodes[k].kind == "subject"]
    by_node: dict[str, list[Objective]] = {}
    for o in objectives:
        by_node.setdefault(o.node_key, []).append(o)
    raw = []
    for s in subjects:
        leaves = tree.leaves_under(s)
        eff = sum((root_weights[leaf] for leaf in leaves), ZERO)
        rem = sum((root_weights[leaf] for leaf in leaves if leaf not in actual), ZERO)
        summ = summarize(tree, actual, s)
        applicable = [o for k in [s, *tree.ancestors(s)] for o in by_node.get(k, [])]
        reqs = [required(tree, actual, o) for o in applicable]
        reqs_r = [r for r in reqs if r.required_mean is not None]
        worst = max(reqs_r, key=lambda r: r.required_mean) if reqs_r else None  # type: ignore[arg-type, return-value]
        reasons: list[str] = []
        if not applicable:
            reasons.append("NO_OBJECTIVE")
        if rem <= FRACTION_TOLERANCE:
            reasons.append("FULLY_GRADED")
        if worst is not None and worst.feasibility == "impossible":
            reasons.append("TARGET_IMPOSSIBLE")
        if worst is not None and worst.feasibility == "guaranteed":
            reasons.append("TARGET_GUARANTEED")
        gap = None
        if worst is not None and worst.required_mean is not None:
            base = summ.provisional if summ.provisional is not None else ZERO
            gap = max(ZERO, worst.required_mean - base) if summ.provisional is not None else max(
                ZERO, worst.required_mean
            )
        raw.append((s, eff, rem, summ, worst, gap, reasons))

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
                reason_codes=tuple(reasons),
            )
        )
    return out

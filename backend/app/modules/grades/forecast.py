"""T47 (P2): uncertain forecasts and study-time allocation — pure, deterministic (seeded).

Forecast model (an explicit assumption, not a calibrated predictor): remaining marks are
independent draws from a normal distribution fitted to the student's own actual marks (/20),
shrunk towards a weak prior N(12, 3²) so that few marks do not produce overconfident
results; draws are clipped to [0, 20]. Results report the sample size and are labeled
``uncalibrated`` until enough marks exist — and even then they are not guarantees.

Time allocation: maximizes Σ w_s·√m_s (diminishing returns, assumption) under a time budget,
giving m_s ∝ w_s². No grade gain per hour is predicted.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass
from decimal import Decimal

from app.modules.grades.domain import (
    FRACTION_TOLERANCE,
    CurriculumTree,
    leaf_weights_exact,
    to_twenty,
)

PRIOR_MEAN, PRIOR_SD, PRIOR_STRENGTH = 12.0, 3.0, 3.0
MIN_MARKS_FOR_CALIBRATION = 8
METHOD = "normal-shrinkage-montecarlo-v1"


@dataclass(frozen=True)
class MarkModel:
    mean: float
    sd: float
    n_marks: int
    calibrated: bool


def fit(marks_on_20: Sequence[float]) -> MarkModel:
    n = len(marks_on_20)
    if n == 0:
        return MarkModel(PRIOR_MEAN, PRIOR_SD, 0, False)
    m = sum(marks_on_20) / n
    var = sum((x - m) ** 2 for x in marks_on_20) / (n - 1) if n > 1 else PRIOR_SD**2
    mean = (n * m + PRIOR_STRENGTH * PRIOR_MEAN) / (n + PRIOR_STRENGTH)
    sd = math.sqrt(((n - 1) * var + PRIOR_STRENGTH * PRIOR_SD**2) / (n - 1 + PRIOR_STRENGTH)) if n > 1 else PRIOR_SD
    return MarkModel(round(mean, 4), round(max(0.5, sd), 4), n, n >= MIN_MARKS_FOR_CALIBRATION)


@dataclass(frozen=True)
class Forecast:
    node_key: str
    target: float | None
    probability: float | None
    p10: float
    p50: float
    p90: float
    deterministic_min: float
    deterministic_max: float
    remaining_weight: float


def forecast_node(
    tree: CurriculumTree,
    actual: Mapping[str, Decimal],
    node_key: str,
    target: Decimal | None,
    model: MarkModel,
    samples: int = 2000,
    seed: int = 0,
    dispensed: AbstractSet[str] = frozenset(),
) -> Forecast:
    rng = random.Random(f"{seed}:{node_key}")
    weights_exact, unknown = leaf_weights_exact(tree, node_key, dispensed)
    if unknown:
        return Forecast(node_key, None if target is None else float(target), None, 0.0, 10.0, 20.0, 0.0, 20.0, 1.0)
    weights = {k: float(v) for k, v in weights_exact.items()}
    known = sum(w * float(to_twenty(tree.nodes[k], actual[k])) for k, w in weights.items() if k in actual)
    remaining = [(k, w) for k, w in weights.items() if k not in actual]
    rem_w = sum(w for _, w in remaining)
    outcomes = []
    for _ in range(samples if rem_w > float(FRACTION_TOLERANCE) else 1):
        total = known
        for _, w in remaining:
            total += w * min(20.0, max(0.0, rng.gauss(model.mean, model.sd)))
        outcomes.append(total)
    outcomes.sort()

    def q(p: float) -> float:
        return round(outcomes[min(len(outcomes) - 1, int(p * len(outcomes)))], 4)

    prob = None
    if target is not None:
        prob = round(sum(1 for x in outcomes if x >= float(target)) / len(outcomes), 4)
    return Forecast(node_key, None if target is None else float(target), prob, q(0.1), q(0.5), q(0.9),
                    round(known, 4), round(known + 20 * rem_w, 4), round(rem_w, 6))


def allocate(weights: Mapping[str, float], available_min: int, floor_min: int = 0, step: int = 5) -> dict[str, int]:
    """m_s ∝ w_s² (optimum of Σ w√m), with a per-subject floor, rounded to ``step`` minutes."""
    keys = sorted(k for k, w in weights.items() if w > 0)
    if not keys or available_min <= 0:
        return {k: 0 for k in weights}
    floor_total = floor_min * len(keys)
    if floor_total > available_min:
        floor_min, floor_total = 0, 0
    budget = available_min - floor_total
    sq = {k: weights[k] ** 2 for k in keys}
    total = sum(sq.values())
    raw = {k: floor_min + budget * sq[k] / total for k in keys}
    out = {k: int(raw[k] // step) * step for k in keys}
    leftover = available_min - sum(out.values())
    for k in sorted(keys, key=lambda k: (raw[k] - out[k], k), reverse=True):  # largest remainders
        if leftover < step:
            break
        out[k] += step
        leftover -= step
    return {**{k: 0 for k in weights}, **out}


def proportional(weights: Mapping[str, float], available_min: int, step: int = 5) -> dict[str, int]:
    return allocate({k: math.sqrt(w) if w > 0 else 0 for k, w in weights.items()}, available_min, 0, step)

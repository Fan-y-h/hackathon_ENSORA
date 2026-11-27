"""T33 (P2): richer evidence dimensions, calibration metrics and readiness — pure functions.

All outputs are pedagogical estimates with explicit sample sizes; nothing here is presented
as a validated psychometric measure or a grade prediction.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

DELAYED_RECALL_DAYS = 1.0  # retention needs recall at least one day after earlier evidence
DERIVED_WEIGHT = 0.8
AUTONOMY_SOURCES = frozenset({"application", "implementation", "reasoning"})
MIN_CALIBRATION_SAMPLES = 20
BINS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0001)


def derived_dimensions(dimension: str, assisted: bool, days_since_prior: float | None) -> list[str]:
    """Which extra dimensions an objective observation also evidences (ADAPTIVE_LEARNING:
    retention requires delayed recall; autonomy requires an unassisted task)."""
    if assisted:
        return []
    out = []
    if dimension != "retention" and days_since_prior is not None and days_since_prior >= DELAYED_RECALL_DAYS:
        out.append("retention")
    if dimension in AUTONOMY_SOURCES:
        out.append("autonomy")
    return out


@dataclass(frozen=True)
class CalibrationReport:
    samples: int
    brier: float | None
    base_rate: float | None
    brier_skill: float | None  # vs always predicting the base rate; > 0 = better than baseline
    bins: list[dict]
    sufficient: bool


def calibration(pairs: Sequence[tuple[float, float]]) -> CalibrationReport:
    """pairs = (predicted probability of success, observed score). Success = score >= 0.5."""
    data = [(min(1.0, max(0.0, p)), 1.0 if s >= 0.5 else 0.0) for p, s in pairs]
    n = len(data)
    if n == 0:
        return CalibrationReport(0, None, None, None, [], False)
    brier = sum((p - o) ** 2 for p, o in data) / n
    base = sum(o for _, o in data) / n
    ref = sum((base - o) ** 2 for _, o in data) / n
    bins = []
    for lo, hi in zip(BINS, BINS[1:], strict=False):
        sel = [(p, o) for p, o in data if lo <= p < hi]
        if sel:
            bins.append({
                "range": [round(lo, 2), round(min(hi, 1.0), 2)], "count": len(sel),
                "mean_predicted": round(sum(p for p, _ in sel) / len(sel), 4),
                "observed_rate": round(sum(o for _, o in sel) / len(sel), 4),
            })
    return CalibrationReport(
        samples=n, brier=round(brier, 4), base_rate=round(base, 4),
        brier_skill=None if ref == 0 else round(1 - brier / ref, 4), bins=bins,
        sufficient=n >= MIN_CALIBRATION_SAMPLES,
    )


def wilson_interval(successes: float, n: float, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class ConceptState:
    concept_id: str
    score: float | None
    n_effective: float
    importance: float = 1.0


def readiness(concepts: Sequence[ConceptState]) -> dict:
    """Importance-weighted mean of known mastery, with coverage and an evidence-based band.
    Unknown concepts lower coverage; they are never counted as 0 or as mastered."""
    total_w = sum(c.importance for c in concepts)
    known = [c for c in concepts if c.score is not None]
    if not concepts:
        return {"estimate": None, "coverage": None, "low": None, "high": None, "concepts": 0, "known": 0}
    known_w = sum(c.importance for c in known)
    if not known:
        return {"estimate": None, "coverage": 0.0, "low": None, "high": None, "concepts": len(concepts), "known": 0}
    est = sum(c.importance * c.score for c in known) / known_w  # type: ignore[operator]
    n_eff = sum(c.n_effective for c in known)
    band = wilson_interval(est * n_eff, n_eff) or (0.0, 1.0)
    coverage = known_w / total_w
    # Unknown share widens the band: it could be anywhere in [0, 1].
    low = band[0] * coverage
    high = band[1] * coverage + (1 - coverage)
    return {"estimate": round(est, 4), "coverage": round(coverage, 4), "low": round(low, 4),
            "high": round(high, 4), "concepts": len(concepts), "known": len(known)}


def exam_frequency(concepts_per_exam: Sequence[set[str]]) -> dict[str, float]:
    """Share of historical exams in which each concept appears (pattern refinement, R07/R11)."""
    n = len(concepts_per_exam)
    if n == 0:
        return {}
    counts: dict[str, int] = {}
    for concepts in concepts_per_exam:
        for c in concepts:
            counts[c] = counts.get(c, 0) + 1
    return {c: k / n for c, k in counts.items()}

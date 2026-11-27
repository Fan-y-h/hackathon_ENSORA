"""Pure adaptive-learning rules (docs/ADAPTIVE_LEARNING.md, MVP estimator).

These are transparent pedagogical heuristics, not calibrated psychometrics and not FSRS.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

ALGORITHM_VERSION = "ema-a0.35/interval-heuristic-v1"
ALPHA = 0.35
ASSISTED_FACTOR = 0.5
# Self-rated flashcard recall is weaker evidence than an auto-graded item (assumption).
CARD_EVIDENCE_WEIGHT = 0.7
ACQUIRED_THRESHOLD = 0.7
WEAK_THRESHOLD = 0.5
MIN_EVIDENCE_FOR_ACQUIRED = 2.0

DIMENSIONS = (
    "theory",
    "memory",
    "reasoning",
    "application",
    "implementation",
    "autonomy",
    "retention",
)
RATINGS = ("again", "hard", "good", "easy")
RATING_SCORE = {"again": 0.0, "hard": 0.6, "good": 0.85, "easy": 1.0}
QUESTION_TYPES = ("single_choice", "multiple_choice", "true_false", "fill_blank", "numeric", "open")


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def next_interval(rating: str, previous_days: int) -> int:
    """Again → 1; Hard → max(1, 1.2·prev); Good → max(2, 2·prev); Easy → max(3, 3·prev)."""
    if rating == "again":
        return 1
    if rating == "hard":
        return max(1, _round_half_up(previous_days * 1.2))
    if rating == "good":
        return max(2, _round_half_up(previous_days * 2))
    if rating == "easy":
        return max(3, _round_half_up(previous_days * 3))
    raise ValueError(f"unknown rating {rating!r}")


def rating_from_score(score: float) -> str:
    if score < WEAK_THRESHOLD:
        return "again"
    if score < ACQUIRED_THRESHOLD:
        return "hard"
    if score < 0.9:
        return "good"
    return "easy"


def evidence_weight(assisted: bool, base: float = 1.0) -> float:
    return base * (ASSISTED_FACTOR if assisted else 1.0)


@dataclass(frozen=True)
class MasteryState:
    score: float | None
    n_effective: float
    evidence_count: int


def update_mastery(state: MasteryState | None, score: float, weight: float) -> MasteryState:
    """First objective evidence initializes; then m ← (1 − α·w)·m + α·w·score."""
    if not 0.0 <= score <= 1.0 or not 0.0 < weight <= 1.0:
        raise ValueError("score must be in [0,1] and weight in (0,1]")
    if state is None or state.score is None:
        return MasteryState(score, weight, 1)
    k = ALPHA * weight
    return MasteryState(
        (1 - k) * state.score + k * score, state.n_effective + weight, state.evidence_count + 1
    )


def confidence(n_effective: float) -> float:
    return min(1.0, n_effective / 5)


def mastery_status(
    score: float | None, n_effective: float, next_due_at: datetime | None, now: datetime
) -> str:
    if score is None:
        return "unknown"
    if score < WEAK_THRESHOLD:
        return "learning"
    if score < ACQUIRED_THRESHOLD or n_effective < MIN_EVIDENCE_FOR_ACQUIRED:
        return "weak"
    if next_due_at is not None and next_due_at < now:
        return "consolidate"
    return "acquired"


def forgetting_risk(elapsed_days: float, interval_days: int) -> float:
    """Scheduling heuristic 1 − exp(−elapsed/interval); not a true probability."""
    return 1 - math.exp(-max(0.0, elapsed_days) / max(interval_days, 1))


# --- objective scoring -------------------------------------------------------------------
def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return None


def validate_answer_key(qtype: str, answer: Any, option_ids: list[str]) -> str | None:
    """Return an error message if the private answer is inconsistent with the question."""
    if qtype == "single_choice":
        if not isinstance(answer, str) or answer not in option_ids:
            return "single_choice answer must be one option id"
    elif qtype == "multiple_choice":
        if (
            not isinstance(answer, list)
            or not answer
            or any(a not in option_ids for a in answer)
            or len(set(answer)) != len(answer)
        ):
            return "multiple_choice answer must be a non-empty list of distinct option ids"
    elif qtype == "true_false":
        if not isinstance(answer, bool):
            return "true_false answer must be a boolean"
    elif qtype == "fill_blank":
        if not isinstance(answer, list) or not answer or not all(isinstance(a, str) for a in answer):
            return "fill_blank answer must be a non-empty list of accepted strings"
    elif qtype == "numeric":
        if not isinstance(answer, dict) or _to_decimal(answer.get("value")) is None:
            return "numeric answer must be {value, tolerance}"
        tol = _to_decimal(answer.get("tolerance", 0))
        if tol is None or tol < 0:
            return "numeric tolerance must be >= 0"
    elif qtype == "open":
        if answer is not None and not isinstance(answer, str):
            return "open answer must be a rubric text or null"
    else:
        return f"unknown question type {qtype!r}"
    if qtype in ("single_choice", "multiple_choice") and len(option_ids) < 2:
        return "choice questions need at least two options"
    return None


def score_item(qtype: str, answer: Any, response: Any, partial_credit: bool = False) -> float | None:
    """Objective item score in [0,1]; ``None`` when not auto-gradable (open answers).
    Multiple choice is exact-set unless the question explicitly enables partial credit:
    (correct picks − wrong picks) / number of correct options, floored at 0."""
    if response is None:
        return 0.0
    if qtype == "single_choice":
        return 1.0 if response == answer else 0.0
    if qtype == "multiple_choice":
        if not isinstance(response, list):
            return 0.0
        if partial_credit:
            picked, correct = set(response), set(answer)
            return max(0.0, (len(picked & correct) - len(picked - correct)) / len(correct))
        return 1.0 if set(response) == set(answer) else 0.0
    if qtype == "true_false":
        return 1.0 if response is answer else 0.0
    if qtype == "fill_blank":
        if not isinstance(response, str):
            return 0.0
        return 1.0 if normalize_text(response) in {normalize_text(a) for a in answer} else 0.0
    if qtype == "numeric":
        value = _to_decimal(response)
        if value is None:
            return 0.0
        target = Decimal(str(answer["value"]))
        tol = Decimal(str(answer.get("tolerance", 0)))
        return 1.0 if abs(value - target) <= tol else 0.0
    if qtype == "open":
        return None
    raise ValueError(f"unknown question type {qtype!r}")

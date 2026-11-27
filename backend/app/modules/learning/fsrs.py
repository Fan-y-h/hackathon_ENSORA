"""FSRS-4.5 memory model (version-pinned, default parameters) — T26 adapter.

Pure functions; the heuristic interval scheduler stays the default and the baseline. Formulas
from the published FSRS-4.5 algorithm (open-spaced-repetition); parameters are the
published defaults, not fitted to this user (no optimizer yet), which is stated in results.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ALGORITHM_VERSION = "fsrs-4.5-default-params"
W = (
    0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031, 1.6474,
    0.1367, 1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755,
)
DECAY = -0.5
FACTOR = 19 / 81  # so that R(S, S) = 0.9
GRADE = {"again": 1, "hard": 2, "good": 3, "easy": 4}
MAX_INTERVAL_DAYS = 365


@dataclass(frozen=True)
class MemoryState:
    stability: float  # days
    difficulty: float  # 1..10


def _clamp_d(d: float) -> float:
    return min(10.0, max(1.0, d))


def retrievability(elapsed_days: float, stability: float) -> float:
    return (1 + FACTOR * max(0.0, elapsed_days) / stability) ** DECAY


def init_state(rating: str) -> MemoryState:
    g = GRADE[rating]
    return MemoryState(stability=W[g - 1], difficulty=_clamp_d(W[4] - (g - 3) * W[5]))


def next_state(state: MemoryState, rating: str, elapsed_days: float) -> MemoryState:
    g = GRADE[rating]
    r = retrievability(elapsed_days, state.stability)
    d0_good = W[4]  # D0(3)
    d = state.difficulty - W[6] * (g - 3)
    d = _clamp_d(W[7] * d0_good + (1 - W[7]) * d)
    s, dd = state.stability, state.difficulty
    if g == 1:
        new_s = W[11] * dd ** (-W[12]) * ((s + 1) ** W[13] - 1) * math.exp(W[14] * (1 - r))
        new_s = min(new_s, s)
    else:
        hard = W[15] if g == 2 else 1.0
        easy = W[16] if g == 4 else 1.0
        new_s = s * (math.exp(W[8]) * (11 - dd) * s ** (-W[9]) * (math.exp(W[10] * (1 - r)) - 1) * hard * easy + 1)
    return MemoryState(stability=max(0.1, new_s), difficulty=d)


def interval_days(stability: float, desired_retention: float) -> int:
    raw = stability / FACTOR * (desired_retention ** (1 / DECAY) - 1)
    return max(1, min(MAX_INTERVAL_DAYS, round(raw)))

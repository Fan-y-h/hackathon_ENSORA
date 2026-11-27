"""T21: deterministic decomposition of project instructions (TXT) into editable steps.

No AI: numbered/bulleted items, "Partie/Question/Exercice/Étape" headings and markdown headings
become steps; deliverable keywords are flagged; effort is split proportionally to text length
(or estimated from length when no total is given). The result is a *proposal* the student edits
before applying it (R24: user confirms proposals). The AI variant belongs to G.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

METHOD = "heuristic-text-v1"
_ITEM = re.compile(
    r"^\s*(?:#{1,6}\s+|(?:\d+|[a-zA-Z]|[ivxIVX]+)[.)]\s+|[-*•]\s+|"
    r"(?:partie|question|exercice|étape|etape|task|step|part)\s*\d*\s*[:.\-–]?\s*)(.+)$",
    re.IGNORECASE,
)
_DELIVERABLE = re.compile(r"\b(rendre|rendu|livrable|deliverable|rapport|report|soutenance|présentation|submit)\b",
                          re.IGNORECASE)


@dataclass(frozen=True)
class Step:
    title: str
    detail: str
    estimated_min: int
    deliverable: bool


def _round5(x: float) -> int:
    return max(5, int(round(x / 5)) * 5)


def decompose(text: str, total_min: int | None = None, max_steps: int = 30) -> list[Step]:
    items: list[list[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _ITEM.match(line)
        if m and len(m.group(1).strip()) >= 3:
            items.append([m.group(1).strip()])
        elif items:
            items[-1].append(line)
    if not items:  # no structure: split into sentences-ish paragraphs
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if len(p.strip()) >= 8]
        items = [[p] for p in parts[:max_steps]] or [[text.strip()[:200]]]
    items = items[:max_steps]
    lengths = [sum(len(x) for x in it) for it in items]
    total_len = sum(lengths) or 1
    steps = []
    for it, n in zip(items, lengths, strict=False):
        title = it[0][:200]
        detail = " ".join(it[1:])[:2000]
        if total_min:
            est = _round5(total_min * n / total_len)
        else:
            est = min(240, _round5(30 + 10 * n / 100))
        steps.append(Step(title, detail, est, bool(_DELIVERABLE.search(" ".join(it)))))
    return steps

"""Base document parser interface and shared extraction helpers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from ..contracts import ParsedDocument

_LATEX_DISPLAY_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.DOTALL)
_LATEX_INLINE_RE = re.compile(r"(?<!\$)\$(?!\$)([^$\n]{2,200})(?<!\$)\$(?!\$)|\\\((.+?)\\\)")
_UNICODE_MATH_LINE_RE = re.compile(r"^.*[∀∃∈∉⊂⊆∪∩∑∏∫√∞≈≠≤≥±×÷→⇒⇔λμσθβαγΩΔ∇].*$")

CODE_EXTENSIONS_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".r": "r",
    ".rmd": "rmarkdown",
    ".qmd": "quarto",
    ".sql": "sql",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".sh": "bash",
    ".bash": "bash",
    ".bat": "batch",
    ".ps1": "powershell",
    ".tex": "latex",
    ".sty": "latex",
    ".cls": "latex",
    ".bib": "bibtex",
    ".rnw": "r",
    ".sas": "sas",
    ".do": "stata",
    ".stan": "stan",
    ".jl": "julia",
    ".m": "matlab",
    ".go": "go",
    ".rs": "rust",
    ".css": "css",
    ".svg": "xml",
    ".rst": "rst",
    ".org": "org",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".csv": "csv",
    ".tsv": "tsv",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
    ".log": "text",
}

_FRENCH_MARKERS = frozenset(
    {"les", "des", "une", "dans", "pour", "avec", "est", "sont", "cours", "exercices", "théorème", "définition"}
)
_ENGLISH_MARKERS = frozenset(
    {"the", "and", "for", "with", "that", "this", "from", "lecture", "theorem", "definition", "proof"}
)


def extract_math_expressions(text: str) -> tuple[str, ...]:
    """Extract reliable LaTeX display/inline math or Unicode math formulas without inventing equations."""
    found: list[str] = []
    seen: set[str] = set()

    for m in _LATEX_DISPLAY_RE.finditer(text):
        expr = (m.group(1) or m.group(2) or "").strip()
        if expr and expr not in seen:
            seen.add(expr)
            found.append(expr)

    for m in _LATEX_INLINE_RE.finditer(text):
        expr = (m.group(1) or m.group(2) or "").strip()
        if expr and expr not in seen:
            seen.add(expr)
            found.append(expr)

    for line in text.splitlines():
        stripped = line.strip()
        if 3 <= len(stripped) <= 200 and _UNICODE_MATH_LINE_RE.match(stripped):
            if stripped not in seen:
                seen.add(stripped)
                found.append(stripped)

    return tuple(found)


def detect_language_hint(text: str) -> str | None:
    """Lightweight deterministic French/English hint for parsed academic text."""
    tokens = re.findall(r"[a-zA-Zàâäéèêëîïôöùûüç]+", text.lower())
    if len(tokens) < 6:
        return None
    fr_hits = sum(1 for t in tokens if t in _FRENCH_MARKERS)
    en_hits = sum(1 for t in tokens if t in _ENGLISH_MARKERS)
    if fr_hits == 0 and en_hits == 0:
        return None
    if fr_hits >= en_hits * 1.2:
        return "fr"
    if en_hits >= fr_hits * 1.2:
        return "en"
    return "fr+en"


def extract_code_language(filename: str) -> str | None:
    lower = filename.lower()
    for ext, lang in CODE_EXTENSIONS_TO_LANGUAGE.items():
        if lower.endswith(ext):
            return lang
    return None


class DocumentParser(ABC):
    """Modular parser interface for a specific document format."""

    name: str
    version: str
    supported_mimes: frozenset[str]
    supported_extensions: frozenset[str]

    def can_parse(self, *, filename: str = "", mime_type: str = "") -> bool:
        norm_mime = mime_type.split(";")[0].strip().lower()
        if norm_mime and norm_mime in self.supported_mimes:
            return True
        lower_name = filename.lower()
        return any(lower_name.endswith(ext) for ext in self.supported_extensions)

    @abstractmethod
    def parse(
        self,
        content: bytes,
        *,
        filename: str,
        mime_type: str,
        provenance: dict[str, Any] | None = None,
    ) -> ParsedDocument:
        """Parse raw document bytes into a structured ParsedDocument."""

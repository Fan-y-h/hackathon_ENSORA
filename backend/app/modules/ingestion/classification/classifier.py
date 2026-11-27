"""Deterministic + optional semantic document classifier for ENSAI academic resources.

Classifies documents into the 11 required categories:
- lecture
- tutorial
- practical_work
- examination
- examination_correction
- assignment
- course_summary
- notebook
- source_code
- administrative
- other

Returns confidence scores, human-readable evidence, and ranked alternative candidates.
Supports an optional P2 semantic classifier hook without duplicating P2 logic.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from ..contracts import (
    ClassificationResult,
    DocumentCategory,
    ParsedDocument,
    SemanticClassifierProtocol,
    SourceRecord,
)
from ..parsers.base import CODE_EXTENSIONS_TO_LANGUAGE


def _fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    folded = "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()
    # Treat underscores and common path separators as word boundaries so snake_case filenames match \b rules
    return re.sub(r"[_\-.]+", " ", folded)


@dataclass(frozen=True)
class _Rule:
    category: DocumentCategory
    pattern: re.Pattern[str]
    weight: float
    label: str


_PATH_AND_TITLE_RULES: tuple[_Rule, ...] = (
    # Examination correction must outscore bare examination when correction keywords appear
    _Rule(
        DocumentCategory.EXAMINATION_CORRECTION,
        re.compile(
            r"(?:corrige|correction|solution|elements?\s+de\s+reponse|bareme|answer\s*key|sol\b).{0,40}(?:exam|partiel|annale|ds\b|epreuve|sujet|rattrapage|final|midterm)|"
            r"(?:exam|partiel|annale|ds\b|epreuve|sujet|rattrapage|final|midterm).{0,40}(?:corrige|correction|solution|bareme|answer\s*key|sol\b)",
            re.IGNORECASE,
        ),
        0.95,
        "title/path matches exam+correction pair pattern",
    ),
    _Rule(
        DocumentCategory.EXAMINATION_CORRECTION,
        re.compile(r"\b(?:corrige|correction|solutions?|corriges)\b", re.IGNORECASE),
        0.55,
        "title/path contains correction keyword",
    ),
    _Rule(
        DocumentCategory.EXAMINATION,
        re.compile(
            r"\b(?:annales?|partiel|examen|exam|epreuve|rattrapage|midterm|final[_\-\s]?exam|ds[_\-\s]?[0-9]?|controle\s+final|sujet[_\-\s]?(?:20[0-9]{2}|exam|partiel))\b",
            re.IGNORECASE,
        ),
        0.82,
        "title/path contains examination/annales keyword",
    ),
    _Rule(
        DocumentCategory.COURSE_SUMMARY,
        re.compile(
            r"\b(?:fiche|synthese|resume|summary|cheat\s*sheet|formulaire|memento|revision\s*sheet|recap)\b",
            re.IGNORECASE,
        ),
        0.84,
        "title/path contains revision sheet/summary keyword",
    ),
    _Rule(
        DocumentCategory.ASSIGNMENT,
        re.compile(
            r"\b(?:devoir|dm[_\-\s]?[0-9]*|projet|project|assignment|homework|consignes?|enonce\s+projet|rendu|livrable|mini[\-\s]?projet)\b",
            re.IGNORECASE,
        ),
        0.80,
        "title/path contains assignment/project keyword",
    ),
    _Rule(
        DocumentCategory.PRACTICAL_WORK,
        re.compile(r"(?:^|[\s/_\-])(?:tp|travaux\s+pratiques|lab|atelier)(?:[\s/_\-0-9]|$)", re.IGNORECASE),
        0.80,
        "title/path contains TP/practical work marker",
    ),
    _Rule(
        DocumentCategory.TUTORIAL,
        re.compile(r"(?:^|[\s/_\-])(?:td|travaux\s+diriges|tutorial|exercices?|feuille\s+d[' ]?exercices?)(?:[\s/_\-0-9]|$)", re.IGNORECASE),
        0.80,
        "title/path contains TD/tutorial marker",
    ),
    _Rule(
        DocumentCategory.LECTURE,
        re.compile(
            r"(?:^|[\s/_\-])(?:cm|cours|lecture|chapitre|chapter|slides?|poly|polycopie|support\s+de\s+cours|amphi)(?:[\s/_\-0-9]|$)",
            re.IGNORECASE,
        ),
        0.78,
        "title/path contains lecture/course material marker",
    ),
    _Rule(
        DocumentCategory.ADMINISTRATIVE,
        re.compile(
            r"\b(?:syllabus|reglement|scolarite|calendrier|emploi\s+du\s+temps|maquette|modalites?\s+d[' ]?evaluation|mcc|organisation|administratif|charte|guide\s+etudiant)\b",
            re.IGNORECASE,
        ),
        0.82,
        "title/path contains administrative/curriculum regulation keyword",
    ),
)

_TEXT_RULES: tuple[_Rule, ...] = (
    _Rule(
        DocumentCategory.EXAMINATION_CORRECTION,
        re.compile(r"\b(?:elements?\s+de\s+correction|bareme\s+indicatif|corrige\s+type|correction\s+de\s+l[' ]?examen)\b", re.IGNORECASE),
        0.45,
        "document body contains exam correction phrasing",
    ),
    _Rule(
        DocumentCategory.EXAMINATION,
        re.compile(r"\b(?:duree\s*:\s*[0-9]+\s*h|documents?\s+(?:autorises?|interdits?)|calculatrice\s+(?:autorisee|interdite)|bar[eè]me\s+sur\s+20|partiel\s+de)\b", re.IGNORECASE),
        0.40,
        "document body contains exam rubric/instructions",
    ),
    _Rule(
        DocumentCategory.COURSE_SUMMARY,
        re.compile(r"\b(?:a\s+retenir|formulaire|synthese\s+du\s+chapitre|key\s+takeaways|summary\s+sheet)\b", re.IGNORECASE),
        0.35,
        "document body contains summary/cheat-sheet phrasing",
    ),
    _Rule(
        DocumentCategory.ASSIGNMENT,
        re.compile(r"\b(?:date\s+de\s+remise|a\s+rendre\s+(?:avant|pour)|deliverables?|submission\s+deadline)\b", re.IGNORECASE),
        0.38,
        "document body contains assignment submission instructions",
    ),
    _Rule(
        DocumentCategory.TUTORIAL,
        re.compile(r"\b(?:exercice\s+1|feuille\s+de\s+td|travaux\s+diriges)\b", re.IGNORECASE),
        0.30,
        "document body contains tutorial exercise structure",
    ),
    _Rule(
        DocumentCategory.PRACTICAL_WORK,
        re.compile(r"\b(?:travaux\s+pratiques|implementer\s+en\s+(?:python|r)|objectif\s+du\s+tp)\b", re.IGNORECASE),
        0.30,
        "document body contains practical lab instructions",
    ),
    _Rule(
        DocumentCategory.LECTURE,
        re.compile(r"\b(?:theoreme\s+[0-9]|proposition\s+[0-9]|definition\s+[0-9]|demonstration|plan\s+du\s+cours)\b", re.IGNORECASE),
        0.28,
        "document body contains lecture/theorem structure",
    ),
    _Rule(
        DocumentCategory.ADMINISTRATIVE,
        re.compile(r"\b(?:reglement\s+des\s+etudes|modalites?\s+de\s+controle|credits?\s+ects|jury\s+de\s+validation)\b", re.IGNORECASE),
        0.42,
        "document body contains institutional/administrative terminology",
    ),
)

_SOURCE_CODE_EXTS = frozenset(
    ext for ext in CODE_EXTENSIONS_TO_LANGUAGE.keys() if ext not in {".json", ".yaml", ".yml", ".csv", ".tex"}
)


class DocumentClassifier:
    """Rule-based academic resource classifier with optional P2 semantic hook."""

    def __init__(
        self,
        *,
        semantic_classifier: SemanticClassifierProtocol | None = None,
        semantic_threshold: float = 0.72,
    ) -> None:
        self._semantic = semantic_classifier
        self._semantic_threshold = semantic_threshold

    def classify(
        self,
        *,
        title: str,
        filename: str | None = None,
        folder_path: str | None = None,
        mime_type: str | None = None,
        record: SourceRecord | None = None,
        parsed: ParsedDocument | None = None,
    ) -> ClassificationResult:
        """Classify a document using path, filename, source metadata, and parsed text."""
        scores: dict[DocumentCategory, float] = {cat: 0.0 for cat in DocumentCategory}
        evidence_by_cat: dict[DocumentCategory, list[str]] = {cat: [] for cat in DocumentCategory}

        def add(cat: DocumentCategory, weight: float, reason: str) -> None:
            scores[cat] = min(0.99, scores[cat] + weight)
            if reason not in evidence_by_cat[cat]:
                evidence_by_cat[cat].append(reason)

        fname = filename or (record.payload.get("filename") if record else None) or title
        lower_fname = fname.lower()
        norm_mime = (mime_type or (record.mime_type if record else "") or "").lower()

        # 1. Explicit source record kind signals
        if record is not None:
            if record.record_kind == "assignment" or record.payload.get("resource_kind") == "assignment":
                add(DocumentCategory.ASSIGNMENT, 0.92, "source record explicitly typed as Moodle assignment")
            elif record.record_kind == "announcement":
                add(DocumentCategory.ADMINISTRATIVE, 0.78, "source record is a course announcement")

        # 2. Format-level deterministic signals
        if lower_fname.endswith(".ipynb") or norm_mime == "application/x-ipynb+json":
            add(DocumentCategory.NOTEBOOK, 0.88, "file format is Jupyter Notebook (.ipynb)")
        elif any(lower_fname.endswith(ext) for ext in _SOURCE_CODE_EXTS):
            add(DocumentCategory.SOURCE_CODE, 0.86, f"file extension matches source code ({fname})")
        elif lower_fname.endswith(".pptx") or "presentation" in norm_mime:
            add(DocumentCategory.LECTURE, 0.35, "PowerPoint slide deck often corresponds to lecture")

        # 3. Title, filename, section, and folder path signals
        meta_text = _fold(" / ".join(p for p in (folder_path or (record.folder_path if record else ""), record.section_title if record else "", fname, title) if p))
        for rule in _PATH_AND_TITLE_RULES:
            if rule.pattern.search(meta_text):
                add(rule.category, rule.weight, rule.label)

        # Boost examination_correction when folder is Annales/Exams AND filename says correction/corrige
        if scores[DocumentCategory.EXAMINATION] >= 0.7 and (
            re.search(r"\b(?:corrige|correction|solution|corriges|sol)\b", meta_text)
        ):
            add(
                DocumentCategory.EXAMINATION_CORRECTION,
                0.50,
                "exam context combined with correction indicator",
            )

        # 4. Parsed body text signals (bounded excerpt for speed)
        body_excerpt = ""
        if parsed is not None and parsed.blocks:
            body_excerpt = parsed.full_text[:4000]
            folded_body = _fold(body_excerpt)
            for rule in _TEXT_RULES:
                if rule.pattern.search(folded_body):
                    add(rule.category, rule.weight, rule.label)

        # 5. Rank candidates
        ranked = sorted(
            ((cat, score) for cat, score in scores.items() if score > 0),
            key=lambda kv: (-kv[1], kv[0].value),
        )

        best_cat = ranked[0][0] if ranked else DocumentCategory.OTHER
        best_score = ranked[0][1] if ranked else 0.25
        method = "deterministic"

        # 6. Optional P2 semantic classification fallback when deterministic confidence is modest
        if self._semantic is not None and best_score < self._semantic_threshold:
            try:
                sem = self._semantic.classify_text(
                    title=title,
                    excerpt=body_excerpt[:2000],
                    metadata={
                        "filename": fname,
                        "folder_path": folder_path,
                        "mime_type": norm_mime,
                    },
                )
                if sem is not None:
                    sem_cat, sem_conf, sem_ev = sem
                    if sem_conf > best_score:
                        best_cat = sem_cat
                        best_score = min(0.99, max(0.0, float(sem_conf)))
                        evidence_by_cat[best_cat].extend(sem_ev)
                        method = "hybrid_semantic"
            except Exception:
                # Never fail ingestion due to optional semantic hook
                pass

        alternatives = tuple(
            (cat, score) for cat, score in ranked if cat != best_cat
        )[:3]
        evidence = tuple(evidence_by_cat.get(best_cat) or ("default fallback classification",))

        return ClassificationResult(
            category=best_cat,
            confidence=round(best_score, 4),
            evidence=evidence,
            method=method,
            alternatives=alternatives,
        )

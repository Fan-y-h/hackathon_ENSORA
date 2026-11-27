"""Academic entity resolution, subject reconciliation, exam-correction pairing & overrides.

Implements:
- Subject normalization & alias matching (cross-source: Moodle, Pamplemousse, Ensora Drive, Local)
- Academic year detection (distinguishing historical exam/archive year from current student year)
- Cohort (1A/2A/3A/Global), semester (S1-S6), specialization, teaching unit (UE), chapter detection
- Examination & examination-correction pairing with ambiguity flagging on ties
- Duplicate resource detection support (content hash + normalized metadata)
- Confidence scoring (>= 0.90 auto if unambiguous, 0.65-0.90 human review, < 0.65 unassigned)
- Manual correction workflow that preserves user overrides and never overwrites confirmed mappings
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable

from ..contracts import (
    AcademicRecord,
    DocumentCategory,
    EntityResolutionResult,
    ExamCorrectionMatch,
    SourceRecord,
)

AUTO_MATCH_THRESHOLD = 0.90
REVIEW_MATCH_THRESHOLD = 0.65

_STOPWORDS = frozenset(
    {
        "de",
        "du",
        "des",
        "la",
        "le",
        "les",
        "l",
        "et",
        "en",
        "pour",
        "sur",
        "avec",
        "au",
        "aux",
        "a",
        "of",
        "the",
        "and",
        "in",
        "to",
        "for",
        "cours",
        "td",
        "tp",
        "cm",
        "exam",
        "examen",
        "partiel",
        "corrige",
        "correction",
        "annale",
        "annales",
        "chapitre",
        "chapter",
    }
)

_COHORT_RE = re.compile(r"(?:^|[\s/_\-])(1a|2a|3a|global)(?:[\s/_\-]|$)", re.IGNORECASE)
_SEMESTER_RE = re.compile(r"(?:^|[\s/_\-])(?:semestre\s*|semester\s*|s)([1-6])(?:[\s/_\-]|$)", re.IGNORECASE)
_YEAR_RANGE_RE = re.compile(r"\b(20[1-3][0-9])\s*[-/_]\s*(20[1-3][0-9])\b")
_SINGLE_YEAR_RE = re.compile(r"\b(20[1-3][0-9])\b")
_CHAPTER_RE = re.compile(
    r"\b(?:chapitre|chap\.?|chapter|ch\.?|section|seance|séance)\s*([0-9]+(?:\.[0-9]+)?)(?:\s*[:\-–]\s*([^\n/._]{2,60}))?",
    re.IGNORECASE,
)
_SPEC_TOKENS: dict[str, str] = {
    "id": "ID",
    "data science": "SD",
    "science des donnees": "SD",
    "sd": "SD",
    "fe": "FE",
    "finance": "FE",
    "biostatistique": "BIO",
    "biostat": "BIO",
    "bio": "BIO",
    "econometrie": "ECON",
    "marketing": "MKT",
    "ingenierie des donnees": "ID",
    "gsp": "GSP",
}


def normalize_text_key(text: str) -> str:
    """Fold accents, lowercase, strip punctuation, and collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_like = "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", ascii_like).strip()
    return re.sub(r"\s+", " ", cleaned)


def significant_tokens(text: str) -> set[str]:
    return {t for t in normalize_text_key(text).split() if len(t) >= 2 and t not in _STOPWORDS}


@dataclass(frozen=True)
class CanonicalSubject:
    """Reference subject entry loaded from documented curriculum / imported coefficients."""

    subject_id: str
    title: str
    code: str | None = None
    aliases: tuple[str, ...] = ()
    cohort: str | None = None
    semester: str | None = None
    specialization: str | None = None
    teaching_unit: str | None = None


@dataclass
class ManualCorrectionStore:
    """Stores user-confirmed subject/chapter overrides and exam-correction links.

    Confirmed mappings are authoritative and are NEVER overwritten by automated resolution.
    """

    subject_overrides: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # external_id -> (subject_id, chapter)
    confirmed_aliases: dict[str, str] = field(default_factory=dict)  # normalized_alias -> subject_id
    confirmed_exam_pairs: dict[str, str] = field(default_factory=dict)  # exam_external_id -> correction_external_id

    def set_subject_override(self, external_id: str, subject_id: str, chapter: str | None = None) -> None:
        self.subject_overrides[external_id] = (subject_id, chapter)

    def confirm_alias(self, alias: str, subject_id: str) -> None:
        norm = normalize_text_key(alias)
        if norm:
            self.confirmed_aliases[norm] = subject_id

    def confirm_exam_pair(self, exam_external_id: str, correction_external_id: str) -> None:
        self.confirmed_exam_pairs[exam_external_id] = correction_external_id


class AcademicEntityResolver:
    """Reconciles subjects, academic metadata, duplicates, and exam-correction pairs."""

    def __init__(
        self,
        subjects: Iterable[CanonicalSubject] = (),
        *,
        current_academic_year: str | None = None,
        student_cohort: str | None = None,
        student_specialization: str | None = None,
        manual_store: ManualCorrectionStore | None = None,
    ) -> None:
        self._subjects: dict[str, CanonicalSubject] = {}
        self._code_index: dict[str, list[str]] = {}
        self._alias_index: dict[str, list[str]] = {}
        self._current_academic_year = current_academic_year
        self._student_cohort = student_cohort
        self._student_specialization = student_specialization
        self._manual = manual_store or ManualCorrectionStore()
        self._seen_hashes: dict[str, str] = {}  # content_hash -> first external_id

        for subj in subjects:
            self.register_subject(subj)

    @property
    def manual_store(self) -> ManualCorrectionStore:
        return self._manual

    def register_subject(self, subject: CanonicalSubject) -> None:
        self._subjects[subject.subject_id] = subject
        if subject.code:
            k = normalize_text_key(subject.code)
            self._code_index.setdefault(k, [])
            if subject.subject_id not in self._code_index[k]:
                self._code_index[k].append(subject.subject_id)

        all_names = [subject.title, *subject.aliases]
        if subject.code:
            all_names.append(subject.code)
        for name in all_names:
            norm = normalize_text_key(name)
            if not norm:
                continue
            self._alias_index.setdefault(norm, [])
            if subject.subject_id not in self._alias_index[norm]:
                self._alias_index[norm].append(subject.subject_id)

    def load_from_academic_records(self, records: Iterable[AcademicRecord]) -> int:
        """Populate canonical subjects & teaching units from imported academic curriculum records."""
        ue_titles: dict[str, str] = {
            r.external_id: r.title for r in records if r.record_type in ("teaching_unit", "unit")
        }
        added = 0
        for rec in records:
            if rec.record_type != "subject":
                continue
            ue_name = ue_titles.get(rec.parent_external_id or "", rec.parent_external_id)
            self.register_subject(
                CanonicalSubject(
                    subject_id=rec.external_id,
                    title=rec.title,
                    code=rec.subject_code,
                    aliases=tuple(a for a in (rec.subject_code,) if a),
                    cohort=rec.cohort,
                    semester=rec.semester,
                    specialization=rec.specialization,
                    teaching_unit=ue_name,
                )
            )
            added += 1
        return added

    def detect_academic_metadata(
        self,
        *,
        title: str = "",
        folder_path: str | None = None,
        section_title: str | None = None,
        extra_text: str | None = None,
    ) -> dict[str, str | None]:
        """Extract cohort, semester, specialization, chapter, and academic/historical years."""
        combined = " / ".join(p for p in (folder_path, section_title, title) if p)
        folded = normalize_text_key(combined)

        cohort: str | None = None
        m_cohort = _COHORT_RE.search(combined)
        if m_cohort:
            raw_c = m_cohort.group(1)
            cohort = "Global" if raw_c.lower() == "global" else raw_c.upper()

        semester: str | None = None
        m_sem = _SEMESTER_RE.search(combined)
        if m_sem:
            semester = f"S{m_sem.group(1)}"

        specialization: str | None = None
        tokens_padded = f" {folded} "
        for key, canonical_spec in _SPEC_TOKENS.items():
            if f" {key} " in tokens_padded:
                specialization = canonical_spec
                break

        chapter: str | None = None
        m_chap = _CHAPTER_RE.search(combined) or (_CHAPTER_RE.search(extra_text) if extra_text else None)
        if m_chap:
            num = m_chap.group(1)
            subtitle = (m_chap.group(2) or "").strip()
            chapter = f"Chapter {num}: {subtitle}" if subtitle else f"Chapter {num}"

        historical_year: str | None = None
        year_probe = re.sub(r"[_.]+", " ", combined)
        m_yr = _YEAR_RANGE_RE.search(year_probe)
        if m_yr:
            historical_year = f"{m_yr.group(1)}-{m_yr.group(2)}"
        else:
            m_sy = _SINGLE_YEAR_RE.search(year_probe)
            if m_sy:
                historical_year = m_sy.group(1)

        academic_year = self._current_academic_year
        if historical_year and academic_year and historical_year == academic_year:
            # Matches student's current year; keep both clear
            pass

        return {
            "cohort": cohort,
            "semester": semester,
            "specialization": specialization,
            "chapter": chapter,
            "academic_year": academic_year,
            "historical_year": historical_year,
        }

    def resolve_record(
        self,
        record: SourceRecord,
        *,
        body_excerpt: str | None = None,
    ) -> EntityResolutionResult:
        """Resolve canonical subject, academic hierarchy, and duplicate status for a record."""
        meta = self.detect_academic_metadata(
            title=record.title,
            folder_path=record.folder_path,
            section_title=record.section_title,
            extra_text=body_excerpt,
        )

        # Duplicate detection by content hash
        duplicate_of: str | None = None
        if record.content_hash:
            prev_ext = self._seen_hashes.get(record.content_hash)
            if prev_ext and prev_ext != record.external_id:
                duplicate_of = prev_ext
            else:
                self._seen_hashes[record.content_hash] = record.external_id

        # 1. Respect manual override unconditionally (never overwrite confirmed human mappings)
        if record.external_id in self._manual.subject_overrides:
            ov_subj_id, ov_chap = self._manual.subject_overrides[record.external_id]
            subj = self._subjects.get(ov_subj_id)
            return EntityResolutionResult(
                canonical_subject_id=ov_subj_id,
                canonical_subject_title=subj.title if subj else ov_subj_id,
                subject_code=subj.code if subj else None,
                confidence=1.0,
                needs_review=False,
                matched_by="manual_override",
                academic_year=meta["academic_year"],
                historical_year=meta["historical_year"],
                cohort=meta["cohort"] or (subj.cohort if subj else self._student_cohort),
                semester=meta["semester"] or (subj.semester if subj else None),
                specialization=meta["specialization"] or (subj.specialization if subj else self._student_specialization),
                teaching_unit=subj.teaching_unit if subj else None,
                chapter=ov_chap or meta["chapter"],
                duplicate_of_external_id=duplicate_of,
                evidence=("preserved human manual override",),
            )

        # Candidate search texts in decreasing specificity
        probes = [
            p
            for p in (
                record.course_id,
                record.course_title,
                record.payload.get("subject_hint") if isinstance(record.payload, dict) else None,
                record.folder_path,
                record.title,
            )
            if p
        ]

        # 2. Confirmed human alias lookup
        for probe in probes:
            norm_p = normalize_text_key(str(probe))
            if norm_p in self._manual.confirmed_aliases:
                sid = self._manual.confirmed_aliases[norm_p]
                subj = self._subjects.get(sid)
                return EntityResolutionResult(
                    canonical_subject_id=sid,
                    canonical_subject_title=subj.title if subj else sid,
                    subject_code=subj.code if subj else None,
                    confidence=0.98,
                    needs_review=False,
                    matched_by="confirmed_alias",
                    academic_year=meta["academic_year"],
                    historical_year=meta["historical_year"],
                    cohort=meta["cohort"] or (subj.cohort if subj else None),
                    semester=meta["semester"] or (subj.semester if subj else None),
                    specialization=meta["specialization"] or (subj.specialization if subj else None),
                    teaching_unit=subj.teaching_unit if subj else None,
                    chapter=meta["chapter"],
                    duplicate_of_external_id=duplicate_of,
                    evidence=(f"matched confirmed alias '{probe}'",),
                )

        # 3. Score all registered canonical subjects (code -> alias -> token -> fuzzy)
        scored: list[tuple[float, str, CanonicalSubject, str]] = []
        full_probe_norm = normalize_text_key(" ".join(str(p) for p in probes))
        full_tokens = significant_tokens(full_probe_norm)
        padded_full = f" {full_probe_norm} "

        for subj in self._subjects.values():
            best_score = 0.0
            best_method = "unassigned"
            reason = ""

            # Cohort/specialization restriction penalty if mismatched
            cohort_penalty = 0.0
            if meta["cohort"] and subj.cohort and meta["cohort"] != "Global" and subj.cohort != meta["cohort"]:
                cohort_penalty = 0.15

            # Exact code match
            if subj.code:
                norm_code = normalize_text_key(subj.code)
                if norm_code and (
                    any(normalize_text_key(str(p)) == norm_code for p in probes)
                    or f" {norm_code} " in padded_full
                ):
                    best_score = max(best_score, 0.96 - cohort_penalty)
                    best_method = "explicit_code"
                    reason = f"exact subject code '{subj.code}'"

            # Exact alias / title match
            for alias in (subj.title, *subj.aliases):
                norm_a = normalize_text_key(alias)
                if not norm_a:
                    continue
                if any(normalize_text_key(str(p)) == norm_a for p in probes):
                    score = 0.95 - cohort_penalty
                    if score > best_score:
                        best_score = score
                        best_method = "confirmed_alias"
                        reason = f"exact alias '{alias}'"
                elif len(norm_a) >= 3 and f" {norm_a} " in padded_full:
                    score = 0.91 - cohort_penalty
                    if score > best_score:
                        best_score = score
                        best_method = "normalized_token"
                        reason = f"contained normalized alias '{alias}'"

            # Token Jaccard & fuzzy similarity
            if best_score < 0.90:
                subj_tokens = significant_tokens(subj.title)
                if subj_tokens and full_tokens:
                    overlap = len(subj_tokens & full_tokens) / len(subj_tokens)
                    if overlap > 0:
                        tok_score = (0.60 + 0.30 * overlap) - cohort_penalty
                        if tok_score > best_score:
                            best_score = tok_score
                            best_method = "normalized_token"
                            reason = f"token overlap ({overlap:.2f}) with '{subj.title}'"

                for probe in probes:
                    ratio = SequenceMatcher(
                        None, normalize_text_key(str(probe)), normalize_text_key(subj.title)
                    ).ratio()
                    fuzzy_score = ratio * 0.88 - cohort_penalty
                    if fuzzy_score > best_score:
                        best_score = fuzzy_score
                        best_method = "fuzzy"
                        reason = f"fuzzy similarity ({ratio:.2f}) to '{subj.title}'"

            if best_score >= 0.45:
                scored.append((round(best_score, 4), best_method, subj, reason))

        scored.sort(key=lambda item: (-item[0], item[2].subject_id))

        candidates = tuple(
            {
                "subject_id": s.subject_id,
                "title": s.title,
                "code": s.code,
                "confidence": sc,
                "method": meth,
                "reason": rsn,
            }
            for sc, meth, s, rsn in scored[:3]
        )

        if not scored or scored[0][0] < REVIEW_MATCH_THRESHOLD:
            return EntityResolutionResult(
                canonical_subject_id=None,
                canonical_subject_title=None,
                subject_code=None,
                confidence=scored[0][0] if scored else 0.0,
                needs_review=bool(scored),
                matched_by="unassigned",
                candidate_subjects=candidates,
                academic_year=meta["academic_year"],
                historical_year=meta["historical_year"],
                cohort=meta["cohort"],
                semester=meta["semester"],
                specialization=meta["specialization"],
                chapter=meta["chapter"],
                duplicate_of_external_id=duplicate_of,
                evidence=("no confident subject match above review threshold",),
            )

        top_score, top_method, top_subj, top_reason = scored[0]
        # Ambiguity / tie detection: if runner-up is within 0.06, preserve for human review!
        has_conflict = len(scored) > 1 and (top_score - scored[1][0]) <= 0.06
        needs_review = has_conflict or (top_score < AUTO_MATCH_THRESHOLD)
        evidence_list = [top_reason]
        if has_conflict:
            evidence_list.append(
                f"ambiguous match: runner-up '{scored[1][2].title}' ({scored[1][0]:.2f}) within tie margin"
            )

        return EntityResolutionResult(
            canonical_subject_id=top_subj.subject_id,
            canonical_subject_title=top_subj.title,
            subject_code=top_subj.code,
            confidence=top_score,
            needs_review=needs_review,
            matched_by=top_method,
            candidate_subjects=candidates,
            academic_year=meta["academic_year"],
            historical_year=meta["historical_year"],
            cohort=meta["cohort"] or top_subj.cohort,
            semester=meta["semester"] or top_subj.semester,
            specialization=meta["specialization"] or top_subj.specialization,
            teaching_unit=top_subj.teaching_unit,
            chapter=meta["chapter"],
            duplicate_of_external_id=duplicate_of,
            evidence=tuple(evidence_list),
        )

    def match_exams_and_corrections(
        self,
        items: list[tuple[SourceRecord, DocumentCategory, EntityResolutionResult]],
    ) -> list[ExamCorrectionMatch]:
        """Associate historical exams with their corrections, flagging ties for confirmation."""
        exams = [item for item in items if item[1] == DocumentCategory.EXAMINATION]
        corrections = [item for item in items if item[1] == DocumentCategory.EXAMINATION_CORRECTION]
        matches: list[ExamCorrectionMatch] = []

        def strip_exam_words(title: str) -> str:
            t = normalize_text_key(title)
            t = re.sub(
                r"\b(?:corrige|correction|corriges|solution|solutions|sujet|enonce|exam|examen|partiel|annale|annales|pdf)\b",
                " ",
                t,
            )
            return re.sub(r"\s+", " ", t).strip()

        for exam_rec, _, exam_res in exams:
            # Respect confirmed manual exam-correction override
            if exam_rec.external_id in self._manual.confirmed_exam_pairs:
                corr_id = self._manual.confirmed_exam_pairs[exam_rec.external_id]
                matches.append(
                    ExamCorrectionMatch(
                        exam_external_id=exam_rec.external_id,
                        correction_external_id=corr_id,
                        confidence=1.0,
                        needs_review=False,
                        evidence=("preserved human confirmed exam-correction link",),
                        subject_id=exam_res.canonical_subject_id,
                        historical_year=exam_res.historical_year,
                    )
                )
                continue

            exam_core = strip_exam_words(exam_rec.title)
            scored_corrs: list[tuple[float, SourceRecord, list[str]]] = []

            for corr_rec, _, corr_res in corrections:
                score = 0.0
                ev: list[str] = []

                # Subject compatibility
                if exam_res.canonical_subject_id and corr_res.canonical_subject_id:
                    if exam_res.canonical_subject_id != corr_res.canonical_subject_id:
                        continue
                    score += 0.42
                    ev.append(f"same canonical subject '{exam_res.canonical_subject_id}'")

                # Historical year compatibility
                if exam_res.historical_year and corr_res.historical_year:
                    if exam_res.historical_year != corr_res.historical_year:
                        continue
                    score += 0.30
                    ev.append(f"same historical year '{exam_res.historical_year}'")

                # Same folder proximity
                if exam_rec.folder_path and corr_rec.folder_path and exam_rec.folder_path == corr_rec.folder_path:
                    score += 0.10
                    ev.append("located in same folder")

                # Title/session stem similarity
                corr_core = strip_exam_words(corr_rec.title)
                if exam_core and corr_core:
                    sim = SequenceMatcher(None, exam_core, corr_core).ratio()
                    score += 0.25 * sim
                    ev.append(f"stem similarity {sim:.2f}")

                score = min(0.99, round(score, 4))
                if score >= REVIEW_MATCH_THRESHOLD:
                    scored_corrs.append((score, corr_rec, ev))

            scored_corrs.sort(key=lambda x: (-x[0], x[1].external_id))
            if not scored_corrs:
                continue

            best_score, best_corr, best_ev = scored_corrs[0]
            tie = len(scored_corrs) > 1 and (best_score - scored_corrs[1][0]) <= 0.06
            if tie:
                best_ev.append("tie detected between multiple candidate corrections; confirmation required")

            matches.append(
                ExamCorrectionMatch(
                    exam_external_id=exam_rec.external_id,
                    correction_external_id=best_corr.external_id,
                    confidence=best_score,
                    needs_review=tie or best_score < AUTO_MATCH_THRESHOLD,
                    evidence=tuple(best_ev),
                    subject_id=exam_res.canonical_subject_id,
                    historical_year=exam_res.historical_year,
                )
            )

        return matches

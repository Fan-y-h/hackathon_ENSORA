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


_GENERIC_FOLDER_SEGMENTS = frozenset(
    {
        "ensora",
        "moodle",
        "local",
        "1a",
        "2a",
        "3a",
        "global",
        "cours",
        "td",
        "tp",
        "cm",
        "ds",
        "dm",
        "annales",
        "annale",
        "exam",
        "exams",
        "examen",
        "examens",
        "partiel",
        "partiels",
        "corrige",
        "corriges",
        "correction",
        "corrections",
        "sujet",
        "sujets",
        "exercice",
        "exercices",
        "fiche",
        "fiches",
        "diapos",
        "slides",
        "support",
        "supports",
        "ressources",
        "documents",
        "divers",
        "autres",
        "code",
        "scripts",
        "notebook",
        "notebooks",
        "projet",
        "projets",
        "archives",
        "archive",
        "data",
        "dataset",
        "datasets",
        "donnees",
        "general",
        "tronc commun",
        "s1",
        "s2",
        "s3",
        "s4",
        "s5",
        "s6",
        "semestre 1",
        "semestre 2",
        "semestre 3",
        "semestre 4",
        "semestre 5",
        "semestre 6",
    }
)

_LEADING_CODE_PREFIX_RE = re.compile(r"^\s*([A-Z]{2,6}[\-_]?\d{2,4}[A-Z]?)\s*[-:–—/]\s*(.+)$")
_LEADING_COHORT_OR_SEM_PREFIX_RE = re.compile(
    r"^\s*(?:1A|2A|3A|S[1-6]|Semestre\s*[1-6]|UE\s*\d+[A-Z]?)\s*[-:–—/]\s*(.+)$",
    re.IGNORECASE,
)
_TRAILING_TAG_RE = re.compile(
    r"\s*(?:\([^)]{1,32}\)|[-:–—]\s*(?:cm(?:\s*/\s*td)?|td|tp|cours|s[1-6]|1a|2a|3a|option|obligatoire))\s*$",
    re.IGNORECASE,
)
_INTRO_WRAPPER_RE = re.compile(
    r"^(?:introduction\s+(?:a\s+l\s+|au\s+|a\s+la\s+|aux\s+|to\s+)|elements\s+d[e']\s*|fondements\s+d[e']\s*|bases\s+d[e']\s*|cours\s+d[e']\s*)(.+)$",
    re.IGNORECASE,
)

# Conservative bilingual French/English ENSAI curriculum equivalents (normalized keys).
# Only used as secondary aliases and ALWAYS subject to strict tie/conflict detection!
_BILINGUAL_EQUIVALENTS: tuple[tuple[str, ...], ...] = (
    ("apprentissage automatique", "apprentissage statistique", "machine learning"),
    ("apprentissage profond", "deep learning"),
    ("series temporelles", "serie temporelle", "series chronologiques", "time series"),
    ("base de donnees", "bases de donnees", "databases", "database", "bdd"),
    ("probabilite", "probabilites", "probability", "proba"),
    ("econometrie", "econometrics"),
    ("statistique mathematique", "statistiques mathematiques", "stat math", "mathematical statistics"),
    ("statistique bayesienne", "statistiques bayesiennes", "bayesian statistics", "bayesien"),
    ("calcul stochastique", "stochastic calculus"),
    ("processus stochastique", "processus stochastiques", "stochastic processes"),
    ("analyse de donnees", "analyse des donnees", "data analysis"),
    ("modele lineaire", "modeles lineaires", "linear model", "linear models"),
    ("theorie des sondages", "sondages", "sampling", "survey sampling"),
    ("microeconomie", "microeconomics"),
    ("macroeconomie", "macroeconomics"),
    ("optimisation", "optimization"),
    ("algebre lineaire", "linear algebra"),
    ("programmation orientee objet", "object oriented programming", "poo"),
    ("algorithme", "algorithmique", "algorithms"),
    ("biostatistique", "biostatistiques", "biostatistics", "biostat"),
)


def _stem_token(token: str) -> str:
    """Conservative French/English plural/inflection stemmer for academic subject matching."""
    t = token.strip().lower()
    if len(t) <= 3:
        return t
    if t.endswith("aux") and len(t) >= 6:
        return t[:-3] + "al"
    if t.endswith("ies") and len(t) >= 5:
        return t[:-3] + "y"
    if t.endswith("es") and len(t) >= 6 and t[-3] in ("s", "x", "z", "h"):
        return t[:-2]
    if t.endswith(("s", "x")) and not t.endswith(("ss", "us", "is", "as")):
        return t[:-1]
    return t


def _stem_phrase(text: str) -> str:
    norm = normalize_text_key(text)
    if not norm:
        return ""
    return " ".join(_stem_token(tok) for tok in norm.split())


def significant_tokens(text: str) -> set[str]:
    return {t for t in normalize_text_key(text).split() if len(t) >= 2 and t not in _STOPWORDS}


def significant_stemmed_tokens(text: str) -> set[str]:
    return {_stem_token(t) for t in significant_tokens(text)}


def _extract_subject_core_variants(title: str, code: str | None = None) -> tuple[list[str], str | None]:
    """Derive clean deterministic core title variants & inferred leading code from a curriculum title."""
    variants: list[str] = []
    inferred_code = code
    cur = (title or "").strip()
    if not cur:
        return variants, inferred_code

    def _add(val: str) -> None:
        clean = " ".join(val.split()).strip(" -–—:/")
        if clean and clean not in variants:
            variants.append(clean)

    _add(cur)
    # Strip leading explicit subject code (e.g. "ST201 - Séries temporelles")
    m_code = _LEADING_CODE_PREFIX_RE.match(cur)
    if m_code:
        if not inferred_code:
            inferred_code = m_code.group(1).strip()
        cur = m_code.group(2).strip()
        _add(cur)

    # Strip leading cohort/semester/UE prefix (e.g. "2A - Économétrie")
    for _ in range(2):
        m_pre = _LEADING_COHORT_OR_SEM_PREFIX_RE.match(cur)
        if not m_pre:
            break
        cur = m_pre.group(1).strip()
        _add(cur)

    # Strip trailing parentheticals / course-format suffixes (e.g. "(S3)", "- CM/TD")
    stripped_tail = cur
    for _ in range(2):
        next_tail = _TRAILING_TAG_RE.sub("", stripped_tail).strip()
        if next_tail == stripped_tail or not next_tail:
            break
        stripped_tail = next_tail
        _add(stripped_tail)

    # Strip common introductory wrapper only when core remains specific enough
    norm_tail = normalize_text_key(stripped_tail)
    m_intro = _INTRO_WRAPPER_RE.match(norm_tail)
    if m_intro:
        core = m_intro.group(1).strip()
        if len(core) >= 8 or len(significant_tokens(core)) >= 2:
            _add(core)

    return variants, inferred_code


def _bilingual_aliases_for(normalized_names: Iterable[str]) -> list[str]:
    extras: list[str] = []
    stemmed_inputs = {_stem_phrase(n) for n in normalized_names if n}
    raw_inputs = {normalize_text_key(n) for n in normalized_names if n}
    for group in _BILINGUAL_EQUIVALENTS:
        group_stemmed = {_stem_phrase(g) for g in group}
        if (raw_inputs & set(group)) or (stemmed_inputs & group_stemmed):
            for term in group:
                if term not in raw_inputs and term not in extras:
                    extras.append(term)
    return extras


def _subject_initialism(title: str) -> str | None:
    """Derive a conservative acronym from significant words of a multi-word subject title (e.g. 'Machine Learning' -> 'ml')."""
    words = [w for w in normalize_text_key(title).split() if len(w) >= 2 and w not in _STOPWORDS]
    if len(words) < 2:
        return None
    initials = "".join(w[0] for w in words)
    return initials if 2 <= len(initials) <= 6 else None


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


@dataclass(frozen=True)
class _IndexedSubject:
    """Precomputed normalized, stemmed, bilingual, and token representations for one subject.

    Computed once at ``register_subject()`` time rather than inside the inner per-document
    scoring loop so reconciling 900+ documents against 70+ curriculum subjects avoids
    repeating accent folding, stemming, and alias expansion O(docs * subjects) times.
    """

    subject: CanonicalSubject
    effective_code: str | None
    norm_code: str
    compact_code: str
    semester_upper: str | None
    # (raw_alias, normalized_alias, stemmed_alias)
    explicit_aliases: tuple[tuple[str, str, str], ...]
    # (bilingual_alias, normalized_alias, stemmed_alias)
    bilingual_aliases: tuple[tuple[str, str, str], ...]
    # Acronym candidates derived from the first two core variants
    initialisms: tuple[str, ...]
    # (core_variant, significant_stemmed_tokens, stemmed_variant)
    core_variants: tuple[tuple[str, frozenset[str], str], ...]
    title_stem: str


def _build_indexed_subject(subject: CanonicalSubject) -> _IndexedSubject:
    """Precompute deterministic normalized keys and aliases for ``subject``."""
    core_variants, effective_code = _extract_subject_core_variants(subject.title, subject.code)
    raw_explicit = list(dict.fromkeys([*core_variants, *subject.aliases]))
    raw_bilingual = _bilingual_aliases_for(raw_explicit)

    norm_code = normalize_text_key(effective_code) if effective_code else ""
    compact_code = norm_code.replace(" ", "") if norm_code else ""

    explicit_entries: list[tuple[str, str, str]] = []
    for alias in raw_explicit:
        norm_a = normalize_text_key(alias)
        if not norm_a:
            continue
        explicit_entries.append((alias, norm_a, _stem_phrase(alias)))

    bilingual_entries: list[tuple[str, str, str]] = [
        (b_alias, normalize_text_key(b_alias), _stem_phrase(b_alias))
        for b_alias in raw_bilingual
    ]

    initialisms: list[str] = []
    for variant in core_variants[:2]:
        acr = _subject_initialism(variant)
        if acr and acr not in initialisms:
            initialisms.append(acr)

    variant_entries: list[tuple[str, frozenset[str], str]] = [
        (variant, frozenset(significant_stemmed_tokens(variant)), _stem_phrase(variant))
        for variant in core_variants
    ]

    return _IndexedSubject(
        subject=subject,
        effective_code=effective_code,
        norm_code=norm_code,
        compact_code=compact_code,
        semester_upper=subject.semester.upper() if subject.semester else None,
        explicit_aliases=tuple(explicit_entries),
        bilingual_aliases=tuple(bilingual_entries),
        initialisms=tuple(initialisms),
        core_variants=tuple(variant_entries),
        title_stem=_stem_phrase(subject.title),
    )


def _fuzzy_ratio_pruned(a: str, b: str, min_ratio: float) -> float:
    """Compute ``SequenceMatcher(None, a, b).ratio()`` with exact upper-bound pruning.

    Uses the mathematical length upper bound ``2 * min(|a|, |b|) / (|a| + |b|)``
    and ``SequenceMatcher.quick_ratio()`` to skip full O(|a|*|b|) alignment whenever
    the resulting score is guaranteed to fall below ``min_ratio``. Returns exact
    ``SequenceMatcher.ratio()`` whenever the bound is met, or ``0.0`` if pruned.
    """
    la = len(a)
    lb = len(b)
    if la == 0 or lb == 0:
        return 0.0
    if min_ratio > 0.0:
        if (2.0 * (la if la < lb else lb) / (la + lb)) < min_ratio:
            return 0.0
    matcher = SequenceMatcher(None, a, b)
    if min_ratio > 0.0 and matcher.quick_ratio() < min_ratio:
        return 0.0
    return matcher.ratio()


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
    """Reconciles subjects, academic metadata, duplicates, and exam-correction pairs.

    Pre-indexes every registered ``CanonicalSubject`` in ``register_subject()`` so
    reconciling large document corpora avoids repeating text normalization, stemming,
    and bilingual alias expansion inside the per-document scoring loop. Preserves
    deterministic confidence thresholds (``>= 0.90`` auto-match when unambiguous,
    ``0.65-0.90`` human review, ``<= 0.06`` tie margin forced to review) and gives
    unconditional precedence to confirmed human overrides.
    """

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
        self._indexed_subjects: dict[str, _IndexedSubject] = {}
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
        """Return the mutable manual override and confirmed alias store."""
        return self._manual

    def register_subject(self, subject: CanonicalSubject) -> None:
        """Register or update a canonical curriculum subject and precompute its search index."""
        prev = self._subjects.get(subject.subject_id)
        if prev == subject and subject.subject_id in self._indexed_subjects:
            return
        self._subjects[subject.subject_id] = subject
        self._indexed_subjects[subject.subject_id] = _build_indexed_subject(subject)
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

        # Candidate search texts in decreasing specificity (including individual non-generic folder segments
        # so Drive paths like 'Ensora/2A/Econometrie/Annales' match canonical subject 'Econometrie' cleanly!)
        folder_segments: list[str] = []
        if record.folder_path:
            for seg in str(record.folder_path).split("/"):
                clean_seg = seg.strip()
                norm_seg = normalize_text_key(clean_seg)
                if clean_seg and norm_seg and norm_seg not in _GENERIC_FOLDER_SEGMENTS:
                    folder_segments.append(clean_seg)

        probes = [
            p
            for p in (
                record.course_id,
                record.course_title,
                record.payload.get("subject_hint") if isinstance(record.payload, dict) else None,
                *folder_segments,
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

        # 3. Score all registered canonical subjects (code -> alias/core -> bilingual -> stemmed token -> fuzzy)
        scored: list[tuple[float, str, CanonicalSubject, str]] = []
        full_probe_norm = normalize_text_key(" ".join(str(p) for p in probes))
        full_probe_compact = full_probe_norm.replace(" ", "")
        full_probe_stemmed = _stem_phrase(full_probe_norm)
        full_tokens = significant_stemmed_tokens(full_probe_norm)
        padded_full = f" {full_probe_norm} "
        padded_stemmed = f" {full_probe_stemmed} "
        dedicated_probes = [
            str(p)
            for p in (
                record.course_id,
                record.course_title,
                record.payload.get("subject_hint") if isinstance(record.payload, dict) else None,
                *folder_segments,
            )
            if p
        ]
        norm_probes = [normalize_text_key(str(p)) for p in probes if p]
        norm_probes_set = set(norm_probes)
        compact_probes_set = {np.replace(" ", "") for np in norm_probes}
        stemmed_probes = [_stem_phrase(str(p)) for p in probes if p]
        stemmed_probes_set = set(stemmed_probes)
        dedicated_norm = [n for p in dedicated_probes if (n := normalize_text_key(p))]
        dedicated_norm_set = set(dedicated_norm)
        dedicated_stemmed = [s for p in dedicated_probes if (s := _stem_phrase(p))]
        dedicated_stemmed_set = set(dedicated_stemmed)
        dedicated_stemmed_long = [s for s in dedicated_stemmed if len(s) >= 5]
        dedicated_token_union: set[str] = set()
        for dp in dedicated_probes:
            dedicated_token_union.update(significant_stemmed_tokens(dp))

        doc_cohort = meta["cohort"]
        doc_semester_upper = meta["semester"].upper() if meta["semester"] else None

        for idx_subj in self._indexed_subjects.values():
            subj = idx_subj.subject
            best_score = 0.0
            best_method = "unassigned"
            reason = ""

            # Cohort/specialization restriction penalty if explicitly mismatched
            cohort_penalty = 0.0
            if doc_cohort and subj.cohort and doc_cohort != "Global" and subj.cohort != doc_cohort:
                cohort_penalty = 0.15

            # Deterministic semester tie-breaker when BOTH document and subject carry explicit semester
            semester_bonus = 0.0
            if doc_semester_upper and idx_subj.semester_upper:
                if doc_semester_upper == idx_subj.semester_upper:
                    semester_bonus = 0.04
                else:
                    cohort_penalty += 0.08

            # Exact code match (including compact form e.g. STAT-201 <-> STAT201)
            norm_code = idx_subj.norm_code
            if norm_code:
                compact_code = idx_subj.compact_code
                if (
                    norm_code in norm_probes_set
                    or compact_code in compact_probes_set
                    or f" {norm_code} " in padded_full
                    or (len(compact_code) >= 4 and compact_code in full_probe_compact)
                ):
                    best_score = max(best_score, 0.96 - cohort_penalty + semester_bonus)
                    best_method = "explicit_code"
                    reason = f"exact subject code '{idx_subj.effective_code}'"

            # Exact alias / core title / stemmed-core match
            for alias, norm_a, stem_a in idx_subj.explicit_aliases:
                if norm_a in norm_probes_set or (stem_a and stem_a in stemmed_probes_set):
                    score = 0.95 - cohort_penalty + semester_bonus
                    if score > best_score:
                        best_score = score
                        best_method = "confirmed_alias"
                        reason = f"exact normalized/stemmed title '{alias}'"
                elif (len(norm_a) >= 4 and f" {norm_a} " in padded_full) or (
                    len(stem_a) >= 4 and f" {stem_a} " in padded_stemmed
                ):
                    score = 0.91 - cohort_penalty + semester_bonus
                    if score > best_score:
                        best_score = score
                        best_method = "normalized_token"
                        reason = f"contained normalized/stemmed title '{alias}'"

            # Conservative bilingual French/English ENSAI curriculum match
            if best_score < 0.92 and idx_subj.bilingual_aliases:
                for b_alias, norm_b, stem_b in idx_subj.bilingual_aliases:
                    if norm_b in dedicated_norm_set or (stem_b and stem_b in dedicated_stemmed_set):
                        score = 0.92 - cohort_penalty + semester_bonus
                        if score > best_score:
                            best_score = score
                            best_method = "normalized_token"
                            reason = f"bilingual curriculum alias '{b_alias}' for '{subj.title}'"
                    elif (len(norm_b) >= 5 and f" {norm_b} " in padded_full) or (
                        len(stem_b) >= 5 and f" {stem_b} " in padded_stemmed
                    ):
                        score = 0.90 - cohort_penalty + semester_bonus
                        if score > best_score:
                            best_score = score
                            best_method = "normalized_token"
                            reason = f"contained bilingual alias '{b_alias}' for '{subj.title}'"

            # Conservative acronym/initialism review hint (capped below AUTO_MATCH_THRESHOLD)
            if best_score < 0.85 and idx_subj.initialisms:
                for acr in idx_subj.initialisms:
                    if acr in norm_probes_set or (len(acr) >= 3 and f" {acr} " in padded_full):
                        acr_score = 0.82 - cohort_penalty
                        if acr_score > best_score:
                            best_score = acr_score
                            best_method = "normalized_token"
                            reason = f"subject initialism '{acr.upper()}' matches '{subj.title}'"

            # Stemmed token overlap & per-segment fuzzy similarity
            if best_score < 0.92:
                if full_tokens:
                    for variant, subj_tokens, _var_stem in idx_subj.core_variants:
                        if not subj_tokens:
                            continue
                        matched_count = len(subj_tokens & full_tokens)
                        if matched_count > 0:
                            overlap = matched_count / len(subj_tokens)
                            # Full stemmed coverage of >=2 distinctive tokens (or 1 long distinctive token
                            # matching a dedicated folder/course segment) reaches confident auto-match,
                            # still subject to strict tie/conflict detection!
                            if overlap >= 1.0 and (
                                len(subj_tokens) >= 2
                                or (
                                    len(subj_tokens) == 1
                                    and len(next(iter(subj_tokens))) >= 7
                                    and next(iter(subj_tokens)) in dedicated_token_union
                                )
                            ):
                                tok_score = 0.92 - cohort_penalty + semester_bonus
                            else:
                                tok_score = (0.60 + 0.30 * overlap) - cohort_penalty + semester_bonus
                            if tok_score > best_score:
                                best_score = tok_score
                                best_method = "normalized_token"
                                reason = f"stemmed token overlap ({overlap:.2f}) with '{variant}'"

                # Dedicated folder/course segment fuzzy similarity (handles minor spelling/accent variations)
                for seg_stem in dedicated_stemmed_long:
                    for variant, _subj_tokens, var_stem in idx_subj.core_variants:
                        if len(var_stem) < 5:
                            continue
                        min_needed = max(best_score, 0.4499)
                        min_ratio = (min_needed + cohort_penalty - semester_bonus) / 0.94
                        ratio = _fuzzy_ratio_pruned(seg_stem, var_stem, min_ratio)
                        if ratio <= 0.0:
                            continue
                        scale = 0.94 if ratio >= 0.92 else 0.88
                        fuzzy_score = ratio * scale - cohort_penalty + semester_bonus
                        if fuzzy_score > best_score:
                            best_score = fuzzy_score
                            best_method = "fuzzy"
                            reason = f"folder segment similarity ({ratio:.2f}) to '{variant}'"

                # Fallback fuzzy similarity across all probes (strictly capped below AUTO_MATCH_THRESHOLD)
                if best_score < 0.85 and idx_subj.title_stem:
                    title_stem = idx_subj.title_stem
                    for probe_s in stemmed_probes:
                        min_needed = max(best_score, 0.4499)
                        min_ratio = (min_needed + cohort_penalty) / 0.86
                        ratio = _fuzzy_ratio_pruned(probe_s, title_stem, min_ratio)
                        if ratio <= 0.0:
                            continue
                        fuzzy_score = ratio * 0.86 - cohort_penalty
                        if fuzzy_score > best_score:
                            best_score = fuzzy_score
                            best_method = "fuzzy"
                            reason = f"fuzzy similarity ({ratio:.2f}) to '{subj.title}'"

            best_score = min(0.99, best_score)
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

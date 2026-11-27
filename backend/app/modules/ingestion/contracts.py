"""Typed contracts, protocols, and normalized DTOs for Ensora+ source ingestion.

Aligned with docs/DATA_ENGINEERING.md, docs/DATA_MODEL.md, docs/API_CONTRACTS.md,
and docs/GRADE_TRACKER.md. Designed to remain decoupled from FastAPI/ORM models
and bridge cleanly into Codex-owned shared contracts (backend/app/core and
backend/app/contracts) when present.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol, Sequence, runtime_checkable


def utc_now_iso() -> str:
    """Return current UTC timestamp formatted as ISO 8601."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SourceKind(str, Enum):
    """Supported read-only educational and academic sources.

    Personal Google Drive is intentionally excluded by P1 policy.
    """

    MOODLE = "moodle"
    PAMPLEMOUSSE = "pamplemousse"
    ENSORA_DRIVE = "ensora_drive"
    GOOGLE_CALENDAR = "google_calendar"
    LOCAL_FILE = "local_file"
    ACADEMIC_IMPORT = "academic_import"


class AuthState(str, Enum):
    """Lifecycle state of a source authentication session."""

    UNCONFIGURED = "unconfigured"
    NEEDS_LOGIN = "needs_login"
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class AccessStatus(str, Enum):
    """Access status of a remote or local resource."""

    ACTIVE = "active"
    TOMBSTONED = "tombstoned"
    REVOKED = "revoked"
    BLOCKED = "blocked"
    ERROR = "error"


class DocumentCategory(str, Enum):
    """Normalized document classification categories."""

    LECTURE = "lecture"
    TUTORIAL = "tutorial"
    PRACTICAL_WORK = "practical_work"
    EXAMINATION = "examination"
    EXAMINATION_CORRECTION = "examination_correction"
    ASSIGNMENT = "assignment"
    COURSE_SUMMARY = "course_summary"
    NOTEBOOK = "notebook"
    SOURCE_CODE = "source_code"
    ADMINISTRATIVE = "administrative"
    OTHER = "other"


class CalendarEventType(str, Enum):
    """Normalized calendar event categories matching docs/API_CONTRACTS.md."""

    CLASS = "class"
    TD = "td"
    TP = "tp"
    EXAM = "exam"
    PRESENTATION = "presentation"
    PROJECT = "project"
    SPORT = "sport"
    DEADLINE = "deadline"
    PERSONAL = "personal"
    OTHER = "other"


class ValueStatus(str, Enum):
    """Explicit provenance/certainty status for academic data (never fabricate values)."""

    CONFIRMED = "confirmed"
    PROVISIONAL = "provisional"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AuthStatus:
    """Safe authentication status report (never contains secrets or tickets)."""

    source: SourceKind
    state: AuthState
    authenticated: bool
    detail: str
    account_hint: str | None = None
    scopes: tuple[str, ...] = ()
    checked_at: str = field(default_factory=utc_now_iso)
    requires_interactive_action: bool = False
    next_action: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        data["state"] = self.state.value
        data["scopes"] = list(self.scopes)
        return data


@dataclass(frozen=True)
class ConnectorCapabilities:
    """Declares what operations and entity types a connector supports."""

    source: SourceKind
    supports_documents: bool = False
    supports_events: bool = False
    supports_assignments: bool = False
    supports_academic_records: bool = False
    supports_incremental: bool = False
    supports_full_scan: bool = True
    supports_download: bool = False
    read_only: bool = True
    fallback_formats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        data["fallback_formats"] = list(self.fallback_formats)
        return data


@dataclass(frozen=True)
class SourceRecord:
    """Normalized record emitted by connectors for Codex's ETL pipeline."""

    source_kind: SourceKind
    external_id: str
    title: str
    record_kind: str = "document"  # document | event | assignment | announcement | academic
    remote_version: str | None = None
    remote_modified_at: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    content_hash: str | None = None
    folder_path: str | None = None
    course_id: str | None = None
    course_title: str | None = None
    section_title: str | None = None
    source_url: str | None = None
    access_status: AccessStatus = AccessStatus.ACTIVE
    download_url: str | None = None
    local_path: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_kind"] = self.source_kind.value
        data["access_status"] = self.access_status.value
        return data


@dataclass(frozen=True)
class CalendarEventRecord:
    """Normalized timetable or personal calendar event."""

    source_kind: SourceKind
    external_id: str
    title: str
    event_type: CalendarEventType = CalendarEventType.OTHER
    start_at: str | None = None  # ISO 8601 UTC
    end_at: str | None = None  # ISO 8601 UTC
    all_day_date: str | None = None  # YYYY-MM-DD when all-day
    all_day_end_date: str | None = None
    original_timezone: str | None = None
    status: str = "confirmed"  # confirmed | tentative | cancelled
    room: str | None = None
    teacher: str | None = None
    group: str | None = None
    subject_hint: str | None = None
    course_id: str | None = None
    recurrence_key: str | None = None
    confirmation_required: bool = False
    confirmation_reason: str | None = None
    busy: bool = True
    description: str | None = None
    source_url: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_kind"] = self.source_kind.value
        data["event_type"] = self.event_type.value
        return data


@dataclass(frozen=True)
class AvailabilityInterval:
    """Privacy-preserving busy/free time interval for Smart Planner."""

    start_at: str  # ISO 8601 UTC
    end_at: str  # ISO 8601 UTC
    status: str  # busy | free | tentative
    all_day: bool = False
    source_kind: SourceKind = SourceKind.GOOGLE_CALENDAR
    external_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_kind"] = self.source_kind.value
        return data


@dataclass(frozen=True)
class AcademicRecord:
    """Normalized academic curriculum, weight, rule, or grade record.

    All numeric decimals are serialized as strings to avoid binary float drift.
    Missing values remain None with explicit ValueStatus.MISSING/UNKNOWN.
    """

    source_kind: SourceKind
    external_id: str
    record_type: str  # program | unit | subject | assessment_group | assessment | grade | rule
    title: str
    parent_external_id: str | None = None
    academic_year: str | None = None
    cohort: str | None = None
    semester: str | None = None
    specialization: str | None = None
    subject_code: str | None = None
    weight: Decimal | None = None
    weight_mode: str | None = None  # coefficient | fraction | None
    weight_status: ValueStatus = ValueStatus.MISSING
    grade_value: Decimal | None = None
    grade_scale_max: Decimal | None = None
    grade_kind: str | None = None  # actual | estimated | None
    grade_status: ValueStatus = ValueStatus.MISSING
    observed_at: str | None = None
    rule_payload: dict[str, Any] | None = None
    rule_verification_status: ValueStatus = ValueStatus.UNKNOWN
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind.value,
            "external_id": self.external_id,
            "record_type": self.record_type,
            "title": self.title,
            "parent_external_id": self.parent_external_id,
            "academic_year": self.academic_year,
            "cohort": self.cohort,
            "semester": self.semester,
            "specialization": self.specialization,
            "subject_code": self.subject_code,
            "weight": str(self.weight) if self.weight is not None else None,
            "weight_mode": self.weight_mode,
            "weight_status": self.weight_status.value,
            "grade_value": str(self.grade_value) if self.grade_value is not None else None,
            "grade_scale_max": str(self.grade_scale_max) if self.grade_scale_max is not None else None,
            "grade_kind": self.grade_kind,
            "grade_status": self.grade_status.value,
            "observed_at": self.observed_at,
            "rule_payload": self.rule_payload,
            "rule_verification_status": self.rule_verification_status.value,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ChangePage:
    """Paginated result returned by SourceConnector.list_changes."""

    items: tuple[SourceRecord, ...]
    next_cursor: str | None
    complete: bool
    sync_token: str | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "next_cursor": self.next_cursor,
            "complete": self.complete,
            "sync_token": self.sync_token,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class FetchResult:
    """Result of downloading or reading a source blob."""

    external_id: str
    content: bytes
    content_hash: str
    mime_type: str
    size_bytes: int
    filename: str
    exported_from_mime: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class Locator:
    """Citation-ready location inside a parsed document (1-based UI labels)."""

    kind: str  # page | slide | cell | line | section | table | figure
    page: int | None = None
    slide: int | None = None
    cell: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    section: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedBlock:
    """Structured content unit emitted by document parsers."""

    ordinal: int
    kind: str  # heading | paragraph | code | math | table | figure | note | output | raw
    text: str
    locator: Locator
    heading_path: tuple[str, ...] = ()
    language: str | None = None
    code_language: str | None = None
    table_rows: tuple[tuple[str, ...], ...] = ()
    math_expressions: tuple[str, ...] = ()
    figure_caption: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "kind": self.kind,
            "text": self.text,
            "locator": self.locator.to_dict(),
            "heading_path": list(self.heading_path),
            "language": self.language,
            "code_language": self.code_language,
            "table_rows": [list(r) for r in self.table_rows],
            "math_expressions": list(self.math_expressions),
            "figure_caption": self.figure_caption,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ParsedDocument:
    """Complete output of parsing a document, preserving provenance & quality."""

    parser_name: str
    parser_version: str
    title: str | None
    mime_type: str
    content_hash: str
    blocks: tuple[ParsedBlock, ...]
    headings: tuple[str, ...] = ()
    math_expressions: tuple[str, ...] = ()
    tables: tuple[dict[str, Any], ...] = ()
    figures: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    extraction_quality: float = 1.0  # 0.0 .. 1.0
    ocr_used: bool = False
    unsupported_elements: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def full_text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks if b.text.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "title": self.title,
            "mime_type": self.mime_type,
            "content_hash": self.content_hash,
            "blocks": [b.to_dict() for b in self.blocks],
            "headings": list(self.headings),
            "math_expressions": list(self.math_expressions),
            "tables": list(self.tables),
            "figures": list(self.figures),
            "metadata": self.metadata,
            "provenance": self.provenance,
            "extraction_quality": self.extraction_quality,
            "ocr_used": self.ocr_used,
            "unsupported_elements": list(self.unsupported_elements),
            "warnings": list(self.warnings),
            "error": self.error,
        }


@dataclass(frozen=True)
class ClassificationResult:
    """Result of document classification with confidence and evidence."""

    category: DocumentCategory
    confidence: float
    evidence: tuple[str, ...]
    method: str = "deterministic"  # deterministic | hybrid_semantic
    alternatives: tuple[tuple[DocumentCategory, float], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "confidence": round(self.confidence, 4),
            "evidence": list(self.evidence),
            "method": self.method,
            "alternatives": [(c.value, round(s, 4)) for c, s in self.alternatives],
        }


@dataclass(frozen=True)
class EntityResolutionResult:
    """Normalized academic classification & cross-source reconciliation result."""

    canonical_subject_id: str | None
    canonical_subject_title: str | None
    subject_code: str | None
    confidence: float
    needs_review: bool
    matched_by: str  # manual_override | explicit_code | confirmed_alias | normalized_token | fuzzy | unassigned
    candidate_subjects: tuple[dict[str, Any], ...] = ()
    academic_year: str | None = None
    historical_year: str | None = None
    cohort: str | None = None
    semester: str | None = None
    specialization: str | None = None
    teaching_unit: str | None = None
    chapter: str | None = None
    duplicate_of_external_id: str | None = None
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExamCorrectionMatch:
    """Candidate or confirmed association between an exam and its correction."""

    exam_external_id: str
    correction_external_id: str
    confidence: float
    needs_review: bool
    evidence: tuple[str, ...]
    subject_id: str | None = None
    historical_year: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class CredentialStoreProtocol(Protocol):
    """Protocol compatible with Codex's keyring/credential abstraction."""

    def get_secret(self, namespace: str, key: str) -> str | None: ...
    def set_secret(self, namespace: str, key: str, value: str) -> None: ...
    def delete_secret(self, namespace: str, key: str) -> None: ...


@runtime_checkable
class AuthenticationProviderProtocol(Protocol):
    """Protocol for authentication adapters compatible with Codex's AuthenticationManager."""

    source: SourceKind

    def status(self) -> AuthStatus: ...
    def authenticate(self, *, interactive: bool = True) -> AuthStatus: ...
    def validate_connection(self) -> AuthStatus: ...
    def disconnect(self) -> AuthStatus: ...


@runtime_checkable
class SemanticClassifierProtocol(Protocol):
    """Optional P2 hook for semantic document classification without duplicating P2."""

    def classify_text(
        self,
        *,
        title: str,
        excerpt: str,
        metadata: dict[str, Any],
    ) -> tuple[DocumentCategory, float, Sequence[str]] | None: ...


def normalize_date_str(value: date | datetime | str | None) -> str | None:
    """Normalize a date/datetime into ISO 8601 UTC string when possible."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)

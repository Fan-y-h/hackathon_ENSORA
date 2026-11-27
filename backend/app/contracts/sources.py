"""Source-independent contracts for authorized, read-only ingestion."""
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

Key = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


class SourceKind(StrEnum):
    MOODLE = "moodle"
    PAMPLEMOUSSE = "pamplemousse"
    ENSORA_DRIVE = "ensora_drive"
    GOOGLE_CALENDAR = "google_calendar"
    LOCAL = "local"
    ACADEMIC_IMPORT = "academic_import"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Provenance(Contract):
    external_id: Key
    remote_revision: Key | None = None
    modified_at: AwareDatetime | None = None
    source: SourceKind | None = None
    source_url: str | None = None
    folder: str | None = None
    academic_year: Key | None = None


class Document(Contract):
    kind: Literal["document"] = "document"
    provenance: Provenance
    title: Key
    mime_type: Key = "application/octet-stream"
    subject_id: Key | None = None
    chapter_id: Key | None = None
    course_id: Key | None = None
    course_title: Key | None = None
    academic_category: Key | None = None
    section_id: Key | None = None
    section_path: list[Key] = Field(default_factory=list)
    teaching_unit_id: Key | None = None
    resource_kind: Literal["document", "announcement", "educational_html"] = "document"
    access_status: Literal["active", "tombstoned", "revoked"] = "active"


class DocumentVersion(Contract):
    id: Key
    document_id: Key
    sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    size_bytes: int = Field(ge=0)
    mime_type: Key
    status: Literal["ready", "partial", "unsupported", "failed"]


class DocumentChunk(Contract):
    text: str
    section: list[str] = Field(default_factory=list)
    locator: dict[str, str | int] = Field(default_factory=dict)
    block_kind: Key | None = None
    structured: dict[str, object] = Field(default_factory=dict)


class ParsedContent(Contract):
    parser_version: Key
    status: Literal["ready", "partial"] = "ready"
    chunks: list[DocumentChunk] = Field(default_factory=list)
    warning_codes: list[Key] = Field(default_factory=list)


class ClassificationResult(Contract):
    resource_type: Key = "other"
    confidence: float = Field(default=0, ge=0, le=1)


class EntityMatch(Contract):
    teaching_unit_id: Key | None = None
    subject_id: Key | None = None
    chapter_id: Key | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    confirmed: bool = False


class CalendarEvent(Contract):
    kind: Literal["event"] = "event"
    provenance: Provenance
    title: Key
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    all_day_start: date | None = None
    all_day_end: date | None = None
    subject_id: Key | None = None
    status: Literal["confirmed", "tentative", "cancelled"] = "confirmed"
    room: str | None = None
    group: str | None = None
    event_type: Key = "other"
    teacher: str | None = None
    confirmation_required: bool = False
    confirmation_reason: str | None = None
    recurrence_key: str | None = None
    original_timezone: str | None = None
    busy: bool = True
    duration_minutes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def interval(self):
        if self.status == "cancelled" and all(value is None for value in (
            self.starts_at, self.ends_at, self.all_day_start, self.all_day_end,
        )):
            return self
        if self.all_day_start is not None:
            valid = (self.all_day_end is not None and self.all_day_start < self.all_day_end
                     and self.starts_at is None and self.ends_at is None)
        else:
            valid = (self.starts_at is not None and self.ends_at is not None
                     and self.starts_at < self.ends_at and self.all_day_end is None)
        if not valid:
            raise ValueError("Expected one positive timed or all-day interval")
        if self.duration_minutes is None and self.starts_at is not None and self.ends_at is not None:
            self.duration_minutes = int((self.ends_at - self.starts_at).total_seconds() // 60)
        return self


class PersonalAvailability(Contract):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    available: bool = True

    @model_validator(mode="after")
    def interval(self):
        if self.starts_at >= self.ends_at:
            raise ValueError("Expected a positive interval")
        return self


class AcademicRecord(Contract):
    provenance: Provenance
    title: Key
    parent_key: Key | None = None
    record_status: Literal["known", "missing", "ambiguous"] = "known"
    source_code: Key | None = None


class AcademicYear(AcademicRecord):
    kind: Literal["academic_year"] = "academic_year"


class Specialization(AcademicRecord):
    kind: Literal["specialization"] = "specialization"


class Semester(AcademicRecord):
    kind: Literal["semester"] = "semester"


class TeachingUnit(AcademicRecord):
    kind: Literal["teaching_unit"] = "teaching_unit"
    ects: Decimal | None = Field(default=None, ge=0)


class Subject(AcademicRecord):
    kind: Literal["subject"] = "subject"
    code: Key | None = None
    ects: Decimal | None = Field(default=None, ge=0)
    aliases: list[Key] = Field(default_factory=list)
    optional_activity: bool | None = None


class Chapter(AcademicRecord):
    kind: Literal["chapter"] = "chapter"
    ordinal: int = Field(default=0, ge=0)


class ConceptEvidence(Contract):
    chunk_ordinal: int = Field(ge=0)
    locator: dict[str, str | int] = Field(default_factory=dict)


class Concept(AcademicRecord):
    kind: Literal["concept"] = "concept"
    evidence: list[ConceptEvidence] = Field(default_factory=list)


class Assessment(AcademicRecord):
    kind: Literal["assessment"] = "assessment"
    deadline_at: AwareDatetime | None = None
    format: Key | None = None
    instructions: str | None = None
    assessment_kind: Key | None = None


class SubjectCoefficient(AcademicRecord):
    kind: Literal["coefficient"] = "coefficient"
    weight: Decimal | None = Field(ge=0)
    level: Literal["unspecified", "year", "semester", "teaching_unit", "subject", "assessment"] = "unspecified"
    mode: Literal["raw", "relative", "fraction"] = "raw"

    @model_validator(mode="after")
    def validate_coefficient(self):
        if self.weight is None and self.record_status == "known":
            raise ValueError("A known coefficient requires a value")
        if self.mode == "fraction" and self.weight is not None and self.weight > 1:
            raise ValueError("Fractional coefficients must be at most one")
        return self


class AssessmentWeight(AcademicRecord):
    kind: Literal["assessment_weight"] = "assessment_weight"
    weight: Decimal = Field(ge=0)
    mode: Literal["relative", "fraction"] = "relative"
    weight_basis: Literal["source_reported", "normalized_sibling_coefficients"] = "source_reported"

    @model_validator(mode="after")
    def fractional_bound(self):
        if self.mode == "fraction" and self.weight > 1:
            raise ValueError("Fractional weights must be at most one")
        return self


class PublishedGrade(AcademicRecord):
    kind: Literal["grade"] = "grade"
    value: Decimal | None
    minimum: Decimal = Decimal(0)
    maximum: Decimal | None = None
    grade_kind: Literal["unspecified", "official_aggregate", "assessment"] = "unspecified"
    rank: int | None = Field(default=None, ge=1)
    published_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def scale(self):
        if self.value is None and self.record_status == "known":
            raise ValueError("A known grade requires a value")
        if self.maximum is not None and self.minimum >= self.maximum:
            raise ValueError("Invalid published grade scale")
        if self.value is not None and (self.value < self.minimum or
                                       (self.maximum is not None and self.value > self.maximum)):
            raise ValueError("Grade lies outside the published scale")
        return self


SourceRecord = Annotated[
    Document | CalendarEvent | AcademicYear | Specialization | Semester | TeachingUnit |
    Subject | Chapter | Concept | Assessment | SubjectCoefficient | AssessmentWeight |
    PublishedGrade, Field(discriminator="kind")
]


class SyncScope(Contract):
    source: SourceKind | None = None
    subject_id: Key | None = None
    folder_id: Key | None = None
    full: bool = False
    discovery_only: bool = False
    academic_year: Key | None = None
    window_start: AwareDatetime | None = None
    window_end: AwareDatetime | None = None

    @model_validator(mode="after")
    def check_window(self):
        if self.academic_year and self.source not in (SourceKind.MOODLE, SourceKind.PAMPLEMOUSSE):
            raise ValueError("Academic-year scope requires Moodle or Pamplemousse")
        if self.window_start is None and self.window_end is None:
            return self
        if (self.source != SourceKind.GOOGLE_CALENDAR or self.window_start is None or
                self.window_end is None or self.window_start >= self.window_end):
            raise ValueError("A positive window requires both endpoints and the Google Calendar source")
        return self


OpaqueCursor = Annotated[str, StringConstraints(min_length=1, max_length=16384)]


class CalendarSnapshot(Contract):
    external_id_prefix: Annotated[str, StringConstraints(pattern=r"^google_calendar:.+:$", max_length=512)]
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def check_bounds(self):
        if (self.starts_at is None) != (self.ends_at is None):
            raise ValueError("Snapshot window needs both bounds")
        if self.starts_at is not None and self.starts_at >= self.ends_at:
            raise ValueError("Snapshot window must have positive duration")
        return self


class FetchedDocument(Contract):
    content: bytes
    mime_type: Key | None = None
    filename: Key | None = None

    @model_validator(mode="after")
    def check_filename(self):
        if self.filename and ("/" in self.filename or chr(92) in self.filename):
            raise ValueError("Fetched filenames must be basenames")
        return self


Observation = Literal[
    "courses_discovered", "courses_traversed", "sections_discovered", "sections_traversed",
    "folders_traversed", "resources_excluded", "resources_discovered", "pages_visited",
    "timetable_events", "enrolled_subjects", "teaching_units", "semester_associations",
    "subject_coefficients", "ects_values", "available_grades", "assessment_records",
    "aggregate_grades", "missing_or_ambiguous_count",
]
NonnegativeCount = Annotated[int, Field(ge=0)]


class ChangePage(Contract):
    items: list[SourceRecord] = Field(default_factory=list)
    next_cursor: OpaqueCursor | None = None
    complete: bool = True
    resume_cursor: OpaqueCursor | None = None
    calendar_snapshot: CalendarSnapshot | None = None
    observations: dict[Observation, NonnegativeCount] = Field(default_factory=dict)
    warning_codes: list[Key] = Field(default_factory=list)
    error_codes: list[Key] = Field(default_factory=list)


class RetryableSourceError(Exception):
    """The connector encountered a transient upstream failure."""


class SourceConnector(Protocol):
    def list_changes(self, scope: SyncScope, cursor: str | None) -> ChangePage: ...
    def fetch_blob(self, record: Document) -> bytes | FetchedDocument: ...


class DocumentParser(Protocol):
    def parse(self, document: Document, content: bytes) -> ParsedContent | None: ...


class DocumentClassifier(Protocol):
    def classify(self, document: Document, parsed: ParsedContent | None) -> ClassificationResult: ...


class EntityMatcher(Protocol):
    def match(self, document: Document, classification: ClassificationResult) -> EntityMatch: ...

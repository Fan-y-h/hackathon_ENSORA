"""Authorized read-only source connectors for Ensora+ P1."""

from .base import (
    AuthenticationRequiredError,
    ConnectorError,
    PermissionDeniedError,
    RateLimiter,
    SourceConnector,
    TransientSourceError,
    guess_mime_type,
    is_same_origin,
    retry_with_backoff,
    sha256_bytes,
)
from .ensora_drive import DriveFolderMetadata, EnsoraDriveConnector
from .google_calendar import GoogleCalendarConnector, SyncTokenExpiredError
from .local_import import LocalFileConnector, StructuredAcademicImporter
from .moodle import (
    MoodleConnector,
    MoodleCourseSummary,
    MoodleResourceItem,
    extract_assignment_deadline,
    parse_moodle_course_html,
    parse_moodle_dashboard_html,
)
from .pamplemousse import (
    PamplemousseConnector,
    classify_timetable_event,
    normalize_timestamp_utc,
    parse_ics_events,
    parse_pamplemousse_academic_html,
    parse_pamplemousse_timetable_html,
)

__all__ = [
    "AuthenticationRequiredError",
    "ConnectorError",
    "PermissionDeniedError",
    "RateLimiter",
    "SourceConnector",
    "TransientSourceError",
    "guess_mime_type",
    "is_same_origin",
    "retry_with_backoff",
    "sha256_bytes",
    "DriveFolderMetadata",
    "EnsoraDriveConnector",
    "GoogleCalendarConnector",
    "SyncTokenExpiredError",
    "LocalFileConnector",
    "StructuredAcademicImporter",
    "MoodleConnector",
    "MoodleCourseSummary",
    "MoodleResourceItem",
    "extract_assignment_deadline",
    "parse_moodle_course_html",
    "parse_moodle_dashboard_html",
    "PamplemousseConnector",
    "classify_timetable_event",
    "normalize_timestamp_utc",
    "parse_ics_events",
    "parse_pamplemousse_academic_html",
    "parse_pamplemousse_timetable_html",
]

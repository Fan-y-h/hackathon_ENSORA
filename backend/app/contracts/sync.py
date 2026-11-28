from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from .authentication import AuthenticationReason, ConnectionStatus
from .sources import Contract, SourceKind


DEFAULT_SYNC_INTERVALS_MINUTES = {
    SourceKind.ENSORA_DRIVE.value: 7 * 24 * 60,
    SourceKind.MOODLE.value: 7 * 24 * 60,
    "pamplemousse.timetable": 6 * 60,
    "pamplemousse.grades": 24 * 60,
    "pamplemousse.catalog": 7 * 24 * 60,
    SourceKind.GOOGLE_CALENDAR.value: 30,
}
SUPPORTED_SYNC_INTERVALS_MINUTES = {30, 60, 360, 720, 1440, 4320, 10080, 20160, 43200}
LEGACY_SYNC_INTERVAL_KEYS = {SourceKind.PAMPLEMOUSSE.value}
AUTO_SYNC_SOURCES = [
    SourceKind.ENSORA_DRIVE, SourceKind.MOODLE, SourceKind.PAMPLEMOUSSE, SourceKind.GOOGLE_CALENDAR,
]


class SyncScheduleSettings(Contract):
    """Persisted automatic refresh policy. Local imports remain event driven/manual."""

    enabled: bool = True
    enabled_sources: list[SourceKind] = Field(default_factory=lambda: list(AUTO_SYNC_SOURCES))
    intervals_minutes: dict[str, int | None] = Field(default_factory=lambda: dict(DEFAULT_SYNC_INTERVALS_MINUTES))

    @field_validator("intervals_minutes")
    @classmethod
    def validate_intervals(cls, value):
        allowed = set(DEFAULT_SYNC_INTERVALS_MINUTES) | LEGACY_SYNC_INTERVAL_KEYS
        if set(value) - allowed or any(minutes is not None and minutes not in SUPPORTED_SYNC_INTERVALS_MINUTES
                                       for minutes in value.values()):
            raise ValueError("Use a supported refresh interval or null for manual-only synchronization")
        return value

    @field_validator("enabled_sources")
    @classmethod
    def validate_enabled_sources(cls, value):
        if set(value) - set(AUTO_SYNC_SOURCES) or len(value) != len(set(value)):
            raise ValueError("Automatic synchronization is unavailable for this source")
        return value


class SourceScheduleStatus(Contract):
    source: SourceKind
    scope_key: Literal["timetable", "grades", "catalog"] | None = None
    enabled: bool
    manual_only: bool = False
    interval_minutes: int | None = None
    connection_status: ConnectionStatus | None = None
    last_success_at: datetime | None = None
    next_scheduled_at: datetime | None = None
    backoff_until: datetime | None = None
    progress: dict[str, object] | None = None
    recent_failure_code: str | None = None


class SyncScheduleStatus(Contract):
    enabled: bool
    sources: list[SourceScheduleStatus]
    supported_intervals_minutes: list[int] = Field(default_factory=lambda: sorted(SUPPORTED_SYNC_INTERVALS_MINUTES))
    active_jobs: int = 0


class SourceSyncReport(Contract):
    source: SourceKind
    status: str = "queued"
    stage: str | None = None
    page_number: int = 0
    items_remaining_in_page: int | None = None
    connection_status: ConnectionStatus | None = None
    auth_reason: AuthenticationReason | None = None
    next_action: Literal["reauthorize", "check_access", "retry_validation", "grant_consent", "configure_source"] | None = None
    documents: int = 0
    records: int = 0
    unchanged: int = 0
    errors: int = 0
    metrics: dict[str, int] = Field(default_factory=dict)
    warning_codes: list[str] = Field(default_factory=list)
    error_codes: list[str] = Field(default_factory=list)

from pydantic import Field

from .sources import Contract, Key


class Profile(Contract):
    display_name: Key | None = None
    email: Key | None = None
    study_year: Key | None = None
    academic_year: Key | None = None
    specialization: Key | None = None
    timezone: Key = "Europe/Paris"


class Preferences(Contract):
    formats: list[Key] = Field(default_factory=list)
    daily_minutes: int = Field(default=120, ge=0, le=1440)


class SourceConnection(Contract):
    id: Key
    source: Key
    status: Key
    last_success_at: str | None = None

from typing import Literal

from pydantic import Field

from .authentication import AuthenticationReason, ConnectionStatus
from .sources import Contract, SourceKind


class SourceSyncReport(Contract):
    source: SourceKind
    status: str = "queued"
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

"""Public authentication interfaces. Credentials are passed only in process."""
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal, Mapping, Protocol
from enum import StrEnum

from pydantic import SecretStr

from .sources import Contract, SourceKind


class ConnectionStatus(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONNECTED = "CONNECTED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    ACCESS_DENIED = "ACCESS_DENIED"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


AuthenticationReason = Literal[
    "adapter_missing", "consent_required", "transient_failure", "credential_expired",
    "profile_busy", "configuration_missing", "adapter_error", "cas_login_required",
    "service_access_denied", "service_verification_required",
]


class AuthenticationResult(Contract):
    status: ConnectionStatus
    reason: AuthenticationReason | None = None
    session_state: Literal["unknown", "active", "reauth_required"] = "unknown"
    service_access: Literal["unknown", "verified", "denied"] = "unknown"


class ReauthenticationRequired(Exception):
    """A human must renew authorization; expose only an allowlisted reason."""

    def __init__(self, reason: str = "credential_expired"):
        # Older bridges may pass private upstream text. Discard it.
        self.reason = reason if reason in ("cas_login_required", "credential_expired") else "credential_expired"
        super().__init__(self.reason)


class ServiceAccessDenied(Exception):
    """The source denied the requested service access."""


class ProfileBusy(Exception):
    """The persistent browser profile is already leased."""


class CredentialStore(Protocol):
    def get(self, key: str) -> SecretStr | None: ...
    def set(self, key: str, value: SecretStr) -> None: ...
    def delete(self, key: str) -> None: ...


class BrowserSessionManager(Protocol):
    def lease(self) -> AbstractContextManager[Path]: ...


class SourceAuthenticationAdapter(Protocol):
    def authorize(self, inputs: Mapping[str, SecretStr]) -> AuthenticationResult: ...
    def validate(self) -> AuthenticationResult: ...
    def disconnect(self) -> None: ...


class ConnectionValidator(Protocol):
    def validate(self, source: SourceKind) -> AuthenticationResult: ...

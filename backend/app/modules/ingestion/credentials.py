"""Secure credential storage, origin validation, and masked interactive input.

Integrates with Codex's credential abstraction when present in backend/app/core,
falling back to the OS keyring (keyring package) for secure secret persistence.
Never logs credentials, CAS tickets, or OAuth tokens.
"""

from __future__ import annotations

import getpass
import importlib
import logging
import re
import warnings
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlparse

from .config import ALLOWED_ENSAI_AUTH_ORIGINS
from .contracts import CredentialStoreProtocol

logger = logging.getLogger(__name__)

DEFAULT_KEYRING_SERVICE_PREFIX = "ensora-plus"
_ALLOWED_OS_KEYRING_MODULES: tuple[str, ...] = (
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.libsecret",
)

_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(ticket=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(access_token=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(refresh_token=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(wstoken=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+/=]+", re.IGNORECASE),
    re.compile(r"(password[\"']?\s*[:=]\s*[\"']?)[^\"',\s}]+", re.IGNORECASE),
)


def redact_sensitive_text(text: str) -> str:
    """Redact tokens, passwords, and CAS tickets from log/error text."""
    sanitized = text
    for pattern in _REDACT_PATTERNS:
        sanitized = pattern.sub(r"\1[REDACTED]", sanitized)
    return sanitized


def validate_official_origin(
    url: str,
    allowed_origins: Iterable[str] = ALLOWED_ENSAI_AUTH_ORIGINS,
) -> bool:
    """Validate that a URL uses HTTPS and matches an allowlisted official origin."""
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return False
    origin = f"https://{parsed.netloc.lower()}"
    allowed = {o.rstrip("/").lower() for o in allowed_origins}
    return origin in allowed


class KeyringCredentialStore:
    """OS keyring-backed secret store implementing CredentialStoreProtocol.

    Matches Codex's canonical OS keyring schema (`service="ensora-plus"`,
    `key="{namespace}:{key}"`) and rejects non-OS / plaintext keyring backends.
    """

    def __init__(self, service_prefix: str = DEFAULT_KEYRING_SERVICE_PREFIX) -> None:
        self._prefix = service_prefix

    def _legacy_service(self, namespace: str) -> str:
        return f"{self._prefix}:{namespace}"

    @staticmethod
    def _canonical_key(namespace: str, key: str) -> str:
        return f"{namespace}:{key}"

    def _verified_backend(self):
        try:
            import keyring  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Python 'keyring' package is unavailable; install keyring to persist secrets securely."
            ) from exc
        backend = keyring.get_keyring()
        module = type(backend).__module__
        if not module.startswith(_ALLOWED_OS_KEYRING_MODULES):
            raise RuntimeError(
                "An operating-system keyring (SecretService/KWallet/macOS/Windows) is required; "
                "refusing to store passwords in an insecure or plaintext backend."
            )
        return backend

    def is_available(self) -> bool:
        try:
            self._verified_backend().get_password(self._prefix, "__availability_probe__")
        except Exception:
            return False
        return True

    def get_secret(self, namespace: str, key: str) -> str | None:
        try:
            backend = self._verified_backend()
            val = backend.get_password(self._prefix, self._canonical_key(namespace, key))
            if val is not None:
                return val
            # Read-only compatibility fallback for pre-unification entries
            return backend.get_password(self._legacy_service(namespace), key)
        except Exception as exc:  # pragma: no cover - OS backend failure
            logger.warning("Keyring read failed for namespace=%s: %s", namespace, type(exc).__name__)
            return None

    def set_secret(self, namespace: str, key: str, value: str) -> None:
        if not value:
            raise ValueError("Refusing to store an empty secret.")
        backend = self._verified_backend()
        backend.set_password(self._prefix, self._canonical_key(namespace, key), value)

    def delete_secret(self, namespace: str, key: str) -> None:
        try:
            backend = self._verified_backend()
        except Exception:
            return
        for svc, k in (
            (self._prefix, self._canonical_key(namespace, key)),
            (self._legacy_service(namespace), key),
        ):
            try:
                backend.delete_password(svc, k)
            except Exception:
                # Missing entry or backend-specific PasswordDeleteError is non-fatal on delete
                pass


class InMemoryCredentialStore:
    """Ephemeral in-memory credential store for deterministic unit testing only."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def is_available(self) -> bool:
        return True

    def get_secret(self, namespace: str, key: str) -> str | None:
        return self._store.get((namespace, key))

    def set_secret(self, namespace: str, key: str, value: str) -> None:
        self._store[(namespace, key)] = value

    def delete_secret(self, namespace: str, key: str) -> None:
        self._store.pop((namespace, key), None)


class CodexCredentialBridge:
    """Adapts a Codex-provided credential store into CredentialStoreProtocol."""

    def __init__(self, backend: object) -> None:
        self._backend = backend

    def get_secret(self, namespace: str, key: str) -> str | None:
        if hasattr(self._backend, "get_secret"):
            return self._backend.get_secret(namespace, key)
        if hasattr(self._backend, "get"):
            return self._backend.get(f"{namespace}:{key}")
        raise TypeError("Unsupported Codex credential backend interface.")

    def set_secret(self, namespace: str, key: str, value: str) -> None:
        if hasattr(self._backend, "set_secret"):
            self._backend.set_secret(namespace, key, value)
            return
        if hasattr(self._backend, "set"):
            self._backend.set(f"{namespace}:{key}", value)
            return
        raise TypeError("Unsupported Codex credential backend interface.")

    def delete_secret(self, namespace: str, key: str) -> None:
        if hasattr(self._backend, "delete_secret"):
            self._backend.delete_secret(namespace, key)
            return
        if hasattr(self._backend, "delete"):
            self._backend.delete(f"{namespace}:{key}")
            return
        raise TypeError("Unsupported Codex credential backend interface.")


def resolve_credential_store(
    codex_store: object | None = None,
) -> CredentialStoreProtocol:
    """Return Codex's credential abstraction when available, else OS keyring."""
    if codex_store is not None:
        if isinstance(codex_store, CredentialStoreProtocol):
            return codex_store
        return CodexCredentialBridge(codex_store)

    # Probe standard Codex core locations without hard coupling
    for module_path, attr in (
        ("backend.app.core.credentials", "get_credential_store"),
        ("backend.app.core.auth", "get_credential_store"),
        ("app.core.credentials", "get_credential_store"),
        ("app.core.auth", "get_credential_store"),
    ):
        try:
            mod = importlib.import_module(module_path)
            factory = getattr(mod, attr, None)
            if callable(factory):
                instance = factory()
                if instance is not None:
                    return (
                        instance
                        if isinstance(instance, CredentialStoreProtocol)
                        else CodexCredentialBridge(instance)
                    )
        except Exception:
            continue

    return KeyringCredentialStore()


@dataclass(frozen=True)
class EnsaiCredentials:
    """Runtime institutional credentials (never serialize or log)."""

    username: str
    password: str

    def __repr__(self) -> str:  # pragma: no cover - defensive redaction
        return f"EnsaiCredentials(username={self.username!r}, password='[REDACTED]')"


class SecureCredentialManager:
    """Manages runtime retrieval and masked interactive prompting of credentials."""

    ENSAI_NAMESPACE = "ensai_cas"
    MOODLE_WS_NAMESPACE = "moodle_ws"

    def __init__(
        self,
        store: CredentialStoreProtocol | None = None,
        *,
        input_fn: Callable[[str], str] = input,
        secret_input_fn: Callable[[str], str] = getpass.getpass,
    ) -> None:
        self._store = store or resolve_credential_store()
        self._input_fn = input_fn
        self._secret_input_fn = secret_input_fn

    def is_keyring_available(self) -> bool:
        """Return True when the configured credential backend is available for secure storage."""
        checker = getattr(self._store, "is_available", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        inner = getattr(self._store, "_store", None) or getattr(self._store, "_backend", None)
        inner_checker = getattr(inner, "is_available", None)
        if callable(inner_checker):
            try:
                return bool(inner_checker())
            except Exception:
                return False
        return True

    def has_ensai_credentials(self) -> bool:
        try:
            username = self._store.get_secret(self.ENSAI_NAMESPACE, "username")
            password = self._store.get_secret(self.ENSAI_NAMESPACE, "password")
            return bool(username and password)
        except Exception:
            return False

    def get_ensai_username(self) -> str | None:
        try:
            return self._store.get_secret(self.ENSAI_NAMESPACE, "username")
        except Exception:
            return None

    def get_ensai_credentials(self) -> EnsaiCredentials | None:
        try:
            username = self._store.get_secret(self.ENSAI_NAMESPACE, "username")
            password = self._store.get_secret(self.ENSAI_NAMESPACE, "password")
        except Exception:
            return None
        if username and password:
            return EnsaiCredentials(username=username, password=password)
        return None

    def store_ensai_credentials(self, username: str, password: str) -> None:
        clean_user = username.strip()
        if not clean_user or not password:
            raise ValueError("Both institutional username and password are required.")
        self._store.set_secret(self.ENSAI_NAMESPACE, "username", clean_user)
        self._store.set_secret(self.ENSAI_NAMESPACE, "password", password)

    def replace_ensai_credentials(self, username: str, password: str) -> None:
        """Securely replace existing ENSAI credentials in the OS keyring."""
        clean_user = username.strip()
        if not clean_user or not password:
            raise ValueError("Both institutional username and password are required.")
        self._store.set_secret(self.ENSAI_NAMESPACE, "username", clean_user)
        self._store.set_secret(self.ENSAI_NAMESPACE, "password", password)

    def clear_ensai_credentials(self) -> None:
        self._store.delete_secret(self.ENSAI_NAMESPACE, "username")
        self._store.delete_secret(self.ENSAI_NAMESPACE, "password")

    def _read_masked_secret(self, prompt: str) -> str:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                return self._secret_input_fn(prompt)
            except getpass.GetPassWarning as exc:
                raise RuntimeError(
                    "Masked password input is unavailable in this terminal; refusing to echo password."
                ) from exc

    def prompt_missing_ensai_credentials(
        self,
        *,
        username_hint: str | None = None,
        persist: bool = True,
        force_replace: bool = False,
        require_consent: bool = False,
        consent_fn: Callable[[str], bool] | None = None,
    ) -> EnsaiCredentials:
        """Prompt user via masked terminal input for missing (or replaced) credentials.

        - When `require_consent=True` and `persist=True`, explicitly confirms user consent
          before writing secrets to the operating-system keyring.
        - When `force_replace=True`, prompts for replacement credentials even if stored.
        - Never echoes passwords or writes them to plaintext files.
        """
        existing = self.get_ensai_credentials()
        if existing is not None and not force_replace:
            return existing

        if persist and not self.is_keyring_available():
            raise RuntimeError(
                "Operating-system keyring is unavailable; refusing to store institutional password in plaintext."
            )

        if persist and require_consent:
            prompt = (
                "Store your ENSAI institutional password securely in the operating-system keyring "
                "(never in plaintext or sources.env)?"
            )
            if consent_fn is not None:
                approved = bool(consent_fn(prompt))
            else:
                approved = self._input_fn(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}
            if not approved:
                raise PermissionError(
                    "User declined OS keyring credential storage; interactive browser login remains active."
                )

        default_user = (existing.username if existing else None) or self.get_ensai_username() or username_hint
        if force_replace:
            suffix = f" [{default_user}]" if default_user else ""
            entered = self._input_fn(f"ENSAI institutional username{suffix}: ").strip()
            username = entered or default_user or ""
        else:
            username = default_user or self._input_fn("ENSAI institutional username (e.g., id1234): ").strip()
        if not username:
            raise ValueError("ENSAI username cannot be empty.")

        password = self._read_masked_secret(
            "ENSAI institutional password (masked, stored in OS keyring): "
            if persist
            else "ENSAI institutional password (masked, session only): "
        )
        if not password:
            raise ValueError("ENSAI password cannot be empty.")

        if persist:
            self.replace_ensai_credentials(username, password)
        return EnsaiCredentials(username=username, password=password)

    def get_moodle_ws_token(self) -> str | None:
        """Return optional verified Moodle Web Services token if explicitly stored."""
        return self._store.get_secret(self.MOODLE_WS_NAMESPACE, "token")

    def store_moodle_ws_token(self, token: str) -> None:
        self._store.set_secret(self.MOODLE_WS_NAMESPACE, "token", token.strip())

    def clear_moodle_ws_token(self) -> None:
        self._store.delete_secret(self.MOODLE_WS_NAMESPACE, "token")

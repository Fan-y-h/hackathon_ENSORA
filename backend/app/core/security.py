"""Operating-system secrets and cross-process browser leases."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path

import keyring
from keyring.errors import PasswordDeleteError
from pydantic import SecretStr

from ..contracts.authentication import ProfileBusy
from .settings import private_directory


class KeyringCredentialStore:
    def __init__(self, service: str):
        self.service = service

    def _backend(self):
        backend = keyring.get_keyring()
        module = type(backend).__module__
        if not module.startswith(("keyring.backends.SecretService", "keyring.backends.kwallet",
                                  "keyring.backends.macOS", "keyring.backends.Windows",
                                  "keyring.backends.libsecret")):
            raise RuntimeError("An operating-system keyring is required")
        return backend

    def is_available(self) -> bool:
        try:
            self._backend().get_password(self.service, "__availability_probe__")
        except Exception:
            return False
        return True

    def get(self, key: str) -> SecretStr | None:
        value = self._backend().get_password(self.service, key)
        return SecretStr(value) if value is not None else None

    def set(self, key: str, value: SecretStr) -> None:
        self._backend().set_password(self.service, key, value.get_secret_value())

    def delete(self, key: str) -> None:
        backend = self._backend()
        try:
            backend.delete_password(self.service, key)
        except PasswordDeleteError:
            if backend.get_password(self.service, key) is not None:
                raise


class LockedBrowserSession:
    def __init__(self, profile: Path):
        self.profile = profile

    @contextmanager
    def lease(self):
        private_directory(self.profile)
        fd = os.open(self.profile / ".ensora.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProfileBusy() from None
            yield self.profile
        finally:
            os.close(fd)

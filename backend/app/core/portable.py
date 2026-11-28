"""Cross-platform file primitives for the P1 runtime (Linux and Windows).

P1 was written on Linux (``fcntl.flock``, ``os.O_NOFOLLOW``). These helpers keep the exact
Linux behaviour and provide the closest Windows equivalent:

* ``O_NOFOLLOW`` is ``0`` where the OS has no such flag (Windows does not follow symlinks
  implicitly for regular files opened by path in the same way, and private paths live under
  the user profile);
* exclusive non-blocking locks use ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows.
"""

from __future__ import annotations

import os

O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)

try:  # POSIX
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    _fcntl = None
    import msvcrt as _msvcrt


def lock_exclusive_nonblocking(fd: int) -> None:
    """Raise ``BlockingIOError`` (POSIX) / ``OSError`` (Windows) if another process holds it."""
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        return
    os.lseek(fd, 0, os.SEEK_SET)  # pragma: no cover - Windows
    try:  # pragma: no cover - Windows
        _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
    except OSError as exc:  # pragma: no cover - Windows
        raise BlockingIOError(*exc.args) from exc


def unlock(fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        return
    os.lseek(fd, 0, os.SEEK_SET)  # pragma: no cover - Windows
    try:  # pragma: no cover - Windows
        _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
    except OSError:  # pragma: no cover - already released
        pass


def chmod_private(fd: int) -> None:
    """``fchmod(fd, 0o600)`` where supported; a no-op where the OS has no POSIX modes."""
    try:
        os.fchmod(fd, 0o600)
    except (AttributeError, NotImplementedError, OSError):  # pragma: no cover - Windows
        pass

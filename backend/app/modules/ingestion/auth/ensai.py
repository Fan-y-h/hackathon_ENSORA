"""Authorized Playwright-based ENSAI authentication adapter (CAS, Moodle, Pamplemousse).

Features:
- Playwright & Chromium binary detection + installation guidance
- Persistent browser profile at ~/.config/ensora-plus/sessions/ensai/
- Cross-process browser profile locking and graceful shutdown
- Direct service navigation (Moodle / Pamplemousse) with MyEnsai fallback
- Strict CAS origin validation before any credential handling
- Optional authorized credential fill ONLY when explicitly permitted by policy
  and no MFA/CAPTCHA is present; automatic interactive login fallback otherwise
- Never bypasses MFA/CAPTCHA, never logs credentials, never extracts/replays CAS tickets
- Shared-session validation across Moodle and Pamplemousse with automatic fallback
  to isolated per-source profiles behind the same unified interface
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import select
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol
from urllib.parse import quote, urljoin, urlparse

from ..config import ALLOWED_ENSAI_AUTH_ORIGINS, SourcesRuntimeConfig
from ..contracts import AuthState, AuthStatus, SourceKind
from ..credentials import (
    SecureCredentialManager,
    redact_sensitive_text,
    validate_official_origin,
)
from ....core.portable import lock_exclusive_nonblocking, unlock

logger = logging.getLogger(__name__)

_MFA_OR_CAPTCHA_MARKERS: tuple[str, ...] = (
    "recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "captcha",
    "two-factor",
    "2fa",
    "mfa",
    "authenticator",
    "code de vérification",
    "double authentification",
    "otp",
    "totp",
)

_CAS_ERROR_MARKERS: tuple[str, ...] = (
    "invalid credentials",
    "identifiants invalides",
    "authentification échouée",
    "authentication failed",
    "mauvais identifiant",
    "compte verrouillé",
    "account locked",
)

_MOODLE_LOGIN_MARKERS: tuple[str, ...] = (
    "/login/index.php",
    "vous n'êtes pas connecté",
    "you are not logged in",
    "connexion",
    "se connecter",
)

_PAMPLEMOUSSE_LOGIN_MARKERS: tuple[str, ...] = (
    "ssocas.ensai.fr/cas/login",
    "authentification requise",
    "session expirée",
    "session expired",
    "login",
)

_CAS_SUCCESS_MARKERS: tuple[str, ...] = (
    "log in successful",
    "login successful",
    "connexion réussie",
    "connexion reussie",
    "authentification réussie",
    "authentification reussie",
    "vous êtes maintenant connecté",
    "vous etes maintenant connecte",
    "you have successfully logged in",
)

_CAS_TICKET_QUERY_RE = re.compile(r"([?&]ticket=)[^&\s]+", re.IGNORECASE)
_MOODLE_POSITIVE_USERID_RE = re.compile(r'data-userid\s*=\s*["\']([2-9]|[1-9]\d+)["\']', re.IGNORECASE)
SESSION_COOKIES_FILENAME = ".ensora_session_cookies.json"
DEFAULT_ENSAI_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_ALLOWED_COOKIE_KEYS: frozenset[str] = frozenset(
    {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
)


def sanitize_url_for_logs(url: str) -> str:
    """Strip transient CAS service tickets before storing or logging URLs."""
    return _CAS_TICKET_QUERY_RE.sub(r"\1[REDACTED]", url)


def sanitize_url_host_path(url: str) -> tuple[str, str]:
    """Return ONLY (hostname, path) with ALL query parameters, tickets, and fragments stripped."""
    if not url:
        return ("", "")
    parsed = urlparse(str(url))
    return (parsed.netloc.lower(), parsed.path or "/")


def is_transient_sso_url(url: str) -> bool:
    """Return True when the browser is mid-CAS/SSO ticket validation or session handshake.

    Never interrupt these URLs with `page.goto()` or classify them as terminal failures:
    - `?ticket=ST-...`: CAS service ticket being validated server-side via `/serviceValidate`
    - `?testsession=...`: Moodle post-CAS cookie verification redirect
    - `/login/index.php?authCAS=CAS`: Moodle CAS initiation/callback hop
    """
    parsed = urlparse(str(url or ""))
    path = parsed.path.lower()
    query = parsed.query.lower()
    if "ticket=" in query or "testsession=" in query:
        return True
    if "/login/index.php" in path and "authcas=" in query:
        return True
    return False


@dataclass(frozen=True)
class EnsaiAuthDiagnostics:
    """Strictly privacy-preserving authentication diagnostics.

    Never stores or logs passwords, cookies, CAS tickets, tokens, query parameters,
    or private page contents — only phase, navigation state, sanitized host + path,
    boolean persistence flags, and machine-readable failure reason.
    """

    phase: str
    navigation_state: str
    current_host: str
    current_path: str
    redirect_completed: bool
    session_persisted: bool
    service_cookies_present: bool
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format_safe_summary(self, source: SourceKind) -> str:
        reason_part = f" reason={self.failure_reason}" if self.failure_reason else ""
        return (
            f"[ensora-auth] source={source.value} phase={self.phase} "
            f"nav={self.navigation_state} host={self.current_host or 'none'} "
            f"path={self.current_path or '/'} redirect_done={self.redirect_completed} "
            f"persisted={self.session_persisted} cookies={self.service_cookies_present}"
            f"{reason_part}"
        )


@dataclass(frozen=True)
class PlaywrightEnvironmentStatus:
    """Diagnostic status of Playwright Python package and Chromium browser."""

    playwright_installed: bool
    chromium_installed: bool
    chromium_executable: str | None
    install_command: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_playwright_environment() -> PlaywrightEnvironmentStatus:
    """Detect whether Playwright and a Chromium binary are available."""
    install_cmd = (
        r"backend\.venv\Scripts\python.exe -m playwright install chromium" if os.name == "nt"
        else ".venv/bin/python -m playwright install chromium"
    )
    spec = importlib.util.find_spec("playwright")
    if spec is None:
        return PlaywrightEnvironmentStatus(
            playwright_installed=False,
            chromium_installed=False,
            chromium_executable=None,
            install_command=install_cmd,
            detail="Playwright Python package is not installed in the active environment.",
        )

    # Check Playwright cache without launching a browser process
    cache_candidates: list[Path] = []
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_path and env_path != "0":
        cache_candidates.append(Path(env_path).expanduser())
    # Playwright's default browser cache per OS (Linux, Windows, macOS).
    cache_candidates.append(Path.home() / ".cache" / "ms-playwright")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        cache_candidates.append(Path(local_app_data) / "ms-playwright")
    cache_candidates.append(Path.home() / "Library" / "Caches" / "ms-playwright")

    chromium_exec: str | None = None
    for base in cache_candidates:
        if not base.is_dir():
            continue
        for pattern in (
            "chromium-*/chrome-linux/chrome",
            "chromium-*/chrome-linux64/chrome",
            "chromium-*/chrome-win64/chrome.exe",
            "chromium-*/chrome-win/chrome.exe",
            "chromium-*/chrome-mac*/*.app",
            "chromium_headless_shell-*/chrome-linux/headless_shell",
            "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
            "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
        ):
            matches = sorted(base.glob(pattern))
            if matches:
                chromium_exec = str(matches[-1])
                break
        if chromium_exec:
            break

    if chromium_exec:
        return PlaywrightEnvironmentStatus(
            playwright_installed=True,
            chromium_installed=True,
            chromium_executable=chromium_exec,
            install_command=install_cmd,
            detail="Playwright and Chromium are installed and ready.",
        )

    return PlaywrightEnvironmentStatus(
        playwright_installed=True,
        chromium_installed=False,
        chromium_executable=None,
        install_command=install_cmd,
        detail=(
            "Playwright is installed, but Chromium browser binary was not detected. "
            f"Run: {install_cmd}"
        ),
    )


class BrowserProfileLockError(RuntimeError):
    """Raised when the persistent browser profile is already locked by another process."""


class BrowserProfileLock:
    """Advisory file lock preventing concurrent access to the same browser profile.

    Uses the exact same `.ensora.lock` filename and `fcntl.flock` protocol as
    Codex's `backend.app.core.security.LockedBrowserSession` so standalone CLI
    calls and Codex Runtime share a single mutually exclusive browser lease.
    """

    LOCK_FILENAME = ".ensora.lock"

    def __init__(self, profile_dir: Path, *, timeout_sec: float = 0.0) -> None:
        self._profile_dir = profile_dir
        self._lock_path = profile_dir / self.LOCK_FILENAME
        self._timeout_sec = timeout_sec
        self._fd: int | None = None

    def acquire(self) -> None:
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._profile_dir, 0o700)
        except OSError:
            pass
        deadline = time.monotonic() + self._timeout_sec
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(self._lock_path), flags, 0o600)
        while True:
            try:
                lock_exclusive_nonblocking(fd)
                self._fd = fd
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()}\n".encode("ascii"))
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise BrowserProfileLockError(
                        f"Browser profile {self._profile_dir} is locked by another process."
                    )
                time.sleep(0.05)

    def release(self) -> None:
        if self._fd is not None:
            try:
                unlock(self._fd)
            finally:
                os.close(self._fd)
                self._fd = None

    def __enter__(self) -> "BrowserProfileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class BrowserPageProtocol(Protocol):
    """Minimal browser page interface enabling deterministic unit testing."""

    @property
    def url(self) -> str: ...
    def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 30_000) -> Any: ...
    def content(self) -> str: ...
    def title(self) -> str: ...
    def fill(self, selector: str, value: str, *, timeout: int = 5_000) -> None: ...
    def click(self, selector: str, *, timeout: int = 5_000) -> None: ...
    def wait_for_load_state(self, state: str = "networkidle", *, timeout: int = 15_000) -> None: ...
    def wait_for_url(self, predicate: Any, *, timeout: int = 120_000) -> None: ...


class BrowserContextProtocol(Protocol):
    """Minimal persistent browser context protocol."""

    @property
    def pages(self) -> list[Any]: ...
    def new_page(self) -> Any: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class EnsaiSessionProbe:
    """Outcome of inspecting a target ENSAI service URL in the browser context."""

    source: SourceKind
    requested_url: str
    final_url: str
    state: AuthState
    authenticated: bool
    cas_redirect_detected: bool
    mfa_or_captcha_detected: bool
    auth_error_detected: bool
    origin_verified: bool
    detail: str
    profile_dir: str
    used_myensai_fallback: bool = False
    html_excerpt_available: bool = False
    failure_reason: str | None = None
    diagnostics: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = self.source.value
        data["state"] = self.state.value
        data["final_url"] = sanitize_url_for_logs(self.final_url)
        return data


@dataclass
class _PlaywrightSessionHandle:
    playwright: Any
    context: BrowserContextProtocol
    lock: BrowserProfileLock
    profile_dir: Path

    def close(self) -> None:
        try:
            self.context.close()
        except Exception:
            pass
        try:
            if self.playwright is not None:
                self.playwright.stop()
        except Exception:
            pass
        self.lock.release()


class EnsaiAuthAdapter:
    """Unified ENSAI authentication adapter for Moodle and Pamplemousse.

    Supports shared persistent browser sessions at ~/.config/ensora-plus/sessions/ensai/
    and falls back cleanly to isolated per-source profiles when shared SSO is not accepted.
    """

    def __init__(
        self,
        config: SourcesRuntimeConfig,
        credential_manager: SecureCredentialManager | None = None,
        *,
        context_factory: Callable[[Path, bool], BrowserContextProtocol] | None = None,
        allowed_origins: frozenset[str] = ALLOWED_ENSAI_AUTH_ORIGINS,
        session_manager: Any | None = None,
    ) -> None:
        self._config = config
        self._credentials = credential_manager or SecureCredentialManager()
        self._context_factory = context_factory
        self._allowed_origins = allowed_origins
        self._session_manager = session_manager
        self._shared_session_supported: bool | None = (
            True if config.ensai_use_shared_session else False
        )

    @property
    def shared_session_supported(self) -> bool | None:
        return self._shared_session_supported

    def environment_status(self) -> PlaywrightEnvironmentStatus:
        return detect_playwright_environment()

    def profile_dir_for(self, source: SourceKind) -> Path:
        """Return shared profile directory or isolated per-source fallback profile."""
        if self._shared_session_supported is False:
            return self._config.ensai_profile_dir / source.value
        return self._config.ensai_profile_dir

    def service_url_for(self, source: SourceKind) -> str:
        if source == SourceKind.MOODLE:
            return self._config.moodle_base_url
        if source == SourceKind.PAMPLEMOUSSE:
            return self._config.pamplemousse_base_url
        raise ValueError(f"Unsupported ENSAI source: {source.value}")

    def protected_url_for(self, source: SourceKind) -> str:
        """Return an actual protected service resource URL that requires authenticated access.

        Never validates Moodle against the public front page `/` because public Moodle pages
        do not trigger CAS redirection and can produce false-positive CONNECTED states.
        """
        if source == SourceKind.MOODLE:
            return self._config.moodle_protected_url or urljoin(
                self._config.moodle_base_url, "my/courses.php"
            )
        if source == SourceKind.PAMPLEMOUSSE:
            return self._config.pamplemousse_protected_url or self._config.pamplemousse_base_url
        raise ValueError(f"Unsupported ENSAI source: {source.value}")

    @staticmethod
    def _is_page_closed(page: Any) -> bool:
        if hasattr(page, "is_closed"):
            try:
                return bool(page.is_closed())
            except Exception:
                return True
        return False

    @staticmethod
    def _settle_navigation(
        page: Any,
        *,
        timeout_ms: int = 8_000,
        max_transient_wait_sec: float = 12.0,
    ) -> None:
        """Wait for SSO / CAS ticket redirects and dynamic SPA shell rendering without interrupting them.

        When CAS redirects back with `?ticket=ST-...` or Moodle performs its post-CAS
        `?testsession=...` redirect, wait patiently until the transient redirect finishes
        so we never abort back-channel ticket validation with a premature `page.goto()`.
        """
        if hasattr(page, "wait_for_load_state"):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(5_000, timeout_ms))
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass

        # Wait out in-flight CAS ticket validation (`?ticket=`) or Moodle `?testsession=` hops
        deadline = time.monotonic() + max_transient_wait_sec
        while is_transient_sso_url(str(getattr(page, "url", "") or "")):
            if EnsaiAuthAdapter._is_page_closed(page):
                return
            if hasattr(page, "wait_for_load_state"):
                try:
                    page.wait_for_load_state("networkidle", timeout=min(3_000, timeout_ms))
                except Exception:
                    pass
            if not is_transient_sso_url(str(getattr(page, "url", "") or "")):
                return
            if time.monotonic() >= deadline:
                return
            if hasattr(page, "wait_for_timeout"):
                try:
                    page.wait_for_timeout(250)
                except Exception:
                    time.sleep(0.25)
            else:
                time.sleep(0.05)

    @staticmethod
    def _collect_page_html(page: Any, target_host: str) -> str:
        """Collect HTML from main page AND any same-origin child frames (e.g. Pamplemousse/Leos framesets)."""
        parts: list[str] = []
        try:
            main_html = page.content() or ""
            if main_html:
                parts.append(main_html)
        except Exception:
            pass

        frames = getattr(page, "frames", None)
        if frames:
            main_frame = getattr(page, "main_frame", None)
            for frame in frames:
                if frame is main_frame:
                    continue
                try:
                    f_url = str(getattr(frame, "url", "") or "")
                    if not f_url or urlparse(f_url).netloc.lower() != target_host:
                        continue
                    f_html = frame.content() or ""
                    if f_html:
                        parts.append(f_html)
                except Exception:
                    continue
        return "\n".join(parts)

    @staticmethod
    def _inspect_session_persistence(
        context: Any,
        profile_dir: Path,
        target_host: str,
    ) -> tuple[bool, bool]:
        """Return (session_persisted, service_cookies_present) without ever reading or logging cookie values."""
        service_cookies_present = False
        if context is not None and hasattr(context, "cookies"):
            try:
                cookies = context.cookies() or []
                for cookie in cookies:
                    domain = str(cookie.get("domain") or "").lstrip(".").lower()
                    if not domain:
                        continue
                    if target_host == domain or target_host.endswith(f".{domain}"):
                        service_cookies_present = True
                        break
            except Exception:
                pass

        profile_has_files = False
        try:
            if profile_dir.is_dir():
                for child in profile_dir.iterdir():
                    if child.name not in (BrowserProfileLock.LOCK_FILENAME, ".ensora_profile.lock"):
                        profile_has_files = True
                        break
        except OSError:
            pass

        return (profile_has_files or service_cookies_present, service_cookies_present)

    def _is_settled_service_url(self, url: str, target_host: str) -> bool:
        """Return True ONLY when URL is on target_host AND NOT mid-CAS/login redirect."""
        if not url:
            return False
        parsed = urlparse(str(url))
        if parsed.netloc.lower() != target_host:
            return False
        if is_transient_sso_url(url):
            return False
        path = parsed.path.lower()
        if "/login/index.php" in path or "/login/" in path:
            return False
        return True

    def _build_diagnostics(
        self,
        *,
        source: SourceKind,
        phase: str,
        current_url: str,
        target_host: str,
        protected_url: str,
        probe: EnsaiSessionProbe | None,
        session_persisted: bool,
        service_cookies_present: bool,
        failure_reason_override: str | None = None,
        navigation_state_override: str | None = None,
    ) -> EnsaiAuthDiagnostics:
        host, path = sanitize_url_host_path(current_url)
        cas_host = urlparse(self._config.cas_login_url).netloc.lower()
        protected_path = urlparse(protected_url).path.lower().rstrip("/") or "/"
        cur_path_norm = path.lower().rstrip("/") or "/"

        if navigation_state_override is not None:
            nav_state = navigation_state_override
        elif probe is not None and not probe.origin_verified:
            nav_state = "origin_blocked"
        elif is_transient_sso_url(current_url):
            nav_state = "ticket_exchange_in_progress"
        elif host == cas_host and "/cas" in path.lower():
            if probe is not None and probe.failure_reason == "cas_login_succeeded_without_redirect":
                nav_state = "cas_login_succeeded_no_redirect"
            else:
                nav_state = "on_cas_login"
        elif "login.microsoftonline.com" in host:
            nav_state = "on_microsoft_login"
        elif host == target_host:
            if "/login/" in path.lower():
                nav_state = "on_service_login_chooser"
            elif cur_path_norm == protected_path:
                nav_state = "on_protected_resource"
            else:
                nav_state = "on_service_page"
        else:
            nav_state = "other_page"

        redirect_completed = (
            host == target_host
            and not is_transient_sso_url(current_url)
            and "/login/" not in path.lower()
        )
        failure_reason = failure_reason_override
        if failure_reason is None and probe is not None and not probe.authenticated:
            failure_reason = probe.failure_reason or probe.state.value

        return EnsaiAuthDiagnostics(
            phase=phase,
            navigation_state=nav_state,
            current_host=host,
            current_path=path,
            redirect_completed=redirect_completed,
            session_persisted=session_persisted,
            service_cookies_present=service_cookies_present,
            failure_reason=None if (probe is not None and probe.authenticated) else failure_reason,
        )

    def _emit_interactive_notice(self, message: str, *, interactive: bool) -> None:
        """Emit strictly privacy-safe operator progress to stderr during real interactive login."""
        logger.info("%s", message)
        if interactive and self._context_factory is None:
            try:
                sys.stderr.write(f"{message}\n")
                sys.stderr.flush()
            except Exception:
                pass

    def _extract_service_cas_link(self, html: str, current_url: str) -> str | None:
        """Extract an official CAS login link from a Moodle/Pamplemousse login chooser page.

        Preserves exact service redirect parameters (`?authCAS=CAS` or `?service=...`)
        instead of discarding them by jumping to bare `cas_login_url`.
        """
        cas_host = urlparse(self._config.cas_login_url).netloc.lower()
        hrefs = re.findall(r'(?:href|action)\s*=\s*["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
        candidates: list[str] = []
        for raw_href in hrefs:
            cleaned = raw_href.replace("&amp;", "&").strip()
            if not cleaned:
                continue
            full = urljoin(current_url, cleaned)
            low = full.lower()
            if not validate_official_origin(full, self._allowed_origins):
                continue
            if cas_host in low and "/cas" in low:
                return full
            if "authcas=" in low or "/login/index.php?authcas" in low or "cas=1" in low:
                candidates.append(full)
        return candidates[0] if candidates else None

    def _cas_url_with_service(self, service_url: str) -> str:
        """Build official CAS login URL preserving the target service redirect parameter."""
        base_cas = self._config.cas_login_url.strip()
        sep = "&" if "?" in base_cas else "?"
        return f"{base_cas}{sep}service={quote(service_url, safe='')}"

    @contextmanager
    def _acquire_profile_lease(self, profile_dir: Path) -> Iterator[Path]:
        """Use Codex's shared BrowserSessionManager lease when on the shared profile,
        otherwise acquire the compatible `.ensora.lock` BrowserProfileLock.
        """
        if (
            self._session_manager is not None
            and profile_dir.resolve() == self._config.ensai_profile_dir.resolve()
        ):
            with self._session_manager.lease() as leased_path:
                yield Path(leased_path)
            return

        lock = BrowserProfileLock(profile_dir)
        with lock:
            yield profile_dir

    def _allowed_cookie_hosts(self) -> set[str]:
        hosts: set[str] = set()
        for origin in self._allowed_origins:
            h = urlparse(origin).netloc.lower()
            if h:
                hosts.add(h)
        for cfg_url in (
            self._config.cas_login_url,
            self._config.moodle_base_url,
            self._config.pamplemousse_base_url,
            self._config.myensai_url,
        ):
            h = urlparse(cfg_url).netloc.lower()
            if h:
                hosts.add(h)
        return hosts

    def _is_allowed_cookie_domain(self, domain: str) -> bool:
        clean = str(domain or "").strip().lstrip(".").lower()
        if not clean or "." not in clean:
            return False
        for host in self._allowed_cookie_hosts():
            if host == clean or host.endswith(f".{clean}"):
                return True
        return False

    def _filter_institutional_cookies(self, raw_cookies: Any) -> list[dict[str, Any]]:
        """Filter cookies to allowlisted ENSAI/Moodle/Pamplemousse domains & non-expired entries."""
        if not isinstance(raw_cookies, list):
            return []
        now_ts = time.time()
        filtered: list[dict[str, Any]] = []
        for item in raw_cookies:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain") or "")
            name = item.get("name")
            value = item.get("value")
            if not name or value is None or not self._is_allowed_cookie_domain(domain):
                continue
            expires = item.get("expires")
            if isinstance(expires, (int, float)) and expires > 0 and expires < now_ts:
                continue
            cleaned = {k: v for k, v in item.items() if k in _ALLOWED_COOKIE_KEYS and v is not None}
            cleaned.setdefault("path", "/")
            filtered.append(cleaned)
        return filtered

    def _save_session_cookies(self, context: Any, profile_dir: Path) -> bool:
        """Persist live institutional session cookies (including expires==-1) atomically (0o600).

        Chromium purges session cookies (`expires == -1`, such as MoodleSession, PHPSESSID,
        and CAS TGC) on `context.close()`. Persisting allowlisted institutional cookies inside
        the private profile directory (`0o600`, `O_NOFOLLOW`) preserves valid authenticated
        sessions across browser restarts and subsequent headless validations without ever
        logging cookie names or values.
        """
        raw_cookies: list[dict[str, Any]] = []
        if hasattr(context, "storage_state"):
            try:
                state = context.storage_state() or {}
                raw_cookies = state.get("cookies", []) if isinstance(state, dict) else []
            except Exception:
                raw_cookies = []
        if not raw_cookies and hasattr(context, "cookies"):
            try:
                raw_cookies = context.cookies() or []
            except Exception:
                raw_cookies = []
        cookies = self._filter_institutional_cookies(raw_cookies)
        if not cookies:
            return False

        profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(profile_dir, 0o700)
        except OSError:
            pass
        target = profile_dir / SESSION_COOKIES_FILENAME
        tmp_path = profile_dir / f".{SESSION_COOKIES_FILENAME}.tmp.{os.getpid()}"
        payload = json.dumps({"version": 1, "cookies": cookies}, ensure_ascii=True)
        fd = os.open(
            str(tmp_path),
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                os.fchmod(fh.fileno(), 0o600)
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, target)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
            return True
        except Exception:
            return False
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _restore_session_cookies(self, context: Any, profile_dir: Path) -> bool:
        """Restore persisted institutional session cookies into a newly opened browser context."""
        if not hasattr(context, "add_cookies"):
            return False
        target = profile_dir / SESSION_COOKIES_FILENAME
        if not target.exists():
            return False
        try:
            fd = os.open(str(target), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return False
        raw_cookies = data.get("cookies", []) if isinstance(data, dict) else []
        cookies = self._filter_institutional_cookies(raw_cookies)
        if not cookies:
            return False
        try:
            context.add_cookies(cookies)
            return True
        except Exception:
            return False

    @staticmethod
    def _cleanup_stale_chromium_locks(leased_dir: Path) -> bool:
        """Remove dead Chromium SingletonLock/Socket/Cookie leftovers after an unclean restart.

        Only called while holding our exclusive `.ensora_profile.lock` lease. Inspects the
        `SingletonLock` symlink (`<hostname>-<pid>`) and removes stale lock files ONLY when
        the recorded PID is confirmed dead (`ProcessLookupError`), never touching a live browser.
        """
        lock_file = leased_dir / "SingletonLock"
        if not lock_file.exists() and not lock_file.is_symlink():
            return False
        pid_alive = False
        if lock_file.is_symlink():
            try:
                target = os.readlink(lock_file)
                if "-" in target:
                    pid_str = target.rsplit("-", 1)[-1]
                    if pid_str.isdigit():
                        pid = int(pid_str)
                        try:
                            os.kill(pid, 0)
                            pid_alive = True
                        except ProcessLookupError:
                            pid_alive = False
                        except PermissionError:
                            pid_alive = True
            except OSError:
                pid_alive = False
        if pid_alive:
            return False
        removed = False
        for fname in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            p = leased_dir / fname
            if p.exists() or p.is_symlink():
                try:
                    p.unlink(missing_ok=True)
                    removed = True
                except OSError:
                    pass
        if removed:
            logger.info("Cleaned stale post-restart Chromium Singleton lock files in %s", leased_dir)
        return removed

    def _probe_via_cookies_http(
        self,
        source: SourceKind,
        *,
        profile_dir: Path,
        phase: str = "check_connection",
    ) -> EnsaiSessionProbe | None:
        """Fast cookie-backed HTTP validation of a protected ENSAI resource (no Chromium launch).

        Returns an authenticated `EnsaiSessionProbe` ONLY when persisted allowlisted cookies
        yield HTTP 200 on the official target origin AND pass `classify_page_state()`.
        Returns None whenever cookies are missing, expired, or redirected to CAS so callers
        can seamlessly fall back to headless Playwright (for silent CAS SSO / auto-reauth).
        """
        import requests

        cookie_path = profile_dir / SESSION_COOKIES_FILENAME
        if not cookie_path.is_file() or cookie_path.is_symlink():
            return None
        try:
            fd = os.open(str(cookie_path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return None
        raw_cookies = data.get("cookies", []) if isinstance(data, dict) else []
        cookies = self._filter_institutional_cookies(raw_cookies)
        if not cookies:
            return None

        target_host = urlparse(self.service_url_for(source)).netloc.lower()
        service_cookies = [
            c
            for c in cookies
            if target_host == str(c.get("domain") or "").lstrip(".").lower()
            or target_host.endswith("." + str(c.get("domain") or "").lstrip(".").lower())
        ]
        if not service_cookies:
            return None

        candidate_urls = [self.protected_url_for(source)]
        if source == SourceKind.MOODLE:
            profile_fallback = urljoin(self._config.moodle_base_url, "user/profile.php")
            if profile_fallback not in candidate_urls:
                candidate_urls.append(profile_fallback)

        session = requests.Session()
        session.headers.update({"User-Agent": DEFAULT_ENSAI_USER_AGENT})
        for c in service_cookies:
            session.cookies.set(
                str(c["name"]),
                str(c["value"]),
                domain=str(c.get("domain") or "").lstrip(".") or None,
                path=str(c.get("path") or "/"),
            )

        timeout_sec = min(15.0, max(5.0, float(self._config.request_timeout_sec)))
        try:
            for start_url in candidate_urls:
                cur_url = start_url
                try:
                    resp = None
                    for hop in range(6):
                        if not validate_official_origin(cur_url, self._allowed_origins):
                            return None
                        resp = session.get(cur_url, timeout=timeout_sec, allow_redirects=False)
                        if resp.status_code in (301, 302, 303, 307, 308):
                            loc = resp.headers.get("Location") or resp.headers.get("location")
                            if not loc:
                                resp = None
                                break
                            nxt = urljoin(cur_url, str(loc))
                            if not validate_official_origin(nxt, self._allowed_origins):
                                return None
                            cas_host = urlparse(self._config.cas_login_url).netloc.lower()
                            if urlparse(nxt).netloc.lower() == cas_host:
                                # Session needs CAS/Playwright silent SSO or reauth
                                return None
                            cur_url = nxt
                            continue
                        break
                    if resp is None or resp.status_code != 200:
                        continue
                    html_text = resp.text or ""
                    title_m = re.search(r"<title>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
                    page_title = " ".join(title_m.group(1).split()).strip() if title_m else ""
                    probe = self.classify_page_state(
                        source,
                        requested_url=start_url,
                        current_url=cur_url,
                        html_content=html_text,
                        page_title=page_title,
                        profile_dir=profile_dir,
                        has_service_cookies=True,
                    )
                    if probe.authenticated and probe.origin_verified:
                        diag = self._build_diagnostics(
                            source=source,
                            phase=phase,
                            current_url=cur_url,
                            target_host=target_host,
                            protected_url=start_url,
                            probe=probe,
                            session_persisted=True,
                            service_cookies_present=True,
                        )
                        return EnsaiSessionProbe(
                            source=probe.source,
                            requested_url=probe.requested_url,
                            final_url=probe.final_url,
                            state=probe.state,
                            authenticated=True,
                            cas_redirect_detected=False,
                            mfa_or_captcha_detected=False,
                            auth_error_detected=False,
                            origin_verified=True,
                            detail=f"{probe.detail} (verified via persisted session cookies).",
                            profile_dir=probe.profile_dir,
                            used_myensai_fallback=False,
                            html_excerpt_available=probe.html_excerpt_available,
                            failure_reason=None,
                            diagnostics=diag.to_dict(),
                        )
                except Exception:
                    continue
            return None
        finally:
            session.close()

    @contextmanager
    def open_context(
        self,
        source: SourceKind = SourceKind.MOODLE,
        *,
        headless: bool | None = None,
    ) -> Iterator[BrowserContextProtocol]:
        """Acquire profile lease, open persistent browser context, restore session cookies, and close cleanly."""
        profile_dir = self.profile_dir_for(source)
        use_headless = self._config.ensai_headless if headless is None else headless
        with self._acquire_profile_lease(profile_dir) as leased_dir:
            pw = None
            context: BrowserContextProtocol | None = None
            try:
                if self._context_factory is not None:
                    context = self._context_factory(leased_dir, use_headless)
                else:
                    self._cleanup_stale_chromium_locks(leased_dir)
                    env_status = self.environment_status()
                    if not env_status.playwright_installed or not env_status.chromium_installed:
                        raise RuntimeError(env_status.detail)
                    from playwright.sync_api import sync_playwright  # type: ignore

                    pw = sync_playwright().start()
                    context = pw.chromium.launch_persistent_context(
                        user_data_dir=str(leased_dir),
                        headless=use_headless,
                        accept_downloads=True,
                        user_agent=DEFAULT_ENSAI_USER_AGENT,
                    )
                self._restore_session_cookies(context, leased_dir)
                yield context
            finally:
                # Always close Chromium and stop Playwright BEFORE releasing the profile lease
                if context is not None:
                    try:
                        context.close()
                    except Exception as exc:
                        logger.debug("Graceful browser context close warning: %s", type(exc).__name__)
                if pw is not None:
                    try:
                        pw.stop()
                    except Exception:
                        pass

    def classify_page_state(
        self,
        source: SourceKind,
        *,
        requested_url: str,
        current_url: str,
        html_content: str,
        page_title: str = "",
        profile_dir: Path | None = None,
        used_myensai_fallback: bool = False,
        has_service_cookies: bool = False,
    ) -> EnsaiSessionProbe:
        """Pure, deterministic inspection of redirect URL & page DOM for auth state."""
        safe_url = sanitize_url_for_logs(current_url)
        lower_url = current_url.lower()
        lower_html = html_content.lower()
        lower_title = page_title.lower()
        combined = f"{lower_title}\n{lower_html}"

        origin_ok = validate_official_origin(current_url, self._allowed_origins)
        cas_host = urlparse(self._config.cas_login_url).netloc.lower()
        is_cas = cas_host in lower_url and "/cas" in lower_url
        has_mfa_or_captcha = any(marker in combined for marker in _MFA_OR_CAPTCHA_MARKERS)
        has_auth_error = any(marker in combined for marker in _CAS_ERROR_MARKERS)
        has_cas_success = any(marker in combined for marker in _CAS_SUCCESS_MARKERS)

        target_host = urlparse(self.service_url_for(source)).netloc.lower()
        on_target_host = urlparse(current_url).netloc.lower() == target_host

        if not origin_ok:
            return EnsaiSessionProbe(
                source=source,
                requested_url=requested_url,
                final_url=safe_url,
                state=AuthState.ERROR,
                authenticated=False,
                cas_redirect_detected=is_cas,
                mfa_or_captcha_detected=has_mfa_or_captcha,
                auth_error_detected=True,
                origin_verified=False,
                detail=f"Blocked unexpected or non-HTTPS origin: {urlparse(current_url).scheme}://{urlparse(current_url).netloc}",
                profile_dir=str(profile_dir or self.profile_dir_for(source)),
                used_myensai_fallback=used_myensai_fallback,
                html_excerpt_available=bool(html_content),
                failure_reason="origin_unverified",
            )

        if has_auth_error:
            return EnsaiSessionProbe(
                source=source,
                requested_url=requested_url,
                final_url=safe_url,
                state=AuthState.ERROR,
                authenticated=False,
                cas_redirect_detected=is_cas,
                mfa_or_captcha_detected=has_mfa_or_captcha,
                auth_error_detected=True,
                origin_verified=True,
                detail="Authentication failure detected on ENSAI CAS login page.",
                profile_dir=str(profile_dir or self.profile_dir_for(source)),
                used_myensai_fallback=used_myensai_fallback,
                html_excerpt_available=bool(html_content),
                failure_reason="auth_error_on_cas",
            )

        if is_cas or "login.microsoftonline.com" in lower_url:
            if is_cas and has_cas_success and not has_mfa_or_captcha:
                return EnsaiSessionProbe(
                    source=source,
                    requested_url=requested_url,
                    final_url=safe_url,
                    state=AuthState.NEEDS_LOGIN,
                    authenticated=False,
                    cas_redirect_detected=True,
                    mfa_or_captcha_detected=False,
                    auth_error_detected=False,
                    origin_verified=True,
                    detail="ENSAI CAS login succeeded without automatic redirect back to the target service.",
                    profile_dir=str(profile_dir or self.profile_dir_for(source)),
                    used_myensai_fallback=used_myensai_fallback,
                    html_excerpt_available=bool(html_content),
                    failure_reason="cas_login_succeeded_without_redirect",
                )
            return EnsaiSessionProbe(
                source=source,
                requested_url=requested_url,
                final_url=safe_url,
                state=AuthState.NEEDS_LOGIN,
                authenticated=False,
                cas_redirect_detected=True,
                mfa_or_captcha_detected=has_mfa_or_captcha,
                auth_error_detected=False,
                origin_verified=True,
                detail=(
                    "Redirected to institutional login; interactive MFA/CAPTCHA required."
                    if has_mfa_or_captcha
                    else "Redirected to official ENSAI CAS authentication page."
                ),
                profile_dir=str(profile_dir or self.profile_dir_for(source)),
                used_myensai_fallback=used_myensai_fallback,
                html_excerpt_available=bool(html_content),
                failure_reason="mfa_or_captcha_pending" if has_mfa_or_captcha else "cas_login_pending",
            )

        if is_transient_sso_url(current_url):
            return EnsaiSessionProbe(
                source=source,
                requested_url=requested_url,
                final_url=safe_url,
                state=AuthState.NEEDS_LOGIN,
                authenticated=False,
                cas_redirect_detected=False,
                mfa_or_captcha_detected=has_mfa_or_captcha,
                auth_error_detected=False,
                origin_verified=True,
                detail="CAS ticket exchange or session verification redirect is still in progress.",
                profile_dir=str(profile_dir or self.profile_dir_for(source)),
                used_myensai_fallback=used_myensai_fallback,
                html_excerpt_available=bool(html_content),
                failure_reason="ticket_exchange_incomplete",
            )

        if source == SourceKind.MOODLE:
            parsed_cur = urlparse(current_url)
            cur_path = parsed_cur.path.lower()
            is_login_page = "/login/index.php" in cur_path or "/login/" in cur_path or (
                ('id="login"' in lower_html or 'class="loginform"' in lower_html)
                and 'name="password"' in lower_html
            )
            has_guest_marker = any(
                m in lower_html
                for m in (
                    "vous n'êtes pas connecté",
                    "vous n&#039;êtes pas connecté",
                    "vous n&apos;êtes pas connecté",
                    "you are not logged in",
                    "non connecté",
                    "not logged in",
                    "accès anonyme",
                    "guest access",
                )
            )
            has_positive_userid = bool(_MOODLE_POSITIVE_USERID_RE.search(lower_html))
            has_dashboard_shell = (
                cur_path.startswith("/my")
                and any(
                    m in lower_html
                    for m in (
                        "pagelayout-mydashboard",
                        "page-my-index",
                        'data-region="myoverview"',
                        "block_myoverview",
                        "/course/view.php?id=",
                    )
                )
            )
            has_logout_or_session_token = (
                any(
                    marker in lower_html
                    for marker in (
                        "logout.php",
                        "sesskey=",
                        '"sesskey"',
                        'data-region="usermenu"',
                        "usermenu",
                        "action-menu-trigger",
                    )
                )
                or has_positive_userid
                or has_dashboard_shell
            )
            # If the page has a login link inside logininfo WITHOUT authenticated markers, user is NOT authenticated
            has_login_prompt_only = (
                "/login/index.php" in lower_html
                and "logout.php" not in lower_html
                and "sesskey" not in lower_html
                and 'data-region="usermenu"' not in lower_html
                and not has_positive_userid
            )
            if (
                on_target_host
                and not is_login_page
                and not has_guest_marker
                and not has_login_prompt_only
                and has_logout_or_session_token
            ):
                return EnsaiSessionProbe(
                    source=source,
                    requested_url=requested_url,
                    final_url=safe_url,
                    state=AuthState.AUTHENTICATED,
                    authenticated=True,
                    cas_redirect_detected=False,
                    mfa_or_captcha_detected=False,
                    auth_error_detected=False,
                    origin_verified=True,
                    detail="Authenticated Moodle ENSAI protected resource confirmed.",
                    profile_dir=str(profile_dir or self.profile_dir_for(source)),
                    used_myensai_fallback=used_myensai_fallback,
                    html_excerpt_available=bool(html_content),
                    failure_reason=None,
                )
            if is_login_page:
                fail_reason = "moodle_login_chooser"
            elif has_guest_marker or has_login_prompt_only:
                fail_reason = "unauthenticated_moodle_page"
            elif not on_target_host:
                fail_reason = "off_target_host"
            else:
                fail_reason = "missing_authenticated_shell"
            return EnsaiSessionProbe(
                source=source,
                requested_url=requested_url,
                final_url=safe_url,
                state=AuthState.EXPIRED if on_target_host else AuthState.NEEDS_LOGIN,
                authenticated=False,
                cas_redirect_detected=False,
                mfa_or_captcha_detected=has_mfa_or_captcha,
                auth_error_detected=False,
                origin_verified=True,
                detail="Moodle session is unauthenticated or expired.",
                profile_dir=str(profile_dir or self.profile_dir_for(source)),
                used_myensai_fallback=used_myensai_fallback,
                html_excerpt_available=bool(html_content),
                failure_reason=fail_reason,
            )

        if source == SourceKind.PAMPLEMOUSSE:
            is_login_form = (
                'type="password"' in lower_html
                or 'name="password"' in lower_html
                or "session expir" in lower_html
                or "authentification requise" in lower_html
                or "veuillez vous identifier" in lower_html
                or "veuillez vous connecter" in lower_html
                or "ssocas.ensai.fr/cas/login" in lower_html
            )
            has_authenticated_shell = any(
                marker in combined
                for marker in (
                    "deconnexion",
                    "déconnexion",
                    "logout",
                    "disconnect",
                    "se déconnecter",
                    "quitter",
                    "emploi du temps",
                    "planning",
                    "scolarit",
                    "mes notes",
                    "relevé",
                    "releve",
                    "bulletin",
                    "calendrier",
                    "agenda",
                    "mon compte",
                    "mon espace",
                    "espace étudiant",
                    "espace etudiant",
                    "absences",
                    "cursus",
                    "trombinoscope",
                    "notes",
                )
            )
            # Some Leos/Pamplemousse portal pages render a frameset or JS workspace shell
            # once authenticated with active service cookies and zero login prompts.
            has_cookie_backed_portal_shell = (
                has_service_cookies
                and bool(lower_html.strip())
                and any(
                    marker in lower_html
                    for marker in (
                        "<frameset",
                        "<iframe",
                        "<nav",
                        "<table",
                        "menu",
                        "accueil",
                        "formation",
                        "etudiant",
                        "étudiant",
                        "pamplemousse",
                    )
                )
            )
            if (
                on_target_host
                and not is_login_form
                and (has_authenticated_shell or has_cookie_backed_portal_shell)
            ):
                return EnsaiSessionProbe(
                    source=source,
                    requested_url=requested_url,
                    final_url=safe_url,
                    state=AuthState.AUTHENTICATED,
                    authenticated=True,
                    cas_redirect_detected=False,
                    mfa_or_captcha_detected=False,
                    auth_error_detected=False,
                    origin_verified=True,
                    detail="Authenticated Pamplemousse ENSAI session confirmed.",
                    profile_dir=str(profile_dir or self.profile_dir_for(source)),
                    used_myensai_fallback=used_myensai_fallback,
                    html_excerpt_available=bool(html_content),
                    failure_reason=None,
                )
            if is_login_form:
                fail_reason = "pamplemousse_login_form"
            elif not on_target_host:
                fail_reason = "off_target_host"
            else:
                fail_reason = "missing_authenticated_shell"
            return EnsaiSessionProbe(
                source=source,
                requested_url=requested_url,
                final_url=safe_url,
                state=AuthState.EXPIRED if on_target_host else AuthState.NEEDS_LOGIN,
                authenticated=False,
                cas_redirect_detected=False,
                mfa_or_captcha_detected=has_mfa_or_captcha,
                auth_error_detected=False,
                origin_verified=True,
                detail="Pamplemousse session is unauthenticated or expired.",
                profile_dir=str(profile_dir or self.profile_dir_for(source)),
                used_myensai_fallback=used_myensai_fallback,
                html_excerpt_available=bool(html_content),
                failure_reason=fail_reason,
            )

        return EnsaiSessionProbe(
            source=source,
            requested_url=requested_url,
            final_url=safe_url,
            state=AuthState.NEEDS_LOGIN,
            authenticated=False,
            cas_redirect_detected=is_cas,
            mfa_or_captcha_detected=has_mfa_or_captcha,
            auth_error_detected=False,
            origin_verified=True,
            detail="Unable to confirm authenticated session.",
            profile_dir=str(profile_dir or self.profile_dir_for(source)),
            used_myensai_fallback=used_myensai_fallback,
            html_excerpt_available=bool(html_content),
            failure_reason="unknown_auth_state",
        )

    def _inspect_current_page(
        self,
        page: Any,
        context: Any,
        source: SourceKind,
        *,
        requested_url: str,
        profile_dir: Path,
        phase: str = "inspecting",
        used_myensai_fallback: bool = False,
    ) -> tuple[EnsaiSessionProbe, EnsaiAuthDiagnostics]:
        """Inspect current page (including same-origin child frames) without issuing a new goto()."""
        target_host = urlparse(self.service_url_for(source)).netloc.lower()
        protected_url = self.protected_url_for(source)
        cur_url = str(getattr(page, "url", "") or "")
        html = self._collect_page_html(page, target_host)
        try:
            title = page.title() or ""
        except Exception:
            title = ""
        session_persisted, service_cookies_present = self._inspect_session_persistence(
            context, profile_dir, target_host
        )
        probe = self.classify_page_state(
            source,
            requested_url=requested_url,
            current_url=cur_url,
            html_content=html,
            page_title=title,
            profile_dir=profile_dir,
            used_myensai_fallback=used_myensai_fallback,
            has_service_cookies=service_cookies_present,
        )
        diag = self._build_diagnostics(
            source=source,
            phase=phase,
            current_url=cur_url,
            target_host=target_host,
            protected_url=protected_url,
            probe=probe,
            session_persisted=session_persisted,
            service_cookies_present=service_cookies_present,
        )
        enriched = EnsaiSessionProbe(
            source=probe.source,
            requested_url=probe.requested_url,
            final_url=probe.final_url,
            state=probe.state,
            authenticated=probe.authenticated,
            cas_redirect_detected=probe.cas_redirect_detected,
            mfa_or_captcha_detected=probe.mfa_or_captcha_detected,
            auth_error_detected=probe.auth_error_detected,
            origin_verified=probe.origin_verified,
            detail=probe.detail,
            profile_dir=probe.profile_dir,
            used_myensai_fallback=probe.used_myensai_fallback,
            html_excerpt_available=probe.html_excerpt_available,
            failure_reason=probe.failure_reason,
            diagnostics=diag.to_dict(),
        )
        return enriched, diag

    def _goto_with_timeout_recovery(
        self,
        page: BrowserPageProtocol,
        url: str,
        *,
        timeout_ms: int,
    ) -> None:
        """Navigate with bounded timeout recovery, translating unrecoverable stalls to TransientSourceError."""
        from ..connectors.base import TransientSourceError

        def _is_timeout(exc: Exception) -> bool:
            name = type(exc).__name__.lower()
            msg = str(exc).lower()
            return "timeout" in name or "timed out" in msg or "timeout " in msg

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return
        except Exception as first_exc:
            if not _is_timeout(first_exc):
                raise
            logger.warning(
                "ENSAI navigation timed out (%dms) reaching %s; retrying with commit fallback.",
                timeout_ms,
                sanitize_url_for_logs(url),
            )
            retry_timeout = max(timeout_ms, 45_000)
            try:
                page.goto(url, wait_until="commit", timeout=retry_timeout)
            except Exception as second_exc:
                if _is_timeout(second_exc):
                    raise TransientSourceError(
                        f"Playwright navigation timed out reaching {sanitize_url_for_logs(url)} "
                        f"after retry ({retry_timeout}ms)."
                    ) from second_exc
                raise
            if hasattr(page, "wait_for_load_state"):
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(15_000, retry_timeout))
                except Exception:
                    # Page committed and may already hold usable DOM; let caller inspect content.
                    pass

    def _probe_on_page(
        self,
        page: BrowserPageProtocol,
        source: SourceKind,
        *,
        profile_dir: Path,
        allow_myensai_fallback: bool = True,
        target_url: str | None = None,
        context: Any | None = None,
        phase: str = "verifying_protected_resource",
    ) -> EnsaiSessionProbe:
        # Always probe an actual protected resource URL (e.g. Moodle /my/courses.php)
        from ..connectors.base import TransientSourceError

        probe_url = target_url or self.protected_url_for(source)
        try:
            self._goto_with_timeout_recovery(page, probe_url, timeout_ms=self._config.ensai_timeout_ms)
        except TransientSourceError:
            # If Moodle /my/courses.php stalls, try lightweight static /user/profile.php before failing
            if source == SourceKind.MOODLE and target_url is None:
                alt_url = urljoin(self._config.moodle_base_url, "user/profile.php")
                if alt_url != probe_url:
                    logger.warning(
                        "Moodle protected probe timed out on %s; falling back to %s.",
                        sanitize_url_for_logs(probe_url),
                        sanitize_url_for_logs(alt_url),
                    )
                    probe_url = alt_url
                    self._goto_with_timeout_recovery(
                        page, probe_url, timeout_ms=self._config.ensai_timeout_ms
                    )
                else:
                    raise
            else:
                raise
        self._settle_navigation(page, timeout_ms=min(8_000, self._config.ensai_timeout_ms))
        probe, _ = self._inspect_current_page(
            page,
            context,
            source,
            requested_url=probe_url,
            profile_dir=profile_dir,
            phase=phase,
            used_myensai_fallback=False,
        )
        # If landed on a service login chooser page (e.g. Moodle /login/index.php with a CAS button),
        # follow the official service-provided CAS link so silent SSO reuse works and `?service=` is kept.
        if (
            not probe.authenticated
            and not probe.cas_redirect_detected
            and probe.origin_verified
        ):
            html = self._collect_page_html(page, urlparse(self.service_url_for(source)).netloc.lower())
            cas_entry = self._extract_service_cas_link(html, page.url)
            if cas_entry:
                self._goto_with_timeout_recovery(page, cas_entry, timeout_ms=self._config.ensai_timeout_ms)
                self._settle_navigation(page, timeout_ms=min(8_000, self._config.ensai_timeout_ms))
                probe, _ = self._inspect_current_page(
                    page,
                    context,
                    source,
                    requested_url=probe_url,
                    profile_dir=profile_dir,
                    phase=phase,
                    used_myensai_fallback=False,
                )
        # Only fall back to MyEnsai if direct service URL neither authenticated nor offered CAS
        if (
            allow_myensai_fallback
            and not probe.authenticated
            and not probe.cas_redirect_detected
            and probe.origin_verified
        ):
            self._goto_with_timeout_recovery(
                page,
                self._config.myensai_url,
                timeout_ms=self._config.ensai_timeout_ms,
            )
            self._settle_navigation(page, timeout_ms=min(8_000, self._config.ensai_timeout_ms))
            myensai_probe, _ = self._inspect_current_page(
                page,
                context,
                source,
                requested_url=self._config.myensai_url,
                profile_dir=profile_dir,
                phase=phase,
                used_myensai_fallback=True,
            )
            if myensai_probe.cas_redirect_detected or myensai_probe.authenticated:
                return myensai_probe
        return probe

    def _optional_account_hint(self) -> str | None:
        try:
            stored = self._credentials.get_ensai_username()
            if stored:
                return stored
        except Exception:
            pass
        return self._config.ensai_username_hint

    def check_source_connection(
        self,
        source: SourceKind,
        *,
        headless: bool = True,
        allow_auto_reauth: bool = False,
    ) -> AuthStatus:
        """Non-interactive check verifying whether a valid persistent session exists for a source.

        Reuses valid persistent Chromium sessions across restarts. When the session has
        expired (or no profile exists yet) AND `allow_auto_reauth=True` AND
        `ENSAI_AUTO_LOGIN=true` AND credentials exist in the OS keyring, performs a single
        bounded headless automatic reauthentication (never bypassing MFA/CAPTCHA).
        """
        profile_dir = self.profile_dir_for(source)
        can_headless_auto = (
            allow_auto_reauth
            and self._config.ensai_allow_automated_credential_submit
            and self._credentials.has_ensai_credentials()
        )
        if self._context_factory is None and not profile_dir.exists():
            if can_headless_auto:
                reauth = self.authenticate(
                    source,
                    interactive=False,
                    allow_automated_submit=True,
                )
                if reauth.authenticated:
                    meta = dict(reauth.metadata or {})
                    meta["reauthenticated_automatically"] = True
                    return AuthStatus(
                        source=reauth.source,
                        state=reauth.state,
                        authenticated=True,
                        detail=reauth.detail,
                        account_hint=reauth.account_hint,
                        requires_interactive_action=False,
                        metadata=meta,
                    )
                return reauth
            diag = EnsaiAuthDiagnostics(
                phase="check_connection",
                navigation_state="no_profile",
                current_host="",
                current_path="",
                redirect_completed=False,
                session_persisted=False,
                service_cookies_present=False,
                failure_reason="profile_missing",
            )
            return AuthStatus(
                source=source,
                state=AuthState.NEEDS_LOGIN,
                authenticated=False,
                detail="No persisted ENSAI browser session found yet.",
                requires_interactive_action=True,
                next_action=f"Run ENSAI authentication for {source.value}.",
                metadata={"profile_dir": str(profile_dir), "diagnostics": diag.to_dict()},
            )
        # Fast path: validate persisted allowlisted session cookies over HTTP before launching Chromium
        if self._context_factory is None:
            fast_probe = self._probe_via_cookies_http(
                source, profile_dir=profile_dir, phase="check_connection"
            )
            if fast_probe is not None and fast_probe.authenticated:
                return AuthStatus(
                    source=source,
                    state=fast_probe.state,
                    authenticated=True,
                    detail=fast_probe.detail,
                    account_hint=self._optional_account_hint(),
                    requires_interactive_action=False,
                    next_action=None,
                    metadata=fast_probe.to_dict(),
                )
        try:
            with self.open_context(source, headless=headless) as context:
                page = context.pages[0] if context.pages else context.new_page()
                probe = self._probe_on_page(
                    page,
                    source,
                    profile_dir=profile_dir,
                    allow_myensai_fallback=False,
                    context=context,
                    phase="check_connection",
                )
                if probe.authenticated:
                    self._save_session_cookies(context, profile_dir)
            if not probe.authenticated and probe.origin_verified and can_headless_auto:
                reauth = self.authenticate(
                    source,
                    interactive=False,
                    allow_automated_submit=True,
                )
                meta = dict(reauth.metadata or {})
                meta["reauthenticated_automatically"] = bool(reauth.authenticated)
                return AuthStatus(
                    source=reauth.source,
                    state=reauth.state,
                    authenticated=reauth.authenticated,
                    detail=reauth.detail,
                    account_hint=reauth.account_hint,
                    requires_interactive_action=not reauth.authenticated,
                    next_action=reauth.next_action,
                    metadata=meta,
                )
            if not probe.authenticated and self._context_factory is None and probe.diagnostics:
                d = probe.diagnostics
                self._emit_interactive_notice(
                    f"[ensora-auth] source={source.value} phase=validate_failed "
                    f"nav={d.get('navigation_state')} host={d.get('current_host')} "
                    f"path={d.get('current_path')} redirect_done={d.get('redirect_completed')} "
                    f"persisted={d.get('session_persisted')} cookies={d.get('service_cookies_present')} "
                    f"reason={d.get('failure_reason')}",
                    interactive=True,
                )
            return AuthStatus(
                source=source,
                state=probe.state,
                authenticated=probe.authenticated,
                detail=probe.detail,
                account_hint=self._optional_account_hint(),
                requires_interactive_action=not probe.authenticated,
                next_action=None if probe.authenticated else "Reauthenticate via EnsaiAuthAdapter.",
                metadata=probe.to_dict(),
            )
        except BrowserProfileLockError:
            raise
        except Exception as exc:
            from ..connectors.base import TransientSourceError

            if type(exc).__name__ == "ProfileBusy":
                raise
            is_transient = isinstance(exc, TransientSourceError) or (
                "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()
            )
            err_type = type(exc).__name__
            logger.warning(
                "ENSAI connection check failed for %s (%s, transient=%s)",
                source.value,
                err_type,
                is_transient,
            )
            if self._context_factory is None:
                self._emit_interactive_notice(
                    f"[ensora-auth] source={source.value} phase=check_connection_error "
                    f"exc_type={err_type} transient={is_transient}",
                    interactive=True,
                )
            return AuthStatus(
                source=source,
                state=AuthState.ERROR,
                authenticated=False,
                detail=redact_sensitive_text(f"Connection check failed ({err_type}): {exc}"),
                requires_interactive_action=not is_transient,
                next_action=(
                    "Retry validation (transient navigation timeout)."
                    if is_transient
                    else f"Reauthenticate via EnsaiAuthAdapter for {source.value}."
                ),
                metadata={
                    "profile_dir": str(profile_dir),
                    "error_type": err_type,
                    "errorcode": "transient_failure" if is_transient else "adapter_error",
                },
            )

    def _wait_for_interactive_login(
        self,
        page: Any,
        context: Any,
        source: SourceKind,
        *,
        protected_url: str,
        profile_dir: Path,
        wait_timeout_ms: int,
        confirm_fn: Callable[[str], str] | None = None,
        poll_interval_sec: float = 0.5,
    ) -> tuple[EnsaiSessionProbe, EnsaiAuthDiagnostics]:
        """Wait patiently for human ENSAI CAS login, complete redirects, and verify protected access.

        - Never interrupts mid-flight `?ticket=` / `?testsession=` / `?authCAS=` redirects.
        - Automatically bridges CAS "Log In Successful" pages that forgot to redirect back.
        - Supports BOTH automatic post-login detection AND optional explicit human confirmation
          (Enter in interactive TTY or injected `confirm_fn`), without closing Chromium early.
        """
        target_host = urlparse(protected_url).netloc.lower()
        protected_path = urlparse(protected_url).path.lower().rstrip("/") or "/"
        deadline = time.monotonic() + max(1.0, wait_timeout_ms / 1000.0)
        cas_success_redirect_attempted = False
        auth_error_noticed = False
        use_tty_confirm = (
            confirm_fn is None
            and self._context_factory is None
            and hasattr(sys.stdin, "isatty")
            and sys.stdin.isatty()
        )

        init_host, init_path = sanitize_url_host_path(str(getattr(page, "url", "") or ""))
        self._emit_interactive_notice(
            f"[ensora-auth] Opened official ENSAI login window for {source.value} "
            f"(host={init_host or 'none'} path={init_path or '/'}). "
            f"Complete CAS login (and any MFA) in Chromium. Detection completes automatically "
            f"once redirects finish, or press Enter here once {source.value} is loaded "
            f"(type 'q' + Enter to cancel).",
            interactive=True,
        )

        last_probe, last_diag = self._inspect_current_page(
            page,
            context,
            source,
            requested_url=protected_url,
            profile_dir=profile_dir,
            phase="waiting_for_login",
        )

        while True:
            if self._is_page_closed(page):
                persisted, cookies_ok = self._inspect_session_persistence(
                    context, profile_dir, target_host
                )
                diag = self._build_diagnostics(
                    source=source,
                    phase="failed",
                    current_url=last_probe.final_url,
                    target_host=target_host,
                    protected_url=protected_url,
                    probe=None,
                    session_persisted=persisted,
                    service_cookies_present=cookies_ok,
                    failure_reason_override="browser_closed_by_user",
                    navigation_state_override="browser_closed",
                )
                closed_probe = EnsaiSessionProbe(
                    source=source,
                    requested_url=protected_url,
                    final_url=last_probe.final_url,
                    state=AuthState.NEEDS_LOGIN,
                    authenticated=False,
                    cas_redirect_detected=False,
                    mfa_or_captcha_detected=False,
                    auth_error_detected=False,
                    origin_verified=True,
                    detail="Browser window was closed before ENSAI authentication completed.",
                    profile_dir=str(profile_dir),
                    failure_reason="browser_closed_by_user",
                    diagnostics=diag.to_dict(),
                )
                return closed_probe, diag

            cur_url = str(getattr(page, "url", "") or "")
            if is_transient_sso_url(cur_url):
                # Mid-ticket exchange: wait patiently without interrupting!
                self._settle_navigation(
                    page,
                    timeout_ms=min(5_000, self._config.ensai_timeout_ms),
                    max_transient_wait_sec=min(8.0, max(0.5, deadline - time.monotonic())),
                )
                cur_url = str(getattr(page, "url", "") or "")

            probe, diag = self._inspect_current_page(
                page,
                context,
                source,
                requested_url=protected_url,
                profile_dir=profile_dir,
                phase="waiting_for_login",
            )
            last_probe, last_diag = probe, diag

            # 1. Authenticated on target service!
            if probe.authenticated:
                cur_path_norm = urlparse(cur_url).path.lower().rstrip("/") or "/"
                if cur_path_norm == protected_path:
                    final_diag = self._build_diagnostics(
                        source=source,
                        phase="completed",
                        current_url=cur_url,
                        target_host=target_host,
                        protected_url=protected_url,
                        probe=probe,
                        session_persisted=diag.session_persisted,
                        service_cookies_present=diag.service_cookies_present,
                    )
                    verified = EnsaiSessionProbe(
                        **{**asdict(probe), "diagnostics": final_diag.to_dict()}
                    )
                    return verified, final_diag
                # Authenticated on another page of target_host (e.g., Moodle `/` or `/my/`):
                # now that ticket exchange & cookies are settled, verify protected_url directly.
                self._settle_navigation(page, timeout_ms=min(5_000, self._config.ensai_timeout_ms))
                verified_probe = self._probe_on_page(
                    page,
                    source,
                    profile_dir=profile_dir,
                    allow_myensai_fallback=False,
                    target_url=protected_url,
                    context=context,
                    phase="verifying_protected_resource",
                )
                if verified_probe.authenticated:
                    persisted, cookies_ok = self._inspect_session_persistence(
                        context, profile_dir, target_host
                    )
                    final_diag = self._build_diagnostics(
                        source=source,
                        phase="completed",
                        current_url=str(getattr(page, "url", "") or ""),
                        target_host=target_host,
                        protected_url=protected_url,
                        probe=verified_probe,
                        session_persisted=persisted,
                        service_cookies_present=cookies_ok,
                    )
                    verified = EnsaiSessionProbe(
                        **{**asdict(verified_probe), "diagnostics": final_diag.to_dict()}
                    )
                    return verified, final_diag
                last_probe = verified_probe

            # 2. CAS displayed "Log In Successful" without redirecting back -> trigger silent SSO!
            if (
                probe.failure_reason == "cas_login_succeeded_without_redirect"
                and not cas_success_redirect_attempted
            ):
                cas_success_redirect_attempted = True
                self._emit_interactive_notice(
                    f"[ensora-auth] CAS login confirmed for {source.value}; completing redirect to protected service...",
                    interactive=True,
                )
                verified_probe = self._probe_on_page(
                    page,
                    source,
                    profile_dir=profile_dir,
                    allow_myensai_fallback=False,
                    target_url=protected_url,
                    context=context,
                    phase="settling_redirects",
                )
                if verified_probe.authenticated:
                    persisted, cookies_ok = self._inspect_session_persistence(
                        context, profile_dir, target_host
                    )
                    final_diag = self._build_diagnostics(
                        source=source,
                        phase="completed",
                        current_url=str(getattr(page, "url", "") or ""),
                        target_host=target_host,
                        protected_url=protected_url,
                        probe=verified_probe,
                        session_persisted=persisted,
                        service_cookies_present=cookies_ok,
                    )
                    return (
                        EnsaiSessionProbe(**{**asdict(verified_probe), "diagnostics": final_diag.to_dict()}),
                        final_diag,
                    )
                last_probe = verified_probe

            if probe.auth_error_detected and not auth_error_noticed:
                auth_error_noticed = True
                self._emit_interactive_notice(
                    f"[ensora-auth] CAS reported an authentication error for {source.value}; "
                    "browser remains open so you can retry login.",
                    interactive=True,
                )

            # 3. Check optional explicit human confirmation (non-blocking TTY or injected confirm_fn)
            user_response: str | None = None
            waited_in_select = False
            if confirm_fn is not None:
                user_response = confirm_fn(
                    f"Press Enter once {source.value} is loaded in Chromium (or 'q' to cancel): "
                )
            elif use_tty_confirm:
                remaining = max(0.05, min(poll_interval_sec, deadline - time.monotonic()))
                try:
                    ready, _, _ = select.select([sys.stdin], [], [], remaining)
                    waited_in_select = True
                    if ready:
                        user_response = sys.stdin.readline()
                except Exception:
                    waited_in_select = False

            if user_response is not None:
                cmd = user_response.strip().lower()
                if cmd in ("q", "quit", "cancel", "abort"):
                    persisted, cookies_ok = self._inspect_session_persistence(
                        context, profile_dir, target_host
                    )
                    diag = self._build_diagnostics(
                        source=source,
                        phase="failed",
                        current_url=str(getattr(page, "url", "") or ""),
                        target_host=target_host,
                        protected_url=protected_url,
                        probe=last_probe,
                        session_persisted=persisted,
                        service_cookies_present=cookies_ok,
                        failure_reason_override="cancelled_by_user",
                        navigation_state_override="cancelled",
                    )
                    cancelled = EnsaiSessionProbe(
                        source=source,
                        requested_url=protected_url,
                        final_url=sanitize_url_for_logs(str(getattr(page, "url", "") or "")),
                        state=AuthState.NEEDS_LOGIN,
                        authenticated=False,
                        cas_redirect_detected=last_probe.cas_redirect_detected,
                        mfa_or_captcha_detected=last_probe.mfa_or_captcha_detected,
                        auth_error_detected=False,
                        origin_verified=last_probe.origin_verified,
                        detail="Interactive ENSAI login cancelled by user.",
                        profile_dir=str(profile_dir),
                        failure_reason="cancelled_by_user",
                        diagnostics=diag.to_dict(),
                    )
                    return cancelled, diag

                # Human confirmed login finished: wait out any transient ticket exchange first,
                # inspect current page, and if needed navigate to protected_url to verify access.
                self._settle_navigation(page, timeout_ms=min(8_000, self._config.ensai_timeout_ms))
                probe_now, diag_now = self._inspect_current_page(
                    page,
                    context,
                    source,
                    requested_url=protected_url,
                    profile_dir=profile_dir,
                    phase="confirming_session",
                )
                cur_after = str(getattr(page, "url", "") or "")
                cur_after_path = urlparse(cur_after).path.lower().rstrip("/") or "/"
                if not probe_now.authenticated or cur_after_path != protected_path:
                    probe_now = self._probe_on_page(
                        page,
                        source,
                        profile_dir=profile_dir,
                        allow_myensai_fallback=False,
                        target_url=protected_url,
                        context=context,
                        phase="verifying_protected_resource",
                    )
                persisted, cookies_ok = self._inspect_session_persistence(
                    context, profile_dir, target_host
                )
                final_diag = self._build_diagnostics(
                    source=source,
                    phase="completed" if probe_now.authenticated else "confirming_session",
                    current_url=str(getattr(page, "url", "") or ""),
                    target_host=target_host,
                    protected_url=protected_url,
                    probe=probe_now,
                    session_persisted=persisted,
                    service_cookies_present=cookies_ok,
                )
                enriched_now = EnsaiSessionProbe(
                    **{**asdict(probe_now), "diagnostics": final_diag.to_dict()}
                )
                if enriched_now.authenticated:
                    return enriched_now, final_diag
                last_probe, last_diag = enriched_now, final_diag
                self._emit_interactive_notice(
                    f"{final_diag.format_safe_summary(source)} "
                    "(session not yet verified; finish login in browser then press Enter again, or 'q' to cancel)",
                    interactive=True,
                )
                if confirm_fn is not None:
                    # Avoid tight infinite loops when a deterministic test supplies a one-shot confirm_fn
                    return enriched_now, final_diag

            if time.monotonic() >= deadline:
                persisted, cookies_ok = self._inspect_session_persistence(
                    context, profile_dir, target_host
                )
                timeout_reason = last_probe.failure_reason or "login_timeout"
                if timeout_reason in ("cas_login_pending", "mfa_or_captcha_pending"):
                    timeout_reason = f"login_timeout:{timeout_reason}"
                diag = self._build_diagnostics(
                    source=source,
                    phase="failed",
                    current_url=str(getattr(page, "url", "") or ""),
                    target_host=target_host,
                    protected_url=protected_url,
                    probe=last_probe,
                    session_persisted=persisted,
                    service_cookies_present=cookies_ok,
                    failure_reason_override=timeout_reason,
                )
                timed_out = EnsaiSessionProbe(
                    **{
                        **asdict(last_probe),
                        "failure_reason": timeout_reason,
                        "diagnostics": diag.to_dict(),
                    }
                )
                return timed_out, diag

            if not waited_in_select:
                if hasattr(page, "wait_for_timeout"):
                    try:
                        page.wait_for_timeout(int(poll_interval_sec * 1000))
                    except Exception:
                        time.sleep(poll_interval_sec)
                else:
                    time.sleep(min(poll_interval_sec, 0.05))

    def authenticate(
        self,
        source: SourceKind = SourceKind.MOODLE,
        *,
        interactive: bool = True,
        allow_automated_submit: bool | None = None,
        wait_timeout_ms: int = 180_000,
        target_url: str | None = None,
        confirm_fn: Callable[[str], str] | None = None,
        keep_open_until_confirmed: bool = False,
    ) -> AuthStatus:
        """Authenticate against ENSAI CAS and persist the authorized browser session.

        - Navigates to an actual protected resource URL first to reuse valid sessions or
          silent CAS SSO cookies and preserve exact `?service=` redirect parameters.
        - Validates official origin strictly before any credential submission.
        - Never bypasses MFA or CAPTCHA: opens the official page in a visible window for human login.
        - Never interrupts mid-flight `?ticket=` / `?testsession=` redirects.
        - Persists allowlisted institutional session cookies (`expires == -1` included) atomically
          (`0o600`) before closing Chromium so reopened headless validations reuse the live session.
        - Supports optional `keep_open_until_confirmed=True` debugging mode keeping Chromium open
          on the verified protected resource until the operator explicitly confirms closure.
        """
        auto_submit = (
            self._config.ensai_allow_automated_credential_submit
            if allow_automated_submit is None
            else allow_automated_submit
        )
        profile_dir = self.profile_dir_for(source)
        protected_url = target_url or self.protected_url_for(source)
        target_host = urlparse(protected_url).netloc.lower()
        # Interactive human login uses a visible window; non-interactive auto-reauth is strictly headless
        headless = False if interactive else True

        def _confirm_keep_open_and_persist(
            ctx: Any,
            pg: Any,
            verified_probe: EnsaiSessionProbe,
            verified_diag: EnsaiAuthDiagnostics,
        ) -> tuple[EnsaiSessionProbe, EnsaiAuthDiagnostics, bool]:
            """Optionally keep Chromium open until operator confirms, then persist session cookies."""
            if interactive and keep_open_until_confirmed:
                prompt = (
                    f"[ensora-auth] stage=PROTECTED_RESOURCE_VERIFIED source={source.value} "
                    f"host={verified_diag.current_host} path={verified_diag.current_path} — "
                    "Inspect Chromium and press Enter to persist session & close (or 'q' to abort): "
                )
                if confirm_fn is not None:
                    ans = str(confirm_fn(prompt) or "").strip().lower()
                elif hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
                    sys.stderr.write(prompt)
                    sys.stderr.flush()
                    ans = sys.stdin.readline().strip().lower()
                else:
                    ans = ""
                if ans in ("q", "quit", "cancel", "abort"):
                    abort_diag = self._build_diagnostics(
                        source=source,
                        phase="failed",
                        current_url=str(getattr(pg, "url", "") or verified_probe.final_url),
                        target_host=target_host,
                        protected_url=protected_url,
                        probe=verified_probe,
                        session_persisted=False,
                        service_cookies_present=verified_diag.service_cookies_present,
                        failure_reason_override="cancelled_by_user",
                        navigation_state_override="cancelled",
                    )
                    aborted = EnsaiSessionProbe(
                        source=source,
                        requested_url=protected_url,
                        final_url=verified_probe.final_url,
                        state=AuthState.NEEDS_LOGIN,
                        authenticated=False,
                        cas_redirect_detected=False,
                        mfa_or_captcha_detected=False,
                        auth_error_detected=False,
                        origin_verified=True,
                        detail="Session persistence cancelled by user during debug inspection.",
                        profile_dir=str(profile_dir),
                        failure_reason="cancelled_by_user",
                        diagnostics=abort_diag.to_dict(),
                    )
                    return aborted, abort_diag, False

            cookies_saved = self._save_session_cookies(ctx, profile_dir)
            if self._context_factory is None:
                time.sleep(0.5)
            persisted_now, cookies_now = self._inspect_session_persistence(
                ctx, profile_dir, target_host
            )
            final_d = self._build_diagnostics(
                source=source,
                phase="completed",
                current_url=str(getattr(pg, "url", "") or verified_probe.final_url),
                target_host=target_host,
                protected_url=protected_url,
                probe=verified_probe,
                session_persisted=persisted_now or cookies_saved,
                service_cookies_present=cookies_now or cookies_saved,
            )
            final_p = EnsaiSessionProbe(**{**asdict(verified_probe), "diagnostics": final_d.to_dict()})
            return final_p, final_d, (persisted_now or cookies_saved)

        try:
            with self.open_context(source, headless=headless) as context:
                page = context.pages[0] if context.pages else context.new_page()
                probe = self._probe_on_page(
                    page,
                    source,
                    profile_dir=profile_dir,
                    allow_myensai_fallback=False,
                    target_url=protected_url,
                    context=context,
                    phase="initial_probe",
                )

                if probe.authenticated:
                    persisted, cookies_ok = self._inspect_session_persistence(
                        context, profile_dir, target_host
                    )
                    diag = self._build_diagnostics(
                        source=source,
                        phase="completed",
                        current_url=str(getattr(page, "url", "") or ""),
                        target_host=target_host,
                        protected_url=protected_url,
                        probe=probe,
                        session_persisted=persisted,
                        service_cookies_present=cookies_ok,
                    )
                    final, diag, cookies_persisted = _confirm_keep_open_and_persist(
                        context, page, probe, diag
                    )
                    self._emit_interactive_notice(diag.format_safe_summary(source), interactive=interactive)
                    meta = final.to_dict()
                    meta["session_cookies_persisted"] = cookies_persisted
                    return AuthStatus(
                        source=source,
                        state=final.state,
                        authenticated=final.authenticated,
                        detail=final.detail,
                        account_hint=self._optional_account_hint(),
                        requires_interactive_action=not final.authenticated,
                        metadata=meta,
                    )

                if not probe.origin_verified:
                    return AuthStatus(
                        source=source,
                        state=AuthState.ERROR,
                        authenticated=False,
                        detail=probe.detail,
                        requires_interactive_action=True,
                        metadata=probe.to_dict(),
                    )

                # Navigate to official CAS login WITH `?service=<protected_url>` if not redirected automatically
                if not probe.cas_redirect_detected:
                    cas_with_service = self._cas_url_with_service(protected_url)
                    page.goto(
                        cas_with_service,
                        wait_until="domcontentloaded",
                        timeout=self._config.ensai_timeout_ms,
                    )
                    self._settle_navigation(page, timeout_ms=min(8_000, self._config.ensai_timeout_ms))
                    probe, diag = self._inspect_current_page(
                        page,
                        context,
                        source,
                        requested_url=protected_url,
                        profile_dir=profile_dir,
                        phase="cas_login",
                    )
                    if probe.authenticated:
                        final, diag, cookies_persisted = _confirm_keep_open_and_persist(
                            context, page, probe, diag
                        )
                        self._emit_interactive_notice(diag.format_safe_summary(source), interactive=interactive)
                        meta = final.to_dict()
                        meta["session_cookies_persisted"] = cookies_persisted
                        return AuthStatus(
                            source=source,
                            state=final.state,
                            authenticated=final.authenticated,
                            detail=final.detail,
                            account_hint=self._optional_account_hint(),
                            requires_interactive_action=not final.authenticated,
                            metadata=meta,
                        )
                    if not probe.origin_verified:
                        return AuthStatus(
                            source=source,
                            state=AuthState.ERROR,
                            authenticated=False,
                            detail=probe.detail,
                            metadata=probe.to_dict(),
                        )

                # Authorized automated submission ONLY on verified CAS origin without MFA/CAPTCHA
                # Uses already-stored OS keyring credentials; never blocks an open browser window
                # with terminal prompts, and always falls back to interactive login on MFA/error.
                cas_host = urlparse(self._config.cas_login_url).netloc.lower()
                on_official_cas = (
                    validate_official_origin(page.url, {f"https://{cas_host}"})
                    and "/cas" in page.url.lower()
                )
                if auto_submit and on_official_cas and not probe.mfa_or_captcha_detected:
                    creds = self._credentials.get_ensai_credentials()
                    if creds is not None:
                        page.fill('input[name="username"], #username', creds.username, timeout=5_000)
                        page.fill('input[name="password"], #password', creds.password, timeout=5_000)
                        page.click(
                            'button[type="submit"], input[type="submit"], [name="submit"]',
                            timeout=5_000,
                        )
                        self._settle_navigation(page, timeout_ms=15_000)

                        post_submit, post_diag = self._inspect_current_page(
                            page,
                            context,
                            source,
                            requested_url=protected_url,
                            profile_dir=profile_dir,
                            phase="post_auto_submit",
                        )
                        if post_submit.auth_error_detected:
                            if not interactive:
                                return AuthStatus(
                                    source=source,
                                    state=AuthState.ERROR,
                                    authenticated=False,
                                    detail=post_submit.detail,
                                    account_hint=self._optional_account_hint(),
                                    requires_interactive_action=True,
                                    next_action=(
                                        "Stored OS keyring credentials were rejected by ENSAI CAS. "
                                        "Replace or delete them via: "
                                        ".venv/bin/python scripts/p1_sources_cli.py auth-ensai --replace-credentials"
                                    ),
                                    metadata=post_submit.to_dict(),
                                )
                            # Interactive fallback: keep visible browser open so the user can log in manually
                            self._emit_interactive_notice(
                                f"{post_diag.format_safe_summary(source)} "
                                "(stored credentials rejected; falling back to manual login in Chromium)",
                                interactive=True,
                            )
                        elif post_submit.mfa_or_captcha_detected and not interactive:
                            return AuthStatus(
                                source=source,
                                state=AuthState.NEEDS_LOGIN,
                                authenticated=False,
                                detail=post_submit.detail,
                                account_hint=self._optional_account_hint(),
                                requires_interactive_action=True,
                                next_action=(
                                    "MFA/CAPTCHA challenge required by ENSAI CAS; complete interactive login via: "
                                    f".venv/bin/python scripts/onboarding.py --source {source.value} --reauthorize"
                                ),
                                metadata=post_submit.to_dict(),
                            )
                        elif post_submit.failure_reason == "cas_login_succeeded_without_redirect":
                            # Headless or interactive CAS success without automatic redirect -> follow protected_url
                            probe = self._probe_on_page(
                                page,
                                source,
                                profile_dir=profile_dir,
                                allow_myensai_fallback=False,
                                target_url=protected_url,
                                context=context,
                                phase="post_auto_submit_redirect",
                            )
                        elif post_submit.authenticated:
                            probe = post_submit
                        else:
                            probe = post_submit

                # Wait for human interactive authentication & redirect settlement without aborting ticket exchange
                final_probe: EnsaiSessionProbe | None = None
                final_diag: EnsaiAuthDiagnostics | None = None
                if interactive and not probe.authenticated:
                    use_polling_loop = (
                        self._context_factory is None
                        or confirm_fn is not None
                        or bool(getattr(page, "_supports_polling", False))
                    )
                    if use_polling_loop:
                        final_probe, final_diag = self._wait_for_interactive_login(
                            page,
                            context,
                            source,
                            protected_url=protected_url,
                            profile_dir=profile_dir,
                            wait_timeout_ms=wait_timeout_ms,
                            confirm_fn=confirm_fn,
                        )
                    else:
                        # Compatible path for deterministic fake pages implementing wait_for_url()
                        def _settled_predicate(u: str) -> bool:
                            return self._is_settled_service_url(str(u), target_host)

                        if not _settled_predicate(page.url):
                            try:
                                page.wait_for_url(_settled_predicate, timeout=wait_timeout_ms)
                            except Exception:
                                pass
                        self._settle_navigation(
                            page, timeout_ms=min(10_000, self._config.ensai_timeout_ms)
                        )
                        # Inspect settled page BEFORE deciding whether a protected_url goto is needed
                        inspected, inspected_diag = self._inspect_current_page(
                            page,
                            context,
                            source,
                            requested_url=protected_url,
                            profile_dir=profile_dir,
                            phase="verifying_protected_resource",
                        )
                        cur_path_norm = urlparse(str(getattr(page, "url", "") or "")).path.lower().rstrip("/") or "/"
                        prot_path_norm = urlparse(protected_url).path.lower().rstrip("/") or "/"
                        if inspected.authenticated and cur_path_norm == prot_path_norm:
                            final_probe, final_diag = inspected, inspected_diag
                        elif inspected.failure_reason == "cas_login_succeeded_without_redirect" or inspected.authenticated:
                            final_probe = self._probe_on_page(
                                page,
                                source,
                                profile_dir=profile_dir,
                                allow_myensai_fallback=False,
                                target_url=protected_url,
                                context=context,
                                phase="verifying_protected_resource",
                            )

                if final_probe is None:
                    # Non-interactive or post-wait final protected-resource verification
                    if probe.authenticated:
                        cur_path_norm = urlparse(str(getattr(page, "url", "") or "")).path.lower().rstrip("/") or "/"
                        prot_path_norm = urlparse(protected_url).path.lower().rstrip("/") or "/"
                        if cur_path_norm == prot_path_norm:
                            final_probe = probe
                        else:
                            final_probe = self._probe_on_page(
                                page,
                                source,
                                profile_dir=profile_dir,
                                allow_myensai_fallback=False,
                                target_url=protected_url,
                                context=context,
                                phase="verifying_protected_resource",
                            )
                    else:
                        final_probe = self._probe_on_page(
                            page,
                            source,
                            profile_dir=profile_dir,
                            allow_myensai_fallback=False,
                            target_url=protected_url,
                            context=context,
                            phase="verifying_protected_resource",
                        )

                cookies_persisted = False
                if final_probe.authenticated:
                    if final_diag is None:
                        persisted, cookies_ok = self._inspect_session_persistence(
                            context, profile_dir, target_host
                        )
                        final_diag = self._build_diagnostics(
                            source=source,
                            phase="completed",
                            current_url=str(getattr(page, "url", "") or final_probe.final_url),
                            target_host=target_host,
                            protected_url=protected_url,
                            probe=final_probe,
                            session_persisted=persisted,
                            service_cookies_present=cookies_ok,
                        )
                    final_probe, final_diag, cookies_persisted = _confirm_keep_open_and_persist(
                        context, page, final_probe, final_diag
                    )
                elif final_diag is None:
                    persisted, cookies_ok = self._inspect_session_persistence(
                        context, profile_dir, target_host
                    )
                    final_diag = self._build_diagnostics(
                        source=source,
                        phase="failed",
                        current_url=str(getattr(page, "url", "") or final_probe.final_url),
                        target_host=target_host,
                        protected_url=protected_url,
                        probe=final_probe,
                        session_persisted=persisted,
                        service_cookies_present=cookies_ok,
                    )
                    final_probe = EnsaiSessionProbe(
                        **{**asdict(final_probe), "diagnostics": final_diag.to_dict()}
                    )

                self._emit_interactive_notice(
                    final_diag.format_safe_summary(source),
                    interactive=interactive,
                )

                meta = final_probe.to_dict()
                meta["session_cookies_persisted"] = cookies_persisted
                return AuthStatus(
                    source=source,
                    state=final_probe.state,
                    authenticated=final_probe.authenticated,
                    detail=final_probe.detail,
                    account_hint=self._optional_account_hint(),
                    requires_interactive_action=not final_probe.authenticated,
                    next_action=(
                        None
                        if final_probe.authenticated
                        else "Complete login in the official ENSAI CAS browser window."
                    ),
                    metadata=meta,
                )
        except BrowserProfileLockError:
            raise
        except Exception as exc:
            if type(exc).__name__ == "ProfileBusy":
                raise
            return AuthStatus(
                source=source,
                state=AuthState.ERROR,
                authenticated=False,
                detail=redact_sensitive_text(f"ENSAI authentication error: {exc}"),
                requires_interactive_action=True,
                metadata={"profile_dir": str(profile_dir)},
            )

    def verify_session_lifecycle(
        self,
        source: SourceKind,
        *,
        interactive: bool = True,
        keep_open_until_confirmed: bool = True,
        allow_automated_submit: bool | None = None,
        wait_timeout_ms: int = 180_000,
        confirm_fn: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        """End-to-end 5-stage authentication & persistence verification for debugging and live validation.

        Explicitly distinguishes:
        - LOGIN_COMPLETED
        - PROTECTED_RESOURCE_VERIFIED
        - SESSION_PERSISTED
        - SESSION_REOPENED
        - SESSION_REUSE_VERIFIED

        Never reports `connected=True` unless ALL 5 stages succeed on the reopened persistent profile.
        """
        stages: dict[str, bool] = {
            "LOGIN_COMPLETED": False,
            "PROTECTED_RESOURCE_VERIFIED": False,
            "SESSION_PERSISTED": False,
            "SESSION_REOPENED": False,
            "SESSION_REUSE_VERIFIED": False,
        }
        profile_dir = self.profile_dir_for(source)

        # Stage 1-3: Authenticate in interactive window, verify protected resource before closing,
        # optionally keep Chromium open until operator confirms, then persist & close cleanly.
        initial = self.authenticate(
            source,
            interactive=interactive,
            allow_automated_submit=allow_automated_submit,
            wait_timeout_ms=wait_timeout_ms,
            confirm_fn=confirm_fn,
            keep_open_until_confirmed=keep_open_until_confirmed,
        )
        init_meta = initial.metadata or {}
        init_diag = init_meta.get("diagnostics") or {}

        if initial.authenticated:
            stages["LOGIN_COMPLETED"] = True
            stages["PROTECTED_RESOURCE_VERIFIED"] = True
            stages["SESSION_PERSISTED"] = bool(
                init_meta.get("session_cookies_persisted")
                or init_diag.get("session_persisted")
            )
            self._emit_interactive_notice(
                f"[ensora-auth] stage=LOGIN_COMPLETED,PROTECTED_RESOURCE_VERIFIED,"
                f"SESSION_PERSISTED={stages['SESSION_PERSISTED']} source={source.value}",
                interactive=interactive,
            )
        else:
            self._emit_interactive_notice(
                f"[ensora-auth] stage=FAILED_BEFORE_PERSIST source={source.value} "
                f"reason={init_diag.get('failure_reason') or initial.state.value}",
                interactive=interactive,
            )
            return {
                "source": source.value,
                "connected": False,
                "stages": stages,
                "profile_dir": str(profile_dir),
                "failure_reason": init_diag.get("failure_reason") or initial.state.value,
                "initial_auth": initial.to_dict(),
                "reopen_verification": None,
            }

        # Stage 4-5: Reopen the EXACT SAME persistent Chromium profile headlessly (without auto-reauth!)
        # to prove session cookies genuinely survive context closure and verify the same protected resource.
        reopened = self.check_source_connection(
            source,
            headless=True,
            allow_auto_reauth=False,
        )
        reopen_meta = reopened.metadata or {}
        reopen_diag = reopen_meta.get("diagnostics") or {}
        stages["SESSION_REOPENED"] = reopened.state != AuthState.ERROR and bool(
            reopen_diag.get("navigation_state") != "no_profile"
        )
        stages["SESSION_REUSE_VERIFIED"] = bool(reopened.authenticated)

        self._emit_interactive_notice(
            f"[ensora-auth] stage=SESSION_REOPENED={stages['SESSION_REOPENED']},"
            f"SESSION_REUSE_VERIFIED={stages['SESSION_REUSE_VERIFIED']} source={source.value} "
            f"host={reopen_diag.get('current_host', '')} path={reopen_diag.get('current_path', '')} "
            f"cookies={reopen_diag.get('service_cookies_present', False)}"
            + (
                f" reason={reopen_diag.get('failure_reason')}"
                if not reopened.authenticated and reopen_diag.get("failure_reason")
                else ""
            ),
            interactive=interactive,
        )

        all_ok = all(stages.values())
        return {
            "source": source.value,
            "connected": all_ok,
            "stages": stages,
            "profile_dir": str(profile_dir),
            "failure_reason": None if all_ok else (reopen_diag.get("failure_reason") or reopened.state.value),
            "initial_auth": initial.to_dict(),
            "reopen_verification": reopened.to_dict(),
        }

    def validate_shared_session(self, *, headless: bool = True) -> dict[SourceKind, AuthStatus]:
        """Validate whether Moodle and Pamplemousse share the same authenticated session.

        If one succeeds on the shared profile while the other rejects it, marks
        shared_session_supported=False so future calls transparently isolate per-source
        browser profiles behind the same unified EnsaiAuthAdapter interface.
        """
        moodle_status = self.check_source_connection(SourceKind.MOODLE, headless=headless)
        pamp_status = self.check_source_connection(SourceKind.PAMPLEMOUSSE, headless=headless)

        if moodle_status.authenticated and pamp_status.authenticated:
            self._shared_session_supported = True
        elif moodle_status.authenticated != pamp_status.authenticated:
            self._shared_session_supported = False
            logger.info(
                "Shared ENSAI session authenticated only one service; enabling per-source session profiles."
            )

        return {
            SourceKind.MOODLE: moodle_status,
            SourceKind.PAMPLEMOUSSE: pamp_status,
        }

    def ensure_authenticated(
        self,
        source: SourceKind,
        *,
        interactive: bool = False,
        target_url: str | None = None,
    ) -> AuthStatus:
        """Check current protected-resource session and trigger bounded reauthentication if allowed."""
        current = self.check_source_connection(source, headless=True)
        if current.authenticated:
            return current
        if not interactive and not self._config.ensai_allow_automated_credential_submit:
            return current
        return self.authenticate(source, interactive=interactive, target_url=target_url)

    def fetch_protected_page(
        self,
        source: SourceKind,
        url: str,
        *,
        interactive: bool = False,
        max_reauth_attempts: int = 1,
    ) -> tuple[str, str]:
        """Fetch an authenticated protected HTML page with bounded CAS reauthentication & resume.

        - Attempts headless access first (reusing valid session or silent CAS SSO).
        - If redirected to CAS / expired:
          - In interactive mode (or when ENSAI_AUTO_LOGIN is enabled), executes at most
            `max_reauth_attempts` bounded reauthentication flow(s) preserving the exact
            service redirect URL, persists the session, and resumes fetching `url`.
          - In non-interactive mode without auto-login, never opens a visible browser window
            and raises AuthenticationRequiredError with clear actionable guidance.
        """
        from ..connectors.base import AuthenticationRequiredError, PermissionDeniedError, is_same_origin

        if not is_same_origin(url, self.service_url_for(source)):
            raise PermissionDeniedError(
                f"Refused to navigate outside {source.value} origin: {sanitize_url_for_logs(url)}"
            )

        reauth_used = 0
        target_host = urlparse(self.service_url_for(source)).netloc.lower()
        while True:
            profile_dir = self.profile_dir_for(source)
            with self.open_context(source, headless=True) as context:
                page = context.pages[0] if context.pages else context.new_page()
                probe = self._probe_on_page(
                    page,
                    source,
                    profile_dir=profile_dir,
                    allow_myensai_fallback=False,
                    target_url=url,
                    context=context,
                    phase="fetch_protected_page",
                )
                if probe.authenticated:
                    self._save_session_cookies(context, profile_dir)
                    return self._collect_page_html(page, target_host), page.url
                if not probe.origin_verified:
                    raise PermissionDeniedError(probe.detail)
                last_detail = probe.detail

            can_reauth = reauth_used < max_reauth_attempts and (
                interactive
                or (
                    self._config.ensai_allow_automated_credential_submit
                    and self._credentials.has_ensai_credentials()
                )
            )
            if not can_reauth:
                raise AuthenticationRequiredError(
                    f"{last_detail} Run interactive authentication: "
                    f".venv/bin/python scripts/onboarding.py or "
                    f".venv/bin/python scripts/p1_sources_cli.py auth-ensai --source {source.value}"
                )

            reauth_used += 1
            auth_status = self.authenticate(source, interactive=interactive, target_url=url)
            if not auth_status.authenticated:
                raise AuthenticationRequiredError(
                    auth_status.detail
                    or f"Reauthentication required for {source.value} ({auth_status.state.value})."
                )

    def disconnect(
        self,
        source: SourceKind | None = None,
        *,
        clear_keyring: bool = False,
        clear_shared_profile: bool = False,
    ) -> AuthStatus:
        """Withdraw source session safely without wiping a shared profile unless explicitly confirmed.

        When Moodle and Pamplemousse share `~/.config/ensora-plus/sessions/ensai/`,
        disconnecting a single source preserves the shared browser directory unless
        `clear_shared_profile=True` (or `source is None`) is explicitly requested.
        """
        import shutil

        if source is None:
            targets = [self._config.ensai_profile_dir]
            report_source = SourceKind.MOODLE
            should_wipe = True
        else:
            report_source = source
            target = self.profile_dir_for(source)
            is_shared = target.resolve() == self._config.ensai_profile_dir.resolve()
            should_wipe = (not is_shared) or clear_shared_profile
            targets = [target] if should_wipe else []

        for target in targets:
            if target.exists():
                with self._acquire_profile_lease(target):
                    for child in target.iterdir():
                        if child.name in (BrowserProfileLock.LOCK_FILENAME, ".ensora_profile.lock"):
                            continue
                        if child.is_dir():
                            shutil.rmtree(child, ignore_errors=True)
                        else:
                            child.unlink(missing_ok=True)
        if clear_keyring:
            self._credentials.clear_ensai_credentials()
        return AuthStatus(
            source=report_source,
            state=AuthState.REVOKED,
            authenticated=False,
            detail=(
                "Cleared local ENSAI browser session."
                if should_wipe
                else "Withdrew source-level session state; preserved shared ENSAI browser profile for other active sources."
            ),
            metadata={"shared_profile_cleared": should_wipe},
        )

    def clear_shared_session(self, *, confirmed: bool = False, clear_keyring: bool = False) -> AuthStatus:
        """Explicitly confirmed operation to wipe the shared institutional browser profile."""
        if not confirmed:
            raise PermissionError(
                "Clearing the shared ENSAI browser profile requires explicit confirmation (confirmed=True)."
            )
        return self.disconnect(source=None, clear_keyring=clear_keyring, clear_shared_profile=True)

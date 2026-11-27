"""Unit tests for config, credentials, ENSAI Playwright auth, Google Cloud setup, and Google OAuth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.modules.ingestion.auth.ensai import (
    BrowserProfileLock,
    BrowserProfileLockError,
    EnsaiAuthAdapter,
    sanitize_url_for_logs,
)
from backend.app.modules.ingestion.auth.google_cloud import (
    GOOGLE_CALENDAR_API,
    GOOGLE_DRIVE_API,
    CommandResult,
    GoogleCloudSetupHelper,
    validate_google_credentials_file,
)
from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
from backend.app.modules.ingestion.auth.onboarding import OnboardingOrchestrator
from backend.app.modules.ingestion.config import (
    DEFAULT_GOOGLE_CLOUD_PROJECT_ID,
    GOOGLE_SCOPE_CALENDAR_READONLY,
    GOOGLE_SCOPE_DRIVE_READONLY,
    SourcesRuntimeConfig,
)
from backend.app.modules.ingestion.contracts import AuthState, SourceKind
from backend.app.modules.ingestion.credentials import (
    InMemoryCredentialStore,
    SecureCredentialManager,
    redact_sensitive_text,
    validate_official_origin,
)


class _FakePage:
    def __init__(self, responses: dict[str, tuple[str, str, str]]) -> None:
        self._responses = responses
        self.url = ""
        self._html = ""
        self._title = ""
        self.visited: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.clicked: list[str] = []

    def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 30_000) -> None:
        self.visited.append(url)
        final_url, html, title = self._responses.get(url, (url, "<html></html>", ""))
        self.url = final_url
        self._html = html
        self._title = title

    def content(self) -> str:
        return self._html

    def title(self) -> str:
        return self._title

    def fill(self, selector: str, value: str, *, timeout: int = 5_000) -> None:
        self.filled.append((selector, value))

    def click(self, selector: str, *, timeout: int = 5_000) -> None:
        self.clicked.append(selector)
        if "__after_submit__" in self._responses:
            self.url, self._html, self._title = self._responses["__after_submit__"]

    def wait_for_load_state(self, state: str = "networkidle", *, timeout: int = 15_000) -> None:
        return None

    def wait_for_url(self, predicate: Any, *, timeout: int = 120_000) -> None:
        if "__after_interactive__" in self._responses:
            self.url, self._html, self._title = self._responses["__after_interactive__"]


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.pages = [page]
        self.closed = False

    def new_page(self) -> _FakePage:
        return self.pages[0]

    def close(self) -> None:
        self.closed = True


class _FakeGoogleCreds:
    def __init__(
        self,
        *,
        valid: bool = True,
        expired: bool = False,
        refresh_token: str | None = "fake-refresh-token",
        scopes: tuple[str, ...] = (GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY),
        refresh_error: Exception | None = None,
    ) -> None:
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.token = "fake-access-token"
        self.scopes = list(scopes)
        self.granted_scopes = list(scopes)
        self._refresh_error = refresh_error
        self.refresh_calls = 0

    def refresh(self, request: Any) -> None:
        self.refresh_calls += 1
        if self._refresh_error is not None:
            raise self._refresh_error
        self.valid = True
        self.expired = False
        self.token = "refreshed-access-token"

    def to_json(self) -> str:
        return json.dumps(
            {
                "token": self.token,
                "refresh_token": self.refresh_token,
                "scopes": self.scopes,
            }
        )


def test_sources_env_preserves_existing_values_and_adds_missing(tmp_path: Path) -> None:
    env_file = tmp_path / "sources.env"
    env_file.write_text(
        "MOODLE_BASE_URL=https://foad-moodle.ensai.fr/\nENSAI_USERNAME=student_custom\n",
        encoding="utf-8",
    )
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    assert cfg.ensai_username_hint == "student_custom"

    added = cfg.ensure_missing_defaults_in_env_file()
    assert "MOODLE_BASE_URL" not in added
    assert "ENSORA_DRIVE_ROOT_FOLDER_ID" in added

    content = env_file.read_text(encoding="utf-8")
    assert "ENSAI_USERNAME=student_custom" in content
    assert "1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO" in content


def test_origin_validation_and_secret_redaction() -> None:
    assert validate_official_origin("https://ssocas.ensai.fr/cas/login?service=https://foad-moodle.ensai.fr/")
    assert not validate_official_origin("http://ssocas.ensai.fr/cas/login")
    assert not validate_official_origin("https://evil-phishing.example/cas/login")

    ticket_url = "https://foad-moodle.ensai.fr/?ticket=ST-123456-SECRET-CAS-TICKET&course=42"
    sanitized = sanitize_url_for_logs(ticket_url)
    assert "ST-123456-SECRET-CAS-TICKET" not in sanitized
    assert "[REDACTED]" in sanitized

    redacted = redact_sensitive_text("Authorization: Bearer abc.def.ghi wstoken=xyz123 password=mysecret")
    assert "abc.def.ghi" not in redacted
    assert "xyz123" not in redacted
    assert "mysecret" not in redacted


def test_masked_credential_prompt_and_keyring_store() -> None:
    store = InMemoryCredentialStore()
    mgr = SecureCredentialManager(
        store=store,
        input_fn=lambda _: "etudiant_test",
        secret_input_fn=lambda _: "secret_pass_value",
    )
    assert not mgr.has_ensai_credentials()
    creds = mgr.prompt_missing_ensai_credentials(persist=True)
    assert creds.username == "etudiant_test"
    assert mgr.has_ensai_credentials()
    assert "secret_pass_value" not in repr(creds)
    mgr.clear_ensai_credentials()
    assert not mgr.has_ensai_credentials()


def test_ensai_cas_redirect_auth_status_and_unverified_origin_blocked(tmp_path: Path) -> None:
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    adapter = EnsaiAuthAdapter(cfg, SecureCredentialManager(InMemoryCredentialStore()))

    # 1. CAS redirect detected cleanly
    probe_cas = adapter.classify_page_state(
        SourceKind.MOODLE,
        requested_url=cfg.moodle_base_url,
        current_url="https://ssocas.ensai.fr/cas/login?service=https://foad-moodle.ensai.fr/",
        html_content='<form id="fm1"><input name="username"><input name="password"></form>',
        page_title="ENSAI CAS",
    )
    assert probe_cas.state == AuthState.NEEDS_LOGIN
    assert probe_cas.cas_redirect_detected is True
    assert probe_cas.origin_verified is True

    # 2. Unverified phishing redirect blocked
    probe_evil = adapter.classify_page_state(
        SourceKind.MOODLE,
        requested_url=cfg.moodle_base_url,
        current_url="https://fake-cas.attacker.invalid/cas/login",
        html_content='<input name="password">',
    )
    assert probe_evil.state == AuthState.ERROR
    assert probe_evil.origin_verified is False

    # 3. MFA / CAPTCHA detected and never bypassed
    probe_mfa = adapter.classify_page_state(
        SourceKind.MOODLE,
        requested_url=cfg.moodle_base_url,
        current_url="https://ssocas.ensai.fr/cas/login",
        html_content='<div class="g-recaptcha"></div><p>Code de vérification 2FA</p>',
    )
    assert probe_mfa.mfa_or_captcha_detected is True
    assert probe_mfa.authenticated is False

    # 4. Expired session on Moodle login page
    probe_expired = adapter.classify_page_state(
        SourceKind.MOODLE,
        requested_url=cfg.moodle_base_url,
        current_url="https://foad-moodle.ensai.fr/login/index.php",
        html_content="<p>Vous n'êtes pas connecté</p>",
    )
    assert probe_expired.state == AuthState.EXPIRED
    assert probe_expired.authenticated is False

    # 5. Authenticated Moodle session
    probe_ok = adapter.classify_page_state(
        SourceKind.MOODLE,
        requested_url=cfg.moodle_base_url,
        current_url="https://foad-moodle.ensai.fr/my/",
        html_content='<div data-region="usermenu"><a href="/login/logout.php">Logout</a></div>',
    )
    assert probe_ok.state == AuthState.AUTHENTICATED
    assert probe_ok.authenticated is True


def test_ensai_shared_session_fallback_and_profile_lock(tmp_path: Path) -> None:
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    responses = {
        "https://foad-moodle.ensai.fr/my/courses.php": (
            "https://foad-moodle.ensai.fr/my/courses.php",
            '<div data-region="usermenu"><a href="/login/logout.php">Logout</a></div>',
            "Moodle",
        ),
        cfg.pamplemousse_base_url: (
            "https://ssocas.ensai.fr/cas/login?service=pamplemousse",
            '<form><input type="password"></form>',
            "CAS",
        ),
    }
    contexts: list[_FakeContext] = []

    def factory(profile_dir: Path, headless: bool) -> _FakeContext:
        ctx = _FakeContext(_FakePage(responses))
        contexts.append(ctx)
        return ctx

    adapter = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=factory,
    )
    statuses = adapter.validate_shared_session(headless=True)
    assert statuses[SourceKind.MOODLE].authenticated is True
    assert statuses[SourceKind.PAMPLEMOUSSE].authenticated is False
    # Automatically switches to isolated per-source profiles when shared session fails
    assert adapter.shared_session_supported is False
    assert adapter.profile_dir_for(SourceKind.PAMPLEMOUSSE).name == "pamplemousse"
    assert all(c.closed for c in contexts)

    # Profile lock prevents concurrent acquisition
    lock1 = BrowserProfileLock(cfg.ensai_profile_dir, timeout_sec=0.1)
    lock2 = BrowserProfileLock(cfg.ensai_profile_dir, timeout_sec=0.1)
    with lock1:
        with pytest.raises(BrowserProfileLockError):
            lock2.acquire()


def test_google_cloud_setup_helper_and_approval_gate(tmp_path: Path) -> None:
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    creds_file = cfg.google_credentials_path
    creds_file.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "1234567890-abcdef.apps.googleusercontent.com",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
        encoding="utf-8",
    )
    val = validate_google_credentials_file(creds_file)
    assert val.valid is True
    assert val.client_type == "installed"

    enabled_calls: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> CommandResult:
        if cmd[:3] == ["gcloud", "auth", "list"]:
            return CommandResult(0, json.dumps([{"account": "dev@ensai.fr", "status": "ACTIVE"}]), "")
        if cmd[:3] == ["gcloud", "projects", "describe"]:
            return CommandResult(0, json.dumps({"projectId": DEFAULT_GOOGLE_CLOUD_PROJECT_ID}), "")
        if cmd[:3] == ["gcloud", "services", "list"]:
            return CommandResult(0, json.dumps([{"config": {"name": GOOGLE_DRIVE_API}}]), "")
        if cmd[:3] == ["gcloud", "services", "enable"]:
            enabled_calls.append(cmd)
            return CommandResult(0, "Operation finished successfully.", "")
        return CommandResult(1, "", "unknown command")

    helper = GoogleCloudSetupHelper(
        cfg,
        command_runner=fake_runner,
        which_fn=lambda name: "/usr/bin/gcloud" if name == "gcloud" else None,
    )
    report = helper.inspect()
    assert report.project_id == "ensora-516015"
    assert report.project_accessible is True
    assert report.enabled_apis == (GOOGLE_DRIVE_API,)
    assert report.missing_apis == (GOOGLE_CALENDAR_API,)
    assert "ensora-516015" in report.console_links["oauth_credentials"]

    with pytest.raises(PermissionError):
        helper.enable_required_apis(approved=False)

    res = helper.enable_required_apis(approved=True)
    assert res.returncode == 0
    assert enabled_calls and "--project=ensora-516015" in enabled_calls[0]


def test_google_oauth_refresh_expiration_granular_consent_and_revoke(tmp_path: Path) -> None:
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_credentials_path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test-client-id.apps.googleusercontent.com",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
        encoding="utf-8",
    )

    # Disallowed write scope is strictly rejected
    with pytest.raises(ValueError):
        GoogleOAuthAdapter(cfg, scopes=["https://www.googleapis.com/auth/drive"])

    # 1. Expired access token refreshes automatically and persists
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    expired_creds = _FakeGoogleCreds(valid=False, expired=True)
    oauth = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.ENSORA_DRIVE,
        credentials_loader=lambda p, s: expired_creds,
        request_factory=lambda: object(),
    )
    status = oauth.status()
    assert status.authenticated is True
    assert status.state == AuthState.AUTHENTICATED
    assert expired_creds.refresh_calls == 1

    # 2. Refresh token expiration detected cleanly
    broken_creds = _FakeGoogleCreds(
        valid=False, expired=True, refresh_error=RuntimeError("invalid_grant")
    )
    oauth_expired = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.ENSORA_DRIVE,
        credentials_loader=lambda p, s: broken_creds,
        request_factory=lambda: object(),
    )
    st_exp = oauth_expired.status()
    assert st_exp.authenticated is False
    assert st_exp.state == AuthState.EXPIRED

    # 3. Granular consent missing required Drive scope rejected
    partial_creds = _FakeGoogleCreds(scopes=(GOOGLE_SCOPE_CALENDAR_READONLY,))
    oauth_partial = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.ENSORA_DRIVE,
        credentials_loader=lambda p, s: None,
        flow_runner=lambda cp, sc, port, ob: partial_creds,
    )
    st_denied = oauth_partial.authenticate(interactive=True)
    assert st_denied.authenticated is False
    assert st_denied.state == AuthState.REVOKED

    # 4. Revocation calls Google revoke endpoint via POST form body (NOT URL params) and removes local token
    revoked_tokens: list[str] = []

    class _Resp:
        status_code = 200

    def fake_post(url: str, *, data: dict[str, str], headers: dict[str, str], timeout: float):
        revoked_tokens.append(data["token"])
        return _Resp()

    oauth_rev = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.ENSORA_DRIVE,
        credentials_loader=lambda p, s: _FakeGoogleCreds(),
        http_post=fake_post,
    )
    st_rev = oauth_rev.disconnect(revoke_remote=True)
    assert st_rev.state == AuthState.REVOKED
    assert revoked_tokens == ["fake-refresh-token"]
    assert not cfg.google_token_path.exists()


def test_onboarding_orchestrator_detects_completed_steps(tmp_path: Path) -> None:
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.enable_moodle = False
    cfg.enable_pamplemousse = False
    cfg.enable_google_calendar = False
    cfg.ensure_private_directories()
    cfg.google_credentials_path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "1234567890-ok.apps.googleusercontent.com",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
        encoding="utf-8",
    )
    cfg.google_token_path.write_text("{}", encoding="utf-8")

    drive_oauth = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.ENSORA_DRIVE,
        credentials_loader=lambda p, s: _FakeGoogleCreds(),
    )
    gcp_helper = GoogleCloudSetupHelper(cfg, which_fn=lambda _: None)
    orch = OnboardingOrchestrator(cfg, google_cloud=gcp_helper, drive_oauth=drive_oauth)
    # Without explicit consent, run_setup remains strictly local-only (GEMINI-REV-017)
    local_report = orch.run_setup(interactive=False)
    assert local_report.source_statuses["ensora_drive"].metadata.get("local_only") is True

    # With explicit durable consent for ENSORA_DRIVE, orchestrator validates the consented source
    report = orch.run_setup(
        interactive=False,
        consented_sources={SourceKind.ENSORA_DRIVE},
        allow_network=True,
    )
    assert report.all_enabled_ready is True
    assert report.source_statuses["ensora_drive"].authenticated is True
    assert len(local_report.added_env_keys) > 0

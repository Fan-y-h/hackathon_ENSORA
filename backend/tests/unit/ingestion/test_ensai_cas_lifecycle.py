"""Regression tests for live ENSAI CAS/SSO authentication lifecycle & acquisition resumption.

Covers:
- Protected-resource validation (public Moodle front page never false-positives CONNECTED)
- Official CAS `?service=` redirect preservation & Moodle `/login/index.php?authCAS=CAS` chooser
- Non-interactive REAUTH_REQUIRED (never opens unexpected visible browser window)
- Bounded interactive reauthentication & automatic resumption of interrupted Moodle/Pamplemousse sync
- Nested Moodle folder expansion, `/mod/resource/` wrapper unwrapping, Content-Disposition filenames,
  and CAS redirect vs untrusted redirect differentiation during browser attachment downloads
- `ENSAI_AUTO_LOGIN` configuration preservation across `SourcesRuntimeConfig` and Codex `Settings`
- MFA/CAPTCHA non-bypass guarantee during `ENSAI_AUTO_LOGIN`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from backend.app.core.settings import Settings
from backend.app.modules.ingestion.auth.ensai import EnsaiAuthAdapter
from backend.app.modules.ingestion.codex_bridge import runtime_config_from_codex_settings
from backend.app.modules.ingestion.config import SourcesRuntimeConfig
from backend.app.modules.ingestion.connectors.base import (
    AuthenticationRequiredError,
    PermissionDeniedError,
    RateLimiter,
)
from backend.app.modules.ingestion.connectors.moodle import MoodleConnector
from backend.app.modules.ingestion.connectors.pamplemousse import PamplemousseConnector
from backend.app.modules.ingestion.contracts import AuthState, SourceKind
from backend.app.modules.ingestion.credentials import (
    InMemoryCredentialStore,
    SecureCredentialManager,
)


class _StatefulPage:
    """Simulated Playwright Page supporting CAS redirects, login chooser, auto-submit, and interactive wait."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state
        self.url: str = "about:blank"
        self._html: str = ""
        self._title: str = ""
        self.visited: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.clicked: list[str] = []

    def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 30_000) -> None:
        self.visited.append(url)
        handler = self._state["router"]
        self.url, self._html, self._title = handler(url, self._state)

    def content(self) -> str:
        return self._html

    def title(self) -> str:
        return self._title

    def fill(self, selector: str, value: str, *, timeout: int = 5_000) -> None:
        self.filled.append((selector, value))

    def click(self, selector: str, *, timeout: int = 5_000) -> None:
        self.clicked.append(selector)
        on_submit = self._state.get("on_submit")
        if on_submit is not None:
            self.url, self._html, self._title = on_submit(self.url, self._state)

    def wait_for_load_state(self, state: str = "networkidle", *, timeout: int = 15_000) -> None:
        return None

    def wait_for_url(self, predicate: Any, *, timeout: int = 120_000) -> None:
        on_interactive = self._state.get("on_interactive_login")
        if on_interactive is not None:
            self.url, self._html, self._title = on_interactive(self.url, self._state)
            assert predicate(self.url)


class _StatefulContext:
    def __init__(self, state: dict[str, Any], *, api_request: Any = None) -> None:
        self.page = _StatefulPage(state)
        self.pages = [self.page]
        self.request = api_request
        self.closed = False

    def new_page(self) -> _StatefulPage:
        return self.page

    def close(self) -> None:
        self.closed = True


def test_moodle_public_frontpage_never_false_positives_and_validate_uses_protected_resource(
    tmp_path: Path,
) -> None:
    """Public Moodle front page `/` must NEVER be classified AUTHENTICATED; validate probes `/my/courses.php`."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    adapter = EnsaiAuthAdapter(cfg, SecureCredentialManager(InMemoryCredentialStore()))

    # 1. Public Moodle front page containing 'Tableau de bord' / 'coursebox' + '/login/index.php' link (NO logout.php)
    public_front_html = """
    <html><head><title>Moodle ENSAI</title></head><body>
      <div class="logininfo"><a href="https://foad-moodle.ensai.fr/login/index.php">Connexion</a></div>
      <nav>Tableau de bord</nav>
      <div class="coursebox">Catalogue public</div>
    </body></html>
    """
    probe_pub = adapter.classify_page_state(
        SourceKind.MOODLE,
        requested_url="https://foad-moodle.ensai.fr/",
        current_url="https://foad-moodle.ensai.fr/",
        html_content=public_front_html,
        page_title="Moodle ENSAI",
    )
    assert probe_pub.authenticated is False
    assert probe_pub.state == AuthState.EXPIRED

    # 2. check_source_connection(MOODLE) navigates directly to protected `/my/courses.php` -> sees CAS redirect!
    visited_urls: list[str] = []

    def router(url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        visited_urls.append(url)
        if url.endswith("/my/courses.php"):
            return (
                "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2Ffoad-moodle.ensai.fr%2Flogin%2Findex.php%3FauthCAS%3DCAS",
                '<form id="fm1"><input name="username"><input name="password"></form>',
                "ENSAI CAS",
            )
        return (url, public_front_html, "Moodle ENSAI")

    state: dict[str, Any] = {"router": router}
    adapter2 = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=lambda p, h: _StatefulContext(state),
    )
    st = adapter2.check_source_connection(SourceKind.MOODLE, headless=True)
    assert visited_urls == ["https://foad-moodle.ensai.fr/my/courses.php"]
    assert st.authenticated is False
    assert st.state == AuthState.NEEDS_LOGIN


def test_cas_service_redirect_preserved_and_moodle_login_chooser_followed(tmp_path: Path) -> None:
    """Moodle login chooser -> official CAS link preserves `?service=...` -> interactive login -> verifies `/my/courses.php`."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cas_urls_seen: list[str] = []
    headless_flags: list[bool] = []

    def router(url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        if st.get("logged_in") and "foad-moodle.ensai.fr" in url:
            return (
                "https://foad-moodle.ensai.fr/my/courses.php",
                '<div data-region="usermenu"><a href="/login/logout.php?sesskey=abc">Déconnexion</a></div>'
                '<a href="/course/view.php?id=101">Machine Learning</a>',
                "Mes cours",
            )
        if url.endswith("/my/courses.php"):
            # Moodle chooser page offering official CAS login button
            return (
                "https://foad-moodle.ensai.fr/login/index.php",
                '<div class="loginform"><a href="https://foad-moodle.ensai.fr/login/index.php?authCAS=CAS">Connexion ENSAI CAS</a></div>',
                "Connexion",
            )
        if "authCAS=CAS" in url:
            cas_url = "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2Ffoad-moodle.ensai.fr%2Flogin%2Findex.php%3FauthCAS%3DCAS"
            cas_urls_seen.append(cas_url)
            return (
                cas_url,
                '<form id="fm1"><input name="username"><input name="password"></form>',
                "ENSAI CAS",
            )
        return (url, "", "")

    def on_interactive(cur_url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        assert "service=" in cur_url  # Service redirect parameter strictly preserved!
        st["logged_in"] = True
        return (
            "https://foad-moodle.ensai.fr/my/courses.php",
            '<div data-region="usermenu"><a href="/login/logout.php?sesskey=abc">Déconnexion</a></div>',
            "Mes cours",
        )

    state: dict[str, Any] = {"router": router, "on_interactive_login": on_interactive, "logged_in": False}

    def factory(p: Path, headless: bool) -> _StatefulContext:
        headless_flags.append(headless)
        return _StatefulContext(state)

    adapter = EnsaiAuthAdapter(cfg, SecureCredentialManager(InMemoryCredentialStore()), context_factory=factory)
    status = adapter.authenticate(SourceKind.MOODLE, interactive=True)
    assert status.authenticated is True
    assert status.state == AuthState.AUTHENTICATED
    assert headless_flags == [False]  # Interactive login opened visible browser window
    assert len(cas_urls_seen) == 1 and "service=" in cas_urls_seen[0]


def test_session_expiry_bounded_reauth_and_acquisition_resume_moodle_and_pamplemousse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Expired session returns REAUTH_REQUIRED non-interactively, and resumes acquisition after bounded interactive CAS login."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})

    # --- 1. Moodle expired -> non-interactive refuses popup; interactive reauths & resumes (incl. nested folder!) ---
    moodle_headless_calls: list[bool] = []
    moodle_state: dict[str, Any] = {"logged_in": False}

    def moodle_router(url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        if not st["logged_in"]:
            return (
                "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2Ffoad-moodle.ensai.fr%2Fmy%2Fcourses.php",
                '<form><input name="username"><input name="password"></form>',
                "ENSAI CAS",
            )
        if url.endswith("/my/courses.php"):
            return (
                url,
                '<div data-region="usermenu"><a href="/login/logout.php">Logout</a></div>'
                '<a href="/course/view.php?id=42">Nom du cours Econometrie 2A</a>',
                "Mes cours",
            )
        if "/course/view.php?id=42" in url:
            return (
                url,
                '<div data-region="usermenu"><a href="/login/logout.php">Logout</a></div>'
                '<h3 class="sectionname">Semaine 1</h3>'
                '<a href="/mod/folder/view.php?id=88">Supports PDF</a>'
                '<a href="/mod/assign/view.php?id=99" data-deadline="2026-12-15T23:59:00Z">DM1</a>',
                "Econometrie",
            )
        if "/mod/folder/view.php?id=88" in url:
            return (
                url,
                '<div data-region="usermenu"><a href="/login/logout.php">Logout</a></div>'
                '<a href="/pluginfile.php/88/mod_folder/content/0/CM1_Eco.pdf">CM1_Eco.pdf</a>',
                "Supports PDF",
            )
        return (url, "", "")

    def moodle_interactive(cur_url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        st["logged_in"] = True
        return moodle_router("https://foad-moodle.ensai.fr/my/courses.php", st)

    moodle_state["router"] = moodle_router
    moodle_state["on_interactive_login"] = moodle_interactive

    def moodle_factory(p: Path, headless: bool) -> _StatefulContext:
        moodle_headless_calls.append(headless)
        return _StatefulContext(moodle_state)

    moodle_auth = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=moodle_factory,
    )
    moodle_conn = MoodleConnector(cfg, auth_adapter=moodle_auth, rate_limiter=RateLimiter(0.0))

    # 1a. Non-interactive mode NEVER opens visible browser (headless=False) and raises AuthenticationRequiredError
    with pytest.raises(AuthenticationRequiredError):
        moodle_conn.list_changes(scope={"single_course_first": True, "interactive": False})
    assert moodle_headless_calls == [True]

    # 1b. Standalone CLI sync-moodle --non-interactive prints clean REAUTH_REQUIRED JSON and exits 2
    import scripts.p1_sources_cli as cli

    monkeypatch.setattr(cli, "MoodleConnector", lambda c: moodle_conn)
    rc_non = cli.main(["--config-dir", str(tmp_path), "sync-moodle", "--single-course", "--non-interactive", "--yes"])
    assert rc_non == 2
    out_json = json.loads(capsys.readouterr().out)
    assert out_json["status"] == "REAUTH_REQUIRED"
    assert out_json["source"] == "moodle"

    # 1c. Interactive mode performs bounded 1-attempt visible reauth, persists session, and resumes acquisition!
    moodle_headless_calls.clear()
    page = moodle_conn.list_changes(scope={"single_course_first": True, "interactive": True})
    assert False in moodle_headless_calls  # Visible browser opened for interactive CAS login
    titles = {r.title for r in page.items}
    assert "CM1_Eco.pdf" in titles  # Nested /mod/folder/ PDF discovered after resuming!
    assert "DM1" in titles
    assert page.items[0].course_title == "Econometrie 2A"  # Screen-reader prefix stripped!

    # --- 2. Pamplemousse expired -> bounded interactive reauth -> resumes & follows subpage ---
    pamp_state: dict[str, Any] = {"logged_in": False}

    def pamp_router(url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        if not st["logged_in"]:
            return (
                "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2F23ed753f.leos.fr%2Findex.php",
                '<form><input name="username"><input name="password"></form>',
                "ENSAI CAS",
            )
        if url.endswith("index.php"):
            return (
                url,
                '<html><body><a href="/logout.php">Déconnexion</a>'
                '<a href="/planning.ics">Export EDT ICS</a>'
                '<a href="/notes.php">Mes notes</a></body></html>',
                "Pamplemousse",
            )
        if url.endswith("planning.ics"):
            return (
                url,
                '<!-- Déconnexion -->BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:p1\r\nSUMMARY:Sout.ID PPDP\r\n'
                'DTSTART:20261207T083000Z\r\nDTEND:20261207T103000Z\r\nLOCATION:Amphi 1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n',
                "EDT",
            )
        if url.endswith("notes.php"):
            return (
                url,
                '<html><body><a href="/logout.php">Déconnexion</a>'
                '<table><tr><th>Matière</th><th>Note</th><th>Coef</th></tr>'
                '<tr><td>Machine Learning</td><td>16,5</td><td></td></tr></table></body></html>',
                "Notes",
            )
        return (url, "", "")

    def pamp_interactive(cur_url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        st["logged_in"] = True
        return pamp_router("https://23ed753f.leos.fr/index.php", st)

    pamp_state["router"] = pamp_router
    pamp_state["on_interactive_login"] = pamp_interactive

    pamp_auth = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=lambda p, h: _StatefulContext(pamp_state),
    )
    pamp_conn = PamplemousseConnector(cfg, auth_adapter=pamp_auth, rate_limiter=RateLimiter(0.0))

    pamp_page = pamp_conn.list_changes(scope={"interactive": True})
    ev_recs = [r for r in pamp_page.items if r.record_kind == "event"]
    ac_recs = [r for r in pamp_page.items if r.record_kind == "academic"]
    assert len(ev_recs) == 1 and ev_recs[0].payload["confirmation_required"] is True
    assert len(ac_recs) == 1 and ac_recs[0].payload["grade_value"] == "16.5"
    assert ac_recs[0].payload["weight_status"] == "missing"  # Never invents missing coef!


def test_ensai_auto_login_config_mfa_guard_and_moodle_blob_cas_resume(tmp_path: Path) -> None:
    """ENSAI_AUTO_LOGIN is preserved; MFA blocks auto-submit; Moodle fetch_blob distinguishes CAS vs evil redirects & unwraps wrappers."""
    # 1. ENSAI_AUTO_LOGIN honored in both SourcesRuntimeConfig and runtime_config_from_codex_settings
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "sources.env").write_text("ENSAI_AUTO_LOGIN=true\n", encoding="utf-8")

    rcfg = SourcesRuntimeConfig.load(config_dir=cfg_dir, environ={})
    assert rcfg.ensai_allow_automated_credential_submit is True

    codex_settings = Settings.load(cfg_dir / "sources.env")
    bridged_cfg = runtime_config_from_codex_settings(codex_settings)
    assert bridged_cfg.ensai_allow_automated_credential_submit is True

    # 2. MFA/CAPTCHA detected on CAS prevents automated credential fill even when ENSAI_AUTO_LOGIN=true
    creds = SecureCredentialManager(InMemoryCredentialStore())
    creds.store_ensai_credentials("student_u", "student_p")
    mfa_pages: list[_StatefulPage] = []

    def mfa_router(url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        return (
            "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2Ffoad-moodle.ensai.fr%2Fmy%2Fcourses.php",
            '<form><input name="username"><input name="password"><div class="g-recaptcha">2FA</div></form>',
            "CAS MFA",
        )

    mfa_state: dict[str, Any] = {"router": mfa_router}

    def mfa_factory(p: Path, h: bool) -> _StatefulContext:
        ctx = _StatefulContext(mfa_state)
        mfa_pages.append(ctx.page)
        return ctx

    mfa_auth = EnsaiAuthAdapter(rcfg, creds, context_factory=mfa_factory)
    st_mfa = mfa_auth.authenticate(SourceKind.MOODLE, interactive=False, allow_automated_submit=True)
    assert st_mfa.authenticated is False
    assert mfa_pages[0].filled == []  # Never submits credentials when MFA/CAPTCHA is present!

    # 3. Moodle browser fetch_blob: CAS redirect -> reauth & resume -> unwrap /mod/resource/ -> Content-Disposition filename;
    #    evil cross-origin redirect -> PermissionDeniedError!
    dl_state: dict[str, Any] = {"logged_in": False}

    class _FakeAPIResp:
        def __init__(self, status: int, headers: dict[str, str], body: bytes = b"", url: str = "") -> None:
            self.status = status
            self.headers = headers
            self._body = body
            self.url = url

        def body(self) -> bytes:
            return self._body

    class _FakeAPIReq:
        def get(self, url: str, *, timeout: int = 30_000, max_redirects: int = 0) -> _FakeAPIResp:
            if "evil_link" in url:
                return _FakeAPIResp(302, {"Location": "https://evil.example/payload.pdf"}, url=url)
            if not dl_state["logged_in"]:
                return _FakeAPIResp(
                    302,
                    {"Location": "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2Ffoad-moodle.ensai.fr%2Fmod%2Fresource%2Fview.php%3Fid%3D10"},
                    url=url,
                )
            if "/mod/resource/view.php" in url:
                return _FakeAPIResp(
                    200,
                    {"Content-Type": "text/html; charset=utf-8"},
                    b'<a href="https://foad-moodle.ensai.fr/pluginfile.php/10/mod_resource/content/1/slides.pdf">Open</a>',
                    url=url,
                )
            return _FakeAPIResp(
                200,
                {
                    "Content-Type": "application/pdf",
                    "Content-Disposition": 'attachment; filename="CM1_Slides.pdf"',
                },
                b"%PDF-1.4\n%%EOF",
                url=url,
            )

    def dl_router(url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        if st["logged_in"]:
            return (
                url,
                '<div data-region="usermenu"><a href="/login/logout.php">Logout</a></div>',
                "Moodle",
            )
        return (
            "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2Ffoad-moodle.ensai.fr%2Fmod%2Fresource%2Fview.php%3Fid%3D10",
            '<form><input name="username"><input name="password"></form>',
            "CAS",
        )

    def dl_interactive(cur_url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        st["logged_in"] = True
        return (
            "https://foad-moodle.ensai.fr/mod/resource/view.php?id=10",
            '<div data-region="usermenu"><a href="/login/logout.php">Logout</a></div>',
            "Moodle",
        )

    dl_state["router"] = dl_router
    dl_state["on_interactive_login"] = dl_interactive

    dl_cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "dl_cfg", environ={})
    dl_auth = EnsaiAuthAdapter(
        dl_cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=lambda p, h: _StatefulContext(dl_state, api_request=_FakeAPIReq()),
    )
    dl_conn = MoodleConnector(dl_cfg, auth_adapter=dl_auth, rate_limiter=RateLimiter(0.0))

    res_rec = dl_conn.normalize(
        {
            "course_id": "10",
            "module_id": "10",
            "title": "Slides",
            "url": "https://foad-moodle.ensai.fr/mod/resource/view.php?id=10",
            "download_url": "https://foad-moodle.ensai.fr/mod/resource/view.php?id=10",
        }
    )

    # Non-interactive download raises AuthenticationRequiredError (NOT PermissionDeniedError!) on CAS redirect
    with pytest.raises(AuthenticationRequiredError):
        dl_conn.fetch_blob(res_rec, interactive=False)

    # Interactive download reauthenticates via CAS, resumes, unwraps /mod/resource/ -> /pluginfile.php/, and extracts Content-Disposition filename!
    blob = dl_conn.fetch_blob(res_rec, interactive=True)
    assert blob.content == b"%PDF-1.4\n%%EOF"
    assert blob.filename == "CM1_Slides.pdf"
    assert blob.mime_type == "application/pdf"

    # Untrusted cross-origin redirect is STILL rejected with PermissionDeniedError!
    evil_rec = dl_conn.normalize(
        {
            "course_id": "10",
            "module_id": "99",
            "title": "Evil",
            "url": "https://foad-moodle.ensai.fr/evil_link",
            "download_url": "https://foad-moodle.ensai.fr/evil_link",
        }
    )
    with pytest.raises(PermissionDeniedError):
        dl_conn.fetch_blob(evil_rec, interactive=True)


def test_bundled_bridge_reports_verified_service_access_and_safe_cas_exceptions(tmp_path: Path) -> None:
    """Bundled Runtime bridge emits service_access='verified' only after protected probe, and never auto-reauths inside Runtime connector."""
    from backend.app.contracts.authentication import (
        ConnectionStatus,
        ReauthenticationRequired,
        ServiceAccessDenied,
    )
    from backend.app.contracts.sources import SourceKind as CodexSourceKind, SyncScope
    from backend.app.core.runtime import Runtime

    settings = Settings(
        config_dir=tmp_path / "cfg",
        local_data_dir=tmp_path / "data",
        ensai_profile_dir=tmp_path / "cfg/sessions/ensai",
        ensai_auto_login=True,
    )
    probe_state: dict[str, Any] = {"moodle_ok": True, "pamp_ok": False}

    def router(url: str, st: dict[str, Any]) -> tuple[str, str, str]:
        if "foad-moodle.ensai.fr" in url:
            if st["moodle_ok"]:
                return (
                    url,
                    '<div data-region="usermenu"><a href="/login/logout.php">Logout</a></div>',
                    "Mes cours",
                )
            return (
                "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2Ffoad-moodle.ensai.fr%2Fmy%2Fcourses.php",
                '<form><input name="username"><input name="password"></form>',
                "CAS",
            )
        if st["pamp_ok"]:
            return (url, '<a href="/logout.php">Déconnexion</a><div>Planning</div>', "Pamplemousse")
        return (
            "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2F23ed753f.leos.fr%2Findex.php",
            '<form><input name="username"><input name="password"></form>',
            "CAS",
        )

    probe_state["router"] = router
    headless_seen: list[bool] = []

    def factory(p: Path, h: bool) -> _StatefulContext:
        headless_seen.append(h)
        return _StatefulContext(probe_state)

    with Runtime(settings) as rt:
        # Inject fake context factory onto the default bundled EnsaiAuthAdapter
        moodle_auth_bridge = rt.auth.adapters[CodexSourceKind.MOODLE]
        moodle_auth_bridge._adapter._context_factory = factory

        rt.consent.record(CodexSourceKind.MOODLE, "read", True)
        rt.consent.record(CodexSourceKind.PAMPLEMOUSSE, "read", True)

        # 1. Protected Moodle probe -> CONNECTED with session_state="active", service_access="verified"
        res_m = rt.auth.validate(CodexSourceKind.MOODLE)
        assert res_m.status == ConnectionStatus.CONNECTED
        assert res_m.session_state == "active"
        assert res_m.service_access == "verified"

        # 2. Pamplemousse redirected to CAS -> REAUTH_REQUIRED / cas_login_required (never implied by Moodle!)
        res_p = rt.auth.validate(CodexSourceKind.PAMPLEMOUSSE)
        assert res_p.status == ConnectionStatus.REAUTH_REQUIRED
        assert res_p.reason == "cas_login_required"

        # 3. Runtime connector listing on expired Moodle raises ReauthenticationRequired("cas_login_required")
        #    with ZERO headed browser windows and ZERO connector-side auto-submit (even with ensai_auto_login=True!)
        probe_state["moodle_ok"] = False
        headless_seen.clear()
        moodle_bridge = rt.sync.connectors[CodexSourceKind.MOODLE]
        with pytest.raises(ReauthenticationRequired) as exc_info:
            moodle_bridge.list_changes(SyncScope(source=CodexSourceKind.MOODLE), None)
        assert exc_info.value.reason == "cas_login_required"
        assert headless_seen == [True]  # Strictly single headless attempt, no connector-side CAS retry!

        # 4. Explicit PermissionDeniedError on institutional connector maps to ServiceAccessDenied
        class _DeniedConn:
            source_kind = SourceKind.MOODLE

            def list_changes(self, scope=None, cursor=None):
                raise PermissionDeniedError("403")

        from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge

        with pytest.raises(ServiceAccessDenied):
            CodexConnectorBridge(_DeniedConn()).list_changes(SyncScope(source=CodexSourceKind.MOODLE), None)


class _PollingFrame:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self._html = html

    def content(self) -> str:
        return self._html


class _PollingPage:
    """Fake Playwright page exercising the real polling + redirect-settlement loop."""

    _supports_polling = True

    def __init__(
        self,
        *,
        router: Any,
        steps: list[tuple[str, str, str, list[_PollingFrame] | None, list[dict[str, str]] | None]],
        forbid_goto_during_transient: bool = True,
    ) -> None:
        self._router = router
        self._steps = list(steps)
        self._step_idx = 0
        self._forbid_goto_during_transient = forbid_goto_during_transient
        self.url: str = "about:blank"
        self._html: str = ""
        self._title: str = ""
        self.frames: list[_PollingFrame] = []
        self.cookies_store: list[dict[str, str]] = []
        self.visited: list[str] = []
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed

    def _advance(self) -> None:
        if self._step_idx < len(self._steps):
            u, h, t, fr, ck = self._steps[self._step_idx]
            self._step_idx += 1
            self.url, self._html, self._title = u, h, t
            if fr is not None:
                self.frames = fr
            if ck is not None:
                self.cookies_store = ck

    def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 30_000) -> None:
        if self._forbid_goto_during_transient and (
            "ticket=" in self.url.lower() or "testsession=" in self.url.lower()
        ):
            raise AssertionError(f"Premature page.goto({url}) aborted in-flight CAS redirect {self.url}!")
        self.visited.append(url)
        res = self._router(url, self)
        self.url, self._html, self._title = res[0], res[1], res[2]
        if len(res) > 3 and res[3] is not None:
            self.frames = res[3]
        if len(res) > 4 and res[4] is not None:
            self.cookies_store = res[4]

    def content(self) -> str:
        return self._html

    def title(self) -> str:
        return self._title

    def fill(self, selector: str, value: str, *, timeout: int = 5_000) -> None:
        return None

    def click(self, selector: str, *, timeout: int = 5_000) -> None:
        return None

    def wait_for_load_state(self, state: str = "networkidle", *, timeout: int = 15_000) -> None:
        if "ticket=" in self.url.lower() or "testsession=" in self.url.lower():
            self._advance()

    def wait_for_timeout(self, ms: int) -> None:
        self._advance()

    def wait_for_url(self, predicate: Any, *, timeout: int = 120_000) -> None:
        return None


class _PollingContext:
    def __init__(self, page: _PollingPage) -> None:
        self.page = page
        self.pages = [page]
        self.closed = False

    def new_page(self) -> _PollingPage:
        return self.page

    def cookies(self) -> list[dict[str, str]]:
        return list(self.page.cookies_store)

    def close(self) -> None:
        self.closed = True


def test_post_login_ticket_and_testsession_redirects_not_aborted_by_premature_goto(tmp_path: Path) -> None:
    """Interactive login must wait out ?ticket= and ?testsession= hops without aborting them via page.goto()."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})

    def router(url: str, page: _PollingPage) -> tuple[str, str, str]:
        return (
            "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2Ffoad-moodle.ensai.fr%2Flogin%2Findex.php%3FauthCAS%3DCAS",
            '<form id="fm1"><input name="username"><input name="password"></form>',
            "ENSAI CAS",
        )

    steps = [
        # Step 1: Human submitted CAS form; CAS redirects back with transient service ticket
        (
            "https://foad-moodle.ensai.fr/login/index.php?authCAS=CAS&ticket=ST-99999-TOPSECRET-TICKET",
            "<html><body>Validating ticket...</body></html>",
            "Redirecting",
            [],
            [],
        ),
        # Step 2: Moodle validates ticket and redirects through ?testsession= to verify cookies
        (
            "https://foad-moodle.ensai.fr/login/index.php?testsession=4242",
            "<html><body>Checking session...</body></html>",
            "Redirecting",
            [],
            [{"name": "MoodleSession", "value": "SUPER_SECRET_COOKIE_VAL", "domain": "foad-moodle.ensai.fr"}],
        ),
        # Step 3: Moodle settles on protected /my/courses.php with authenticated user shell
        (
            "https://foad-moodle.ensai.fr/my/courses.php",
            '<html class="pagelayout-mydashboard"><body data-userid="4242">'
            '<a href="/course/view.php?id=12">Statistique</a></body></html>',
            "Mes cours",
            [],
            [{"name": "MoodleSession", "value": "SUPER_SECRET_COOKIE_VAL", "domain": "foad-moodle.ensai.fr"}],
        ),
    ]
    polling_page = _PollingPage(router=router, steps=steps, forbid_goto_during_transient=True)
    adapter = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=lambda p, h: _PollingContext(polling_page),
    )

    status = adapter.authenticate(SourceKind.MOODLE, interactive=True, wait_timeout_ms=5_000)
    assert status.authenticated is True
    assert status.state == AuthState.AUTHENTICATED
    # Only the initial probe goto occurred — ticket & testsession hops were NOT interrupted!
    assert polling_page.visited == ["https://foad-moodle.ensai.fr/my/courses.php"]
    diag = status.metadata["diagnostics"]
    assert diag["phase"] == "completed"
    assert diag["navigation_state"] == "on_protected_resource"
    assert diag["current_host"] == "foad-moodle.ensai.fr"
    assert diag["current_path"] == "/my/courses.php"
    assert diag["redirect_completed"] is True
    assert diag["service_cookies_present"] is True
    assert diag["failure_reason"] is None
    # Strictly privacy-preserving: NO CAS tickets, NO cookie values, NO query strings!
    serialized = json.dumps(status.to_dict())
    assert "ST-99999-TOPSECRET-TICKET" not in serialized
    assert "SUPER_SECRET_COOKIE_VAL" not in serialized


def test_pamplemousse_frameset_and_cookie_backed_post_login_and_no_premature_skip(tmp_path: Path) -> None:
    """Pamplemousse login form on target host does NOT skip waiting; same-origin frames & cookies confirm session."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})

    def router(url: str, page: _PollingPage) -> tuple[str, str, str]:
        if "ssocas.ensai.fr" in url:
            return (url, '<form><input name="username"><input name="password"></form>', "ENSAI CAS")
        # Initial Pamplemousse landing page renders a login form on 23ed753f.leos.fr itself
        return (
            "https://23ed753f.leos.fr/index.php",
            '<form><input type="password" name="password"><p>Veuillez vous identifier</p></form>',
            "Pamplemousse Connexion",
        )

    same_origin_frame = _PollingFrame(
        "https://23ed753f.leos.fr/menu.php",
        '<nav><a href="/logout.php">Déconnexion</a><a href="/edt.php">Emploi du temps</a></nav>',
    )
    evil_cross_frame = _PollingFrame(
        "https://evil.example/frame.html",
        "<p>Déconnexion</p>",
    )
    steps = [
        (
            "https://23ed753f.leos.fr/index.php?ticket=ST-PAMP-SECRET-777",
            "<html><body>Ticket validation</body></html>",
            "Redirect",
            [],
            [],
        ),
        (
            "https://23ed753f.leos.fr/index.php",
            '<html><frameset><frame src="menu.php"><frame src="main.php"></frameset></html>',
            "Pamplemousse",
            [same_origin_frame, evil_cross_frame],
            [{"name": "PHPSESSID", "value": "PAMP_COOKIE_SECRET", "domain": "23ed753f.leos.fr"}],
        ),
    ]
    polling_page = _PollingPage(router=router, steps=steps, forbid_goto_during_transient=True)
    adapter = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=lambda p, h: _PollingContext(polling_page),
    )

    status = adapter.authenticate(SourceKind.PAMPLEMOUSSE, interactive=True, wait_timeout_ms=5_000)
    assert status.authenticated is True
    assert status.state == AuthState.AUTHENTICATED
    diag = status.metadata["diagnostics"]
    assert diag["current_host"] == "23ed753f.leos.fr"
    assert diag["current_path"] == "/index.php"
    assert diag["redirect_completed"] is True
    assert diag["service_cookies_present"] is True
    assert diag["failure_reason"] is None
    serialized = json.dumps(status.to_dict())
    assert "ST-PAMP-SECRET-777" not in serialized
    assert "PAMP_COOKIE_SECRET" not in serialized


def test_cas_login_succeeded_without_redirect_automatically_completes_service_sso(tmp_path: Path) -> None:
    """When CAS displays 'Connexion réussie' without redirecting back, adapter triggers protected_url SSO."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    sso_state = {"cas_tgc_active": False}

    def router(url: str, page: _PollingPage) -> tuple[str, str, str]:
        if sso_state["cas_tgc_active"] and "foad-moodle.ensai.fr" in url:
            return (
                "https://foad-moodle.ensai.fr/my/courses.php",
                '<div data-region="usermenu"><a href="/login/logout.php?sesskey=ok">Déconnexion</a></div>',
                "Mes cours",
            )
        return (
            "https://ssocas.ensai.fr/cas/login",
            '<form id="fm1"><input name="username"><input name="password"></form>',
            "ENSAI CAS",
        )

    steps = [
        (
            "https://ssocas.ensai.fr/cas/login",
            "<html><body><h1>Connexion réussie</h1><p>Vous êtes maintenant connecté au CAS.</p></body></html>",
            "Log In Successful",
            [],
            [{"name": "TGC", "value": "TGT_SECRET_VAL", "domain": "ssocas.ensai.fr"}],
        ),
    ]
    polling_page = _PollingPage(router=router, steps=steps)

    orig_advance = polling_page._advance

    def _advance_and_activate() -> None:
        orig_advance()
        sso_state["cas_tgc_active"] = True

    polling_page._advance = _advance_and_activate  # type: ignore[method-assign]

    adapter = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=lambda p, h: _PollingContext(polling_page),
    )
    status = adapter.authenticate(SourceKind.MOODLE, interactive=True, wait_timeout_ms=5_000)
    assert status.authenticated is True
    assert status.state == AuthState.AUTHENTICATED
    assert status.metadata["diagnostics"]["phase"] == "completed"
    assert "TGT_SECRET_VAL" not in json.dumps(status.to_dict())


def test_explicit_human_confirmation_verifies_protected_resource_and_handles_cancel(tmp_path: Path) -> None:
    """Explicit human Enter confirmation verifies protected access; 'q' cancels cleanly with safe diagnostics."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    logged_in = {"ok": False}

    def router(url: str, page: _PollingPage) -> tuple[str, str, str]:
        if logged_in["ok"] and "foad-moodle.ensai.fr" in url:
            return (
                "https://foad-moodle.ensai.fr/my/courses.php",
                '<div data-region="usermenu"><a href="/login/logout.php">Déconnexion</a></div>',
                "Mes cours",
            )
        return (
            "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2Ffoad-moodle.ensai.fr%2Fmy%2Fcourses.php",
            '<form><input name="username"><input name="password"></form>',
            "ENSAI CAS",
        )

    # 1. Human finishes login and presses Enter to confirm
    p1 = _PollingPage(router=router, steps=[])
    adapter1 = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=lambda p, h: _PollingContext(p1),
    )

    def _confirm_enter(prompt: str) -> str:
        logged_in["ok"] = True
        return ""

    st_ok = adapter1.authenticate(
        SourceKind.MOODLE,
        interactive=True,
        wait_timeout_ms=5_000,
        confirm_fn=_confirm_enter,
    )
    assert st_ok.authenticated is True
    assert st_ok.metadata["diagnostics"]["phase"] == "completed"

    # 2. Human types 'q' to cancel while still on CAS
    logged_in["ok"] = False
    p2 = _PollingPage(router=router, steps=[])
    adapter2 = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=lambda p, h: _PollingContext(p2),
    )
    st_cancel = adapter2.authenticate(
        SourceKind.MOODLE,
        interactive=True,
        wait_timeout_ms=5_000,
        confirm_fn=lambda _: "q",
    )
    assert st_cancel.authenticated is False
    assert st_cancel.state == AuthState.NEEDS_LOGIN
    diag = st_cancel.metadata["diagnostics"]
    assert diag["navigation_state"] == "cancelled"
    assert diag["failure_reason"] == "cancelled_by_user"
    assert diag["current_host"] == "ssocas.ensai.fr"
    assert diag["current_path"] == "/cas/login"
    assert "?" not in diag["current_path"]


def test_browser_closed_mid_login_reports_safe_diagnostics(tmp_path: Path) -> None:
    """Closing Chromium before login completes produces browser_closed_by_user without leaking query/secrets."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})

    def router(url: str, page: _PollingPage) -> tuple[str, str, str]:
        return (
            "https://ssocas.ensai.fr/cas/login?service=https%3A%2F%2Ffoad-moodle.ensai.fr%2Fmy%2Fcourses.php&ticket=ST-LEAK-TEST",
            '<form><input name="username"><input name="password" value="NEVER_LOG_PASSWORD"></form>',
            "ENSAI CAS",
        )

    p = _PollingPage(router=router, steps=[])
    orig_wait = p.wait_for_timeout

    def _close_on_wait(ms: int) -> None:
        orig_wait(ms)
        p._closed = True

    p.wait_for_timeout = _close_on_wait  # type: ignore[method-assign]

    adapter = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=lambda pd, h: _PollingContext(p),
    )
    st = adapter.authenticate(SourceKind.MOODLE, interactive=True, wait_timeout_ms=5_000)
    assert st.authenticated is False
    diag = st.metadata["diagnostics"]
    assert diag["navigation_state"] == "browser_closed"
    assert diag["failure_reason"] == "browser_closed_by_user"
    assert diag["current_host"] == "ssocas.ensai.fr"
    assert diag["current_path"] == "/cas/login"
    dumped = json.dumps(st.to_dict())
    assert "ST-LEAK-TEST" not in dumped
    assert "NEVER_LOG_PASSWORD" not in dumped


def test_persistent_session_reuse_across_restarts_and_headless_auto_reauth(tmp_path: Path) -> None:
    """Verify persistent session reuse across separate Runtime executions, shared Moodle/Pamplemousse
    SSO, headless ENSAI_AUTO_LOGIN reauth on expiry, and MFA interactive fallback."""
    from pydantic import SecretStr

    from backend.app.contracts.authentication import ConnectionStatus
    from backend.app.contracts.sources import SourceKind as CodexSourceKind
    from backend.app.core.runtime import Runtime

    cfg_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    profile_dir = cfg_dir / "sessions" / "ensai"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    # Seed private sources.env with ENSAI_AUTO_LOGIN=true and existing custom settings
    (cfg_dir / "sources.env").write_text(
        "ENSAI_AUTO_LOGIN=true\nENSAI_SHARED_SESSION=true\nGOOGLE_CALENDAR_ID=primary\n",
        encoding="utf-8",
    )

    # Disk-backed synthetic browser profile marker simulating Chromium persistent cookies across restarts
    def _cookie_file(p_dir: Path) -> Path:
        return p_dir / "session_cookies.json"

    class _RestartSafeStore:
        """Simulates OS keyring persisting across separate application executions."""

        def __init__(self) -> None:
            self.data: dict[str, str] = {}

        def is_available(self) -> bool:
            return True

        def get(self, key: str) -> SecretStr | None:
            val = self.data.get(key)
            return SecretStr(val) if val is not None else None

        def set(self, key: str, value: SecretStr) -> None:
            self.data[key] = value.get_secret_value()

        def delete(self, key: str) -> None:
            self.data.pop(key, None)

    os_keyring = _RestartSafeStore()
    os_keyring.set("ensai_cas:username", SecretStr("id_student_01"))
    os_keyring.set("ensai_cas:password", SecretStr("SuperSecretPass!2026"))

    sim_flags = {"mfa_active": False, "headless_launches": [], "auto_submits": 0}

    class _PersistentBrowserPage:
        def __init__(self, p_dir: Path) -> None:
            self._p_dir = p_dir
            self.url = "about:blank"
            self._html = ""
            self._title = ""
            self._filled: dict[str, str] = {}

        def _session_valid(self) -> bool:
            cf = _cookie_file(self._p_dir)
            if not cf.is_file():
                return False
            return json.loads(cf.read_text(encoding="utf-8")).get("valid") is True

        def _write_session(self, valid: bool) -> None:
            self._p_dir.mkdir(parents=True, exist_ok=True)
            _cookie_file(self._p_dir).write_text(json.dumps({"valid": valid}), encoding="utf-8")

        def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 30_000) -> None:
            if self._session_valid():
                if "foad-moodle.ensai.fr" in url:
                    self.url = "https://foad-moodle.ensai.fr/my/courses.php"
                    self._html = '<div data-region="usermenu"><a href="/login/logout.php">Déconnexion</a></div>'
                    self._title = "Mes cours"
                    return
                if "23ed753f.leos.fr" in url:
                    self.url = "https://23ed753f.leos.fr/index.php"
                    self._html = '<a href="/logout.php">Déconnexion</a><div>Emploi du temps</div>'
                    self._title = "Pamplemousse"
                    return
            # Unauthenticated -> official ENSAI CAS redirect
            self.url = f"https://ssocas.ensai.fr/cas/login?service={url}"
            if sim_flags["mfa_active"]:
                self._html = '<form><input name="username"><input name="password"><div class="g-recaptcha">2FA</div></form>'
                self._title = "ENSAI CAS MFA"
            else:
                self._html = '<form id="fm1"><input name="username"><input name="password"></form>'
                self._title = "ENSAI CAS"

        def content(self) -> str:
            return self._html

        def title(self) -> str:
            return self._title

        def fill(self, selector: str, value: str, *, timeout: int = 5_000) -> None:
            if "username" in selector:
                self._filled["u"] = value
            elif "password" in selector:
                self._filled["p"] = value

        def click(self, selector: str, *, timeout: int = 5_000) -> None:
            sim_flags["auto_submits"] += 1
            if self._filled.get("u") == "id_student_01" and self._filled.get("p") == "SuperSecretPass!2026":
                self._write_session(True)
                self.url = "https://foad-moodle.ensai.fr/my/courses.php"
                self._html = '<div data-region="usermenu"><a href="/login/logout.php">Déconnexion</a></div>'
                self._title = "Mes cours"
            else:
                self._html = "<p>Identifiants invalides</p>"

        def wait_for_load_state(self, state: str = "networkidle", *, timeout: int = 15_000) -> None:
            return None

        def wait_for_url(self, predicate: Any, *, timeout: int = 120_000) -> None:
            # Interactive fallback completes login (e.g. human completes MFA)
            self._write_session(True)
            self.url = "https://foad-moodle.ensai.fr/my/courses.php"
            self._html = '<div data-region="usermenu"><a href="/login/logout.php">Déconnexion</a></div>'
            self._title = "Mes cours"

    class _PersistentCtx:
        def __init__(self, p_dir: Path) -> None:
            self.page = _PersistentBrowserPage(p_dir)
            self.pages = [self.page]

        def new_page(self) -> _PersistentBrowserPage:
            return self.page

        def cookies(self) -> list[dict[str, str]]:
            if _cookie_file(self.page._p_dir).is_file() and json.loads(
                _cookie_file(self.page._p_dir).read_text(encoding="utf-8")
            ).get("valid"):
                return [
                    {"name": "MoodleSession", "value": "SECRET_M", "domain": "foad-moodle.ensai.fr"},
                    {"name": "PHPSESSID", "value": "SECRET_P", "domain": "23ed753f.leos.fr"},
                ]
            return []

        def close(self) -> None:
            return None

    def make_factory(p_dir: Path, headless: bool) -> _PersistentCtx:
        sim_flags["headless_launches"].append(headless)
        return _PersistentCtx(p_dir)

    def _wire_runtime(rt: Runtime) -> None:
        # Inject shared persistent OS keyring mock & persistent-disk browser context across separate restarts
        rt.credentials = os_keyring
        moodle_bridge = rt.auth.adapters[CodexSourceKind.MOODLE]
        moodle_bridge._creds._store._store = os_keyring
        moodle_bridge._adapter._context_factory = make_factory

    settings = Settings.load(cfg_dir / "sources.env").model_copy(
        update={"local_data_dir": data_dir, "ensai_profile_dir": profile_dir}
    )

    # --- Execution 1: Initial interactive login persists shared browser session ---
    with Runtime(settings) as rt1:
        _wire_runtime(rt1)
        rt1.consent.record(CodexSourceKind.MOODLE, "read", True)
        rt1.consent.record(CodexSourceKind.PAMPLEMOUSSE, "read", True)
        r1 = rt1.auth.authorize(CodexSourceKind.MOODLE)
        assert r1.status == ConnectionStatus.CONNECTED
        assert r1.service_access == "verified"
        assert sim_flags["auto_submits"] == 1  # Used OS keyring automatically on CAS!

    # --- Execution 2 (Separate application restart!): Valid persistent session reused with ZERO submits ---
    sim_flags["headless_launches"].clear()
    sim_flags["auto_submits"] = 0
    with Runtime(settings) as rt2:
        _wire_runtime(rt2)
        m2 = rt2.auth.validate(CodexSourceKind.MOODLE)
        p2 = rt2.auth.validate(CodexSourceKind.PAMPLEMOUSSE)
        assert m2.status == ConnectionStatus.CONNECTED and m2.service_access == "verified"
        assert p2.status == ConnectionStatus.CONNECTED and p2.service_access == "verified"
        assert sim_flags["auto_submits"] == 0  # Session reused directly; zero credential submission!
        assert sim_flags["headless_launches"] == [True, True]  # Pure headless reuse across Moodle & Pamplemousse!

    # --- Execution 3 (Separate restart after CAS session expiration): Headless auto-reauth refreshes session ---
    _cookie_file(profile_dir).write_text(json.dumps({"valid": False}), encoding="utf-8")
    sim_flags["headless_launches"].clear()
    sim_flags["auto_submits"] = 0
    with Runtime(settings) as rt3:
        _wire_runtime(rt3)
        m3 = rt3.auth.validate(CodexSourceKind.MOODLE)
        assert m3.status == ConnectionStatus.CONNECTED and m3.service_access == "verified"
        assert sim_flags["auto_submits"] == 1  # Automatically reauthenticated via OS keyring!
        assert all(h is True for h in sim_flags["headless_launches"])  # Zero visible popups!
        # Immediately reused by Pamplemousse on the shared profile with zero extra submit!
        p3 = rt3.auth.validate(CodexSourceKind.PAMPLEMOUSSE)
        assert p3.status == ConnectionStatus.CONNECTED
        assert sim_flags["auto_submits"] == 1

    # --- Execution 4 (Restart with MFA active): Headless NEVER bypasses MFA; interactive fallback works ---
    _cookie_file(profile_dir).write_text(json.dumps({"valid": False}), encoding="utf-8")
    sim_flags["mfa_active"] = True
    sim_flags["headless_launches"].clear()
    sim_flags["auto_submits"] = 0
    with Runtime(settings) as rt4:
        _wire_runtime(rt4)
        m4_headless = rt4.auth.validate(CodexSourceKind.MOODLE)
        assert m4_headless.status == ConnectionStatus.REAUTH_REQUIRED
        assert m4_headless.reason == "cas_login_required"
        assert sim_flags["auto_submits"] == 0  # NEVER submitted credentials when MFA present!
        assert all(h is True for h in sim_flags["headless_launches"])

        # Interactive fallback opens visible browser (headless=False) for human MFA completion
        m4_interactive = rt4.auth.reauthorize(CodexSourceKind.MOODLE)
        assert m4_interactive.status == ConnectionStatus.CONNECTED
        assert False in sim_flags["headless_launches"]


def test_secure_keyring_schema_consent_replace_delete_and_env_preservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify canonical OS keyring schema alignment, explicit consent, masked replace/delete,
    plaintext rejection, and non-destructive ENSAI_AUTO_LOGIN toggling in sources.env."""
    import getpass
    import sys
    import types

    from backend.app.modules.ingestion.credentials import KeyringCredentialStore

    # 1. Verify KeyringCredentialStore rejects non-OS/plaintext backends
    class _InsecurePlaintextBackend:
        __module__ = "keyrings.alt.file"

        def get_password(self, svc: str, key: str) -> str | None:
            return None

        def set_password(self, svc: str, key: str, val: str) -> None:
            raise AssertionError("Must never write to plaintext backend!")

    fake_kr_bad = types.SimpleNamespace(get_keyring=lambda: _InsecurePlaintextBackend())
    monkeypatch.setitem(sys.modules, "keyring", fake_kr_bad)
    bad_store = KeyringCredentialStore("ensora-plus")
    assert bad_store.is_available() is False
    with pytest.raises(RuntimeError, match="operating-system keyring"):
        bad_store.set_secret("ensai_cas", "password", "secret")

    # 2. Verify canonical OS keyring schema matches Codex (`service="ensora-plus"`, `key="ensai_cas:..."`)
    os_entries: dict[tuple[str, str], str] = {}

    class _FakeSecretServiceBackend:
        __module__ = "keyring.backends.SecretService"

        def get_password(self, svc: str, key: str) -> str | None:
            return os_entries.get((svc, key))

        def set_password(self, svc: str, key: str, val: str) -> None:
            os_entries[(svc, key)] = val

        def delete_password(self, svc: str, key: str) -> None:
            os_entries.pop((svc, key), None)

    fake_kr_good = types.SimpleNamespace(get_keyring=lambda: _FakeSecretServiceBackend())
    monkeypatch.setitem(sys.modules, "keyring", fake_kr_good)

    store = KeyringCredentialStore("ensora-plus")
    assert store.is_available() is True

    # Consent declined -> nothing stored
    mgr_decline = SecureCredentialManager(
        store,
        input_fn=lambda _: "n",
        secret_input_fn=lambda _: "NeverStored!",
    )
    with pytest.raises(PermissionError):
        mgr_decline.prompt_missing_ensai_credentials(persist=True, require_consent=True)
    assert not mgr_decline.has_ensai_credentials()

    # GetPassWarning (masked input unavailable) -> refuses to echo/store password
    def _warn_getpass(prompt: str) -> str:
        import warnings

        warnings.warn("Can not control echo on the terminal.", getpass.GetPassWarning)
        return "echoed"

    mgr_warn = SecureCredentialManager(store, input_fn=lambda _: "id123", secret_input_fn=_warn_getpass)
    with pytest.raises(RuntimeError, match="Masked password input is unavailable"):
        mgr_warn.prompt_missing_ensai_credentials(persist=True, require_consent=False)

    # Consent approved -> stored under canonical Codex-compatible service/key
    inputs_seq = iter(["id_initial"])
    mgr = SecureCredentialManager(
        store,
        input_fn=lambda _: next(inputs_seq),
        secret_input_fn=lambda _: "FirstSecret!1",
    )
    creds = mgr.prompt_missing_ensai_credentials(
        persist=True,
        require_consent=True,
        consent_fn=lambda _: True,
    )
    assert creds.username == "id_initial"
    assert os_entries[("ensora-plus", "ensai_cas:username")] == "id_initial"
    assert os_entries[("ensora-plus", "ensai_cas:password")] == "FirstSecret!1"

    # Replacement updates credentials cleanly
    replace_inputs = iter(["id_rotated"])
    mgr_rep = SecureCredentialManager(
        store,
        input_fn=lambda _: next(replace_inputs),
        secret_input_fn=lambda _: "SecondSecret!2",
    )
    creds2 = mgr_rep.prompt_missing_ensai_credentials(
        persist=True,
        force_replace=True,
        require_consent=True,
        consent_fn=lambda _: True,
    )
    assert creds2.username == "id_rotated"
    assert os_entries[("ensora-plus", "ensai_cas:password")] == "SecondSecret!2"

    # Deletion wipes both canonical and any legacy entries
    os_entries[("ensora-plus:ensai_cas", "password")] = "OldLegacySecret"
    mgr_rep.clear_ensai_credentials()
    assert not mgr_rep.has_ensai_credentials()
    assert ("ensora-plus", "ensai_cas:password") not in os_entries
    assert ("ensora-plus:ensai_cas", "password") not in os_entries

    # 3. sources.env safe toggle preserves existing settings verbatim & refuses sensitive keys
    cfg_dir = tmp_path / "env_cfg"
    cfg_dir.mkdir(parents=True)
    env_file = cfg_dir / "sources.env"
    env_file.write_text(
        "# Private config\nGOOGLE_CLOUD_PROJECT_ID=ensora-516015\nENSAI_USERNAME=id_keep\n",
        encoding="utf-8",
    )
    cfg = SourcesRuntimeConfig.load(config_dir=cfg_dir, environ={})
    with pytest.raises(ValueError, match="sensitive"):
        cfg.set_non_sensitive_env_flag("ENSAI_PASSWORD", "never")
    cfg.set_non_sensitive_env_flag("ENSAI_AUTO_LOGIN", "true")
    content = env_file.read_text(encoding="utf-8")
    assert "# Private config" in content
    assert "GOOGLE_CLOUD_PROJECT_ID=ensora-516015" in content
    assert "ENSAI_USERNAME=id_keep" in content
    assert "ENSAI_AUTO_LOGIN=true" in content
    assert "FirstSecret!1" not in content and "SecondSecret!2" not in content
    assert (env_file.stat().st_mode & 0o777) == 0o600

    # 4. CLI auth-ensai --delete-credentials --disable-auto-login works cleanly without leaking secrets
    import scripts.p1_sources_cli as cli

    os_entries[("ensora-plus", "ensai_cas:username")] = "id_cli"
    os_entries[("ensora-plus", "ensai_cas:password")] = "CliSecret!9"
    rc = cli.main(
        ["--config-dir", str(cfg_dir), "auth-ensai", "--delete-credentials", "--disable-auto-login"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "CREDENTIALS_DELETED"
    assert payload["credentials_stored"] is False
    assert payload["auto_login_enabled"] is False
    assert "CliSecret!9" not in json.dumps(payload)
    assert "ENSAI_AUTO_LOGIN=false" in env_file.read_text(encoding="utf-8")


def test_session_cookie_persistence_across_close_and_codex_post_reauthorize_validate(tmp_path: Path) -> None:
    """Reproduces the exact live failure: Chromium drops expires=-1 session cookies (MoodleSession,
    PHPSESSID, TGC) on context.close() right before Codex AuthenticationManager calls adapter.validate().

    Verifies that saving/restoring allowlisted institutional session cookies (0o600) preserves
    authentication across close+reopen so onboarding reports CONNECTED for Moodle & Pamplemousse."""
    from backend.app.contracts.authentication import ConnectionStatus
    from backend.app.contracts.sources import SourceKind as CodexSourceKind
    from backend.app.core.runtime import Runtime
    from backend.app.modules.ingestion.auth.ensai import SESSION_COOKIES_FILENAME

    cfg_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    profile_dir = cfg_dir / "sessions" / "ensai"

    class _ChromiumSessionCookiePage:
        """Simulates real Chromium: session cookies (expires==-1) exist ONLY in memory per context."""

        def __init__(self, ctx: "_ChromiumSessionCookieContext") -> None:
            self._ctx = ctx
            self.url = "about:blank"
            self._html = ""
            self._title = ""

        def _has_cookie(self, name: str) -> bool:
            return any(c.get("name") == name for c in self._ctx._mem_cookies)

        def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 30_000) -> None:
            if "foad-moodle.ensai.fr" in url and (self._has_cookie("MoodleSession") or self._has_cookie("TGC")):
                if not self._has_cookie("MoodleSession"):
                    self._ctx._mem_cookies.append(
                        {"name": "MoodleSession", "value": "SECRET_MOODLE_SESS", "domain": "foad-moodle.ensai.fr", "path": "/", "expires": -1, "secure": True, "httpOnly": True}
                    )
                self.url = "https://foad-moodle.ensai.fr/my/courses.php"
                self._html = '<div data-region="usermenu"><a href="/login/logout.php">Déconnexion</a></div>'
                self._title = "Mes cours"
                return
            if "23ed753f.leos.fr" in url and (self._has_cookie("PHPSESSID") or self._has_cookie("TGC")):
                if not self._has_cookie("PHPSESSID"):
                    self._ctx._mem_cookies.append(
                        {"name": "PHPSESSID", "value": "SECRET_PAMP_SESS", "domain": "23ed753f.leos.fr", "path": "/", "expires": -1, "secure": True, "httpOnly": True}
                    )
                self.url = "https://23ed753f.leos.fr/index.php"
                self._html = '<a href="/logout.php">Déconnexion</a><div>Emploi du temps</div>'
                self._title = "Pamplemousse"
                return
            self.url = f"https://ssocas.ensai.fr/cas/login?service={url}"
            self._html = '<form id="fm1"><input name="username"><input name="password"></form>'
            self._title = "ENSAI CAS"

        def content(self) -> str:
            return self._html

        def title(self) -> str:
            return self._title

        def fill(self, selector: str, value: str, *, timeout: int = 5_000) -> None:
            return None

        def click(self, selector: str, *, timeout: int = 5_000) -> None:
            return None

        def wait_for_load_state(self, state: str = "networkidle", *, timeout: int = 15_000) -> None:
            return None

        def wait_for_url(self, predicate: Any, *, timeout: int = 120_000) -> None:
            # Human logs in via CAS -> sets expires=-1 session cookies + an unrelated 3rd-party cookie
            self._ctx._mem_cookies.extend(
                [
                    {"name": "TGC", "value": "SECRET_CAS_TGC", "domain": "ssocas.ensai.fr", "path": "/cas", "expires": -1, "secure": True, "httpOnly": True},
                    {"name": "MoodleSession", "value": "SECRET_MOODLE_SESS", "domain": "foad-moodle.ensai.fr", "path": "/", "expires": -1, "secure": True, "httpOnly": True},
                    {"name": "tracker", "value": "UNRELATED", "domain": "ads.evil.example", "path": "/", "expires": -1},
                ]
            )
            self.url = "https://foad-moodle.ensai.fr/my/courses.php"
            self._html = '<div data-region="usermenu"><a href="/login/logout.php">Déconnexion</a></div>'
            self._title = "Mes cours"

    class _ChromiumSessionCookieContext:
        def __init__(self) -> None:
            # Brand-new context starts with ZERO session cookies (simulating Chromium close purge!)
            self._mem_cookies: list[dict[str, Any]] = []
            self.page = _ChromiumSessionCookiePage(self)
            self.pages = [self.page]
            self.closed = False

        def new_page(self) -> _ChromiumSessionCookiePage:
            return self.page

        def cookies(self) -> list[dict[str, Any]]:
            return list(self._mem_cookies)

        def storage_state(self) -> dict[str, Any]:
            return {"cookies": list(self._mem_cookies), "origins": []}

        def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
            self._mem_cookies.extend(cookies)

        def close(self) -> None:
            # Chromium purges all expires==-1 session cookies on close!
            self._mem_cookies = [c for c in self._mem_cookies if (c.get("expires") or -1) > 0]
            self.closed = True

    launches: list[bool] = []

    def factory(p_dir: Path, headless: bool) -> _ChromiumSessionCookieContext:
        launches.append(headless)
        return _ChromiumSessionCookieContext()

    settings = Settings(
        config_dir=cfg_dir,
        local_data_dir=data_dir,
        ensai_profile_dir=profile_dir,
    )
    with Runtime(settings) as rt:
        rt.auth.adapters[CodexSourceKind.MOODLE]._adapter._context_factory = factory
        rt.consent.record(CodexSourceKind.MOODLE, "read", True)
        rt.consent.record(CodexSourceKind.PAMPLEMOUSSE, "read", True)

        # 1. Interactive reauthorize verifies protected access without discarding it on an extra probe
        res_m = rt.auth.reauthorize(CodexSourceKind.MOODLE)
        assert res_m.status == ConnectionStatus.CONNECTED
        assert res_m.service_access == "verified"
        assert res_m.session_state == "active"
        assert launches == [False]

        # 2. Verify persisted cookie file is 0o600 and excludes non-institutional third-party cookies
        cookie_store = profile_dir / SESSION_COOKIES_FILENAME
        assert cookie_store.is_file()
        assert (cookie_store.stat().st_mode & 0o777) == 0o600
        saved_text = cookie_store.read_text(encoding="utf-8")
        assert "ads.evil.example" not in saved_text
        assert "foad-moodle.ensai.fr" in saved_text
        assert "ssocas.ensai.fr" in saved_text

        # 3. Separate fresh headless validate for BOTH Moodle and Pamplemousse reuses persisted session cookies!
        res_m_reopen = rt.auth.validate(CodexSourceKind.MOODLE)
        assert res_m_reopen.status == ConnectionStatus.CONNECTED
        assert res_m_reopen.service_access == "verified"
        res_p = rt.auth.validate(CodexSourceKind.PAMPLEMOUSSE)
        assert res_p.status == ConnectionStatus.CONNECTED
        assert res_p.service_access == "verified"
        assert launches == [False, True, True]

        # 4. Disconnect with explicit shared profile clear wipes persisted session cookie file
        rt.auth.adapters[CodexSourceKind.MOODLE]._adapter.clear_shared_session(confirmed=True)
        assert not cookie_store.exists()


def test_debug_session_lifecycle_five_stages_and_keep_open_confirmation(tmp_path: Path) -> None:
    """verify_session_lifecycle() distinguishes all 5 stages, honors keep_open_until_confirmed,
    and never reports connected=True if reopened verification fails."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    server_state = {"accept_cookie": True}
    prompts_seen: list[str] = []

    class _StagePage:
        def __init__(self, ctx: "_StageCtx") -> None:
            self._ctx = ctx
            self.url = "about:blank"
            self._html = ""
            self._title = ""

        def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 30_000) -> None:
            has_sess = any(c.get("name") == "MoodleSession" for c in self._ctx._cookies)
            if has_sess and server_state["accept_cookie"]:
                self.url = "https://foad-moodle.ensai.fr/my/courses.php"
                self._html = '<div data-region="usermenu"><a href="/login/logout.php">Déconnexion</a></div>'
                self._title = "Mes cours"
                return
            self.url = f"https://ssocas.ensai.fr/cas/login?service={url}"
            self._html = '<form><input name="username"><input name="password"></form>'
            self._title = "ENSAI CAS"

        def content(self) -> str:
            return self._html

        def title(self) -> str:
            return self._title

        def fill(self, selector: str, value: str, *, timeout: int = 5_000) -> None:
            return None

        def click(self, selector: str, *, timeout: int = 5_000) -> None:
            return None

        def wait_for_load_state(self, state: str = "networkidle", *, timeout: int = 15_000) -> None:
            return None

        def wait_for_url(self, predicate: Any, *, timeout: int = 120_000) -> None:
            self._ctx._cookies.append(
                {"name": "MoodleSession", "value": "SESS_DBG", "domain": "foad-moodle.ensai.fr", "path": "/", "expires": -1}
            )
            self.url = "https://foad-moodle.ensai.fr/my/courses.php"
            self._html = '<div data-region="usermenu"><a href="/login/logout.php">Déconnexion</a></div>'
            self._title = "Mes cours"

    class _StageCtx:
        def __init__(self) -> None:
            self._cookies: list[dict[str, Any]] = []
            self.page = _StagePage(self)
            self.pages = [self.page]
            self.closed = False

        def new_page(self) -> _StagePage:
            return self.page

        def cookies(self) -> list[dict[str, Any]]:
            return list(self._cookies)

        def storage_state(self) -> dict[str, Any]:
            return {"cookies": list(self._cookies)}

        def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
            self._cookies.extend(cookies)

        def close(self) -> None:
            self.closed = True

    created_ctxs: list[_StageCtx] = []

    def factory(p_dir: Path, headless: bool) -> _StageCtx:
        ctx = _StageCtx()
        created_ctxs.append(ctx)
        return ctx

    def _confirm(prompt: str) -> str:
        prompts_seen.append(prompt)
        # During login & keep-open inspection, Chromium MUST still be open!
        assert created_ctxs[-1].closed is False
        if "PROTECTED_RESOURCE_VERIFIED" not in prompt:
            created_ctxs[-1]._cookies.append(
                {"name": "MoodleSession", "value": "SESS_DBG", "domain": "foad-moodle.ensai.fr", "path": "/", "expires": -1}
            )
            created_ctxs[-1].page.url = "https://foad-moodle.ensai.fr/my/courses.php"
            created_ctxs[-1].page._html = '<div data-region="usermenu"><a href="/login/logout.php">Déconnexion</a></div>'
            created_ctxs[-1].page._title = "Mes cours"
        return ""

    adapter = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        context_factory=factory,
    )

    # 1. All 5 stages succeed
    report_ok = adapter.verify_session_lifecycle(
        SourceKind.MOODLE,
        interactive=True,
        keep_open_until_confirmed=True,
        confirm_fn=_confirm,
    )
    assert report_ok["connected"] is True
    assert report_ok["stages"] == {
        "LOGIN_COMPLETED": True,
        "PROTECTED_RESOURCE_VERIFIED": True,
        "SESSION_PERSISTED": True,
        "SESSION_REOPENED": True,
        "SESSION_REUSE_VERIFIED": True,
    }
    assert any("PROTECTED_RESOURCE_VERIFIED" in p for p in prompts_seen)
    assert "SESS_DBG" not in json.dumps(report_ok)

    # 2. If server rejects the cookie on reopen, SESSION_REUSE_VERIFIED=False and connected=False!
    created_ctxs.clear()

    def _confirm_then_expire(prompt: str) -> str:
        assert created_ctxs[-1].closed is False
        if "PROTECTED_RESOURCE_VERIFIED" in prompt:
            server_state["accept_cookie"] = False
        return ""

    report_fail = adapter.verify_session_lifecycle(
        SourceKind.MOODLE,
        interactive=True,
        keep_open_until_confirmed=True,
        confirm_fn=_confirm_then_expire,
    )
    assert report_fail["connected"] is False
    assert report_fail["stages"]["LOGIN_COMPLETED"] is True
    assert report_fail["stages"]["PROTECTED_RESOURCE_VERIFIED"] is True
    assert report_fail["stages"]["SESSION_PERSISTED"] is True
    assert report_fail["stages"]["SESSION_REOPENED"] is True
    assert report_fail["stages"]["SESSION_REUSE_VERIFIED"] is False
    assert report_fail["failure_reason"] == "cas_login_pending"

"""Chromium detection must find Playwright's browser cache on Windows and macOS, not only Linux."""

from backend.app.modules.ingestion.auth.ensai import detect_playwright_environment


def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")


def test_detects_windows_cache_in_localappdata(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    exe = tmp_path / "AppData" / "ms-playwright" / "chromium-1255" / "chrome-win64" / "chrome.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    status = detect_playwright_environment()
    assert status.chromium_installed is True
    assert status.chromium_executable == str(exe)


def test_reports_missing_browser_with_install_hint(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    status = detect_playwright_environment()
    assert status.chromium_installed is False
    assert "playwright install chromium" in status.install_command

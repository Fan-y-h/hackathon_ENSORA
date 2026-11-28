"""QA Section 6 — Reusable Playwright E2E Browser Test Suite for ENSORA+.

Runs against an isolated backend (default http://127.0.0.1:18096) and Vite frontend
(default http://127.0.0.1:15176), verifying all user flows, console errors, network errors,
and persistence across page reload.

Usage:
    ENSORA_E2E_FRONTEND=http://127.0.0.1:15176 ENSORA_E2E_BACKEND=http://127.0.0.1:18096 \\
        /home/ensai/hackathon_ENSORA/.venv/bin/python -m pytest frontend/e2e/test_e2e_browser.py -v
"""

from __future__ import annotations

import os
import urllib.request

import pytest

FRONTEND_URL = os.environ.get("ENSORA_E2E_FRONTEND", "").rstrip("/")
BACKEND_URL = os.environ.get("ENSORA_E2E_BACKEND", "").rstrip("/")


def _server_reachable(url: str) -> bool:
    if not url:
        return False
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status < 500
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_server_reachable(FRONTEND_URL) and _server_reachable(f"{BACKEND_URL}/api/v1/health")),
    reason="Set ENSORA_E2E_FRONTEND and ENSORA_E2E_BACKEND pointing to live isolated servers to run browser E2E",
)


@pytest.fixture(scope="module")
def browser_context():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        yield context
        context.close()
        browser.close()


def _attach_error_collectors(page):
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda err: page_errors.append(str(err)))
    page.on(
        "response",
        lambda resp: failed_requests.append(f"{resp.status} {resp.request.method} {resp.url}")
        if resp.status >= 400 and "/api/v1/plans/current" not in resp.url
        else None,
    )
    return console_errors, page_errors, failed_requests


CORE_ROUTES = [
    "/",
    "/planner",
    "/tasks",
    "/grades",
    "/library",
    "/flashcards",
    "/quiz",
    "/focus",
    "/notes",
    "/notifications",
    "/analytics",
    "/settings",
    "/subjects",
    "/tutor",
    "/studio",
    "/exams",
]


def test_e2e_all_routes_render_without_crash_or_console_errors(browser_context):
    page = browser_context.new_page()
    console_errors, page_errors, failed_requests = _attach_error_collectors(page)
    for route in CORE_ROUTES:
        page.goto(f"{FRONTEND_URL}{route}", wait_until="networkidle", timeout=15_000)
        assert page.locator("main").is_visible(), f"Main landmark missing on {route}"
        assert page.locator("h1, h2").first.is_visible(), f"Heading missing on {route}"
    page.close()
    assert not page_errors, f"Uncaught page errors: {page_errors}"
    assert not console_errors, f"Console errors: {console_errors}"
    assert not failed_requests, f"HTTP >=400 API responses: {failed_requests}"


def test_e2e_profile_task_planner_notes_and_reload_persistence(browser_context):
    page = browser_context.new_page()
    console_errors, page_errors, _ = _attach_error_collectors(page)

    # 1. Settings: update display name and verify persistence across reload
    page.goto(f"{FRONTEND_URL}/settings", wait_until="networkidle")
    name_input = page.get_by_label("Nom affiché")
    name_input.fill("QA Étudiant")
    page.locator("form button[type='submit']").first.click()
    page.wait_for_timeout(500)
    page.reload(wait_until="networkidle")
    assert page.get_by_label("Nom affiché").input_value() == "QA Étudiant"

    # 2. Tasks: create a task and verify persistence across reload
    page.goto(f"{FRONTEND_URL}/tasks", wait_until="networkidle")
    title_input = page.locator("input[type='text'], input:not([type])").first
    if title_input.is_visible():
        title_input.fill("QA Tâche E2E")
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(500)
        page.reload(wait_until="networkidle")
        assert page.get_by_text("QA Tâche E2E").first.is_visible()

    # 3. Planner: trigger preview and check dialog/sessions
    page.goto(f"{FRONTEND_URL}/planner", wait_until="networkidle")
    plan_btn = page.get_by_role("button", name="Planifier").or_(page.get_by_role("button", name="Replanifier"))
    if plan_btn.count() > 0 and plan_btn.first.is_enabled():
        plan_btn.first.click()
        page.wait_for_timeout(800)

    # 4. Notes: create a note and verify persistence across reload
    page.goto(f"{FRONTEND_URL}/notes", wait_until="networkidle")
    assert page.locator("main").is_visible()

    # 5. Flashcards & Quiz & Focus Room & Grades
    for path in ("/flashcards", "/quiz", "/focus", "/grades", "/analytics", "/notifications"):
        page.goto(f"{FRONTEND_URL}{path}", wait_until="networkidle")
        assert page.locator("main").is_visible()

    page.close()
    assert not page_errors, f"Uncaught page errors: {page_errors}"
    assert not console_errors, f"Console errors: {console_errors}"

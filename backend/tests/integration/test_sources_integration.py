"""End-to-end checks of the unified server: planner API + P1 sources API + agenda bridge.

Everything runs against temporary directories: the user's real P1 store, browser profile
and keyring entries are never touched (``Components()`` has no live connectors).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from backend.app.core.runtime import Components
from backend.app.core.settings import Settings as P1Settings

EXAM = {
    "kind": "event",
    "title": "Partiel Économétrie",
    "event_type": "exam",
    "starts_at": "2026-12-14T08:00:00+00:00",
    "ends_at": "2026-12-14T10:00:00+00:00",
    "room": "Amphi 1",
    "subject_id": "eco",
    "provenance": {"external_id": "exam-eco-2026"},
}
PROJECT = {
    "kind": "event",
    "title": "Rendu projet Python",
    "event_type": "project",
    "starts_at": "2026-12-11T21:00:00+00:00",
    "ends_at": "2026-12-11T22:00:00+00:00",
    "status": "tentative",
    "provenance": {"external_id": "project-py"},
}
CANCELLED = {
    "kind": "event",
    "title": "Cours annulé",
    "event_type": "class",
    "starts_at": "2026-12-15T08:00:00+00:00",
    "ends_at": "2026-12-15T10:00:00+00:00",
    "status": "cancelled",
    "provenance": {"external_id": "cancelled-class"},
}


@pytest.fixture
def p1_settings(tmp_path):
    return P1Settings(
        config_dir=tmp_path / "config",
        local_data_dir=tmp_path / "data",
        ensai_profile_dir=tmp_path / "config/sessions/ensai",
    )


@pytest.fixture
def client(tmp_path, p1_settings):
    app = create_app(
        f"sqlite:///{(tmp_path / 'planner.db').as_posix()}",
        enable_sources=True,
        p1_settings=p1_settings,
        p1_components=Components(),
    )
    with TestClient(app) as test_client:  # runs the lifespan: starts and stops the P1 runtime
        yield test_client


def _auth(p1_settings) -> dict[str, str]:
    token = (p1_settings.local_data_dir / "api-token").read_text()
    return {"Authorization": f"Bearer {token}"}


def _consent(client, headers, source: str) -> None:
    items = client.get("/api/v1/sources/connections", headers=headers).json()["items"]
    connection = next(item for item in items if item["source"] == source)
    base = f"/api/v1/sources/connections/{connection['id']}"
    assert client.put(base + "/consent", json={"purpose": "read", "granted": True}, headers=headers).status_code == 200
    assert client.post(base + "/authorize", headers=headers).json()["status"] == "CONNECTED"


def test_health_reports_both_parts(client):
    body = client.get("/api/v1/health").json()
    assert body == {"status": "ok", "database": True, "sources": True}
    assert client.get("/api/v1/sources/health").json()["status"] == "ready"


def test_sources_api_is_mounted_and_token_protected(client, p1_settings):
    assert client.get("/api/v1/sources/connections").status_code == 401
    headers = _auth(p1_settings)
    sources = {item["source"] for item in client.get("/api/v1/sources/connections", headers=headers).json()["items"]}
    assert {"moodle", "pamplemousse", "ensora_drive", "google_calendar", "local", "academic_import"} <= sources
    assert client.get("/api/v1/sources/onboarding", headers=headers).status_code == 200
    assert client.get("/api/v1/sources/sync/schedule", headers=headers).status_code == 200
    assert client.get("/api/v1/sources/sync/runs", headers=headers).json()["items"] == []
    assert client.get("/api/v1/sources/pipeline/status", headers=headers).status_code == 200
    # Planner routes are untouched by the mount and keep their own (local) identity.
    assert client.get("/api/v1/profile").status_code == 200
    assert client.get("/api/v1/tasks").status_code == 200


def test_document_upload_is_listed_with_content_and_chunks(client, p1_settings):
    headers = _auth(p1_settings)
    _consent(client, headers, "local")
    upload = {"file": ("cours.txt", b"Theoreme de Gauss-Markov : estimateur BLUE.", "text/plain")}
    assert client.post("/api/v1/sources/documents/import", files=upload, headers=headers).status_code == 201
    again = client.post("/api/v1/sources/documents/import", files=upload, headers=headers)
    assert again.json()["result"] == "unchanged"

    document = client.get("/api/v1/sources/documents", headers=headers).json()["items"][0]
    detail = client.get(f"/api/v1/sources/documents/{document['id']}", headers=headers)
    assert detail.status_code == 200
    versions = client.get(f"/api/v1/sources/documents/{document['id']}/versions", headers=headers).json()["items"]
    assert len(versions) == 1
    prefix = f"/api/v1/sources/documents/{document['id']}/versions/{document['active_version_id']}"
    assert client.get(prefix + "/content", headers=headers).content.startswith(b"Theoreme de Gauss-Markov")
    chunk = client.get(prefix + "/chunks", headers=headers).json()["items"][0]
    assert "Gauss-Markov" in chunk["payload"]["text"]


def test_bridge_copies_source_events_into_the_agenda(client, p1_settings):
    headers = _auth(p1_settings)
    _consent(client, headers, "academic_import")
    imported = client.post("/api/v1/sources/academic/import", json=[EXAM, PROJECT, CANCELLED], headers=headers)
    assert imported.status_code == 201, imported.text

    first = client.post("/api/v1/agenda/import-from-sources")
    assert first.status_code == 200, first.text
    (report,) = first.json()["sources"]
    assert report["source"] == "academic_import" and report["agenda_source"] == "import"
    assert report["read"] == 3 and report["skipped"] == 1  # the cancelled class never reaches the agenda
    assert report["result"]["created"] == 2

    events = client.get("/api/v1/events", params={"start": "2026-12-01T00:00:00Z", "end": "2026-12-31T00:00:00Z"})
    by_title = {e["title"]: e for e in events.json()["items"]}
    exam = by_title["Partiel Économétrie"]
    assert exam["type"] == "exam" and exam["source"] == "import" and exam["read_only"] is True
    assert exam["room"] == "Amphi 1" and exam["subject_id"] == "eco"
    project = by_title["Rendu projet Python"]
    assert project["type"] == "deadline"  # P1 "project" folded into the agenda's deadline type
    assert project["confirmation_required"] is True  # tentative upstream -> must be confirmed
    assert "Cours annulé" not in by_title

    # The imported exam becomes an assessment the Smart Planner can revise for.
    titles = {a["title"] for a in client.get("/api/v1/assessments").json()["items"]}
    assert "Partiel Économétrie" in titles

    # Idempotent: a second run changes nothing.
    second = client.post("/api/v1/agenda/import-from-sources").json()["sources"][0]["result"]
    assert second["created"] == 0 and second["unchanged"] == 2


def test_without_sources_the_planner_still_works(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'planner.db').as_posix()}")  # tests/default: sources disabled
    with TestClient(app) as test_client:
        assert test_client.get("/api/v1/health").json()["sources"] is False
        assert test_client.get("/api/v1/sources/connections").status_code == 404
        bridge = test_client.post("/api/v1/agenda/import-from-sources")
        assert bridge.status_code == 503
        assert bridge.json()["error"]["code"] == "sources_unavailable"


def test_finished_sync_reaches_the_agenda_without_a_click(client, p1_settings):
    """A finished sync run (manual or scheduled) is applied to the agenda automatically."""
    import time

    headers = _auth(p1_settings)
    _consent(client, headers, "academic_import")
    assert client.post("/api/v1/sources/academic/import", json=[EXAM], headers=headers).status_code == 201
    importer = client.app.state.agenda_importer
    assert importer.run_once() is None  # no finished sync run yet: nothing applied

    job = client.post("/api/v1/sources/sync", json={}, headers=headers).json()["id"]
    for _ in range(50):
        if client.get(f"/api/v1/sources/jobs/{job}", headers=headers).json()["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    results = importer.run_once()
    assert results and results[0].result.created == 1
    assert importer.run_once() is None  # once per finished run
    titles = [e["title"] for e in client.get(
        "/api/v1/events", params={"start": "2026-12-01T00:00:00Z", "end": "2026-12-31T00:00:00Z"}
    ).json()["items"]]
    assert "Partiel Économétrie" in titles

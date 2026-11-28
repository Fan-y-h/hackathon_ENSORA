"""Lead Architect verification tests: migrations 0005, CORS, catalog, annotations, exam-mode guard."""

from __future__ import annotations

import shutil
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from backend.app.core.database import migrate, open_database


def test_p1_migrations_fresh_and_upgrade_from_0004(tmp_path):
    # Fresh database reaches 0005_p1_read_indexes with all read indexes present.
    fresh = open_database(tmp_path / "fresh.sqlite3")
    migrate(fresh)
    with fresh.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        indexes = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))}
    assert rev == "0005_p1_read_indexes"
    assert {
        "ix_documents_active_version",
        "ix_documents_conn_active_version",
        "ix_documents_conn_subject",
        "ix_source_record_history_recorded",
        "ix_availability_user",
    } <= indexes
    fresh.dispose()

    # Upgrade path from 0004 -> 0005 works cleanly.
    upgraded = open_database(tmp_path / "from_0004.sqlite3")
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    with upgraded.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0004_p1_acceptance")
    migrate(upgraded)
    with upgraded.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0005_p1_read_indexes"
    upgraded.dispose()


def test_p1_migrations_idempotent_on_real_db_copy(tmp_path):
    real_db = Path.home() / ".local" / "share" / "ensora-plus" / "ensora.sqlite3"
    if not real_db.exists():
        return
    copy_path = tmp_path / "copy.sqlite3"
    shutil.copyfile(real_db, copy_path)
    engine = open_database(copy_path)
    migrate(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0005_p1_read_indexes"
    engine.dispose()


def test_cors_allows_both_loopback_and_localhost(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'cors.db').as_posix()}", enable_sources=False)
    client = TestClient(app)
    for origin in ("http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5174", "http://localhost:5174"):
        res = client.options(
            "/api/v1/profile",
            headers={"Origin": origin, "Access-Control-Request-Method": "PATCH"},
        )
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == origin


def test_catalog_subjects_annotations_and_exam_mode_guard(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'lead.db').as_posix()}", enable_sources=False)
    client = TestClient(app)

    # Seed a subject via task + notebook + quiz.
    assert client.post(
        "/api/v1/tasks",
        json={"title": "TD OLS", "subject_id": "2AECO02", "estimated_min": 60, "remaining_min": 60, "priority": 3},
    ).status_code == 201
    nb = client.post("/api/v1/notebooks", json={"title": "Économétrie", "subject_id": "2AECO02"}).json()
    note = client.post("/api/v1/notes", json={"notebook_id": nb["id"], "title": "Gauss-Markov", "markdown": "BLUE"}).json()
    ann = client.post(
        "/api/v1/annotations",
        json={"document_id": "doc-1", "excerpt": "BLUE estimator", "comment": "Important", "note_id": note["id"]},
    )
    assert ann.status_code == 201
    assert len(client.get("/api/v1/annotations", params={"document_id": "doc-1"}).json()) == 1

    # Catalog aggregates subjects and returns full detail.
    subs = client.get("/api/v1/subjects").json()["items"]
    assert any(s["id"] == "2AECO02" for s in subs)
    detail = client.get("/api/v1/subjects/2AECO02").json()
    assert detail["id"] == "2AECO02"
    assert len(detail["notes"]) == 1
    assert client.get("/api/v1/subjects/UNKNOWN_SUB").status_code == 404

    # Exam mode blocks notes, notebooks and annotations with 403 exam_mode_active.
    quiz = client.post(
        "/api/v1/quizzes",
        json={
            "title": "Partiel Eco",
            "subject_id": "2AECO02",
            "type": "exam",
            "time_limit_min": 30,
            "questions": [
                {
                    "type": "true_false",
                    "prompt": "OLS is BLUE under Gauss-Markov",
                    "answer": True,
                    "points": 1,
                    "concept_ids": ["gauss_markov"],
                    "dimension": "theory",
                }
            ],
        },
    ).json()
    attempt = client.post(f"/api/v1/quizzes/{quiz['id']}/attempts", json={"mode": "exam"}).json()
    for method, path in (
        ("GET", "/api/v1/notes"),
        ("GET", "/api/v1/notebooks"),
        ("GET", "/api/v1/annotations"),
    ):
        blocked = client.request(method, path)
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "exam_mode_active"

    # Submitting the exam restores access.
    assert client.post(f"/api/v1/attempts/{attempt['id']}/submit").status_code == 200
    assert client.get("/api/v1/notes").status_code == 200
    assert client.get("/api/v1/annotations").status_code == 200

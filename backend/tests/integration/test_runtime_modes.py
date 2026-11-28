"""Runtime boundaries for local profile initialization and the synthetic demo."""

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from backend.app.core.runtime import Components
from backend.app.core.settings import Settings


def test_demo_does_not_use_real_database_or_source_settings(tmp_path, monkeypatch):
    demo = tmp_path / "demo"
    real_database = tmp_path / "real.sqlite"
    real_sources = tmp_path / "real-sources"
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("ENSORA_DEMO_DIR", str(demo))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{real_database}")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(real_sources))
    with TestClient(create_app(enable_sources=True)) as client:
        assert client.get("/api/v1/runtime-mode").json() == {"mode": "demo"}
        assert client.get("/api/v1/sources/health").is_success
        assert (demo / "runtime" / "ensora.db").is_file()
        assert (demo / "sources" / "ensora.sqlite3").is_file()
    assert not real_database.exists() and not real_sources.exists()
    monkeypatch.setenv("ENSORA_DEMO_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="empty directory"):
        create_app()


def test_real_local_profile_prefills_and_mirrors_to_sources(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("ENSORA_PAUSE_BACKGROUND_SYNC", "true")
    config = tmp_path / "config"
    p1 = Settings(config_dir=config, local_data_dir=tmp_path / "sources",
                  ensai_profile_dir=config / "sessions", study_year="2A",
                  academic_year="2026-2027", specialization="Track")
    with TestClient(create_app("sqlite://", enable_sources=True, p1_settings=p1,
                               p1_components=Components())) as client:
        assert client.get("/api/v1/runtime-mode").json() == {"mode": "real"}
        original = client.get("/api/v1/profile").json()
        assert original["cohort"] == "2A" and original["academic_year"] == "2026-2027"
        change = client.patch("/api/v1/profile", json={
            "expected_revision": original["revision"], "cohort": "3A",
            "academic_year": "2027-2028", "track": "ID", "display_name": "Student",
        })
        assert change.is_success
        token = (p1.local_data_dir / "api-token").read_text()
        profile = client.get("/api/v1/sources/profile", headers={"Authorization": f"Bearer {token}"}).json()
        assert profile["study_year"] == "3A"
        assert profile["academic_year"] == "2027-2028"
        assert profile["specialization"] == "ID"

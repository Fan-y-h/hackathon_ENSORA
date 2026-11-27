from fastapi.testclient import TestClient

from tests.conftest import make_app

WINDOWS = [
    {"weekday": 0, "start": "18:00", "end": "21:00"},
    {"weekday": 2, "start": "14:00", "end": "18:00"},
]


def test_default_profile_is_created_lazily(client):
    body = client.get("/api/v1/profile").json()
    assert body["cohort"] is None
    assert body["timezone"] == "Europe/Paris" and body["locale"] == "fr"
    assert body["preferences"]["work_windows"] == []
    assert body["preferences"]["provider_policy"] == {
        "external_allowed": False,
        "preferred_mode": "mock",
    }
    assert body["onboarding_complete"] is False


def test_patch_availability_preferences_and_goals(client):
    r = client.patch(
        "/api/v1/profile",
        json={
            "expected_revision": 1,
            "cohort": "2A",
            "track": "Data Science",
            "academic_year": "2026-2027",
            "goals": [{"label": "10 h / semaine", "kind": "study_time", "target_minutes_per_week": 600}],
            "preferences": {
                "work_windows": list(reversed(WINDOWS)),
                "daily_max_min": 180,
                "weekly_max_min": 720,
                "preferred_formats": ["quiz", "flashcards", "quiz"],
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == 2
    assert body["preferences"]["revision"] == 2
    assert body["preferences"]["work_windows"] == WINDOWS  # stored sorted
    assert body["preferences"]["preferred_formats"] == ["quiz", "flashcards"]
    assert body["onboarding_complete"] is True
    goal = body["goals"][0]
    assert goal["id"] and goal["target_minutes_per_week"] == 600

    # Updating a goal keeps its id; a profile-only change does not bump preferences.
    r = client.patch(
        "/api/v1/profile",
        json={"goals": [goal | {"label": "12 h / semaine", "target_minutes_per_week": 720}]},
    )
    assert r.json()["goals"][0]["id"] == goal["id"]
    assert r.json()["revision"] == 3 and r.json()["preferences"]["revision"] == 2


def test_noop_patch_does_not_bump_revision(client):
    client.patch("/api/v1/profile", json={"cohort": "1A"})
    r = client.patch("/api/v1/profile", json={"cohort": "1A"})
    assert r.json()["revision"] == 2


def test_stale_revision_returns_409_envelope(client):
    client.patch("/api/v1/profile", json={"cohort": "1A"})
    r = client.patch("/api/v1/profile", json={"expected_revision": 1, "cohort": "3A"})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "stale_revision" and err["request_id"]
    assert err["details"] == {"expected_revision": 1, "current_revision": 2}


def test_validation_errors(client):
    cases = [
        {"cohort": "4A"},
        {"timezone": "Mars/Olympus"},
        {"academic_year": "2026-2028"},
        {"locale": None},
        {"unknown_field": 1},
        {"preferences": {"work_windows": [{"weekday": 0, "start": "10:00", "end": "09:00"}]}},
        {"preferences": {"work_windows": [{"weekday": 7, "start": "09:00", "end": "10:00"}]}},
        {"preferences": {"provider_policy": {"external_allowed": False, "preferred_mode": "external"}}},
        {"preferences": {"daily_max_min": None}},
    ]
    for payload in cases:
        r = client.patch("/api/v1/profile", json=payload)
        assert r.status_code == 422, (payload, r.text)
        assert r.json()["error"]["code"] == "validation_error"


def test_cross_field_preferences_are_checked_on_merged_state(client):
    client.patch("/api/v1/profile", json={"preferences": {"weekly_max_min": 300}})
    r = client.patch("/api/v1/profile", json={"preferences": {"daily_max_min": 400}})
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["code"] == "daily_gt_weekly"

    overlap = [
        {"weekday": 0, "start": "09:00", "end": "11:00"},
        {"weekday": 0, "start": "10:30", "end": "12:00"},
    ]
    r = client.patch("/api/v1/profile", json={"preferences": {"work_windows": overlap}})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "overlap"


def test_profiles_are_isolated_per_user(client, as_bob):
    client.patch("/api/v1/profile", json={"cohort": "3A"})
    assert client.get("/api/v1/profile", headers=as_bob).json()["cohort"] is None


def test_profile_persists_across_restart(tmp_path):
    url = f"sqlite:///{(tmp_path / 'ensora.db').as_posix()}"
    first = TestClient(make_app(url))
    first.patch("/api/v1/profile", json={"cohort": "2A", "preferences": {"work_windows": WINDOWS}})
    first.app.state.db.engine.dispose()

    second = TestClient(make_app(url))
    body = second.get("/api/v1/profile").json()
    assert body["cohort"] == "2A" and body["preferences"]["work_windows"] == WINDOWS
    second.app.state.db.engine.dispose()


def test_root_redirects_to_api_docs(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"
    assert client.get("/docs").status_code == 200

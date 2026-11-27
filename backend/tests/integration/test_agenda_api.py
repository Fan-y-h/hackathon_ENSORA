from sqlalchemy import select

from app.core.events import OutboxEvent
from app.modules.planner.schemas import EventImportItem
from app.modules.planner.service import AgendaService
from tests.conftest import FixedClock

API = "/api/v1"
WEEK = {"start": "2026-11-30T00:00:00+01:00", "end": "2026-12-07T00:00:00+01:00"}
EXAM = {
    "type": "exam",
    "title": "Examen Statistique",
    "start_at": "2026-12-03T08:30:00+01:00",
    "end_at": "2026-12-03T10:30:00+01:00",
    "room": "Amphi B",
}


def outbox_types(client) -> list[str]:
    with client.app.state.db.session_factory() as s:
        return [e.type for e in s.scalars(select(OutboxEvent).order_by(OutboxEvent.occurred_at))]


def import_items(client, items, user="alice"):
    """Simulate a connector/fixture import through the service (C1 owns POST /sync)."""
    with client.app.state.db.session_factory() as s:
        from app.core.identity import ensure_user

        uid = ensure_user(s, f"{user}@test.local", user).id
        res = AgendaService(s, FixedClock()).import_events(
            uid, "fixture", [EventImportItem.model_validate(i) for i in items]
        )
        s.commit()
        return res


# --- events & assessments ---------------------------------------------------------------
def test_create_exam_creates_assessment_and_normalizes_to_utc(client):
    r = client.post(f"{API}/events", json=EXAM)
    assert r.status_code == 201, r.text
    ev = r.json()
    assert ev["start_at"] == "2026-12-03T07:30:00Z" and ev["end_at"] == "2026-12-03T09:30:00Z"
    assert ev["busy"] is True and ev["read_only"] is False and ev["source"] == "manual"
    assert ev["assessment_id"]

    items = client.get(f"{API}/assessments").json()["items"]
    assert len(items) == 1
    a = items[0]
    assert a["id"] == ev["assessment_id"] and a["title"] == "Examen Statistique"
    assert a["deadline_at"] == "2026-12-03T07:30:00Z" and a["duration_min"] == 120
    assert a["scope_status"] == "unconfirmed" and a["status"] == "active"
    assert outbox_types(client) == ["AssessmentChanged", "EventChanged"]


def test_moving_or_cancelling_an_exam_updates_assessment_and_revision(client):
    ev = client.post(f"{API}/events", json=EXAM).json()
    rev0 = client.get(f"{API}/availability", params=WEEK).json()["input_revision"]

    r = client.patch(
        f"{API}/events/{ev['id']}",
        json={
            "expected_revision": 1,
            "start_at": "2026-12-04T13:00:00+01:00",
            "end_at": "2026-12-04T16:00:00+01:00",
        },
    )
    assert r.status_code == 200 and r.json()["revision"] == 2
    a = client.get(f"{API}/assessments").json()["items"][0]
    assert a["deadline_at"] == "2026-12-04T12:00:00Z" and a["duration_min"] == 180
    assert a["revision"] == 2
    assert client.get(f"{API}/availability", params=WEEK).json()["input_revision"] != rev0

    client.patch(f"{API}/events/{ev['id']}", json={"status": "cancelled"})
    assert client.get(f"{API}/assessments").json()["items"] == []
    cancelled = client.get(f"{API}/assessments", params={"status": "cancelled"}).json()["items"]
    assert cancelled[0]["status"] == "cancelled"
    # cancelled events are hidden by default
    assert client.get(f"{API}/events", params=WEEK).json()["items"] == []
    listed = client.get(f"{API}/events", params=WEEK | {"include_cancelled": True}).json()
    assert len(listed["items"]) == 1


def test_stale_event_patch_is_rejected(client):
    ev = client.post(f"{API}/events", json=EXAM).json()
    client.patch(f"{API}/events/{ev['id']}", json={"title": "v2"})
    r = client.patch(f"{API}/events/{ev['id']}", json={"expected_revision": 1, "title": "v3"})
    assert r.status_code == 409


def test_deadline_personal_and_all_day_events(client):
    d = client.post(
        f"{API}/events",
        json={
            "type": "deadline",
            "title": "Rendu projet",
            "start_at": "2026-12-04T23:59:00+01:00",
            "end_at": "2026-12-05T00:00:00+01:00",
        },
    ).json()
    assert d["busy"] is False and d["assessment_id"] is None
    client.post(
        f"{API}/events",
        json={"type": "personal", "title": "Week-end famille", "all_day_date": "2026-12-05"},
    )
    client.post(
        f"{API}/events",
        json={
            "type": "class",
            "title": "CM",
            "start_at": "2026-12-01T08:30:00+01:00",
            "end_at": "2026-12-01T10:00:00+01:00",
        },
    )
    titles = [e["title"] for e in client.get(f"{API}/events", params=WEEK).json()["items"]]
    assert titles == ["CM", "Rendu projet", "Week-end famille"]
    only_personal = client.get(f"{API}/events", params=WEEK | {"type": "personal"}).json()
    assert [e["title"] for e in only_personal["items"]] == ["Week-end famille"]


def test_switching_event_to_all_day_clears_times(client):
    ev = client.post(f"{API}/events", json=EXAM | {"type": "personal"}).json()
    r = client.patch(f"{API}/events/{ev['id']}", json={"all_day_date": "2026-12-03"})
    assert r.status_code == 200
    assert r.json()["start_at"] is None and r.json()["all_day_date"] == "2026-12-03"


def test_event_validation(client):
    bad = [
        EXAM | {"end_at": EXAM["start_at"]},
        EXAM | {"start_at": "2026-12-03T08:30:00"},  # naive datetime
        EXAM | {"all_day_date": "2026-12-03"},  # timed and all-day
        {"type": "exam", "title": "x"},  # no timing
        EXAM | {"type": "party"},
        EXAM | {"end_at": "2026-12-30T10:00:00+01:00"},  # > 14 days
        EXAM | {"source": "pamplemousse"},  # source is server-controlled
    ]
    for payload in bad:
        r = client.post(f"{API}/events", json=payload)
        assert r.status_code == 422, (payload, r.text)
    r = client.get(f"{API}/events", params={"start": WEEK["end"], "end": WEEK["start"]})
    assert r.status_code == 422


def test_pagination(client):
    for day in range(1, 6):
        client.post(
            f"{API}/events",
            json={
                "type": "class",
                "title": f"C{day}",
                "start_at": f"2026-12-0{day}T08:00:00+01:00",
                "end_at": f"2026-12-0{day}T09:00:00+01:00",
            },
        )
    page1 = client.get(f"{API}/events", params=WEEK | {"limit": 2}).json()
    page2 = client.get(f"{API}/events", params=WEEK | {"limit": 2, "cursor": page1["next_cursor"]}).json()
    page3 = client.get(f"{API}/events", params=WEEK | {"limit": 2, "cursor": page2["next_cursor"]}).json()
    titles = [e["title"] for p in (page1, page2, page3) for e in p["items"]]
    assert titles == ["C1", "C2", "C3", "C4", "C5"] and page3["next_cursor"] is None
    assert client.get(f"{API}/events", params=WEEK | {"cursor": "garbage"}).status_code == 422


def test_assessment_scope_confirmation_and_concepts(client):
    ev = client.post(f"{API}/events", json=EXAM).json()
    aid = ev["assessment_id"]
    r = client.patch(
        f"{API}/assessments/{aid}",
        json={
            "expected_revision": 1,
            "format": "written",
            "difficulty": "hard",
            "concept_ids": ["c-2", "c-1", "c-1"],
            "scope_status": "confirmed",
        },
    )
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["concept_ids"] == ["c-1", "c-2"] and a["scope_revision"] == 2
    assert a["scope_status"] == "confirmed" and a["revision"] == 2
    assert client.get(f"{API}/assessments", params={"scope_status": "confirmed"}).json()["items"]
    assert client.patch(f"{API}/assessments/{aid}", json={"format": "song"}).status_code == 422


# --- imported events ---------------------------------------------------------------------
IMPORTED = [
    {
        "external_id": "ppdp",
        "type": "presentation",
        "title": "PPDP soutenance",
        "start_at": "2026-12-02T14:00:00+01:00",
        "end_at": "2026-12-02T14:30:00+01:00",
        "confirmation_required": True,
    },
    {
        "external_id": "cm-1",
        "type": "class",
        "title": "CM Stat",
        "start_at": "2026-12-01T08:30:00+01:00",
        "end_at": "2026-12-01T10:30:00+01:00",
    },
]


def test_import_is_idempotent_and_read_only(client):
    first = import_items(client, IMPORTED)
    assert (first.created, first.updated, first.unchanged) == (2, 0, 0)
    second = import_items(client, IMPORTED)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 2)
    assert second.agenda_revision == first.agenda_revision

    events = client.get(f"{API}/events", params=WEEK).json()["items"]
    assert len(events) == 2
    ppdp = next(e for e in events if e["external_id"] == "ppdp")
    assert ppdp["read_only"] and ppdp["status"] == "tentative" and ppdp["confirmation_required"]

    assert client.patch(f"{API}/events/{ppdp['id']}", json={"title": "x"}).status_code == 403
    r = client.delete(f"{API}/events/{ppdp['id']}")
    assert r.status_code == 403 and r.json()["error"]["code"] == "imported_read_only"

    # A partial listing never deletes previously imported events.
    import_items(client, IMPORTED[:1])
    assert len(client.get(f"{API}/events", params=WEEK).json()["items"]) == 2


def test_ambiguous_slot_confirmation_flow(client):
    import_items(client, IMPORTED)
    ppdp = next(
        e for e in client.get(f"{API}/events", params=WEEK).json()["items"] if e["external_id"] == "ppdp"
    )
    avail = client.get(f"{API}/availability", params=WEEK).json()
    assert avail["pending_confirmations"] == 1 and "PENDING_CONFIRMATIONS" in avail["warnings"]
    assert client.get(f"{API}/assessments").json()["items"][0]["confirmation_required"] is True

    r = client.post(f"{API}/events/{ppdp['id']}/confirm", json={"decision": "confirm"})
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed" and r.json()["confirmation_required"] is False
    again = client.post(f"{API}/events/{ppdp['id']}/confirm", json={"decision": "confirm"})
    assert again.status_code == 409

    # Source moves the slot: event updated, confirmation requested again.
    moved = [IMPORTED[0] | {"start_at": "2026-12-02T15:00:00+01:00", "end_at": "2026-12-02T15:30:00+01:00"}]
    res = import_items(client, moved)
    assert res.updated == 1
    ev = client.get(f"{API}/events/{ppdp['id']}").json()
    assert ev["confirmation_required"] and ev["start_at"] == "2026-12-02T14:00:00Z"

    r = client.post(f"{API}/events/{ppdp['id']}/confirm", json={"decision": "reject"})
    assert r.json()["status"] == "cancelled"
    assert client.get(f"{API}/assessments").json()["items"] == []


# --- isolation ---------------------------------------------------------------------------
def test_users_cannot_see_or_modify_each_other(client, as_bob):
    ev = client.post(f"{API}/events", json=EXAM).json()
    task = client.post(f"{API}/tasks", json={"title": "secret"}).json()
    assert client.get(f"{API}/events", params=WEEK, headers=as_bob).json()["items"] == []
    assert client.get(f"{API}/assessments", headers=as_bob).json()["items"] == []
    assert client.get(f"{API}/tasks", headers=as_bob).json()["items"] == []
    assert client.get(f"{API}/events/{ev['id']}", headers=as_bob).status_code == 404
    assert client.patch(f"{API}/events/{ev['id']}", json={"title": "x"}, headers=as_bob).status_code == 404
    assert client.delete(f"{API}/events/{ev['id']}", headers=as_bob).status_code == 404
    assert (
        client.patch(f"{API}/assessments/{ev['assessment_id']}", json={"format": "oral"}, headers=as_bob)
        .status_code
        == 404
    )
    assert client.patch(f"{API}/tasks/{task['id']}", json={"title": "x"}, headers=as_bob).status_code == 404
    # Bob cannot attach a subtask under Alice's task either.
    r = client.post(f"{API}/tasks", json={"title": "x", "parent_task_id": task["id"]}, headers=as_bob)
    assert r.status_code == 404


# --- tasks -------------------------------------------------------------------------------
def test_task_lifecycle(client):
    t = client.post(
        f"{API}/tasks",
        json={"title": "Rapport ML", "deadline_at": "2026-12-04T23:59:00+01:00", "estimated_min": 240, "priority": 5},
    ).json()
    assert t["remaining_min"] == 240 and t["status"] == "todo" and t["overdue"] is False
    assert t["deadline_at"] == "2026-12-04T22:59:00Z" and t["splittable"] is True

    r = client.patch(f"{API}/tasks/{t['id']}", json={"expected_revision": 1, "remaining_min": 120, "status": "in_progress"})
    assert r.json()["remaining_min"] == 120 and r.json()["revision"] == 2
    r = client.patch(f"{API}/tasks/{t['id']}", json={"status": "done"})
    assert r.json()["remaining_min"] == 0
    assert client.patch(f"{API}/tasks/{t['id']}", json={"expected_revision": 1, "title": "x"}).status_code == 409
    assert client.patch(f"{API}/tasks/{t['id']}", json={"priority": 9}).status_code == 422


def test_task_overdue_sort_and_filters(client):
    client.post(f"{API}/tasks", json={"title": "late", "deadline_at": "2026-11-20T10:00:00+01:00"})
    client.post(f"{API}/tasks", json={"title": "no deadline"})
    client.post(f"{API}/tasks", json={"title": "soon-low", "deadline_at": "2026-12-01T10:00:00+01:00", "priority": 1})
    client.post(f"{API}/tasks", json={"title": "soon-high", "deadline_at": "2026-12-01T10:00:00+01:00", "priority": 5})
    items = client.get(f"{API}/tasks").json()["items"]
    assert [t["title"] for t in items] == ["late", "soon-high", "soon-low", "no deadline"]
    assert items[0]["overdue"] is True and items[1]["overdue"] is False
    due = client.get(f"{API}/tasks", params={"due_before": "2026-11-30T00:00:00Z"}).json()["items"]
    assert [t["title"] for t in due] == ["late"]
    client.patch(f"{API}/tasks/{items[0]['id']}", json={"status": "cancelled"})
    open_ = client.get(f"{API}/tasks", params=[("status", "todo"), ("status", "in_progress")]).json()
    assert len(open_["items"]) == 3


def test_subtasks_cycles_and_cascade_delete(client):
    parent = client.post(f"{API}/tasks", json={"title": "Projet"}).json()
    child = client.post(f"{API}/tasks", json={"title": "Étape 1", "parent_task_id": parent["id"]}).json()
    r = client.patch(f"{API}/tasks/{parent['id']}", json={"parent_task_id": child["id"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "task_cycle"
    assert client.patch(f"{API}/tasks/{parent['id']}", json={"parent_task_id": parent["id"]}).status_code == 422

    assert client.delete(f"{API}/tasks/{parent['id']}").status_code == 204
    assert client.get(f"{API}/tasks").json()["items"] == []
    assert "TaskChanged" in outbox_types(client)


# --- availability ------------------------------------------------------------------------
def test_availability_combines_profile_and_agenda(client):
    client.patch(
        f"{API}/profile",
        json={
            "preferences": {
                "work_windows": [{"weekday": 1, "start": "14:00", "end": "19:00"}],
                "rest_windows": [{"weekday": 1, "start": "16:00", "end": "16:15"}],
                "daily_max_min": 120,
            }
        },
    )
    client.post(
        f"{API}/events",
        json={
            "type": "personal",
            "title": "Sport",
            "start_at": "2026-12-01T17:30:00+01:00",
            "end_at": "2026-12-01T18:40:00+01:00",
        },
    )
    client.post(  # deadlines do not block time
        f"{API}/events",
        json={
            "type": "deadline",
            "title": "Rendu",
            "start_at": "2026-12-01T15:00:00+01:00",
            "end_at": "2026-12-01T15:01:00+01:00",
        },
    )
    body = client.get(f"{API}/availability", params=WEEK).json()
    assert body["timezone"] == "Europe/Paris"
    assert [(f["start_at"], f["end_at"]) for f in body["free"]] == [
        ("2026-12-01T13:00:00Z", "2026-12-01T15:00:00Z"),
        ("2026-12-01T15:15:00Z", "2026-12-01T16:30:00Z"),
    ]  # 17:40–18:00Z is 20 min < min session 25 → dropped
    tuesday = next(d for d in body["days"] if d["date"] == "2026-12-01")
    assert tuesday["free_min"] == 195 and tuesday["plannable_min"] == 120
    assert len(body["busy"]) == 1 and body["warnings"] == []
    assert body["input_revision"].startswith("agenda:2;prefs:2")


def test_availability_excludes_past_and_validates_range(client):
    client.patch(f"{API}/profile", json={"preferences": {"work_windows": [{"weekday": 6, "start": "10:00", "end": "18:00"}]}})
    # "Now" is Sunday 29 Nov 13:00 Paris → only 13:00–18:00 remains that day.
    body = client.get(
        f"{API}/availability", params={"start": "2026-11-29T00:00:00+01:00", "end": "2026-11-30T00:00:00+01:00"}
    ).json()
    assert body["free"] == [{"start_at": "2026-11-29T12:00:00Z", "end_at": "2026-11-29T17:00:00Z", "minutes": 300}]
    r = client.get(f"{API}/availability", params={"start": "2026-11-01T00:00:00Z", "end": "2026-12-15T00:00:00Z"})
    assert r.status_code == 422

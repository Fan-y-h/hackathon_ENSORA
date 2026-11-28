"""Mapping of P1 calendar events to agenda import items (agenda bridge)."""

from app.modules.planner.source_bridge import course_code, to_import_item

SLOT = {
    "kind": "event",
    "starts_at": "2026-12-01T08:00:00+00:00",
    "ends_at": "2026-12-01T10:00:00+00:00",
}


def test_course_code_is_extracted_from_ensai_titles():
    assert course_code("Econométrie et inférence causale 2AECO02 - Marion") == "2AECO02"
    assert course_code("Anglais 2ahum01 - Charles") == "2AHUM01"
    assert course_code("Sport", None) is None
    assert course_code("X12AECO02") is None  # not a standalone code


def test_pamplemousse_slots_become_courses_of_their_subject():
    item = to_import_item("p1", {**SLOT, "title": "Econométrie 2AECO02 - M", "event_type": "other"}, "pamplemousse")
    assert item.type == "class" and item.subject_id == "2AECO02"


def test_explicit_subject_and_types_are_kept():
    item = to_import_item("p2", {**SLOT, "title": "Partiel 2AECO02", "event_type": "exam", "subject_id": "eco"}, "pamplemousse")
    assert item.type == "exam" and item.subject_id == "eco"


def test_other_sources_are_not_reinterpreted():
    item = to_import_item("g1", {**SLOT, "title": "Rendez-vous 2AECO02", "event_type": "other"}, "google_calendar")
    assert item.type == "other" and item.subject_id is None


def test_cancelled_and_incomplete_events_are_skipped():
    assert to_import_item("c", {**SLOT, "title": "x", "status": "cancelled"}, "pamplemousse") is None
    assert to_import_item("n", {"kind": "event", "title": "x", "starts_at": SLOT["starts_at"]}, "pamplemousse") is None

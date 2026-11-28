from app.modules.catalog.service import _read_p1_subjects_and_docs
from backend.app.core.database import LOCAL_USER, connections, consents, documents, metadata, open_database, records, users


def test_catalog_uses_consented_current_subjects_and_joins_canonical_documents(tmp_path):
    engine = open_database(tmp_path / "catalog.db")
    metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(users.insert().values(id=LOCAL_USER, profile={}, preferences={}, onboarded=True))
        for source in ("pamplemousse", "moodle"):
            db.execute(connections.insert().values(id=source, user_id=LOCAL_USER, source=source, status="CONNECTED"))
            db.execute(consents.insert().values(
                id=f"{source}:read", connection_id=source, purpose="read", granted=True, recorded_at="2026-09-01",
            ))
        db.execute(records.insert(), [
            {"id": "current", "connection_id": "pamplemousse", "external_id": "academic:new", "kind": "subject",
             "payload": {"code": "CODE", "title": "Current", "provenance": {"academic_year": "2026-2027"}}},
            {"id": "old", "connection_id": "pamplemousse", "external_id": "academic:old", "kind": "subject",
             "payload": {"code": "CODE", "title": "Old", "provenance": {"academic_year": "2025-2026"}}},
            {"id": "moodle", "connection_id": "moodle", "external_id": "moodle:course:1", "kind": "subject",
             "payload": {"title": "Equivalent Moodle title", "aliases": ["academic:new"]}},
        ])
        for doc_id, state, subject, version in (
            ("current-first", "active", "academic:new", "v1"),
            ("current-legacy", "active", "CODE", "v2"),
            ("revoked", "revoked", "academic:new", "v3"),
            ("not-downloaded", "active", "academic:new", None),
            ("historical", "active", "academic:old", "v4"),
        ):
            db.execute(documents.insert().values(
                id=doc_id, connection_id="moodle", external_id=doc_id, active_version_id=version,
                metadata_json={"title": doc_id, "subject_id": subject, "access_status": state},
            ))

        for doc_id, manual in (("wrong-chapter-match", False), ("manual-subject", True)):
            db.execute(documents.insert().values(
                id=doc_id, connection_id="moodle", external_id=doc_id, active_version_id=f"{doc_id}:version",
                metadata_json={
                    "title": "An unrelated chapter title", "course_id": "1", "subject_id": "academic:old",
                    "match": {"subject_id": "academic:old", "confirmed": True},
                    "user_override": {"subject_id": "academic:old"} if manual else {},
                },
            ))

    subjects, docs, aliases = _read_p1_subjects_and_docs(engine, "2026-2027")
    assert list(subjects) == ["academic:new"]
    assert aliases["CODE"] == aliases["moodle:course:1"] == "academic:new"
    assert {doc.id for doc in docs["academic:new"]} == {"current-first", "current-legacy", "wrong-chapter-match"}

    with engine.begin() as db:
        db.execute(consents.insert().values(
            id="moodle:revocation", connection_id="moodle", purpose="read", granted=False, recorded_at="2026-09-02",
        ))
    subjects, docs, aliases = _read_p1_subjects_and_docs(engine, "2026-2027")
    assert list(subjects) == ["academic:new"]
    assert not docs
    assert "moodle:course:1" not in aliases

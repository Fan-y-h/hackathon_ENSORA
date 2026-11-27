"""Read-only aggregate reporting. Never return document, grade or event values."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ..contracts.sources import SourceKind

METRICS = (
    "courses_discovered", "courses_traversed", "courses_with_processed_resources", "sections_discovered",
    "sections_traversed", "folders_traversed", "resources_discovered", "resources_excluded", "pages_visited",
    "timetable_events", "enrolled_subjects", "teaching_units", "semester_associations",
    "subject_coefficients", "ects_values", "available_grades", "assessment_records", "aggregate_grades",
    "missing_or_ambiguous_count", "academic_year_unknown",
    "documents_discovered", "documents_downloaded", "download_failures", "documents_parsed", "documents_partially_parsed",
    "documents_unsupported", "documents_persisted", "documents_excluded", "parsing_failures",
    "chunks_generated", "announcements_discovered", "assignments_discovered", "assessments_persisted",
    "existing_versions_reused", "busy_intervals_written", "intervals_updated", "intervals_cancelled",
)
SAFE_CODES = frozenset({
    "source_warning", "source_listing_error", "record_conversion_error", "invalid_record_payload",
    "incomplete_event_interval", "record_failure", "source_failure", "calendar_snapshot_scope",
    "academic_year_unknown", "calendar_window_unverified", "discovery_coverage_unknown", "parse_warning", "scanned_pages_without_ocr", "ocr_unavailable",
})
FORMATS = {
    "application/pdf": "pdf",
    "text/html": "html",
    "text/markdown": "markdown",
    "text/plain": "text",
    "text/csv": "csv",
    "application/x-ipynb+json": "notebook",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}
KINDS = (
    "academic_year", "semester", "teaching_unit", "subject", "assessment", "assessment_weight",
    "coefficient", "grade", "event", "chapter", "concept", "specialization",
)


def audit_database(path: Path, job_id: str | None = None) -> dict:
    if not path.is_file():
        return {"database": "missing", "coverage": "unknown"}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        result = {"database": "present", "cumulative": {}, "run": {}}
        for row in db.execute("SELECT id, source, cursor IS NOT NULL AS checkpoint, last_success_at FROM connections"):
            source = SourceKind(row["source"])
            base = source_totals(db, row["id"])
            base.update(checkpoint_present=bool(row["checkpoint"]), last_success_at=row["last_success_at"])
            result["cumulative"][source.value] = base
        result["totals"] = {key: sum(item[key] for item in result["cumulative"].values()) for key in (
            "documents_persisted", "document_versions", "available_chunks", "associated_documents",
            "unresolved_documents",
        )}
        result["totals"]["manual_availability_intervals"] = db.execute("SELECT COUNT(*) FROM availability").fetchone()[0]
        if job_id:
            row = db.execute("SELECT * FROM sync_runs WHERE id = ?", (job_id,)).fetchone()
        else:
            row = db.execute("SELECT * FROM sync_runs ORDER BY started_at DESC, id DESC LIMIT 1").fetchone()
        if row:
            result["run"] = run_summary(row)
        elif job_id:
            result["run"] = {"status": "not_found"}
        return result


def source_totals(db, identifier):
    doc = db.execute("""
        SELECT COUNT(*) AS documents_persisted,
               COUNT(CASE WHEN COALESCE(json_extract(metadata_json, '$.access_status'), 'active') = 'active'
                           AND active_version_id IS NOT NULL THEN 1 END) AS active_documents,
               COUNT(CASE WHEN COALESCE(json_extract(metadata_json, '$.access_status'), 'active') != 'active'
                          THEN 1 END) AS excluded_documents,
               COUNT(CASE WHEN json_extract(metadata_json, '$.subject_id') IS NOT NULL
                          THEN 1 END) AS associated_documents,
               COUNT(CASE WHEN json_extract(metadata_json, '$.subject_id') IS NULL
                          THEN 1 END) AS unresolved_documents,
               COUNT(DISTINCT CASE WHEN active_version_id IS NOT NULL
                                   THEN json_extract(metadata_json, '$.course_id') END) AS courses_with_documents,
               COUNT(CASE WHEN json_extract(metadata_json, '$.resource_kind') = 'announcement'
                          THEN 1 END) AS announcements
        FROM documents WHERE connection_id = ?
    """, (identifier,)).fetchone()
    result = dict(doc)
    result["document_versions"], result["duplicate_blob_references"] = db.execute("""
        SELECT COUNT(*), COUNT(*) - COUNT(DISTINCT v.sha256)
        FROM document_versions v JOIN documents d ON d.id = v.document_id WHERE d.connection_id = ?
    """, (identifier,)).fetchone()
    result["historical_versions"] = db.execute("""
        SELECT COUNT(*) FROM document_versions v JOIN documents d ON d.id = v.document_id
        WHERE d.connection_id = ? AND v.id IS NOT d.active_version_id
    """, (identifier,)).fetchone()[0]
    result["available_chunks"] = db.execute("""
        SELECT COUNT(*) FROM chunks c JOIN documents d ON d.active_version_id = c.version_id
        WHERE d.connection_id = ? AND COALESCE(json_extract(d.metadata_json, '$.access_status'), 'active') = 'active'
    """, (identifier,)).fetchone()[0]
    result["parsing_status"] = dict.fromkeys(("ready", "partial", "unsupported", "failed"), 0)
    result["formats"] = {}
    for row in db.execute("""
        SELECT v.status, v.mime_type, COUNT(*) AS n FROM document_versions v JOIN documents d ON d.id = v.document_id
        WHERE d.connection_id = ? GROUP BY v.status, v.mime_type
    """, (identifier,)):
        if row["status"] in result["parsing_status"]:
            result["parsing_status"][row["status"]] += row["n"]
        format_name = FORMATS.get(row["mime_type"], "other")
        result["formats"][format_name] = result["formats"].get(format_name, 0) + row["n"]
    result["academic_entities"] = dict.fromkeys(KINDS, 0)
    for kind, total in db.execute("SELECT kind, COUNT(*) FROM source_records WHERE connection_id = ? GROUP BY kind",
                                  (identifier,)):
        if kind in result["academic_entities"]:
            result["academic_entities"][kind] = total
    result.update(dict(db.execute("""
        SELECT COUNT(DISTINCT CASE WHEN r.kind = 'coefficient' AND p.kind = 'subject'
                  AND json_extract(r.payload, '$.weight') IS NOT NULL THEN p.id END) AS subjects_with_coefficients,
               COUNT(CASE WHEN r.kind = 'subject' AND json_extract(r.payload, '$.ects') IS NOT NULL
                          THEN 1 END) AS subjects_with_ects,
               COUNT(CASE WHEN r.kind = 'grade' AND json_extract(r.payload, '$.value') IS NOT NULL
                          THEN 1 END) AS available_grades,
               COUNT(CASE WHEN r.kind = 'assessment' AND
                          COALESCE(json_extract(r.payload, '$.assessment_kind'), json_extract(r.payload, '$.format')) =
                          'assignment' THEN 1 END) AS assignments,
               COUNT(CASE WHEN json_extract(r.payload, '$.record_status') IN ('missing', 'ambiguous')
                          OR json_extract(r.payload, '$.confirmation_required') = 1 THEN 1 END) AS incomplete_or_ambiguous,
               COUNT(CASE WHEN r.kind = 'event' AND json_extract(r.payload, '$.status') != 'cancelled'
                          AND COALESCE(json_extract(r.payload, '$.busy'), 1) = 1 THEN 1 END) AS busy_events,
               COUNT(CASE WHEN r.kind = 'event' AND json_extract(r.payload, '$.status') = 'cancelled'
                          THEN 1 END) AS cancelled_events
        FROM source_records r LEFT JOIN source_records p ON p.id = r.parent_id WHERE r.connection_id = ?
    """, (identifier,)).fetchone()))
    return result


def run_summary(row):
    reports = {}
    for source, report in json.loads(row["report"]).items():
        if source not in {kind.value for kind in SourceKind}:
            continue
        metrics = report.get("metrics", {})
        reports[source] = {key: value if isinstance(value := metrics.get(key), int) else None for key in METRICS}
        for kind in ("error_codes", "warning_codes"):
            reports[source][kind] = sorted({code if code in SAFE_CODES else "other" for code in report.get(kind, [])})
    scope = json.loads(row["scope"])
    return {"id": row["id"], "status": row["status"], "started_at": row["started_at"],
            "finished_at": row["finished_at"], "sources": reports,
            "requested_calendar_range": [scope["window_start"], scope["window_end"]]
            if scope.get("source") == SourceKind.GOOGLE_CALENDAR and scope.get("window_start") else None}


def audit_api(base: str, token_path: Path) -> dict:
    from urllib.parse import urlsplit
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    parsed = urlsplit(base)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1")
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("Use a loopback HTTP base URL")

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    token = token_path.read_text(encoding="ascii").strip()
    opener = build_opener(NoRedirect())

    def fetch(path):
        request = Request(base.rstrip("/") + "/api/v1" + path, headers={"Authorization": f"Bearer {token}"})
        with opener.open(request, timeout=10) as response:
            return json.load(response)

    entries = {}
    for endpoint in ("documents", "subjects", "teaching-units", "assessments", "coefficients",
                     "assessment-weights", "grades", "events", "availability"):
        response = fetch(f"/{endpoint}?limit=50")
        entries[endpoint] = {"returned": len(response["items"]), "has_more": response["next_cursor"] is not None}
        if endpoint == "documents" and response["items"]:
            sample = response["items"][0]
            version = sample.get("active_version_id")
            if version:
                chunks = fetch(f"/documents/{sample['id']}/versions/{version}/chunks")
                entries[endpoint]["first_document_chunks"] = len(chunks["items"])
    return entries

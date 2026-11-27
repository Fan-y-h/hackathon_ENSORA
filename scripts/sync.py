"""Run an explicitly requested synchronization or local academic import."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep

if __package__:
    from .bootstrap import configure, use_project_python
else:
    from bootstrap import configure, use_project_python


PROGRESS_METRICS = (
    "courses_discovered", "courses_traversed", "sections_discovered", "sections_traversed",
    "folders_traversed", "resources_discovered", "resources_excluded", "pages_visited",
    "documents_discovered", "documents_downloaded", "download_failures", "documents_parsed",
    "documents_partially_parsed", "documents_unsupported", "documents_persisted", "documents_excluded",
    "parsing_failures", "chunks_generated", "existing_versions_reused", "timetable_events",
    "enrolled_subjects", "teaching_units", "subject_coefficients", "ects_values", "available_grades",
    "assessment_records", "aggregate_grades", "missing_or_ambiguous_count", "academic_year_unknown",
    "busy_intervals_written", "intervals_updated", "intervals_cancelled",
)


def progress_snapshot(reports):
    """Return only allowlisted aggregate counts for CLI progress output."""
    snapshot = {}
    for source, report in reports.items():
        item = {"status": report.get("status")}
        for name in ("documents", "records", "unchanged", "errors"):
            value = report.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                item[name] = value
        metrics = report.get("metrics")
        if isinstance(metrics, dict):
            selected = {
                name: value for name, value in metrics.items()
                if name in PROGRESS_METRICS and isinstance(value, int) and not isinstance(value, bool)
            }
            if selected:
                item["metrics"] = selected
        snapshot[source] = item
    return snapshot


def wait_for_job(runtime, job_id):
    from sqlalchemy import select
    from backend.app.core.database import runs
    last_progress_at = 0.0
    try:
        while True:
            with runtime.engine.connect() as db:
                item = db.execute(select(runs.c.status, runs.c.report).where(runs.c.id == job_id)).one()
            current = monotonic()
            if item.status in ("queued", "running") and current - last_progress_at >= 10:
                print(json.dumps({
                    "id": job_id,
                    "status": item.status,
                    "progress": progress_snapshot(item.report),
                }, sort_keys=True), flush=True)
                last_progress_at = current
            if item.status not in ("queued", "running"):
                print(json.dumps({"id": job_id, "status": item.status, "report": item.report}, indent=2))
                for source, report in item.report.items():
                    if report.get("next_action") == "reauthorize":
                        print(f"Run python3 scripts/onboarding.py --source {source} --reauthorize, then "
                              f"python3 scripts/sync.py --resume {job_id} --source {source} --approve.",
                              file=sys.stderr)
                return item.status
            sleep(0.25)
    except KeyboardInterrupt:
        runtime.sync.cancel(job_id)
        raise


def main():
    settings = configure()
    from pydantic import TypeAdapter, ValidationError
    from backend.app.contracts.sources import Document, SourceKind, SourceRecord, SyncScope
    from backend.app.core.runtime import Runtime, RuntimeBusy
    from backend.app.core.settings import ProtectedImportPath
    from backend.app.core.sync import ResumeUnavailable

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", metavar="JOB_ID", help="Resume a source after reauthentication")
    parser.add_argument("--source", type=SourceKind, choices=list(SourceKind))
    parser.add_argument("--subject-id")
    parser.add_argument("--folder-id")
    parser.add_argument("--academic-year")
    parser.add_argument("--discover-only", action="store_true", help="List resources without downloading or persisting source data")
    parser.add_argument("--window-start", type=datetime.fromisoformat)
    parser.add_argument("--window-end", type=datetime.fromisoformat)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--academic-json", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Validate academic JSON without writing records")
    parser.add_argument("--approve", action="store_true", help="Explicitly approve this synchronization")
    args = parser.parse_args()
    if not args.approve:
        parser.error("Use --approve to authorize this run.")
    if args.resume and (args.folder_id or args.subject_id or args.full or args.academic_json or args.dry_run
                        or args.academic_year or args.window_start or args.window_end or args.discover_only):
        parser.error("--resume reuses the original selection")
    if args.folder_id and not args.source:
        parser.error("--folder-id requires --source.")
    if args.source == SourceKind.LOCAL and not args.folder_id:
        parser.error("Select the local file or directory with --folder-id.")
    try:
        with Runtime(settings) as runtime:
            if args.resume:
                job = runtime.sync.resume(args.resume, args.source)
                return int(wait_for_job(runtime, job) != "succeeded")
            if args.academic_json:
                source = SourceKind.ACADEMIC_IMPORT
                if not runtime.consent.allowed(source, "read"):
                    parser.error("Enable academic import in onboarding first.")
                try:
                    path = runtime.validate_local_import_path(args.academic_json)
                    items = TypeAdapter(list[SourceRecord]).validate_json(path.read_bytes())
                except (OSError, ValidationError, ValueError):
                    parser.error("Invalid academic import file.")
                if any(isinstance(item, Document) for item in items):
                    parser.error("Academic import accepts structured records.")
                results = runtime.pipeline.import_academics(
                    runtime.connection_ids[source], items, dry_run=args.dry_run,
                )
                print(json.dumps({"results": results, "dry_run": args.dry_run}))
                return int(not results or any(item["status"] == "error" for item in results))
            if args.dry_run:
                parser.error("--dry-run requires --academic-json.")
            try:
                scope = SyncScope(source=args.source, subject_id=args.subject_id, folder_id=args.folder_id,
                                  full=args.full, academic_year=args.academic_year, discovery_only=args.discover_only,
                                  window_start=args.window_start, window_end=args.window_end)
            except ValidationError:
                parser.error("Invalid scope. Academic year requires Moodle or Pamplemousse, and Calendar windows need two timezone-aware endpoints.")
            return int(wait_for_job(runtime, runtime.sync.submit(scope)) != "succeeded")
    except (LookupError, ResumeUnavailable, ProtectedImportPath) as exc:
        parser.error(str(exc))
    except RuntimeBusy as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    use_project_python()
    raise SystemExit(main())

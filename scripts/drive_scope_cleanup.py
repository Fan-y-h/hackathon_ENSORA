"""Hide previously imported Global-folder Drive documents without deleting source data."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

if __package__:
    from .bootstrap import ROOT, use_project_python
else:
    from bootstrap import ROOT, use_project_python


def main():
    use_project_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--apply", action="store_true", help="Tombstone matching rows; never deletes files or records")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from backend.app.core.drive_scope import folder_scope
    from backend.app.core.settings import DATA

    data_dir = (args.data_dir or DATA).expanduser().absolute()
    database_path = data_dir / "ensora.sqlite3"
    if not database_path.is_file():
        parser.exit(1, "Local Ensora+ database does not exist.\n")

    lock_fd = None
    engine = None
    try:
        if args.apply:
            # Serialize against the running backend and every ETL writer.
            import fcntl
            lock_fd = os.open(data_dir / ".runtime.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.exit(2, "Stop Ensora+ and wait for active synchronization jobs before cleanup.\n")

        if args.apply:
            from sqlalchemy import select, update
            from backend.app.core.database import connections, documents, open_database
            from backend.app.core.database import LOCAL_USER
            from datetime import UTC, datetime

            engine = open_database(database_path)
            with engine.begin() as db:
                connection_id = db.scalar(select(connections.c.id).where(
                    connections.c.user_id == LOCAL_USER, connections.c.source == "ensora_drive"))
                rows = db.execute(select(documents.c.id, documents.c.metadata_json).where(
                    documents.c.connection_id == connection_id)).mappings().all() if connection_id else []
                candidates = [(row["id"], row["metadata_json"] or {}) for row in rows
                              if (row["metadata_json"] or {}).get("access_status", "active") == "active"
                              and folder_scope(((row["metadata_json"] or {}).get("provenance") or {}).get("folder")) == "global"]
                for document_id, metadata in candidates:
                    updated = {**metadata, "access_status": "tombstoned",
                               "scope_exclusion_reason": "global_folder_excluded",
                               "scope_exclusion_recorded_at": datetime.now(UTC).isoformat()}
                    db.execute(update(documents).where(documents.c.id == document_id).values(metadata_json=updated))
            print(json.dumps({"mode": "applied", "global_documents_tombstoned": len(candidates),
                              "document_versions_deleted": 0, "content_blobs_deleted": 0,
                              "academic_records_deleted": 0}, indent=2))
        else:
            with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as db:
                row = db.execute("SELECT id FROM connections WHERE source = 'ensora_drive' LIMIT 1").fetchone()
                rows = db.execute("SELECT metadata_json FROM documents WHERE connection_id = ?",
                                  (row[0],)).fetchall() if row else []
            candidates = 0
            for (raw,) in rows:
                metadata = json.loads(raw) if isinstance(raw, str) else raw
                if (metadata.get("access_status", "active") == "active"
                        and folder_scope((metadata.get("provenance") or {}).get("folder")) == "global"):
                    candidates += 1
            print(json.dumps({"mode": "dry_run", "global_documents_to_tombstone": candidates,
                              "document_versions_deleted": 0, "content_blobs_deleted": 0,
                              "academic_records_deleted": 0}, indent=2))
    except (OSError, sqlite3.Error, ValueError):
        parser.exit(1, "Drive scope cleanup failed. Check local database access and application state.\n")
    finally:
        if engine is not None:
            engine.dispose()
        if lock_fd is not None:
            os.close(lock_fd)


if __name__ == "__main__":
    main()

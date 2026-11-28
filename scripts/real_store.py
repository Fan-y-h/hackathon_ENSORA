"""Inspect provenance and prepare a private validation copy of the local Ensora stores."""

import argparse
import json
from pathlib import Path
import shutil
import sqlite3


def readonly(path: Path):
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def backup(source: Path, destination: Path) -> None:
    # Exclusive creation protects previous backups and keeps the database private.
    with destination.open("xb"):
        destination.chmod(0o600)
    with readonly(source) as original, sqlite3.connect(destination) as copy:
        original.backup(copy)
        if copy.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("SQLite backup failed its integrity check")


def inspect_planner(db: sqlite3.Connection) -> dict[str, int]:
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts = {}
    if "events" in tables:
        counts["fixture_events"] = db.execute("SELECT count(*) FROM events WHERE source='fixture'").fetchone()[0]
        counts["removable_fixture_events"] = db.execute(
            """SELECT count(*) FROM events WHERE source='fixture'
               AND NOT EXISTS (SELECT 1 FROM assessments WHERE assessments.event_id=events.id)"""
        ).fetchone()[0]
    if "academic_programs" in tables:
        counts["fixture_grade_programs"] = db.execute(
            "SELECT count(*) FROM academic_programs WHERE provenance='fixture'"
        ).fetchone()[0]
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--planner", type=Path, required=True)
    parser.add_argument("--source-store", type=Path)
    parser.add_argument("--copy-to", type=Path, help="Create a new private validation directory")
    parser.add_argument("--remove-unlinked-fixture-events", action="store_true")
    parser.add_argument("--backup-file", type=Path, help="Required for selective removal")
    args = parser.parse_args()

    with readonly(args.planner) as db:
        print(json.dumps(inspect_planner(db), sort_keys=True))

    if args.copy_to:
        if not args.source_store or args.remove_unlinked_fixture_events:
            parser.error("--copy-to requires --source-store and runs independently of removal")
        target = args.copy_to.expanduser().resolve()
        target.mkdir(mode=0o700)
        (target / "runtime").mkdir(mode=0o700)
        (target / "sources").mkdir(mode=0o700)
        backup(args.planner, target / "runtime" / "ensora.db")
        backup(args.source_store / "ensora.sqlite3", target / "sources" / "ensora.sqlite3")
        rag_database = args.source_store / "rag_index.sqlite3"
        if rag_database.is_file():
            backup(rag_database, target / "sources" / rag_database.name)
        workspace_rag = args.planner.with_name(f"{args.planner.stem}.rag.sqlite3")
        if workspace_rag.is_file():
            backup(workspace_rag, target / "runtime" / "ensora.rag.sqlite3")
        for name in ("blobs", "documents"):
            directory = args.source_store / name
            if directory.is_dir():
                shutil.copytree(directory, target / "sources" / name)
        return

    if args.remove_unlinked_fixture_events:
        if args.backup_file is None:
            parser.error("Selective removal requires --backup-file pointing to a new private file")
        # Run with the server stopped. Lock out writers until backup and deletion finish.
        with sqlite3.connect(args.planner) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            backup(args.planner, args.backup_file)
            removed = db.execute(
                """DELETE FROM events WHERE source='fixture'
                   AND NOT EXISTS (SELECT 1 FROM assessments WHERE assessments.event_id=events.id)"""
            ).rowcount
        print(json.dumps({"removed_fixture_events": removed}))


if __name__ == "__main__":
    main()

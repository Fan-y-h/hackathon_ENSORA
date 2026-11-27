"""Report stored connection status or explicitly validate authorized connections."""
import argparse

from bootstrap import configure, use_project_python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true", help="Validate read-consented sources with their adapters")
    parser.add_argument("--source", choices=("moodle", "pamplemousse", "ensora_drive", "google_calendar",
                                             "local", "academic_import"))
    parser.add_argument("--debug-auth", action="store_true", help="Emit redacted authentication state transitions")
    args = parser.parse_args()
    if args.debug_auth and not args.validate:
        parser.error("--debug-auth requires --validate")
    settings = configure()
    from sqlalchemy import select
    from backend.app.contracts.sources import SourceKind
    from backend.app.core.database import connections
    from backend.app.core.runtime import Runtime, RuntimeBusy

    if args.debug_auth:
        from backend.app.core.auth import enable_auth_diagnostics

        enable_auth_diagnostics()
    sources = [SourceKind(args.source)] if args.source else list(SourceKind)

    if not args.validate:
        from contextlib import closing
        import sqlite3

        path = settings.local_data_dir / "ensora.sqlite3"
        statuses = {}
        if path.is_file():
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
                if database.execute("SELECT 1 FROM sqlite_master WHERE name = 'connections'").fetchone():
                    statuses = dict(database.execute("SELECT source, status FROM connections"))
        for source in sources:
            print(source.value, statuses.get(source.value, "NOT_CONFIGURED"))
        return

    try:
        with Runtime(settings) as runtime:
            for source in sources:
                if runtime.consent.allowed(source, "read"):
                    result = runtime.auth.validate(source)
                    print(f"{source.value} {result.status.value} {result.reason or ''}".rstrip())
                    continue
                else:
                    with runtime.engine.connect() as db:
                        status = db.scalar(select(connections.c.status).where(
                            connections.c.id == runtime.connection_ids[source]))
                print(source.value, status)
    except RuntimeBusy as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    use_project_python()
    main()

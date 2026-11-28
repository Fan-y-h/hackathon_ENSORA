"""Print aggregate ETL evidence from the local database without opening source sessions."""
import argparse
import json
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
    parser.add_argument("--job-id", help="Report this job instead of the latest run")
    parser.add_argument("--api-base", help="Also check consent-gated P2/P3 endpoints on a running loopback server")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from backend.app.core.audit import audit_api, audit_database
    from backend.app.core.settings import Settings

    root = args.data_dir or Settings.load().local_data_dir
    try:
        root = root.expanduser()
        result = audit_database(root / "ensora.sqlite3", args.job_id)
        if args.api_base:
            result["api"] = audit_api(args.api_base, root / "api-token")
        print(json.dumps(result, indent=2))
    except (sqlite3.Error, OSError, ValueError):
        parser.exit(1, "Could not read the local data audit or API. Check the local database and server.\n")


if __name__ == "__main__":
    main()

"""R30: export all personal data and delete the account (explicit confirmation).

Generic over the schema: every table with a ``user_id`` column is scoped directly; child
tables without ``user_id`` (questions, intervals, versions, …) are reached through their
foreign keys. Secrets (password hashes) are never exported.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import Base

EXCLUDED_COLUMNS = {"password_hash"}
CONFIRMATION = "DELETE MY ACCOUNT"


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def export_user(db: Session, user_id: str, now: datetime) -> dict:
    tables: dict[str, list[dict]] = {}
    ids: dict[str, set] = {}
    pending = list(Base.metadata.sorted_tables)
    progress = True
    while pending and progress:
        progress = False
        for table in list(pending):
            if table.name == "users":
                rows = db.execute(select(table).where(table.c.id == user_id)).mappings().all()
            elif "user_id" in table.c:
                rows = db.execute(select(table).where(table.c.user_id == user_id)).mappings().all()
            else:
                fks = [(fk.parent, fk.column.table.name) for fk in table.foreign_keys]
                usable = [(col, ref) for col, ref in fks if ref in ids]
                if not usable:
                    if any(ref not in ids for _, ref in fks) and fks:
                        continue  # parent not exported yet
                    pending.remove(table)  # unrelated table (no user scope)
                    progress = True
                    continue
                col, ref = usable[0]
                rows = db.execute(select(table).where(col.in_(ids[ref]))).mappings().all() if ids[ref] else []
            pending.remove(table)
            progress = True
            pk = list(table.primary_key.columns)[0].name
            ids[table.name] = {r[pk] for r in rows}
            if rows:
                tables[table.name] = [
                    {k: _json(v) for k, v in r.items() if k not in EXCLUDED_COLUMNS} for r in rows
                ]
    return {"exported_at": now.isoformat(), "format": "ensora-export-v1", "user_id": user_id, "tables": tables}


def delete_user(db: Session, user_id: str) -> dict[str, int]:
    """Delete every user-scoped row (children first via FK cascade), then the user."""
    counts: dict[str, int] = {}
    for table in reversed(Base.metadata.sorted_tables):
        if "user_id" in table.c and table.name != "users":
            res = db.execute(delete(table).where(table.c.user_id == user_id))
            if res.rowcount:
                counts[table.name] = res.rowcount
    users = Base.metadata.tables["users"]
    counts["users"] = db.execute(delete(users).where(users.c.id == user_id)).rowcount
    return counts

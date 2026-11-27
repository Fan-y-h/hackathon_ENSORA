"""docs/P3_HEURISTICS.md must list the exact values used in code (risk 11 / O08)."""

import datetime  # noqa: F401  (used by eval of repr values)
import importlib
import re
from decimal import Decimal  # noqa: F401
from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "P3_HEURISTICS.md"
ROW = re.compile(r"^\| [^|]+ \| `([a-z_.]+)\.([A-Z_]+)` \| `(.+?)` \|")


def test_register_matches_code():
    rows = [m.groups() for line in DOC.read_text(encoding="utf-8").splitlines() if (m := ROW.match(line))]
    assert len(rows) >= 40
    for module, name, value in rows:
        actual = getattr(importlib.import_module(f"app.modules.{module}"), name)
        assert actual == eval(value), f"{module}.{name}: code={actual!r} doc={value}"

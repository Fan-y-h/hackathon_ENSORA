"""Contract guard (risk 5): the handed-off OpenAPI and endpoint inventory match the code."""

from tests.contract.handoff import INVENTORY_FILE, OPENAPI_FILE, build


def test_handoff_artifacts_are_current():
    spec, md = build()
    assert OPENAPI_FILE.exists() and INVENTORY_FILE.exists(), "run: python -m tests.contract.handoff"
    assert OPENAPI_FILE.read_text(encoding="utf-8") == spec, "OpenAPI changed: python -m tests.contract.handoff"
    assert INVENTORY_FILE.read_text(encoding="utf-8") == md, "inventory stale: python -m tests.contract.handoff"


def test_every_route_has_a_response_schema_and_error_envelope():
    import json

    spec = json.loads(OPENAPI_FILE.read_text(encoding="utf-8"))
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert op.get("responses"), f"{method} {path} has no responses"

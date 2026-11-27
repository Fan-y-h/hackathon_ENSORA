# Codex P1 remediation

**REMEDIATION_COMPLETE** for the Codex correction cycle. Individual findings are **FIXED_PENDING_REVIEW**. Gemini's second independent review has not started.

Full canonical synthetic command using the existing environment:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q backend/tests
```

**42 passed in 3.87 seconds.** This includes 29 Gemini tests and 13 Codex integration tests. The named regression cases below passed. Fixtures, fake adapters, and isolated SQLite databases were used. No live authentication or synchronization was performed.

Paths under core/, contracts/, and main.py are relative to backend/app/. Migration 0002 is backend/migrations/versions/0002_p1_integrity.py. Regression tests are in backend/tests/integration/test_p1.py, with shared cases named by their descriptive suffix.

| Finding | Validation | Status | Modified files | Correction | Synthetic regression | Actual result | Dependencies or limits |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CODEX-REV-001 | Runtime contention reproduced | FIXED_PENDING_REVIEW | core/runtime.py, scripts/check_connections.py, scripts/onboarding.py, scripts/start.py, scripts/sync.py | Single writer retained. Busy commands exit with actionable guidance. Status uses read-only SQLite and can run while the backend holds the lock. | runtime_lock_cleanup_and_consent | Passed | Write CLI commands require the runtime lock by design. |
| CODEX-REV-002 | Unsupported and failed parser cases reproduced | FIXED_PENDING_REVIEW | core/etl.py | Keep the previous ready version active, persist failed attempts, retry in place, preserve confirmed overrides. | recover_failed_version_and_enforce_ownership | Passed | None. |
| CODEX-REV-003 | Changing bytes previously changed upload identity | FIXED_PENDING_REVIEW | main.py | Use a stable title-based upload identity with an optional explicit identity for renames or collisions. | api | Passed | Optional upload identity is caller-managed. |
| CODEX-REV-004 | Calendar availability handoff absent | FIXED_PENDING_REVIEW | contracts/sources.py, main.py | Derive private busy intervals from authorized persisted Calendar records. Exclude cancellation and transparent slots. | api | Passed | Live Calendar remains unverified. |
| CODEX-REV-005 | Strict event DTO rejected ambiguity metadata | FIXED_PENDING_REVIEW | contracts/sources.py | Accept event type, teacher, confirmation, recurrence, timezone and busy metadata; accept ID-only cancellation. | api | Passed | Gemini's bridge maps the new fields. |
| CODEX-REV-006 | Cleanup exception escaped after withdrawal | FIXED_PENDING_REVIEW | core/auth.py, main.py | Keep local withdrawal effective, return a sanitized retryable response if external cleanup fails. | runtime_lock_cleanup_and_consent | Passed | Remote cleanup can require retry. |
| CODEX-REV-007 | OAuth filename defaults and aliases diverged | FIXED_PENDING_REVIEW | core/settings.py | Support canonical and legacy filenames, preserve explicit overrides and accept previously documented environment aliases. | configuration_and_setup | Passed | Existing runtime settings were preserved. |
| CODEX-REV-008 | Local frontend preflight failed | FIXED_PENDING_REVIEW | main.py, core/settings.py | Allow the configured frontend origin and explicit headers and methods. | api | Passed | Frontend browser integration remains a downstream task. |
| CODEX-REV-009 | Local scope and document access status gaps confirmed | FIXED_PENDING_REVIEW | core/sync.py, core/etl.py, contracts/sources.py, main.py | Exclude local roots from unscoped/automatic runs, require an explicit path, retain tombstones and gate read access. | api; durable_cursor_and_partial_diagnostics | Passed | Connectors must surface source-side deletions. |
| CODEX-REV-010 | Timestamp-only consent order could select a stale grant | FIXED_PENDING_REVIEW | core/auth.py, migration 0002 | Use SQLite insertion order for consent decisions and index connection and purpose. | runtime_lock_cleanup_and_consent | Passed | None. |
| CODEX-REV-011 | Active-version ownership, indexes and standalone Alembic missing | FIXED_PENDING_REVIEW | core/database.py, backend/alembic.ini, migrations/env.py, migration 0002 | Keep the historical migration snapshot immutable. Add indexed lookups and SQLite ownership triggers, wire standalone Alembic and target metadata. | recover_failed_version_and_enforce_ownership; upgrade_existing_database_and_standalone_config | Passed | SQLite triggers provide composite ownership enforcement. |
| CODEX-REV-012 | An invalid academic batch returned creation status | FIXED_PENDING_REVIEW | core/etl.py, main.py, scripts/sync.py | Return safe per-record codes and indexes, 422 for total failure and transactional dry-run. | api | Passed | Input still requires normalized parent-before-child order. |
| CODEX-REV-013 | Missing attachment handling confirmed | FIXED_PENDING_REVIEW | main.py | Return 503 for missing blobs and a sanitized filename. Preserve safe MIME types and serve other formats as binary attachments. | api | Passed | None. |
| CODEX-REV-014 | Random UUID pagination did not reflect chronology | FIXED_PENDING_REVIEW | main.py, migration 0002 | Use stable composite cursors for events, published grades and sync runs and insertion order for documents. | api | Passed | Events inserted into a previously viewed time range require a fresh listing. |
| CODEX-REV-015 | Repeated prompts and missing readiness checks confirmed | FIXED_PENDING_REVIEW | scripts/onboarding.py, core/auth.py, core/runtime.py, main.py | Restore consented connections before prompting, call Gemini readiness only for selected Google setup, confirm exact API subset, use the dedicated reauthorization operation. | configuration_and_setup | Passed | Developer OAuth client creation and live Google/ENSAI auth require human authorization. |
| CODEX-REV-016 | Keyring probe and exact-pin-only bootstrap parser missing | FIXED_PENDING_REVIEW | core/security.py, scripts/bootstrap.py, backend/pyproject.toml, scripts/onboarding.py | Probe the OS keyring, retain official browser login when unavailable and parse PEP 508 requirements. | configuration_and_setup; bootstrap_accepts_pep508_requirements | Passed | A supported OS keyring is needed only for optional credential storage. |


## Gemini bridge integration

Terminal resume cursors and safe diagnostics now drive persisted sync progress. A partial listing preserves the prior cursor for replay. Structured parser evidence reaches persisted chunks, parser warning codes are retained in the document metadata, and Gemini's offline concept extractor is available by exact version. Catalog matching resolves against currently read-consented persisted subjects. The test suite covers checkpoint replay and consent withdrawal for catalog matching. Exam/correction pairing remains an adapter helper; no automatic confirmed link is persisted.

Gemini's findings remain pending its independent review. Live source access and Google Cloud provisioning remain unverified by Codex.

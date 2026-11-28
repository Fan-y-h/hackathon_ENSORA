# Gemini Technical Code Review of Codex P1 Implementation

- **Reviewer**: Gemini (P1 Source Integration, Authentication & Document Processing Engineer)
- **Reviewed Scope**: Codex-owned P1 backend foundation on branch `feat/p1-data-engineering` (base commit `89ad3d8`):
  - `backend/app/contracts/` (`authentication.py`, `sources.py`, `sync.py`, `users.py`)
  - `backend/app/core/` (`auth.py`, `database.py`, `etl.py`, `runtime.py`, `security.py`, `settings.py`, `sync.py`)
  - `backend/app/main.py`
  - `backend/migrations/` (`env.py`, `versions/0001_p1.py`)
  - `backend/pyproject.toml`
  - `backend/tests/integration/test_p1.py`
  - `scripts/` (`bootstrap.py`, `check_connections.py`, `onboarding.py`, `start.py`, `sync.py`)
- **Review Date**: 2026-11-27
- **Verification Basis**: Full source inspection + non-destructive synthetic probes against isolated temporary directories + combined pytest execution (`PYTHONPATH=. .venv/bin/python -m pytest backend/tests -v` -> 24 passed). No Codex-owned files were modified.

---

## Executive Summary

Codex's P1 core architecture is concise, cleanly separated, and establishes strong security fundamentals (loopback-only FastAPI with constant-time bearer token validation, atomic `fsync` + `replace` blob writes, SQLite WAL + `PRAGMA foreign_keys=ON`, consent-gated API reads, `O_NOFOLLOW` private directories, and Pydantic `extra="forbid"` contracts).

However, source review and synthetic probing identified **16 confirmed defects/gaps** (0 CRITICAL, 8 HIGH, 7 MEDIUM, 1 LOW) affecting concurrent CLI/server usability, document versioning and user-override preservation, parser failure recovery, P1 Google Calendar availability persistence, Pamplemousse ambiguity contracts, OAuth credential filename alignment, frontend CORS, and disconnect error handling.

---

## Detailed Findings

### CODEX-REV-001 — `Runtime` exclusive lock crashes all CLI scripts while `scripts/start.py` is running
- **Severity**: `HIGH`
- **Affected**: `backend/app/core/runtime.py`, `Runtime.__enter__` (lines 41–52); `scripts/check_connections.py` (line 17); `scripts/sync.py` (line 46); `scripts/onboarding.py` (line 62)
- **Description & Evidence**:
  - `Runtime.__enter__` acquires a non-blocking exclusive lock on `local_data_dir / ".runtime.lock"` (`fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)`) for the entire lifetime of the FastAPI server (`scripts/start.py`).
  - Every CLI utility (`check_connections.py`, `sync.py`, `onboarding.py`) also instantiates `with Runtime(settings) as runtime:` directly.
  - **Synthetic probe confirmation**: Opening a second `Runtime(settings)` while one is active immediately raised an unhandled `BlockingIOError: [Errno 11] Resource temporarily unavailable`.
- **Technical Consequences**: While the backend server is running via `scripts/start.py`, users cannot run `scripts/check_connections.py`, `scripts/sync.py`, or `scripts/onboarding.py` — every CLI command crashes with a raw Python traceback.
- **Recommended Correction**:
  - Have CLI scripts detect an active server (e.g. via `api-token` + `GET /api/v1/health`) and delegate operations over the authenticated loopback HTTP API when the server is running; or catch `BlockingIOError` in `Runtime.__enter__` and raise a clean domain exception with actionable guidance, while using read-only SQLite access for `check_connections.py` (without `--validate`).
- **Resolution Status**: `OPEN`

---

### CODEX-REV-002 — `Pipeline._document` overwrites a `"ready"` active version with `"unsupported"`, erases prior `metadata_json` overrides, and never persists `status="failed"`
- **Severity**: `HIGH`
- **Affected**: `backend/app/core/etl.py`, `Pipeline._document` (lines 55–107)
- **Description & Evidence**:
  1. When `self.parser.parse(record, content)` returns `None` for a new version (corrupt/unsupported upload), lines 93–106 insert `status="unsupported"` **and** unconditionally update `documents.active_version_id = version_id`, clobbering the previous `"ready"` active version.
  2. When `self.parser.parse(record, content)` raises an exception, no `document_versions` row with `status="failed"` is written (making `DocumentVersion.status="failed"` in `sources.py:48` unreachable dead code), and the content blob written at line 67 remains orphaned on disk.
  3. On any new content hash (`version is None`), line 82 rebuilds `metadata = record.model_dump(mode="json")` from scratch instead of preserving confirmed user overrides from `existing["metadata_json"]`.
  - **Synthetic probe confirmation**: Re-ingesting `d1` with an unparseable v2 produced `PROBE3_ACTIVE_OVERWRITTEN: True status=unsupported` and `PROBE3_OVERRIDE_LOST: True`; parser crash left `PROBE3_VERSION_STATUSES_AFTER_CRASH: ['ready', 'unsupported']` (no `'failed'` row).
- **Technical Consequences**: Violates `docs/DATA_ENGINEERING.md` ("Never overwrite confirmed mappings", "preserve user edits") and `docs/RAG_ARCHITECTURE.md` ("Failed index publication retains previous valid version only if still authorized"). Corrupted re-uploads break existing readable documents and leave no parse-failure audit row.
- **Recommended Correction**:
  - Wrap `self.parser.parse(record, content)` in `try/except`: on exception, persist a `document_versions` row with `status="failed"` (storing a safe error code) without replacing a valid previous `active_version_id`.
  - Only advance `documents.active_version_id` when the new version has `status == "ready"` (or when `existing["active_version_id"] is None`).
  - Preserve confirmed/user-edited `subject_id`, `chapter_id`, and user override keys from `existing["metadata_json"]` before merging refreshed source metadata.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-003 — `POST /api/v1/documents/import` hashes content into `external_id`, breaking document versioning on updated re-uploads
- **Severity**: `HIGH`
- **Affected**: `backend/app/main.py`, `import_document` (lines 229–248)
- **Description & Evidence**:
  - Line 241 computes `identity = hashlib.sha256(title.encode() + b"\\0" + content).hexdigest()` (note also `b"\\0"` is two literal bytes `\` and `0`, not a NUL byte `b"\0"`).
  - Because `external_id` includes `content`, uploading an updated version of the same file (`notes.txt` v1 then v2) creates **two separate `documents` rows** instead of 1 document with 2 `document_versions`.
  - **Synthetic probe confirmation**: Uploading `notes.txt` with `b"v1 content"` then `b"v2 content"` yielded `PROBE4_UPLOAD_DOC_COUNT_FOR_SAME_FILENAME: 2`.
- **Technical Consequences**: Breaks `1:N` immutable document versioning (`docs/DATA_MODEL.md`) for local uploads, produces duplicate library entries on re-import, and prevents stale-artifact detection across versions.
- **Recommended Correction**:
  - Derive `external_id` from stable logical identity (e.g., an optional explicit `external_id`/`document_id` form parameter or normalized filename/folder path `f"local_upload:{title}"`), while keeping `remote_revision = sha256(content)`.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-004 — `PersonalAvailability` cannot flow from `GoogleCalendarConnector` through `SyncEngine` / `Pipeline`
- **Severity**: `HIGH`
- **Affected**: `backend/app/contracts/sources.py` (`PersonalAvailability` lines 100–110, `SourceRecord` union lines 184–188); `backend/app/core/etl.py` (`Pipeline.ingest` lines 50–53)
- **Description & Evidence**:
  - `PersonalAvailability` is defined as a contract and `availability` has a dedicated table and `GET/POST /api/v1/availability` endpoints, but `PersonalAvailability` has no `kind` discriminator, no `Provenance`, and is excluded from the `SourceRecord` union.
  - `Pipeline` only persists `Document` (to `documents`) and `SourceRecord` (to `source_records`); nothing in `SyncEngine` or `Pipeline` writes synced calendar availability into the `availability` table.
- **Technical Consequences**: Syncing Google Calendar populates `source_records` with raw `CalendarEvent` entries but leaves `GET /api/v1/availability` permanently empty unless manually POSTed, breaking the P1 privacy-preserving availability extraction and P3 Smart Planner read contract.
- **Recommended Correction**:
  - Either add a discriminated `AvailabilityBlock` variant (with `Provenance`, `starts_at`, `ends_at`, `available`, `status`) to `SourceRecord` and handle it in `Pipeline`, or have `Pipeline._record` derive/upsert `availability` rows (keyed by `connection_id` + `external_id`) whenever a `CalendarEvent` is ingested/cancelled.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-005 — `CalendarEvent` contract omits Pamplemousse ambiguity & timetable fields required by S2 / P3 (`extra="forbid"`)
- **Severity**: `HIGH`
- **Affected**: `backend/app/contracts/sources.py`, `CalendarEvent` (lines 74–98)
- **Description & Evidence**:
  - `CalendarEvent` inherits `Contract` (`ConfigDict(extra="forbid")`) and defines only `title`, `starts_at`, `ends_at`, `all_day_start`, `all_day_end`, `subject_id`, `status`, `room`, `group`.
  - It omits `event_type` (`class|td|tp|exam|presentation|deadline|personal|other`), `teacher`, `confirmation_required`, `confirmation_reason`, `recurrence_key`, and `original_timezone`, all specified in `docs/API_CONTRACTS.md` (lines 54–56) and `docs/DATA_ENGINEERING.md` (lines 12, 17).
  - **Synthetic probe confirmation**: Passing `confirmation_required=True` raised `ValidationError (extra_forbidden)`.
- **Technical Consequences**: Connectors cannot transmit Pamplemousse ambiguity warnings (e.g., red-colored `PPDP` event or `Sout.ID` presentation slots requiring user confirmation) or teacher/event-type metadata to P3 Agenda/Smart Planner.
- **Recommended Correction**:
  - Extend `CalendarEvent` with optional typed fields: `event_type: Literal["class", "td", "tp", "exam", "presentation", "deadline", "personal", "other"] = "other"`, `teacher: str | None = None`, `confirmation_required: bool = False`, `confirmation_reason: str | None = None`, `recurrence_key: str | None = None`, `original_timezone: str | None = None`.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-006 — `AuthenticationManager.disconnect()` leaks unhandled adapter exceptions (HTTP 500) after mutating DB consent/status
- **Severity**: `HIGH`
- **Affected**: `backend/app/core/auth.py`, `AuthenticationManager.disconnect` (lines 77–84); `backend/app/main.py`, `disconnect` (lines 173–176)
- **Description & Evidence**:
  - `disconnect()` writes `consent=False` and `status=NOT_CONFIGURED` to SQLite first, then calls `adapter.disconnect()` with no `try/except` block.
  - **Synthetic probe confirmation**: When an adapter's `disconnect()` raises `ProfileBusy`, `DELETE /api/v1/connections/{id}` crashes with an unhandled exception (HTTP 500 in uvicorn), leaving local consent revoked while the caller receives a server error.
- **Technical Consequences**: Disconnecting a source while Chromium is busy or while offline (e.g., remote Google revoke failure) returns HTTP 500 and inconsistent client state.
- **Recommended Correction**:
  - Catch `ProfileBusy` (return HTTP 409/503 before mutating or schedule cleanup cleanly) and catch non-fatal remote revocation errors in `disconnect()`, logging a safe warning while still guaranteeing local token/consent revocation succeeds deterministically.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-007 — `Settings` OAuth secret filenames diverge from mandated P1 private paths & lack alias support
- **Severity**: `HIGH`
- **Affected**: `backend/app/core/settings.py`, `Settings` (lines 33–34, 65–77)
- **Description & Evidence**:
  - `Settings` defaults to `~/.config/ensora-plus/secrets/google-oauth.json` and `google-token.json`, whereas the P1 specification explicitly mandates `~/.config/ensora-plus/secrets/google_credentials.json` and `google_token.json`.
  - Additionally, `Settings.load()` only maps exact uppercase field names (missing documented env names like `GOOGLE_CLOUD_PROJECT_ID`, `ENSORA_DRIVE_ROOT_FOLDER_ID`, `ENSAI_CAS_LOGIN_URL`).
- **Technical Consequences**: Without Gemini's compatibility shim in `codex_bridge.py`, users following the documented secret paths would hit `configuration_missing` errors.
- **Recommended Correction**:
  - Update defaults to `google_credentials.json` / `google_token.json` (with fallback check for `google-oauth.json` / `google-token.json` so existing developer setups remain preserved) and support documented env key aliases in `Settings.load()`.
- **Resolution Status**: `OPEN` (mitigated in Gemini's `codex_bridge.py`, awaiting alignment in `Settings`)

---

### CODEX-REV-008 — Missing `CORSMiddleware` blocks local React/Vite frontend (`FRONTEND_ORIGIN`)
- **Severity**: `HIGH`
- **Affected**: `backend/app/main.py`, `create_app` (lines 32–41)
- **Description & Evidence**:
  - `create_app()` does not register Starlette/FastAPI `CORSMiddleware`.
  - **Synthetic probe confirmation**: `OPTIONS /api/v1/health` with `Origin: http://127.0.0.1:5173` returned `405 Method Not Allowed` and `access-control-allow-origin=None`.
- **Technical Consequences**: Violates `docs/ARCHITECTURE.md` ("strict CORS") and `docs/DEPLOYMENT.md` (`FRONTEND_ORIGIN`); the P2/G local React/Vite web client on port 5173 cannot call `/api/v1/*` endpoints from the browser.
- **Recommended Correction**:
  - Add configurable `frontend_origin: str = "http://127.0.0.1:5173"` to `Settings` and attach strict `CORSMiddleware(allow_origins=[settings.frontend_origin], allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])`.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-009 — `SyncEngine` does not enforce `LOCAL` exclusion from unscoped/automatic runs and has no tombstone/revocation support
- **Severity**: `MEDIUM`
- **Affected**: `backend/app/core/sync.py`, `SyncEngine._run` (lines 83–86) & `_pages` (lines 121–169); `backend/app/main.py`, `sync` (lines 178–186)
- **Description & Evidence**:
  - Although `CODEX_STATUS.md` states *"Local folder sync is explicitly scoped and excluded from unscoped/automatic runs"*, `SyncEngine._run` iterates `list(self.connectors)` without filtering out `SourceKind.LOCAL` when `scope.source is None` or `automatic=True`, and `POST /api/v1/sync` (`main.py:182`) does not reject `SyncScope(source="local", folder_id=None)` with HTTP 422 (unlike `scripts/sync.py:44`).
  - Furthermore, neither `Document` nor `Pipeline._document` supports tombstoning remote resources deleted/revoked upstream (e.g., `AccessStatus.TOMBSTONED` / `REVOKED`), leaving removed Drive/Moodle resources active in local search/reads.
- **Technical Consequences**: Unscoped automatic syncs needlessly invoke the `LOCAL` connector; remotely deleted/revoked files cannot be tombstoned cleanly per `docs/DATA_ENGINEERING.md`.
- **Recommended Correction**:
  - In `SyncEngine._run`, exclude `SourceKind.LOCAL` when `scope.source is None` or `automatic=True`; in `POST /api/v1/sync`, return HTTP 422 if `scope.source == SourceKind.LOCAL and not scope.folder_id`.
  - Add an optional `access_status: Literal["active", "tombstoned", "revoked"] = "active"` field to `Document` and filter non-active documents out of `GET /api/v1/documents` and `/content` reads.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-010 — `ConsentManager.allowed()` lacks monotonic tie-breaker on `recorded_at` and DB index
- **Severity**: `MEDIUM`
- **Affected**: `backend/app/core/auth.py`, `ConsentManager.allowed` (lines 28–33); `backend/app/core/database.py` (line 27)
- **Description & Evidence**:
  - `allowed()` queries `ORDER BY consents.recorded_at DESC LIMIT 1`. If two consent writes occur within the same clock tick or across an NTP backward adjustment, SQLite tie order is unspecified.
- **Technical Consequences**: Rapid revoke-after-grant (or clock skew) could cause `allowed()` to read a stale `granted=True` row.
- **Recommended Correction**:
  - Add a monotonic integer sequence (or `ORDER BY consents.recorded_at DESC, rowid DESC`) and an index on `consents(connection_id, purpose, recorded_at)`.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-011 — Schema duplication between `database.py` and `0001_p1.py`, missing `active_version_id` FK, missing indexes, and broken CLI `alembic upgrade head`
- **Severity**: `MEDIUM`
- **Affected**: `backend/app/core/database.py` (lines 22–48); `backend/migrations/versions/0001_p1.py` (lines 19–46); `backend/migrations/env.py` (lines 1–6)
- **Description & Evidence**:
  1. All 10 SQLAlchemy `Table` definitions are duplicated verbatim in both `database.py` and `0001_p1.py`.
  2. `documents.active_version_id` has no `ForeignKey("document_versions.id")` constraint.
  3. High-frequency lookup columns (`consents(connection_id, purpose, recorded_at)`, `source_records(kind, connection_id)`, `source_records(parent_id)`, `sync_runs(user_id, status)`) have no secondary indexes.
  4. `backend/migrations/env.py` unconditionally indexes `context.config.attributes["connection"]` with no `alembic.ini` or standalone URL fallback, so `alembic upgrade head` fails from CLI.
- **Technical Consequences**: High risk of schema drift across future P1/P2/P3 migrations, weaker referential integrity on `active_version_id`, slower queries as library grows, and incompatibility with `docs/DEPLOYMENT.md` migration commands.
- **Recommended Correction**:
  - Keep historical migrations immutable using explicit `op.create_table(...)` / `op.create_index(...)`, add FK on `active_version_id` (using `use_alter=True` for circularity), pass `target_metadata=metadata` in `env.py`, and support `sqlalchemy.url` / `Settings.load()` fallback when `attributes["connection"]` is absent.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-012 — `POST /api/v1/academic/import` returns HTTP 201 even when all items fail, with opaque `"invalid_record"` errors
- **Severity**: `MEDIUM`
- **Affected**: `backend/app/main.py`, `academic_import` (lines 298–313); `scripts/sync.py` (lines 58–64)
- **Description & Evidence**:
  - `academic_import` catches `ValueError` (missing parent, cycle, kind change) and appends `"invalid_record"`, still returning HTTP `201 Created`.
  - **Synthetic probe confirmation**: Submitting an orphan grade returned `PROBE4_BAD_ACADEMIC_STATUS: 201 body={'results': ['invalid_record']}`.
- **Technical Consequences**: Clients and P3 Grade Tracker UI cannot distinguish missing-parent vs cycle vs kind-change errors or detect total import failure via HTTP status codes (violating `docs/API_CONTRACTS.md` 422 guidance).
- **Recommended Correction**:
  - Return structured per-item error codes (`{"index": i, "external_id": ..., "status": "error", "reason": "missing_parent" | "cycle" | "kind_changed"}`), and return HTTP `422` when all items fail (or `207`/`200` with explicit summary on partial import). Also support dry-run mode per `docs/API_CONTRACTS.md` line 90.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-013 — `GET /api/v1/documents/{id}/versions/{version_id}/content` hardcodes `filename="document"` and `application/octet-stream` and lacks missing-blob handling
- **Severity**: `MEDIUM`
- **Affected**: `backend/app/main.py`, `content` (lines 269–274)
- **Description & Evidence**:
  - Returns `FileResponse(..., media_type="application/octet-stream", filename="document")` regardless of `item["mime_type"]` or `document["title"]`, and does not check `blob_path.is_file()` before passing it to `FileResponse`.
- **Technical Consequences**: Downloaded PDFs/notebooks lose their filename and extension; missing/corrupted blob files on disk trigger an unhandled 500 error instead of a clear 404/503 storage-integrity error.
- **Recommended Correction**:
  - Check `blob_path.is_file()` (returning HTTP 404/503 if missing), pass a sanitized safe filename preserving extension, and preserve `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff`.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-014 — Cursor pagination sorts by random UUIDv4 (`id`), producing non-chronological ordering and skipping concurrent inserts
- **Severity**: `MEDIUM`
- **Affected**: `backend/app/main.py`, `page` helper (lines 78–83)
- **Description & Evidence**:
  - `page()` orders all list endpoints (`sync/runs`, `documents`, `events`, `grades`, `availability`) by `id_column` (`uuid4()` string).
- **Technical Consequences**: `GET /api/v1/sync/runs` and `GET /api/v1/events` return items in random UUID order rather than chronological order, and concurrent inserts with smaller lexicographical UUIDs are skipped across pages.
- **Recommended Correction**:
  - Use UUIDv7 (time-ordered) or composite keyset cursors `(created_at / started_at, id)` for time-series tables (`sync_runs`, `events`, `documents`).
- **Resolution Status**: `OPEN`

---

### CODEX-REV-015 — `scripts/onboarding.py` does not detect completed setup steps, lacks Google Cloud readiness checks, and `POST /connections/{id}/reauthorize` is identical to `/authorize`
- **Severity**: `MEDIUM`
- **Affected**: `scripts/onboarding.py`, `run_onboarding` (lines 12–52); `backend/app/main.py`, `authenticate` (lines 162–166); `backend/app/core/runtime.py`, `_load_components` (lines 102–112)
- **Description & Evidence**:
  1. `run_onboarding()` prompts `Configure <source>?` for all 6 sources every run without first checking whether a source is already consented & connected, and does not invoke Google Cloud project (`ensora-516015`) readiness/API validation.
  2. `POST /api/v1/connections/{id}/reauthorize` routes to the same handler as `/authorize`, passing empty `inputs={}` and giving adapters no signal to force re-consent/reauthentication.
  3. `Runtime._load_components` swallows adapter factory load errors with a bare `logger.warning("Source adapters could not be loaded")` without logging the exception type/message.
- **Technical Consequences**: Repeated manual prompts degrade UX, force-reauthorize flows cannot be triggered via HTTP API, and broken adapter factories fail silently.
- **Recommended Correction**:
  - In `onboarding.py`, inspect already-connected sources first and skip or summarize completed steps; surface `GoogleCloudSetupHelper(cfg).inspect()` status before Google OAuth.
  - Distinguish `/reauthorize` from `/authorize` (e.g., pass `force=True` or call `reauthorize()` when available) and log `exc_info` / exception type safely in `_load_components`.
- **Resolution Status**: `OPEN`

---

### CODEX-REV-016 — `KeyringCredentialStore` has no availability probe & `scripts/bootstrap.py` assumes exact `==` pins
- **Severity**: `LOW`
- **Affected**: `backend/app/core/security.py`, `KeyringCredentialStore._backend` (lines 19–26); `scripts/bootstrap.py`, `install_missing` (line 40)
- **Description & Evidence**:
  - `KeyringCredentialStore._backend()` raises `RuntimeError("An operating-system keyring is required")` on headless Linux without D-Bus SecretService, with no `is_available()` helper for pre-flight onboarding checks.
  - `bootstrap.py:40` splits requirements with `requirement.split("==", 1)`, which raises `ValueError` if any future dependency in `pyproject.toml` uses `>=`, `~=`, or extras.
- **Technical Consequences**: Unhelpful `adapter_error` on headless Linux machines without unlocked keyring; brittle dependency parsing on future `pyproject.toml` edits.
- **Recommended Correction**:
  - Add `KeyringCredentialStore.is_available() -> bool` so onboarding/CLI can clearly instruct users to unlock or install an OS keyring backend.
  - Parse PEP 508 requirements via `packaging.requirements.Requirement` (or regex splitting on `[=<>!~]`) in `bootstrap.py`.
- **Resolution Status**: `OPEN`

---

## Verified Strengths & Non-Issues Confirmed

- **SQLite integrity**: `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=WAL` are enabled on every connection (`database.py:60–64`).
- **Atomic blob storage**: `Pipeline._store_blob` uses temp file in target dir + `fsync` + atomic `Path.replace` (`etl.py:36–48`).
- **Loopback API auth**: Constant-time `secrets.compare_digest` bearer token verification, `0o600` token file with `O_NOFOLLOW`, cleaned up on `Runtime.__exit__` (`runtime.py:59–64, 122`, `main.py:47–51`).
- **Document subject filtering**: `GET /api/v1/documents?subject_id=...` executed cleanly (HTTP 200) in SQLAlchemy 2.0.58 + SQLite.
- **Test baseline**: All 5 canonical tests in `backend/tests/integration/test_p1.py` and all 19 Gemini ingestion/bridge tests pass (`24 passed in 2.55s`).

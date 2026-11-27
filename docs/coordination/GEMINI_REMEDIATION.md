# Gemini P1 Remediation Log (`GEMINI-REV-001` through `GEMINI-REV-020`)

- **Branch**: `feat/p1-data-engineering` (base commit `89ad3d8`)
- **Remediation Date**: 2026-11-27
- **Combined Verification Command**: `PYTHONPATH=. .venv/bin/python -m pytest backend/tests -v`
- **Actual Test Suite Result**: **34 passed in 1.92s** (29 Gemini unit/bridge/remediation tests + 5 Codex canonical integration tests; 0 failed, 0 skipped).
- **Status Legend**: `OPEN`, `IN_PROGRESS`, `FIXED_PENDING_REVIEW`, `NOT_REPRODUCIBLE`, `BLOCKED` (no finding is marked independently verified prior to Codex review).

---

| Finding ID | Severity | Validation Result | Resolution Status | Regression Test | Actual Test Result |
|---|---|---|---|---|---|
| `GEMINI-REV-001` | HIGH | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev001_ensora_drive_revalidates_moved_folder_and_rejects_trashed_download` | PASSED |
| `GEMINI-REV-002` | HIGH | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev002_google_calendar_multipage_incremental_and_initial_invariants` | PASSED |
| `GEMINI-REV-003` | HIGH | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev003_rev004_cancelled_tombstones_missing_duration_and_privacy_fields` | PASSED |
| `GEMINI-REV-004` | HIGH | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev003_rev004_cancelled_tombstones_missing_duration_and_privacy_fields` | PASSED |
| `GEMINI-REV-005` | HIGH | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev005_rev006_moodle_parent_subject_academic_facts_and_error_checkpointing` | PASSED |
| `GEMINI-REV-006` | HIGH | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev005_rev006_moodle_parent_subject_academic_facts_and_error_checkpointing` & `test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields` | PASSED |
| `GEMINI-REV-007` | HIGH | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev007_rev008_oauth_verified_grants_atomic_0600_and_remote_probe` & `test_google_oauth_refresh_expiration_granular_consent_and_revoke` | PASSED |
| `GEMINI-REV-008` | HIGH | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev007_rev008_oauth_verified_grants_atomic_0600_and_remote_probe` | PASSED |
| `GEMINI-REV-009` | HIGH | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev009_office_parsers_reject_oversized_zip_expansion` | PASSED |
| `GEMINI-REV-010` | MEDIUM | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev010_moodle_rest_discovery_and_bounded_redirect_guarded_download` | PASSED |
| `GEMINI-REV-011` | MEDIUM | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev011_local_cross_root_ids_fetch_containment_and_ics_bridge` | PASSED |
| `GEMINI-REV-012` | MEDIUM | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev012_rev013_rev014_rev015_bridge_parser_catalog_cli_and_settings` & `test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields` | PASSED |
| `GEMINI-REV-013` | MEDIUM | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev012_rev013_rev014_rev015_bridge_parser_catalog_cli_and_settings` | PASSED |
| `GEMINI-REV-014` | MEDIUM | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev012_rev013_rev014_rev015_bridge_parser_catalog_cli_and_settings` | PASSED |
| `GEMINI-REV-015` | MEDIUM | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev012_rev013_rev014_rev015_bridge_parser_catalog_cli_and_settings` | PASSED |
| `GEMINI-REV-016` | LOW | Confirmed | `FIXED_PENDING_REVIEW` | All 10 new tests in `backend/tests/unit/ingestion/test_remediation_regressions.py` | PASSED (34/34 total) |
| `GEMINI-REV-017` | HIGH | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields` | PASSED |
| `GEMINI-REV-018` | MEDIUM | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields` | PASSED |
| `GEMINI-REV-019` | MEDIUM | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields` | PASSED |
| `GEMINI-REV-020` | MEDIUM | Confirmed | `FIXED_PENDING_REVIEW` | `test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields` | PASSED |

---

## Finding-by-Finding Remediation Details

### GEMINI-REV-001
- **Validation result**: Confirmed (`_known_descendant_folders` cached ancestry for process lifetime; `fetch_blob` ignored `meta["trashed"]`).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/connectors/ensora_drive.py`
- **Implemented correction**: Removed process-lifetime `_known_descendant_folders` cache; scoped ancestry memoization strictly per `list_changes` invocation (`verified_in_run`), always revalidating live ancestry on scoped listings and every `fetch_blob`; explicitly rejected `meta.get("trashed") is True` with `PermissionDeniedError`.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev001_ensora_drive_revalidates_moved_folder_and_rejects_trashed_download`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-002
- **Validation result**: Confirmed (multi-page incremental cursor dropped `syncToken` and recalculated `timeMin`/`timeMax` + `singleEvents`/`orderBy`).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/connectors/google_calendar.py`
- **Implemented correction**: Encoded paging cursor as `page:<base64url>` JSON containing `pageToken`, original `syncToken`, frozen `timeMin`/`timeMax`, and a query fingerprint (`calendar_id|mode`). Incremental pages preserve `syncToken` and omit `singleEvents`/`orderBy`/`timeMin`/`timeMax`; initial pages freeze exact `timeMin`/`timeMax` across pages.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev002_google_calendar_multipage_incremental_and_initial_invariants`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-003
- **Validation result**: Confirmed (ID-only cancelled events returned `None`; missing timed `end_at` fabricated a 1-hour duration).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/codex_bridge.py`
- **Implemented correction**: Mapped ID-only cancelled events to Codex's `CalendarEvent(..., status="cancelled")` tombstone; removed fabricated 1-hour duration fallback so incomplete/non-positive timed events raise `RecordConversionError` isolated per record without advancing durable checkpoints.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev003_rev004_cancelled_tombstones_missing_duration_and_privacy_fields`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None (Codex already landed ID-only cancelled support in `backend/app/contracts/sources.py`).

### GEMINI-REV-004
- **Validation result**: Confirmed (bridge dropped `event_type`, `teacher`, `confirmation_required`, `confirmation_reason`, `recurrence_key`, `original_timezone`, `busy`, and did not default Calendar to availability-only).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/codex_bridge.py`, `backend/app/modules/ingestion/config.py`
- **Implemented correction**: Propagated all typed `CalendarEvent` metadata fields through `_to_codex_records`; defaulted `CodexConnectorBridge` for `GOOGLE_CALENDAR` to `availability_only=True` unless explicitly opted in.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev003_rev004_cancelled_tombstones_missing_duration_and_privacy_fields`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-005
- **Validation result**: Confirmed (Moodle omitted parent `Subject`; weighted assessments replaced `Assessment` with `AssessmentWeight`; coefficients on subject/grade rows dropped; missing grade fell through to `Subject`).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/codex_bridge.py`
- **Implemented correction**: Replaced `_to_codex_record` with ordered multi-fact `_to_codex_records(rec, emitted_parents)`: Moodle emits parent `Subject(external_id="moodle:course:{id}")` before course documents/assignments; subjects emit `Subject` + independent `SubjectCoefficient`; assessments emit `Assessment` + optional `AssessmentWeight` + optional `PublishedGrade`; missing grades emit no fabricated `Subject`.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev005_rev006_moodle_parent_subject_academic_facts_and_error_checkpointing`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-006
- **Validation result**: Confirmed (bridge discarded warnings, errors, and `sync_token`; conversion failures aborted the whole page).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/codex_bridge.py`
- **Implemented correction**: Isolated per-record conversion errors, propagated bounded constant `warning_codes` / `error_codes` and separate `resume_cursor` onto Codex's updated `ChangePage`, withheld `resume_cursor` whenever page or conversion errors occurred, and raised `RetryableSourceError` when upstream listing errors produced zero healthy items.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev005_rev006_moodle_parent_subject_academic_facts_and_error_checkpointing` & `test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None (aligned with Codex's `ChangePage.resume_cursor` / `warning_codes` / `error_codes` contract).

### GEMINI-REV-007
- **Validation result**: Confirmed (`_load_stored_credentials` passed requested scopes; `_save_credentials` wrote fixed `.tmp` before `chmod`; `disconnect` passed token in URL `params`).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/auth/google_oauth.py`, `backend/tests/unit/ingestion/test_auth_and_setup.py`
- **Implemented correction**: Persisted verified `granted_scopes` independently and loaded tokens via `from_authorized_user_info` without overwriting grants with requested scopes; replaced fixed temp file with `tempfile.mkstemp` + `os.fchmod(fd, 0o600)` + `os.fsync` + `os.replace`; sent revocation token in POST form body (`data={"token": ...}`).
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev007_rev008_oauth_verified_grants_atomic_0600_and_remote_probe`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-008
- **Validation result**: Confirmed (`CodexGoogleAuthBridge.validate` checked local token only without source-specific Drive/Calendar remote probes).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/codex_bridge.py`, `backend/app/modules/ingestion/connectors/google_calendar.py`
- **Implemented correction**: Added `GoogleCalendarConnector.validate_calendar_access()` and wired read-only remote probes into `CodexGoogleAuthBridge` (`drive_conn.get_root_folder_metadata` and `cal_conn.validate_calendar_access`), mapping 401 -> `ReauthenticationRequired`, 403 -> `ACCESS_DENIED`, 429/5xx -> `UNAVAILABLE (transient_failure)`, plus implemented `reauthorize()`.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev007_rev008_oauth_verified_grants_atomic_0600_and_remote_probe` & `test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-009
- **Validation result**: Confirmed (PPTX/DOCX parsers read uncompressed ZIP members without member-count or aggregate expansion bounds).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/parsers/office_parser.py`
- **Implemented correction**: Added `_validate_zip_bounds` and `_read_bounded_member` enforcing `MAX_OFFICE_ZIP_MEMBERS` (512), `MAX_OFFICE_MEMBER_BYTES` (10 MiB), `MAX_OFFICE_TOTAL_BYTES` (50 MiB), and `MAX_OFFICE_COMPRESSION_RATIO` (100x) before and during streaming reads; unsafe archives fail safely with `extraction_quality=0.0`.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev009_office_parsers_reject_oversized_zip_expansion`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-010
- **Validation result**: Confirmed (Moodle REST only validated token without acquiring courses/contents; `fetch_blob` lacked streaming size limit and final redirect origin validation).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/connectors/moodle.py`
- **Implemented correction**: Implemented authorized REST acquisition (`_ws_call`, `_discover_courses_rest`, `_extract_course_items_rest`) when `moodle_ws_enabled` or `moodle_auth_mode == "rest"`; added `_stream_download_rest` enforcing final redirect origin (`is_same_origin(resp.url, ...)`) and streaming `max_download_bytes` checks (plus redirect & `Content-Length` checks in Playwright path).
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev010_moodle_rest_discovery_and_bounded_redirect_guarded_download`
- **Actual test result**: PASSED.
- **Remaining dependencies**: Live Moodle REST availability at ENSAI remains unverified pending human/institutional confirmation.

### GEMINI-REV-011
- **Validation result**: Confirmed (local IDs collided across roots; `fetch_blob` did not recheck containment/symlinks; `.ics` files were not parsed into event records).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/connectors/local_import.py`, `backend/tests/unit/ingestion/test_connectors.py`
- **Implemented correction**: Namespaced local external IDs with 12-char SHA-256 of canonical root (`local:{root_ns}:{rel_path}`); added `_read_contained_bytes` rechecking containment + symlink rejection + `O_NOFOLLOW` bounded reads at fetch time; parsed `.ics` files in `list_changes` into normalized `event` records.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev011_local_cross_root_ids_fetch_containment_and_ics_bridge`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-012
- **Validation result**: Confirmed (`_record_cache` grew unbounded; Workspace export filename extension/MIME alignment needed; parser structured metadata/warnings dropped; classifier ignored `parsed`).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/codex_bridge.py`
- **Implemented correction**: Replaced unbounded cache with page-scoped bounded `_page_cache` (max 500); aligned exported Workspace document extensions at `list_changes`; populated Codex's `DocumentChunk.block_kind`, `DocumentChunk.structured` (code language, tables, math, quality, figures), and `ParsedContent.warning_codes`; reconstructed parsed text excerpt in `CodexClassifierBridge.classify` so body signals inform classification.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev012_rev013_rev014_rev015_bridge_parser_catalog_cli_and_settings` & `test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-013
- **Validation result**: Confirmed (matcher instantiated without persisted catalog subjects/overrides and forced `SourceKind.LOCAL_FILE`).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/codex_bridge.py`
- **Implemented correction**: Added `catalog_loader`, `sync_catalog_subjects()`, `load_from_sqlite_engine(engine)` (hydrating persisted `subject` records and confirmed `user_override` mappings), inferred source kind from `external_id` prefix, and exposed `link_exam_corrections()` on `CodexEntityMatcherBridge`.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev012_rev013_rev014_rev015_bridge_parser_catalog_cli_and_settings`
- **Actual test result**: PASSED.
- **Remaining dependencies**: Codex `Runtime` may optionally call `matcher.load_from_sqlite_engine(self.engine)` at startup/sync time for automatic hydration.

### GEMINI-REV-014
- **Validation result**: Confirmed (CLI printed raw records/grades/email and ran local/academic imports outside Codex's unified SQLite ETL).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `scripts/p1_sources_cli.py`, `backend/app/modules/ingestion/auth/google_cloud.py`
- **Implemented correction**: Masked developer email in `GoogleCloudSetupHelper.inspect()` (`a***@ensai.fr`); restricted CLI stdout to redacted summaries/counters unless explicit `--export-json <FILE>` (`0o600`) is requested; routed `import-local` and `import-academic` directly through Codex's unified `Runtime` (`SyncEngine` / `Pipeline.import_academics`).
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev012_rev013_rev014_rev015_bridge_parser_catalog_cli_and_settings`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-015
- **Validation result**: Confirmed (`runtime_config_from_codex_settings` silently replaced missing explicit Google paths and dropped source-specific auth modes; keyring write failure left `allow_auto` enabled).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/codex_bridge.py`, `backend/app/modules/ingestion/config.py`, `scripts/p1_sources_cli.py`
- **Implemented correction**: Shared Codex's `Settings.load()` in CLI and preserved explicit `google_credentials_path`/`google_token_path` verbatim; propagated `moodle_auth_mode` and `pamplemousse_auth_mode`; on keyring write failure in `CodexEnsaiAuthBridge.authorize`, explicitly disabled automated submit (`allow_auto = False`) and set safe action code `keyring_unavailable_interactive_login_required`.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev012_rev013_rev014_rev015_bridge_parser_catalog_cli_and_settings`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-016
- **Validation result**: Confirmed (edge cases for moved folders, multi-page incremental Calendar, ID-only cancellations, cross-root IDs, partial page errors, ZIP bombs, REST streaming, etc. lacked regression coverage).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/tests/unit/ingestion/test_remediation_regressions.py`
- **Implemented correction**: Added 10 targeted regression test suites covering `GEMINI-REV-001` through `GEMINI-REV-020` using purely synthetic fixtures and fake transports.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py` (all 10 tests)
- **Actual test result**: PASSED (34/34 total combined tests passed in 1.92s).
- **Remaining dependencies**: Live tests remain separately human-authorized.

### GEMINI-REV-017
- **Validation result**: Confirmed (`status`/`inspect_only` and `validate` could trigger headless browser/OAuth refresh without durable read consent).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/auth/onboarding.py`, `scripts/p1_sources_cli.py`
- **Implemented correction**: Made `OnboardingOrchestrator.inspect_only(allow_network=False)` strictly local-only by default (zero browser launches or network calls); updated `p1_sources_cli.py validate` to require explicit `--source` selection and route through Codex's consent-gated `Runtime.auth.validate(source)`.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-018
- **Validation result**: Confirmed (disconnecting one ENSAI source wiped the shared browser profile used by the other source).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/auth/ensai.py`, `backend/app/modules/ingestion/codex_bridge.py`
- **Implemented correction**: Updated `EnsaiAuthAdapter.disconnect(source, clear_shared_profile=False)` so single-source disconnect withdraws source state without deleting the shared `~/.config/ensora-plus/sessions/ensai/` directory; added explicitly confirmed `clear_shared_session(confirmed=True)`.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-019
- **Validation result**: Confirmed (`gcp-check --enable-apis` enabled all configured APIs instead of the exact approved missing subset and always exited 0).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/auth/google_cloud.py`, `scripts/p1_sources_cli.py`
- **Implemented correction**: Added allowlisted `apis` parameter to `enable_required_apis()` rejecting non-allowlisted APIs; updated CLI to pass `apis=report.missing_apis` and return `res.returncode` (nonzero on failure).
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields`
- **Actual test result**: PASSED.
- **Remaining dependencies**: None.

### GEMINI-REV-020
- **Validation result**: Confirmed (no deterministic provenance-linked `Concept` candidate extraction existed in the bridge).
- **Resolution status**: `FIXED_PENDING_REVIEW`
- **Modified files**: `backend/app/modules/ingestion/codex_bridge.py`
- **Implemented correction**: Implemented deterministic `extract_concept_candidates(document, parsed) -> list[Concept]` extracting explicit definitions, theorems, propositions, algorithms, and math formulas with preserved `Provenance` (`external_id`, `source`, `parent_key`) ready for P2/P3 reviewable concept persistence.
- **Regression test**: `backend/tests/unit/ingestion/test_remediation_regressions.py::test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields`
- **Actual test result**: PASSED.
- **Remaining dependencies**: P2/P3 agreement on persisting candidate concepts automatically during `Pipeline._document` vs explicit T20 concept-extraction job.

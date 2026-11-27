# Gemini Targeted Remediation Round 2 — P1 Source Integration, Authentication & Document Processing

- **Status**: **REMEDIATION_ROUND_2_COMPLETE** (all 11 unresolved original findings + all 6 newly discovered Round 2 defects are `FIXED_PENDING_REVIEW`; awaiting Codex targeted final verification).
- **Repository / Branch / Base**: `/home/ensai/hackathon_ENSORA` · `feat/p1-data-engineering` · base `89ad3d8`
- **Review addressed**: `docs/coordination/CODEX_REVIEW_ROUND_2.md`
  - **9 already VERIFIED** (preserved untouched unless regression-adjacent): `GEMINI-REV-001`, `003`, `004`, `006`, `008`, `009`, `015`, `018`, `019`.
  - **2 REOPENED addressed**: `GEMINI-REV-002`, `GEMINI-REV-017`.
  - **9 PARTIALLY_FIXED addressed**: `GEMINI-REV-005`, `007`, `010`, `011`, `012`, `013`, `014`, `016`, `020`.
  - **6 Newly Discovered Defects addressed**: `ROUND2-NEW-001`, `ROUND2-NEW-002`, `ROUND2-NEW-003`, `ROUND2-NEW-004`, `ROUND2-NEW-005`, `ROUND2-NEW-006`.
- **Combined Synthetic Test Suite**:
  ```sh
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -v backend/tests
  ```
  - **Actual Result**: **61 passed in 7.25s** (19 Codex integration tests including all 6 Round 2 integration tests + 29 original/Round 1 Gemini unit & bridge tests + 13 new Round 2 targeted regression tests).
  - **Offline CLI Smoke Check**: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/p1_sources_cli.py --config-dir /tmp/ensora_r2_smoke onboard --non-interactive` exited `0` with strictly local-only status and zero network/browser probes.
- **Scope Discipline**: Modified ONLY Gemini-owned files (`backend/app/modules/ingestion/**`, `scripts/p1_sources_cli.py`, `backend/tests/unit/ingestion/**`, `docs/coordination/GEMINI_*.md`). Zero edits to Codex-owned files; zero Git write operations; zero live source authentication/sync.

---

## Summary Table — Unresolved Original Findings (11) & Newly Discovered Defects (6)

| ID | Round 2 Severity / Verdict | Status | Modified Files | Targeted Regression Test | Actual Result |
| --- | --- | --- | --- | --- | --- |
| `GEMINI-REV-002` | HIGH (REOPENED) | `FIXED_PENDING_REVIEW` | `connectors/google_calendar.py`, `tests/.../test_remediation_regressions.py`, `tests/.../test_remediation_round2_regressions.py` | `test_rev002_google_calendar_multipage_incremental_and_initial_invariants`, `test_r2_calendar_long_opaque_cursors_and_single_events_invariant` | PASSED |
| `GEMINI-REV-005` | HIGH (PARTIALLY_FIXED) | `FIXED_PENDING_REVIEW` | `codex_bridge.py`, `connectors/local_import.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_nested_academic_weights_and_derived_source_provenance_in_sqlite` | PASSED |
| `GEMINI-REV-007` | HIGH (PARTIALLY_FIXED) | `FIXED_PENDING_REVIEW` | `auth/google_oauth.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_oauth_rejects_requested_only_and_legacy_scope_only_tokens` | PASSED |
| `GEMINI-REV-010` | MEDIUM (PARTIALLY_FIXED) | `FIXED_PENDING_REVIEW` | `connectors/moodle.py`, `codex_bridge.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_moodle_prefollow_redirect_guard_and_rest_only_runtime_auth` | PASSED |
| `GEMINI-REV-011` | MEDIUM (PARTIALLY_FIXED) | `FIXED_PENDING_REVIEW` | `connectors/local_import.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_local_protected_roots_and_dirfd_containment` | PASSED |
| `GEMINI-REV-012` | MEDIUM (PARTIALLY_FIXED) | `FIXED_PENDING_REVIEW` | `codex_bridge.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_bridge_fetched_document_metadata_and_page_size_cap` | PASSED |
| `GEMINI-REV-013` | MEDIUM (PARTIALLY_FIXED) | `FIXED_PENDING_REVIEW` | `codex_bridge.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_matcher_provenance_source_consent_and_persisted_exam_pairs` | PASSED |
| `GEMINI-REV-014` | MEDIUM (PARTIALLY_FIXED) | `FIXED_PENDING_REVIEW` | `scripts/p1_sources_cli.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_private_export_refuses_symlinks_and_writes_atomic_0600` | PASSED |
| `GEMINI-REV-016` | LOW (PARTIALLY_FIXED) | `FIXED_PENDING_REVIEW` | `tests/.../test_remediation_regressions.py`, `tests/.../test_remediation_round2_regressions.py`, `tests/.../test_auth_and_setup.py`, `tests/.../test_connectors.py` | Full 13-test Round 2 regression suite + updated `test_rev002_*` | PASSED (61/61) |
| `GEMINI-REV-017` | HIGH (REOPENED) | `FIXED_PENDING_REVIEW` | `auth/onboarding.py`, `scripts/p1_sources_cli.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_cli_onboard_non_interactive_local_only_and_interactive_routes_shared` | PASSED |
| `GEMINI-REV-020` | MEDIUM (PARTIALLY_FIXED) | `FIXED_PENDING_REVIEW` | `codex_bridge.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_concept_candidates_chunk_evidence_and_prerequisite_hints` | PASSED |
| `ROUND2-NEW-001` | HIGH | `FIXED_PENDING_REVIEW` | `connectors/google_calendar.py`, `codex_bridge.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_calendar_long_opaque_cursors_and_single_events_invariant` | PASSED |
| `ROUND2-NEW-002` | HIGH | `FIXED_PENDING_REVIEW` | `connectors/google_calendar.py`, `codex_bridge.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_calendar_410_recovery_retires_stale_availability` | PASSED |
| `ROUND2-NEW-003` | MEDIUM | `FIXED_PENDING_REVIEW` | `connectors/ensora_drive.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_drive_incremental_watermark_and_validated_tombstones` | PASSED |
| `ROUND2-NEW-004` | HIGH | `FIXED_PENDING_REVIEW` | `codex_bridge.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_conversion_error_logging_redacts_sensitive_content` | PASSED |
| `ROUND2-NEW-005` | HIGH | `FIXED_PENDING_REVIEW` | `connectors/local_import.py`, `codex_bridge.py`, `scripts/p1_sources_cli.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_local_protected_roots_and_dirfd_containment` | PASSED |
| `ROUND2-NEW-006` | MEDIUM | `FIXED_PENDING_REVIEW` | `scripts/p1_sources_cli.py`, `tests/.../test_remediation_round2_regressions.py` | `test_r2_private_export_refuses_symlinks_and_writes_atomic_0600` | PASSED |

---

## Detailed Resolution Notes

### 1. `GEMINI-REV-002` (REOPENED · HIGH) & `ROUND2-NEW-001` (HIGH) — Calendar `singleEvents` Invariance & Long Opaque Cursors
- **Validation**: Confirmed `google_calendar.py` previously omitted `singleEvents` when `syncToken` was present, and base64-encoded both tokens into a cursor that exceeded 512 chars on two 260+ char tokens.
- **Correction**:
  - `backend/app/modules/ingestion/connectors/google_calendar.py`: Always sends `"singleEvents": True` on BOTH initial and incremental `syncToken` queries (omitting only incompatible `timeMin`/`timeMax`/`orderBy` when `syncToken` is present).
  - Replaced base64-encoded multi-page cursors with a bounded in-connector state table `_paging_states` (max 64) issuing compact 24-character `calpage:<16-hex>` handles, while durable terminal tokens remain `sync:<nextSyncToken>` (compatible with Codex's new 16 KiB `OpaqueCursor` contract).
  - Updated `test_rev002_google_calendar_multipage_incremental_and_initial_invariants` to assert `singleEvents=True` on both initial and incremental pages, and added `test_r2_calendar_long_opaque_cursors_and_single_events_invariant` exercising two 300-char opaque tokens end-to-end through `CodexConnectorBridge` + `Runtime` + `SyncEngine` + SQLite.
- **Remaining limitation**: Live Google Calendar recurring-event expansion against a real Google account remains untested pending human authorization.

### 2. `ROUND2-NEW-002` (HIGH) — Calendar HTTP 410 Recovery Retires Stale Availability
- **Validation**: Confirmed 410 full-range restart previously merged into existing SQLite rows without retiring unseen events in the recovered window.
- **Correction**:
  - `backend/app/modules/ingestion/connectors/google_calendar.py`: Exposes `self.last_snapshot_info = (f"google_calendar:{cal_id}:", effective_tmin, effective_tmax)` on initial full scans and HTTP 410 replacement runs (and `None` on incremental `syncToken` deltas or ICS imports), with `effective_tmin`/`effective_tmax` frozen identically across all pages of a multi-page replacement run.
  - `backend/app/modules/ingestion/codex_bridge.py`: Attaches Codex's shared `CalendarSnapshot(external_id_prefix=..., starts_at=..., ends_at=...)` to `ChangePage.calendar_snapshot` on unscoped Google Calendar full/410 replacement runs so `SyncEngine` + `Pipeline.retire_missing_calendar_events()` atomically retire missing events after error-free completion while preserving `record_history`.
  - Verified by `test_r2_calendar_410_recovery_retires_stale_availability` AND Codex's `test_round2_calendar_410_bridge_retires_only_missing_scoped_events`.

### 3. `ROUND2-NEW-003` (MEDIUM) — Ensora Drive Incremental Watermark & Validated Tombstones
- **Validation**: Confirmed `EnsoraDriveConnector.list_changes()` previously treated `cursor` only as a numeric offset and ignored the persisted ISO timestamp watermark.
- **Correction**:
  - `backend/app/modules/ingestion/connectors/ensora_drive.py`: Uses explicitly typed cursors — `drvpage:<offset>` for intra-run pagination and `drvmod:<iso_utc>` for durable incremental watermarks. Consumes `drvmod:` from `cursor` directly into `modified_since` so incremental runs skip unmodified files.
  - Added optional `known_external_ids` reconciliation emitting `AccessStatus.TOMBSTONED` records ONLY after a validated, complete, zero-error full scan (`not errors and max_files is None and not cohorts_filter and start_folder == root`).
  - Verified by `test_r2_drive_incremental_watermark_and_validated_tombstones`.
- **Remaining limitation**: Automated cross-run deletion reconciliation inside `SyncEngine` would require passing persisted active Drive external IDs or a Drive Changes API token (documented in `GEMINI_HANDOFF.md` for future P1/P2 enhancement; live Drive Changes API remains untested without human authorization).

### 4. `ROUND2-NEW-004` (HIGH) — Conversion Warnings Never Disclose Source Content
- **Validation**: Confirmed `CodexConnectorBridge.list_changes()` previously interpolated `item.external_id` and `exc` text into `logger.warning(...)` and `last_page_errors`.
- **Correction**:
  - `backend/app/modules/ingestion/codex_bridge.py`: Maps conversion failures strictly to bounded constant reason codes (`"incomplete_event_interval"`, `"invalid_record_payload"`, `"record_conversion_error"`), logs ONLY `logger.warning("Isolated record conversion failure (code=%s)", code)`, and stores ONLY constant codes in `error_codes`, `last_page_errors`, and `last_page_warnings`.
  - Verified by `test_r2_conversion_error_logging_redacts_sensitive_content` capturing logs via `caplog` with synthetic sensitive strings (`SECRET_STUDENT_ID_9999`, `PRIVATE_NOTE_XYZ`, `25.75`) and confirming zero leakage.

### 5. `ROUND2-NEW-005` (HIGH) & `GEMINI-REV-011` (PARTIALLY_FIXED · MEDIUM) — Protected Local Roots & Atomic Dirfd Containment
- **Validation**: Confirmed local imports previously allowed selecting app private directories (`config_dir`, `local_data_dir`, `secrets/`) and opened files via absolute path after `resolve()`, leaving intermediate directory symlink races and non-regular file (FIFO) risks.
- **Correction**:
  - `backend/app/modules/ingestion/connectors/local_import.py`: Added `default_protected_directories()` + `LocalFileConnector(protected_roots=...)` + `_assert_not_protected()` rejecting any selected path equal to, inside, or enclosing a protected private directory.
  - Rewrote `_read_contained_bytes()` to pin the root with `os.open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)`, walk each intermediate component via `os.open(part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=cur_fd)`, open the leaf with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` (preventing FIFO blocking DoS!), and verify `stat.S_ISREG(st.st_mode)` + `st.st_size <= max_file_bytes` via `os.fstat()`.
  - `backend/app/modules/ingestion/codex_bridge.py`: `build_components()` passes `settings.config_dir`, `settings.local_data_dir`, `settings.ensai_profile_dir`, `settings.google_credentials_path.parent`, and `settings.google_token_path.parent` into `LocalFileConnector(protected_roots=...)`.
  - `scripts/p1_sources_cli.py`: `import-academic` validates `args.path` through `runtime.validate_local_import_path(args.path)` before reading.
  - Verified by `test_r2_local_protected_roots_and_dirfd_containment` (testing protected dirs, enclosing parent dir, intermediate symlink swap, FIFO rejection, and CLI `import-academic` rejection).

### 6. `ROUND2-NEW-006` (MEDIUM) & `GEMINI-REV-014` (PARTIALLY_FIXED · MEDIUM) — Safe Atomic Private Exports
- **Validation**: Confirmed `_write_private_export()` previously opened the destination directly with `O_TRUNC`, which would follow an existing symlink and overwrite the linked file.
- **Correction**:
  - `scripts/p1_sources_cli.py`: `_write_private_export()` rejects symlinks at target or parent directory (`PermissionError`), writes to a private `tempfile.mkstemp(..., dir=parent)` with `os.fchmod(fd, 0o600)` + `flush()` + `os.fsync(fd)`, rechecks target symlink status, and atomically replaces with `os.replace()`.
  - Verified by `test_r2_private_export_refuses_symlinks_and_writes_atomic_0600`.

### 7. `GEMINI-REV-005` (PARTIALLY_FIXED · HIGH) — Nested Academic Weights & Derived `Provenance.source`
- **Validation**: Confirmed derived `:coef`, `:weight`, `:grade` facts previously omitted `Provenance.source`, and nested weighting needed an end-to-end SQLite parent-FK verification + explicit group marker.
- **Correction**:
  - `backend/app/modules/ingestion/codex_bridge.py`: Added `source=codex_src` and `folder=rec.folder_path` to EVERY derived `Provenance` object (`teaching_unit`, `subject`, `assessment`/`assessment_group`, `grade`). Preserved `format="assessment_group"` on composite assessment nodes (`rtype == "assessment_group"`) so recursive weighting forms an unambiguous SQLite `parent_id` FK chain: `Subject <- Assessment(group, format="assessment_group") <- AssessmentWeight(group)` and `Assessment(group) <- Assessment(child) <- AssessmentWeight(child) / PublishedGrade(child)`.
  - `backend/app/modules/ingestion/connectors/local_import.py`: `StructuredAcademicImporter` accepts `"children"` alongside `"components"` and `"assessments"` for nested assessment hierarchies.
  - Verified end-to-end in Codex SQLite by `test_r2_nested_academic_weights_and_derived_source_provenance_in_sqlite`.

### 8. `GEMINI-REV-007` (PARTIALLY_FIXED · HIGH) — OAuth Verified Grants Only
- **Validation**: Confirmed `google_oauth.py` previously fell back to `creds.scopes` (requested scopes) when `granted_scopes` was absent and treated legacy `scopes`-only JSON files as grants.
- **Correction**:
  - `backend/app/modules/ingestion/auth/google_oauth.py`: `_load_stored_credentials()` reads ONLY `verified_granted_scopes` or `granted_scopes` (never legacy `scopes`). `_granted_scopes()` inspects ONLY `_verified_granted_scopes` or `granted_scopes` and NEVER falls back to `creds.scopes`. `_save_credentials()` persists both `verified_granted_scopes` and `granted_scopes` atomically at `0o600`.
  - Verified by `test_r2_oauth_rejects_requested_only_and_legacy_scope_only_tokens`.
- **Remaining limitation**: Live Google OAuth token-grant reporting requires authorized human execution.

### 9. `GEMINI-REV-010` (PARTIALLY_FIXED · MEDIUM) — Moodle Pre-Follow Redirect Guard & REST-Only Runtime Auth
- **Validation**: Confirmed `MoodleConnector` previously allowed `requests.get` / Playwright to follow redirects before checking final origin, `_validate_ws_token()` could fall back to browser probes, and `build_components()` registered `CodexEnsaiAuthBridge` even when `moodle_auth_mode="rest"`.
- **Correction**:
  - `backend/app/modules/ingestion/connectors/moodle.py`: `_stream_download_rest()` passes `allow_redirects=False` and validates each `Location` hop (up to 5 hops) with `is_same_origin()` BEFORE issuing the next request. Playwright `fetch_blob()` passes `max_redirects=0` and validates each `Location` hop before following, checking `Content-Length` before `resp.body()`. `_validate_ws_token()` never falls back to Playwright browser probes.
  - `backend/app/modules/ingestion/codex_bridge.py`: Added `CodexMoodleRestAuthBridge` validating REST WS tokens without Playwright/CAS; `build_components()` automatically selects `CodexMoodleRestAuthBridge` when `moodle_auth_mode == "rest"` or `moodle_ws_enabled` is True.
  - Verified by `test_r2_moodle_prefollow_redirect_guard_and_rest_only_runtime_auth`.
- **Remaining limitation**: Playwright's `APIResponse` exposes `.body()` rather than an async chunk iterator; size is bounded via `Content-Length` pre-check and immediate post-read size check (REST mode streams chunk-by-chunk with hard cutoff).

### 10. `GEMINI-REV-012` (PARTIALLY_FIXED · MEDIUM) — `FetchedDocument` Metadata Propagation & Page-Size Bound
- **Validation**: Confirmed `CodexConnectorBridge.fetch_blob()` previously returned raw `bytes`, discarding fetched `mime_type` and `filename`, and pages were not capped to `MAX_PAGE_CACHE_ENTRIES`.
- **Correction**:
  - `backend/app/modules/ingestion/codex_bridge.py`: `CodexConnectorBridge.list_changes()` forwards `page_size=MAX_PAGE_CACHE_ENTRIES` so returned pages fit inside `_page_cache`. `fetch_blob()` now returns Codex's new `FetchedDocument(content=..., mime_type=..., filename=Path(res.filename).name)` so exported Workspace MIME types and basenames persist cleanly into Codex ETL `documents` and `versions`.
  - Verified by `test_r2_bridge_fetched_document_metadata_and_page_size_cap` AND Codex's `test_round2_fetched_metadata_reaches_version_and_parser`.

### 11. `GEMINI-REV-013` (PARTIALLY_FIXED · MEDIUM) — `Provenance.source` Precedence, Consent-Scoped Overrides & Persisted Exam Pairs
- **Validation**: Confirmed `CodexEntityMatcherBridge` previously inferred source solely from external ID prefix, lacked consent filtering in `load_from_sqlite_engine()`, and had no SQLite-backed exam-pair discovery helper.
- **Correction**:
  - `backend/app/modules/ingestion/codex_bridge.py`: `CodexEntityMatcherBridge.match()` and `link_exam_corrections()` prefer `_from_codex_source_kind(document.provenance.source)` first, falling back to external ID prefix only when `source is None`. `load_from_sqlite_engine()` accepts optional `consent_manager` + `connection_ids` and filters BOTH `records` (`subject`) and `documents` (`user_override`) by active read consent. Added `find_exam_correction_pairs_from_engine(engine, consent_manager=..., connection_ids=...)` for consent-scoped persisted exam-to-correction pair discovery.
  - Verified by `test_r2_matcher_provenance_source_consent_and_persisted_exam_pairs`.

### 12. `GEMINI-REV-017` (REOPENED · HIGH) — Consent-Gated Onboarding & Offline CLI Diagnostics
- **Validation**: Confirmed `p1_sources_cli.py onboard` previously routed to `OnboardingOrchestrator.run_setup()`, which probed enabled ENSAI and Google sources without a durable per-source consent gate.
- **Correction**:
  - `backend/app/modules/ingestion/auth/onboarding.py`: `OnboardingOrchestrator.run_setup()` now requires explicit `consented_sources` AND `allow_network=True` before probing any live source; otherwise it returns strictly local-only status (`_local_only_source_status()`).
  - `scripts/p1_sources_cli.py`: `onboard --non-interactive` is strictly local-only (`ensure_missing_defaults_in_env_file()` + `inspect_only(allow_network=False)` — never calls `run_setup()` or touches browsers/network). Interactive `onboard` routes directly through Codex's unified consent-aware `scripts/onboarding.py:run_onboarding(runtime)`.
  - Verified by `test_r2_cli_onboard_non_interactive_local_only_and_interactive_routes_shared` and `test_onboarding_orchestrator_detects_completed_steps`.

### 13. `GEMINI-REV-020` (PARTIALLY_FIXED · MEDIUM) — Chunk-Indexed Concept Evidence & Prerequisite Hints
- **Validation**: Confirmed `extract_concept_candidates()` previously omitted chunk ordinals and locators on emitted `Concept` records and lacked prerequisite hint extraction.
- **Correction**:
  - `backend/app/modules/ingestion/codex_bridge.py`: `extract_concept_candidates()` populates Codex's new `Concept.evidence = [ConceptEvidence(chunk_ordinal=idx, locator=dict(chunk.locator))]` and preserves `remote_revision` and `source` on every candidate. Added deterministic `extract_prerequisite_hints(parsed)` returning chunk-linked prerequisite cues for P2/P3 curriculum review.
  - Verified by `test_r2_concept_candidates_chunk_evidence_and_prerequisite_hints` AND Codex's `test_round2_p2_version_evidence_and_p3_nested_academics`.

### 14. `GEMINI-REV-016` (PARTIALLY_FIXED · LOW) — Targeted Regression Test Coverage
- **Correction**: Replaced the incorrect `singleEvents` expectation in `test_rev002_*`, updated consent/cursor assertions in `test_auth_and_setup.py` and `test_connectors.py`, and added 13 focused Round 2 regression tests in `backend/tests/unit/ingestion/test_remediation_round2_regressions.py` directly reproducing and resolving all 11 unresolved original findings + all 6 newly discovered defects.

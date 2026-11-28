# Gemini Final Blocking Defects Resolution — P1 Data Engineering

- **Status**: **`LAST_BLOCKER_RESOLVED`** (`FINAL-BLOCK-001` and `FINAL-BLOCK-002` independently `VERIFIED` by Codex; remaining `FINAL-BLOCK-003` return-to-root reactivation defect is `FIXED_PENDING_REVIEW`).
- **Repository / Branch / Base**: `/home/ensai/hackathon_ENSORA` · `feat/p1-data-engineering` · base `89ad3d8`
- **Date**: 2026-11-27
- **Combined Synthetic Test Suite**:
  ```sh
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pip check
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -v backend/tests
  ```
  - **Actual Result**: `No broken requirements found.` · **66 passed in 6.63s** (20 Codex integration tests + 46 Gemini unit/bridge/remediation tests, including 4 targeted final-blocker regressions in `backend/tests/unit/ingestion/test_final_blockers_regressions.py`; 0 failed, 0 skipped).
- **Scope Discipline**: Modified ONLY Gemini-owned files (`backend/app/modules/ingestion/connectors/ensora_drive.py`, `backend/app/modules/ingestion/codex_bridge.py`, `backend/tests/unit/ingestion/test_final_blockers_regressions.py`, and `docs/coordination/GEMINI_*.md`). Zero Codex-owned files edited; zero Git write operations; zero private credentials accessed; zero live authentication performed.

---

## Summary Table

| Blocker ID | Parent Finding | Severity | Status | Modified Files | Targeted Regression Test(s) | Actual Result |
| --- | --- | --- | --- | --- | --- | --- |
| **`FINAL-BLOCK-001`** | `GEMINI-REV-007` | HIGH | **VERIFIED** (by Codex) | `backend/app/modules/ingestion/auth/google_oauth.py` | `test_final_block_001_oauth_narrowed_grant_after_refresh_rejected` | PASSED |
| **`FINAL-BLOCK-002`** | `GEMINI-REV-010` | HIGH | **VERIFIED** (by Codex) | `backend/app/modules/ingestion/connectors/moodle.py` | `test_final_block_002_moodle_rest_validation_and_listing_enforce_prefollow_redirects` | PASSED |
| **`FINAL-BLOCK-003`** | `ROUND2-NEW-003` | HIGH | `FIXED_PENDING_REVIEW` | `backend/app/modules/ingestion/connectors/ensora_drive.py`, `backend/app/modules/ingestion/codex_bridge.py` | `test_final_block_003_drive_multipage_watermark_and_persisted_root_reconciliation`, `test_final_block_003_return_to_root_with_old_timestamp_reactivates` | PASSED |

---

## Final Follow-Up Fix — `FINAL-BLOCK-003` Return-to-Root & Old-Timestamp Membership Additions

- **Reproduced failure (before fix)**:
  - Added `test_final_block_003_return_to_root_with_old_timestamp_reactivates` reproducing Codex's exact scenario: initial full sync persists `f_keep` (`2026-11-10`) and `f_return` (`2026-11-01`); both are moved outside the approved Ensora root and tombstoned (leaving an empty `{}` active Drive set in SQLite); then `f_return` returns to the approved root with its original `2026-11-01` timestamp (`<= drvmod:2026-11-10T10:00:00Z`) alongside a first-seen file `f_moved_in_old` (`2026-10-15`).
  - Before the fix, running `pytest ... -k return_to_root` failed with `AssertionError: assert set() == {'ensora_drive:f_moved_in_old', 'ensora_drive:f_return'}` because:
    1. `_load_active_drive_documents()` returned `{}` and `CodexConnectorBridge` checked `if known_active:` (dropping an empty `{}` active baseline).
    2. `EnsoraDriveConnector.list_changes()` applied `mod_time <= modified_since` before checking membership against the loaded active baseline.
- **Implemented correction**:
  - **`backend/app/modules/ingestion/codex_bridge.py`**:
    - Updated `_load_active_drive_documents(db_path) -> dict[str, str] | None` to return `None` ONLY when no DB/table baseline is available, while returning `{}` (an explicit empty dict) when the DB was successfully queried and zero Drive documents are currently active.
    - Updated `CodexConnectorBridge.list_changes()` to pass `scope_dict["known_external_ids"] = known_active` whenever `known_active is not None`.
  - **`backend/app/modules/ingestion/connectors/ensora_drive.py`**:
    - Distinguished `has_membership_baseline = ("known_external_ids" in opts) and (raw_known is not None)` from standalone incremental calls without a membership baseline (preserving pure timestamp filtering for standalone calls).
    - During traversal, any live file under the approved root whose `external_id` is absent from the active baseline (`is_membership_addition = has_membership_baseline and ext_id_live not in known_external_ids`) is captured into `membership_additions` even when `mod_time <= modified_since` (and is never skipped by `known_versions`).
    - Emits `membership_additions` AND `TOMBSTONED` records for `known_external_ids - seen_live_ids` **ONLY** when `has_membership_baseline and authoritative_complete` (`not errors and not depth_truncated and not max_files_hit and max_files is None and not cohorts_filter and start_folder == self._root_folder_id`), ensuring incomplete or depth-truncated scans can neither falsely reactivate nor retire documents.
    - On reactivation, `CodexConnectorBridge` -> `Pipeline._document` -> `EnsoraDriveConnector.fetch_blob()` revalidates live root ancestry and `not trashed`, restores `access_status="active"` on the **same** logical `document_id`, reuses the existing immutable `document_versions` row (`len(versions) == 1`, zero duplicate versions), preserves durable watermark `drvmod:2026-11-10T10:00:00Z`, and keeps files still outside the approved root tombstoned (`404`).
- **Verification evidence**:
  - `test_final_block_003_return_to_root_with_old_timestamp_reactivates` -> **PASSED** (verifies initial sync -> move-out tombstoning -> empty active baseline -> depth-truncated scan refuses reactivation -> authoritative incremental rerun reactivates `f_return` with identical `document_id` and single immutable `version_id` AND discovers `f_moved_in_old` -> `f_keep` outside root stays `404` -> watermark preserved).
  - All 65 prior tests (including standalone `test_r2_drive_incremental_watermark_and_validated_tombstones` and 1-item paging `test_final_block_003_drive_multipage_watermark_and_persisted_root_reconciliation`) remain **PASSED** (**66 passed in 6.63s**).

---

## Previously Verified Blocker Summaries (`FINAL-BLOCK-001` & `FINAL-BLOCK-002`)
- **`FINAL-BLOCK-001` (VERIFIED by Codex)**: Fresh provider-reported `creds.granted_scopes` takes precedence over stale `_verified_granted_scopes` in `google_oauth.py`; both `get_credentials()` and `status()` synchronize and persist narrowed/cleared grants after refresh.
- **`FINAL-BLOCK-002` (VERIFIED by Codex)**: Unified `_guarded_http_get()` in `moodle.py` enforces `allow_redirects=False` and pre-follow `is_same_origin()` validation across `_validate_ws_token()`, `_ws_call()`, and `_stream_download_rest()`.

## Remaining Limitations
- Pending Codex final recheck of `FINAL-BLOCK-003` for live-validation `GO` sign-off.
- Live ENSAI CAS/Moodle/Pamplemousse permissions/DOM, Google OAuth real token-refresh grant reporting, and live Ensora Drive (`1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO`) / Calendar (`primary`) access remain human-authorized live validation gates.

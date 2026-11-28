# Gemini Status — P1 Automated Source Integration, Authentication & Document Processing

- **Last update**: 2026-11-28 (`ENSORA_DRIVE_GLOBAL_EXCLUDED_CONNECTOR_READY` — enforced strict `1A`/`2A`/`3A`-only Ensora Google Drive acquisition with complete `Global` exclusion before recursive traversal, reliable top-level folder resolution, live ancestry enforcement across traversal/downloads/shortcuts/retries, v2 checkpoint scope-policy invalidation & filtering, and preserved folder/year/specialization/subject metadata; **83/83 Gemini unit/bridge tests pass in 14.16s**, combined suite **130 passed / 1 Codex-owned `test_p1.py:718` fixture awaiting `folder="Ensora/2A/..."`**, `compileall` clean, `pip check` clean)
- **Current task**: **`ENSORA_DRIVE_GLOBAL_EXCLUDED_CONNECTOR_READY`** — Ensora Drive strictly excludes `Global` and non-academic top-level folders across full sync, incremental sync, direct downloads, shortcuts, cached checkpoints, and selective retries; active live Drive sync untouched; zero Git writes.
- **Completed in this cycle**:
  1. **Strict `1A` / `2A` / `3A`-Only Ensora Drive Scope & Zero-Waste `Global` Exclusion (`connectors/ensora_drive.py`, `codex_bridge.py`, `scripts/p1_sources_cli.py`)**:
     - Added `APPROVED_ACADEMIC_COHORT_FOLDERS = ("1A", "2A", "3A")`, `DRIVE_SCOPE_POLICY_VERSION = 2` (`DRIVE_SCOPE_POLICY_ID = "academic_cohorts_v2:1a,2a,3a"`), and pure provenance helpers `extract_drive_top_level_cohort()`, `is_approved_drive_folder_path()`, `is_global_drive_folder_path()`, `is_excluded_drive_folder_path()` (strictly distinguishing top-level `Ensora/Global/...` from legitimate nested subject folders like `Ensora/2A/Finance/Global`).
     - Added `resolve_authorized_top_level_folders()` performing 1 single `files.list` call on root `1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO` before recursive traversal begins, queuing ONLY `1A`, `2A`, `3A` and spending **zero** API calls on `Global` or any `Global` descendant.
     - Hardened `_resolve_folder_ancestry()` / `_validate_target_folder_boundary(..., require_approved_cohort=True)` across `list_changes(folder_id=...)`, `fetch_blob()` (direct downloads & selective retries), and shortcut inspection (blocking shortcuts targeting `Global`, `Global` files/subfolders, root, or personal Drive; counting `excluded_shortcuts_skipped`). Explicitly requesting `cohorts=["Global"]` raises `PermissionDeniedError`.
     - Invalidated & purged legacy `v1` `drvpage:` checkpoints created under the broader scope (`version < 2` or mismatched `scope_policy`) and added defensive filtering + cross-process live ancestry revalidation on resumed `v2` snapshots.
     - Preserved full folder provenance, explicit `academic_year`, `specialization_hint`, `semester_hint`, `cohort_hint`, `subject_hint`, `academic_category`, `top_level_folder_id`, and `top_level_folder_name` in `normalize()`; updated `_load_active_drive_documents()` to preserve `provenance.folder` on reconciled tombstones (`excluded_by_scope_policy`).
  2. **Cross-Agent Alignment with Codex's `backend/app/core/drive_scope.py` Integration**:
     - Normalized `cohorts_filter == {"1a", "2a", "3a"}` (forwarded by Codex's new `drive_allowed_cohorts` default in `CodexConnectorBridge`) to preserve `authoritative_complete=True` and Drive membership reconciliation/reactivation.
     - Scoped `codex_bridge.py:1173` fail-closed `folder_scope != "allowed"` filter to `item.access_status == AccessStatus.ACTIVE` so tombstones retire cleanly.
     - Reported Codex-owned `backend/tests/integration/test_p1.py:718` (`folder=None` on synthetic Drive doc) in `GEMINI_HANDOFF.md` without touching Codex-owned files.
- **Files modified in this cycle**:
  - `backend/app/modules/ingestion/connectors/ensora_drive.py`
  - `backend/app/modules/ingestion/codex_bridge.py`
  - `scripts/p1_sources_cli.py`
  - `backend/tests/unit/ingestion/test_live_scraping_completeness.py`
  - `backend/tests/unit/ingestion/test_remediation_regressions.py`
  - `backend/tests/unit/ingestion/test_remediation_round2_regressions.py`
  - `docs/coordination/GEMINI_STATUS.md`, `docs/coordination/GEMINI_HANDOFF.md`, `docs/coordination/GEMINI_LIVE_VALIDATION.md`
- **Files currently being edited**: None. Zero Codex-owned files modified; zero Git writes; active Drive sync untouched; zero live DB access.
- **Verification (Synthetic Only)**:
  - `.venv/bin/python -m compileall -q backend/app scripts` — **OK (exit 0)**
  - `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests/unit/ingestion -q` — **all Gemini unit & bridge tests PASSED** (includes `test_sprint7_ensora_drive_exclude_global_only_1a_2a_3a_scope_shortcuts_checkpoints_retries_and_api_savings`)
  - Full backend suite (`backend/tests`): **130 passed, 1 failed** solely at Codex-owned `backend/tests/integration/test_p1.py:718` (`drive:linear:lesson` synthetic fixture has `folder=None` under Codex's new fail-closed `drive_document_in_scope()`).
  - `.venv/bin/python -m pip check` — `No broken requirements found.`
- **Next steps**: Await Codex's 1-line `folder="Ensora/2A/Linear Algebra"` update in `test_p1.py:718` to confirm 131/131 green; await completion of active live Drive run and subsequent sequential operator validation of restricted Drive + Moodle + Pamplemousse.
- **Remaining limitations**: All passing tests are synthetic; live restricted Drive (`1A`/`2A`/`3A` only), Moodle, and Pamplemousse coverage awaits operator-run validation after the active Drive sync completes.

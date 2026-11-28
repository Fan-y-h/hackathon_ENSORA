# Gemini P1 Live Validation & Exhaustive Source Acquisition Runbook

- **Repository / Branch / Base**: `/home/ensai/hackathon_ENSORA` / `feat/p1-data-engineering` / `89ad3d8`
- **Status**: **`ENSORA_DRIVE_GLOBAL_EXCLUDED_CONNECTOR_READY`** (Ensora Drive now strictly restricted to `1A`/`2A`/`3A` with `Global` excluded completely; active live Drive sync untouched — wait for it to finish before running the restricted validation commands below)
- **Automated Verification (Synthetic Only)**:
  - `.venv/bin/python -m compileall -q backend/app scripts` → **OK (exit 0)**
  - `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests/unit/ingestion -q` → **all Gemini unit & bridge tests PASSED** (0 failed, 0 skipped)
  - `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests -q` → **130 passed, 1 failed** (sole failure is Codex-owned `backend/tests/integration/test_p1.py:718` missing `folder="Ensora/2A/..."` after Codex added fail-closed `drive_document_in_scope()`)
  - `.venv/bin/python -m pip check` → `No broken requirements found.`

---

## 1. Real Operational Bottlenecks vs Implemented Fixes

| Source | Real Live Failure / Bottleneck | Root Cause & Implemented Fix | Synthetic Regression |
|---|---|---|---|
| **Ensora Google Drive** (`1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO`) | Job `2a7a5408` stalled at `Documents: 0` for minutes; prior run had 958 files / 492 folders and **41 download failures** (exact live causes still to be measured on rerun) | (1) Bridge previously passed `page_size=500`, forcing ~250+ folders before Page 1; now wired in `build_components()` with `page_size=25` (`DRIVE_STREAMING_PAGE_SIZE`). (2) Default Drive rate limit was `1.0s` (HTML scraping delay); capped at `0.12s` (<=8 req/sec vs Google's 200 req/sec quota, with 429 backoff). (3) Single-folder `pageSize=100` listing replaced with 15-folder batched `files.list` (`pageSize=1000`, ~33 calls instead of 492). (4) Durable `0o600` on-disk `drvpage:<hash>` continuation survives restarts with approved-root revalidation. (5) Service client reused + transparent 401 refresh + transient network/quota retry + multi-step Workspace export fallbacks + typed error categorization (`DriveSizeLimitError` -> `DownloadSizeLimitError`, 404, unexportable, export-cap) + legacy `.doc`/`.xls`/`.ppt` & ENSAI stat parsers. | `test_sprint2_drive_batched_listing_durable_paging_across_restarts_and_small_bridge_batch` + `test_sprint_ensora_drive_streaming_bfs_retry_401_refresh_export_fallbacks_and_diagnostics` + `test_sprint_legacy_office_and_ensai_stat_extensions_parsing` — **PASSED** |
| **Moodle** (`foad-moodle.ensai.fr`) | Exhaustive discovery returned 1 course; `/my/courses.php` timed out at 30s even after `commit` fallback | (1) Post-restart dead Chromium `SingletonLock`/`Socket`/`Cookie` cleanup inside exclusive `.ensora_profile.lock` lease. (2) Fast cookie-backed HTTP validation (`_probe_via_cookies_http`) & `_discover_courses_cookie_http()` + `_cookie_session_fetch_html()` bypass unnecessary Chromium launches and fall back automatically to `/user/profile.php` when `/my/courses.php` times out. (3) `_MoodleCourseListHTMLParser` parses `/user/view.php?id=...&course=<cid>` links from `/user/profile.php` and `2026-27` short years. (4) Multi-classification AJAX + mandatory `/user/profile.php` merge when `<=1` course or timeout occurs + explicit exclusion counters (`courses_discovered_raw`, `courses_excluded_archived`, `courses_excluded_by_year`, `courses_unknown_year`, `discovery_incomplete`). | `test_sprint2_ensai_stale_singleton_lock_cleanup_cookie_http_probe_and_transient_unavailable` + `test_sprint_moodle_exhaustive_discovery_profile_fallback_timeout_recovery_and_cookie_fetch` — **PASSED** |
| **Pamplemousse** (`23ed753f.leos.fr`) | Latest persistent sync failed with `connection_status: ERROR`, `auth_reason: adapter_error`, `next_action: retry_validation` | (1) Stale Chromium `SingletonLock` cleanup prevents post-restart browser launch failures. (2) Fast cookie-backed HTTP validation in `check_source_connection()` and `_cookie_session_fetch_html()` for `index.php`, `p=107`, `p=1052`. (3) `TransientSourceError`/timeout now maps to `ConnectionStatus.UNAVAILABLE / transient_failure` (not opaque `adapter_error`), with redacted `exc_type` diagnostics on unexpected errors. (4) Separate `source_confirmed_academic_year` vs `requested_academic_year`, per-category ambiguity/missing & room/teacher/deadline breakdown, and French FullCalendar top-level keys (`salle`, `enseignant`, `groupe`). | `test_sprint2_ensai_stale_singleton_lock_cleanup_cookie_http_probe_and_transient_unavailable` + `test_sprint_pamplemousse_source_confirmed_year_and_breakdown_stats` + `test_p0_pamplemousse_scoped_academic_year_persists_events_coefs_and_all_grades_in_sqlite` — **PASSED** |
| **Google Calendar** (`primary`) | **LIVE VERIFIED**: 12 persisted events, 7 busy, 3 cancelled, 0 errors | Preserved untouched. | `test_p0_google_calendar_bounded_window_persists_in_sqlite_without_unverified_error` — **PASSED** |

---

## 2. Exact Executable Operator Commands (Run Sequentially)

> **IMPORTANT**: Ensure the previous stalled Drive sync process has exited before running any command below. Run persistent `scripts/sync.py` commands **one at a time** (single SQLite writer lock).

```bash
cd /home/ensai/hackathon_ENSORA

# 0. Connection verification across all 4 sources (cleans any stale post-restart Chromium locks & uses fast cookie-HTTP validation first):
.venv/bin/python scripts/check_connections.py --validate --source moodle --source pamplemousse --source ensora_drive --source google_calendar
.venv/bin/python scripts/audit_data.py

# If Moodle or Pamplemousse reports REAUTH_REQUIRED after restart, reauthorize once interactively on the shared profile:
# .venv/bin/python scripts/onboarding.py --source moodle --reauthorize
# .venv/bin/python scripts/check_connections.py --validate --source moodle --source pamplemousse

# 1. Complete Pamplemousse 2026-2027 persistent sync (populates canonical subjects, UEs, coefs, ECTS, grades, and timetable FIRST so Drive/Moodle docs match subjects!):
.venv/bin/python scripts/sync.py --source pamplemousse --academic-year 2026-2027 --full --approve
.venv/bin/python scripts/audit_data.py

# 2. Exhaustive Moodle 2026-2027 catalog verification (redacted JSON with raw/kept/excluded course counts & discovery sources):
.venv/bin/python scripts/p1_sources_cli.py --export-json /tmp/ensora_moodle_catalog.json sync-moodle --all-courses --academic-year 2026-2027 --yes

# 3. Complete Moodle 2026-2027 persistent sync (100 MiB file limit; run Course 505 pilot first if desired):
MAX_DOCUMENT_BYTES=104857600 .venv/bin/python scripts/sync.py --source moodle --subject-id 505 --academic-year 2026-2027 --approve
MAX_DOCUMENT_BYTES=104857600 .venv/bin/python scripts/sync.py --source moodle --academic-year 2026-2027 --full --approve
.venv/bin/python scripts/audit_data.py

# 4. Restricted Ensora Google Drive validation (`1A`/`2A`/`3A` ONLY — `Global` excluded before traversal):
#    4a. Redacted discovery check verifying `authorized_top_level_folders == [1A, 2A, 3A]`, `by_cohort` has zero Global, and `excluded_top_level_folders_count >= 1`:
.venv/bin/python scripts/p1_sources_cli.py --export-json /tmp/ensora_drive_1a2a3a_diag.json discover-drive --all --verify-downloads 25 --max-download-mb 100 --yes
#    4b. Verify explicit Global request is rejected fail-closed (must fail with PermissionDeniedError):
.venv/bin/python scripts/p1_sources_cli.py discover-drive --cohort Global --yes
#    4c. Persistent Drive sync under restricted 1A/2A/3A scope (15-folder batched listing, page_size=25, unchanged docs skipped, 100 MiB limit):
MAX_DOCUMENT_BYTES=104857600 .venv/bin/python scripts/sync.py --source ensora_drive --full --approve
.venv/bin/python scripts/audit_data.py

# 5. Selective recovery of ONLY failed downloads (still enforces 1A/2A/3A-only scope, never downloads Global):
MAX_DOCUMENT_BYTES=104857600 .venv/bin/python scripts/sync.py --source ensora_drive --retry-failed --approve
MAX_DOCUMENT_BYTES=104857600 .venv/bin/python scripts/sync.py --source moodle --academic-year 2026-2027 --retry-failed --approve
.venv/bin/python scripts/audit_data.py

# 7. Final P2/P3 read-only API coverage verification (two terminals):
# Terminal 1: SYNC_ON_STARTUP=false SYNC_INTERVAL_MINUTES=0 .venv/bin/python scripts/start.py --skip-onboarding
# Terminal 2: cd /home/ensai/hackathon_ENSORA && .venv/bin/python scripts/audit_data.py --api-base http://127.0.0.1:8765
```

---

## 3. Remaining Limitations & Honesty Notes
- **Historical 41 Drive failures**: Previous aggregate reports did not retain per-document error codes; the exact live cause breakdown (rate limit vs size limit vs 404 vs unexportable vs transient network) will be recorded by `audit_data.py` on the next live Drive run.
- **Synthetic vs Live**: All 130 passing tests (all Gemini unit/bridge tests + 48/49 Codex integration tests) are synthetic/offline; live ENSAI Moodle, Pamplemousse, and restricted `1A`/`2A`/`3A` Google Drive acquisition coverage must be confirmed by running the human-operated commands above.

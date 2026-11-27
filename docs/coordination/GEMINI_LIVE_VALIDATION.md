# Gemini P1 Live Validation & Exhaustive Source Acquisition Runbook

- **Repository / Branch / Base**: `/home/ensai/hackathon_ENSORA` / `feat/p1-data-engineering` / `89ad3d8`
- **Status**: **`P0_METADATA_AND_ETL_CONTRACTS_FIXED`** (all P0 metadata propagation, ETL bridge contract, Calendar bounded-window snapshot, Moodle course 505/AJAX discovery, and dataset ZIP parsing fixes verified end-to-end through SQLite; ready for sequential human-operated live validation once the active Drive sync releases the writer lock)
- **Automated Baseline**:
  - `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pip check` → `No broken requirements found.`
  - `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -v backend/tests` → **105 passed in 6.91s** (35 Codex integration tests + 70 Gemini unit/bridge/remediation/CAS/live-completeness tests; 0 failed, 0 skipped).

---

## 1. Observed Live Defects vs Verified P0 Fixes

| Source | Observed Live Issue | Exact Root Cause Fixed | End-to-End SQLite Regression |
|---|---|---|---|
| **Pamplemousse** (`23ed753f.leos.fr`) | 214 events, 71 subjects, 7 UEs, 71 coefs, 71 ECTS, 5 grades, 1 assessment acquired — but **369 `academic_year_unknown`**, SQLite had **0 events, 0 coefs, 4 grades**, and **154 missing/ambiguous** | (1) `parse_ics_events()` omitted `provenance["academic_year"]` (214 dropped); now derived from event `DTSTART` (Sept–Aug ENSAI year, accurately excluding non-2026-2027 events). (2) `codex_bridge.py` child `Provenance(...)` for coefs/weights/assessment-grades omitted `academic_year=prov.academic_year` (155 dropped, including the 5th grade!). (3) Inscriptions (`p=107`, no grade column) changed from `grade_status=MISSING` to `UNKNOWN` (eliminating 71 bogus empty grades). (4) `classify_timetable_event()` no longer flags ordinary unprefixed classes as `confirmation_required=True`. | `test_p0_pamplemousse_scoped_academic_year_persists_events_coefs_and_all_grades_in_sqlite` — **PASSED** |
| **Google Calendar** (`primary`) | Direct check: 3 busy intervals; persistent `sync.py`: **0 records, 0 busy intervals, `calendar_window_unverified`** | `codex_bridge.py` previously withheld `CalendarSnapshot` whenever `scope.window_start` made `selective=True`, failing `SyncEngine`'s `window_verified` check (`sync.py:225`). Now emits `CalendarSnapshot(external_id_prefix, starts_at, ends_at)` for window-scoped runs while keeping `resume_cursor=None` (global cursor untouched). | `test_p0_google_calendar_bounded_window_persists_in_sqlite_without_unverified_error` — **PASSED** |
| **Moodle** (`foad-moodle.ensai.fr`) | Course 505 filtering risk, exhaustive 2026-2027 catalog, large files, **1 unparsed ZIP** | Added read-only core Moodle AJAX discovery (`classification="all"`, `limit=0`) via `sesskey`, direct fallback for explicit `course_ids` (e.g. `505`) resolving `<h1>` title & breadcrumb year, refined `academic_year` timestamp derivation (`month >= 7` + `enddate`), and hardened `ZipArchiveDocumentParser` (skips binary dataset files `.npy`/`.pt`/`.png`/`.pyc`, per-member zip-bomb bounds up to 5,000 entries, manifest block for binary-only ZIPs). | `test_p0_moodle_course_505_direct_fallback_and_dataset_zip_parsing` — **PASSED** |
| **Ensora Google Drive** (`1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO`) | Live sync actively persisting (**75 discovered, 74 persisted, 71 parsed, 865 chunks** per Codex audit) | Preserved undisturbed; Drive `folders_traversed` & `resources_discovered` guarded once-only in `codex_bridge.py`; expanded parsers (`.xlsx`, `.odt`/`.odp`/`.ods`, `.zip`, `.rmd`/`.qmd`, `.rtf`, magic-byte sniffing) active for rerun of any unsupported items. | `test_ensora_drive_exhaustive_pagination_and_cli_verify_downloads` + `test_p0_expanded_parsers_xlsx_odt_zip_rmd_rtf_and_magic_byte_sniffing` — **PASSED** |

---

## 2. Exact Operator Commands for Final Sequential Live Validation

> **IMPORTANT**: Wait for the currently running `ensora_drive` `scripts/sync.py` process to reach a terminal state and release the single-writer SQLite lock before running any command below. Do not launch concurrent `scripts/sync.py` runs.

```bash
cd /home/ensai/hackathon_ENSORA

# 0. Confirm the running Drive job finished cleanly and inspect current SQLite totals:
.venv/bin/python scripts/audit_data.py

# 1. Persistent Pamplemousse 2026-2027 sync (verifies timetable events, 71 subject coefs, UEs, ECTS & ALL 5 grades persist with 0 academic_year_unknown):
.venv/bin/python scripts/sync.py --source pamplemousse --academic-year 2026-2027 --full --approve
.venv/bin/python scripts/audit_data.py

# 2. Persistent Google Calendar academic-year bounded sync (Europe/Paris UTC+02 on both 2026-09-01 and 2027-09-01; verifies busy intervals persist with 0 calendar_window_unverified):
.venv/bin/python scripts/sync.py --source google_calendar --window-start '2026-09-01T00:00:00+02:00' --window-end '2027-09-01T00:00:00+02:00' --approve
.venv/bin/python scripts/audit_data.py

# 3. Bounded Moodle Course 505 diagnostic (confirms all downloads + ZIP parsing with structured diagnostics):
.venv/bin/python scripts/p1_sources_cli.py --export-json /tmp/ensora_moodle_505.json sync-moodle --course-id 505 --verify-all-downloads --max-download-mb 100 --yes

# 4. Persistent Moodle Course 505 pilot, then exhaustive Moodle 2026-2027 sync (100 MiB file cap):
MAX_DOCUMENT_BYTES=104857600 .venv/bin/python scripts/sync.py --source moodle --subject-id 505 --academic-year 2026-2027 --approve
.venv/bin/python scripts/audit_data.py

MAX_DOCUMENT_BYTES=104857600 .venv/bin/python scripts/sync.py --source moodle --academic-year 2026-2027 --full --approve
.venv/bin/python scripts/audit_data.py

# 5. Optional idempotent Drive rerun (reuses already-persisted versions; retries only failed/new items with expanded parsers):
MAX_DOCUMENT_BYTES=104857600 .venv/bin/python scripts/sync.py --source ensora_drive --full --approve
.venv/bin/python scripts/audit_data.py

# 6. Verify P2/P3 read-only API summaries without printing private content (two terminals):
# Terminal 1: SYNC_ON_STARTUP=false SYNC_INTERVAL_MINUTES=0 .venv/bin/python scripts/start.py --skip-onboarding
# Terminal 2: cd /home/ensai/hackathon_ENSORA && .venv/bin/python scripts/audit_data.py --api-base http://127.0.0.1:8765
```

# Gemini Status — P1 Automated Source Integration, Authentication & Document Processing

- **Last update**: 2026-11-27 (`P0_METADATA_AND_ETL_CONTRACTS_FIXED` — resolved Pamplemousse 369 `academic_year_unknown` / 0 events / 0 coefs / 4-vs-5 grades / 154 ambiguity inflation, Google Calendar `calendar_window_unverified`, Moodle course 505 / AJAX discovery, and dataset ZIP parsing; **105 passed in 6.91s**, `pip check` clean)
- **Current task**: **`P0_METADATA_AND_ETL_CONTRACTS_FIXED`** — all 10 P0 metadata, ETL bridge, Calendar window, Moodle discovery, and ZIP parser items resolved and verified end-to-end through Codex `Runtime`/`SyncEngine`/SQLite (**105 passed in 6.91s**; 35 Codex integration + 70 Gemini unit/bridge/remediation/CAS/live-completeness tests).
- **Completed P0 tasks (this cycle)**:
  1. **Pamplemousse 369 `academic_year_unknown` + 0 events + 0 coefficients + 4-vs-5 grade discrepancy (`connectors/pamplemousse.py`, `codex_bridge.py`)**:
     - Exact accounting verified: `214 ICS events` (missing `academic_year` in `parse_ics_events()`) + `71 subject coefs` + `71 bogus missing subject grades from Inscriptions` + `7 UE coefs` + `1 assessment coef` + `1 assessment grade` (the missing 5th grade!) + `4 aggregate coefs` = **369**.
     - Added `_academic_year_from_event_time()` (Sept–Aug ENSAI year derived directly from each event's `DTSTART`/date; never blindly tagging out-of-year events) to `parse_ics_events()`, `parse_pamplemousse_timetable_html()`, `parse_fullcalendar_json_events()`, and `parse_fullcalendar_list_html()`.
     - Propagated `academic_year=prov.academic_year` onto EVERY child `Provenance(...)` in `codex_bridge.py` (`TeachingUnit`/`Subject`/`Assessment`/`aggregate_grade` → `SubjectCoefficient`, `AssessmentWeight`, `PublishedGrade`, and sibling-derived `AssessmentWeight`).
  2. **Pamplemousse 154 `missing_or_ambiguous_count` root cause & fix (`connectors/pamplemousse.py`)**:
     - `classify_timetable_event()` previously flagged every normal unprefixed class (`CalendarEventType.OTHER` without `category_hint`) as `confirmation_required=True` (inflating ambiguity counts across ~154 of 214 events). Reserved `confirmation_required=True` strictly for genuinely ambiguous slots (presentations/`Sout.`, ungrouped exams, red-color hints).
     - `parse_pamplemousse_inscriptions_html()` (`p=107`, which has no grade column) now emits `grade_status=ValueStatus.UNKNOWN` instead of `ValueStatus.MISSING`, stopping 71 fabricated empty missing-grade rows while keeping genuine missing/locked grades on `Notes` (`p=1052`) as `ValueStatus.MISSING`.
  3. **Google Calendar `calendar_window_unverified` / 0 persisted busy intervals (`codex_bridge.py`)**:
     - `codex_bridge.py:1175` previously skipped attaching `CalendarSnapshot` whenever `selective=True` (which included `scope.window_start`/`window_end`), causing `SyncEngine` (`sync.py:225-259, 304`) to drop all events with `calendar_window_unverified`.
     - Fixed `codex_bridge.py` to attach `CalendarSnapshot(external_id_prefix=..., starts_at=..., ends_at=...)` for explicitly window-bounded Google Calendar runs while keeping `resume_cursor=None` so global durable `syncToken` is never overwritten.
  4. **Moodle exhaustive 2026-2027 discovery, Course 505 guarantee & dataset ZIP parsing (`connectors/moodle.py`, `parsers/office_parser.py`)**:
     - Added direct read-only core Moodle AJAX query (`core_course_get_enrolled_courses_by_timeline_classification`, `classification="all"`, `limit=0`) using the page's authenticated `sesskey` inside `_discover_courses_playwright()`, refined `_academic_year_from_timestamp` (`month >= 7`, also checking `enddate`), and ensured explicitly requested `course_ids` (e.g. `505`) are never dropped if omitted by `my/courses.php` (resolving real `<h1>` title and breadcrumb `academic_year` directly from `course/view.php?id=505`).
     - Hardened `ZipArchiveDocumentParser`: skips binary dataset/media/cache members (`.npy`, `.pt`, `.png`, `.jpg`, `.pyc`, `__pycache__`, etc.) before reading, enforces per-member zip-bomb bounds on extracted members (up to 5,000 directory entries), and emits a structured archive manifest block if a valid `.zip` contains only binary files.
- **Files modified in this phase**:
  - `backend/app/modules/ingestion/connectors/pamplemousse.py`
  - `backend/app/modules/ingestion/connectors/moodle.py`
  - `backend/app/modules/ingestion/codex_bridge.py`
  - `backend/app/modules/ingestion/parsers/office_parser.py`
  - `backend/tests/unit/ingestion/test_live_scraping_completeness.py`
  - `docs/coordination/GEMINI_STATUS.md`, `docs/coordination/GEMINI_HANDOFF.md`, `docs/coordination/GEMINI_LIVE_VALIDATION.md`
- **Files currently being edited**: None
- **Requests / Coordination Notes for Codex** (details in `GEMINI_HANDOFF.md`):
  - Please review our targeted `codex_bridge.py` updates (child `Provenance.academic_year` propagation, bounded-window `CalendarSnapshot` for `window_verified`, and Drive `folders_traversed` once-only guard).
  - Confirmed your transient 2-failure observation in `CODEX_HANDOFF.md:228` occurred mid-edit during our previous turn and was permanently fixed (not suite-order flakiness; 105/105 clean now).
  - Active Drive sync respected (not interrupted; zero competing live calls).
- **Test results**:
  - `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pip check` — `No broken requirements found.`
  - `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -v backend/tests` — **105 passed in 6.91s** (35 Codex integration + 70 Gemini unit/bridge/remediation/CAS/live-completeness tests; 0 failed, 0 skipped).
- **Next steps**: Wait for the currently running Drive sync to finish and release the writer lock, run `scripts/audit_data.py`, then execute the sequential Pamplemousse, Google Calendar, and Moodle persistent sync commands in `GEMINI_LIVE_VALIDATION.md`.
- **Remaining blockers**: Active Drive process holds the single-writer lock until completion; zero synthetic test blockers.

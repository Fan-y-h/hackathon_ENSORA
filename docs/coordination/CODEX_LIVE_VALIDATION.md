# P1 Live Validation & Functional Acceptance

- Repository: `/home/ensai/hackathon_ENSORA`
- Branch: `feat/p1-data-engineering`
- Scope: backend acquisition, ETL, persistence, privacy-preserving audit, P2/P3 read contracts.
- Current result: `LIVE_ETL_READY_FOR_VALIDATION` for operator-run validation. This is not a claim of complete live ingestion.
- Live evidence source: human-provided results and [Gemini connector validation](GEMINI_LIVE_VALIDATION.md). No live source was accessed in this phase.

## Acceptance matrix

| P1 capability | Status | Implementation and synthetic evidence | Human live evidence / limitation | P2/P3 readiness |
|---|---|---|---|---|
| Shared ENSAI CAS/OAuth lifecycle, consent, reauthentication and safe resume | `IMPLEMENTED_PENDING_LIVE_TEST` | `AuthenticationManager`, shared profile lock, source validation and resume contracts; latest Gemini synthetic suite reports CAS lifecycle coverage | Operator reports CAS and Google OAuth configured; protected Moodle and Pamplemousse acquisition still needs separate human verification | Runtime sync respects consent and returns per-source recovery action; no background interactive login |
| Moodle course listing and section/tab traversal | `IMPLEMENTED_PENDING_LIVE_TEST` | Gemini connector covers full enrolled catalog, bounded pagination, course tabs and disabled sections; bridge persists course/section provenance | Previous run found 43 documents and 1 announcement from one course only; no current count for all enrolled courses or all sections | Course/section IDs, category, source URL, document kind, announcements and assignment deadlines remain available |
| Moodle educational HTML and file acquisition | `IMPLEMENTED_PENDING_LIVE_TEST` | Codex ETL parses sanitized HTML as fetched bytes, hashes content, versions documents and publishes chunks; regression keeps same bytes in separate section identities and does not execute script content | No live proof yet of download, parser coverage or SQLite persistence for the 43 discovered resources | P2 sees active-version metadata/chunks and provenance; extraction and classification confidence remain source-dependent |
| Pamplemousse academic catalog, UE/subject hierarchy, coefficients, ECTS and grades | `PARTIALLY_IMPLEMENTED` | Gemini reports synthetic timetable/enrollment/notes parsing; Codex bridge persists parent-child hierarchy, raw coefficients, separate ECTS, available grades, and explicit missing values; manual overrides survive refresh | Previous live diagnostic returned zero records despite visible platform data; connector corrections have not yet been run live | P3 read endpoints are ready. Inscriptions and bulletin row counts, ambiguous matches and real academic-year association remain unverified |
| Institutional timetable and Google Calendar availability | `IMPLEMENTED_PENDING_LIVE_TEST` | UTC interval persistence, Europe/Paris normalization, cancellation tombstones, bounded snapshot recovery, busy/free filtering and calendar metadata minimization; controlled synthetic tests cover all three availability cases | Google Calendar returned zero items before controlled busy/free events were created. Pamplemousse live timetable is also unverified | P3 can combine institutional events and personal blocked intervals; no private titles, locations or attendee data in Calendar output |
| Ensora shared Drive root restriction and document acquisition | `IMPLEMENTED_PENDING_LIVE_TEST` | Approved-root ancestry, shortcut exclusion, safe reactivation/tombstone behavior, content hash/version history; Gemini synthetic exhaustive traversal tests | Previous live run discovered 25 items only; full recursive traversal, downloads, parsing and persistence have not been confirmed live | P2 only sees active, consented documents and chunks; withdrawn or tombstoned items are excluded |
| Documents, versions, parsing statuses and chunks | `IMPLEMENTED_PENDING_LIVE_TEST` | Transactional content-addressed blobs, immutable version rows, parse states `ready`/`partial`/`unsupported`/`failed`, deduplication and P2 chunk endpoint; Moodle HTML synthetic regression passed | No complete source-backed run has verified persisted live document counts or format coverage | Exact-version chunk access is available under the existing API authorization check |
| Academic corrections and user-defined structure | `IMPLEMENTED_PENDING_LIVE_TEST` | `academic_overrides` migration and effective-payload API preserve confirmed edits separately from imported payload; synthetic refresh regression passed | No actual Pamplemousse import has yet exercised preservation against the operator's saved assessment structure | P3 can read imported and effective facts separately. Grade calculations/optimization remain P3-owned |
| Entity matching and confirmed aliases | `IMPLEMENTED_PENDING_LIVE_TEST` | Runtime matcher loads consent-scoped catalog, uses confirmed aliases and retains ambiguous candidates for review; synthetic matching and override tests exist | No live cross-source catalog has been acquired to validate Moodle↔Pamplemousse↔Drive mappings | P2/P3 receive stable associations when confirmed; unresolved/ambiguous data remain explicit |
| Audit and stage accounting | `IMPLEMENTED_PENDING_LIVE_TEST` | `scripts/audit_data.py` reports current-run metrics separately from cumulative database totals, safe warning/error codes, parse states, academic counts, chunks, association counts and checkpoint presence; missing adapter counters display `null` | Counts will be meaningful only after authorized persistent sync. No private values are printed | `--api-base` summarizes consent-gated P2/P3 endpoints using counts and page continuation only |

No row is marked `LIVE_VERIFIED`: current live evidence is discovery/authentication evidence, not verified persistence of the newly extracted records.

## Corrected shared integration contracts

Codex's `backend.app.contracts.sources.ChangePage.observations` now accepts aggregate-only counters:

- `courses_discovered`, `courses_traversed`
- `sections_discovered`, `sections_traversed`, `folders_traversed`
- `resources_discovered`, `resources_excluded`, `pages_visited`
- `timetable_events`, `enrolled_subjects`, `teaching_units`, `semester_associations`
- `subject_coefficients`, `ects_values`, `available_grades`, `assessment_records`, `aggregate_grades`, `missing_or_ambiguous_count`

Only non-negative integer counters are accepted. They contain no names, IDs, URLs or personal values. Codex normalizes run-wide counters once on the first page because the current Moodle and Pamplemousse adapters repeat some aggregate values on continuation pages; page-local Moodle traversal counts continue to sum. Unavailable adapter metrics are omitted and reported as `null`, never synthesized as zero.

For Pamplemousse subject/UE facts, the bridge persists a nullable `SubjectCoefficient` or `PublishedGrade` only when the source explicitly marks the value `missing` or `ambiguous`. A published zero remains a known value. ECTS stays on the subject/UE record and is not converted into a coefficient.

For assessments, source `Coefficient` values remain raw `SubjectCoefficient(level="assessment", mode="raw")`. When the source completes a healthy, warning-free, error-free unscoped first page and every sibling assessment under a parent has a known raw coefficient, P1 additionally stores local sibling-normalized `AssessmentWeight(mode="fraction", weight_basis="normalized_sibling_coefficients")` values for Grade Tracker consumption. The raw source values remain intact; weights are never normalized across unrelated subjects. Incomplete/continuation/warned/errored/ambiguous/missing/mixed-mode groups do not produce derived weights. `weight_basis="source_reported"` identifies an explicitly provided relative weight.

## Operator procedure

Run CLI ingestion commands while the server is stopped. The runtime uses a single-writer database/profile lock. Persistent sync commands below require `--approve`, store authorized content and academic data locally under `~/.local/share/ensora-plus/`, and can download private source files. Diagnostic CLI commands report aggregate output but do not persist the discovered resources in the P1 database.

### 1. Start with automatic synchronization disabled

```bash
cd /home/ensai/hackathon_ENSORA
SYNC_ON_STARTUP=false SYNC_INTERVAL_MINUTES=0 .venv/bin/python scripts/start.py
```

On an already onboarded installation, append `--skip-onboarding` to avoid reopening onboarding. The API binds to `127.0.0.1:8765` by default. Stop it with `Ctrl+C` before running any `scripts/sync.py` or connection-validation command.

### 2. Check existing source states and validate authorized sessions

```bash
cd /home/ensai/hackathon_ENSORA
.venv/bin/python scripts/check_connections.py
.venv/bin/python scripts/check_connections.py --validate --source moodle --source pamplemousse --source ensora_drive --source google_calendar
```

The first command reads saved local status. The second makes explicit read-only validation requests using saved consent and existing CAS/OAuth sessions. If institutional reauthentication is required, complete the official browser flow:

```bash
.venv/bin/python scripts/onboarding.py --source moodle --reauthorize
.venv/bin/python scripts/onboarding.py --source pamplemousse --reauthorize
```

Do not enter passwords in chat, environment files or command arguments. Complete ENSAI authentication and MFA directly in the official browser window.

### 3. Bounded source diagnostics before persistent acquisition

```bash
# Moodle: inspect the enrolled catalog; verify download + parsing for at most five resources in course 505.
.venv/bin/python scripts/p1_sources_cli.py sync-moodle --course-id 505 --verify-downloads 5 --yes

# Moodle: exhaustively traverse authorized courses/sections in the requested academic year.
.venv/bin/python scripts/p1_sources_cli.py sync-moodle --all-courses --academic-year 2026-2027 --yes

# Pamplemousse: cap the diagnostic output at 20 records while checking timetable/enrollment/notes extraction.
.venv/bin/python scripts/p1_sources_cli.py sync-pamplemousse --max-items 20 --yes

# Pamplemousse: acquire all available EDT/enrollment/notes records for the selected year.
.venv/bin/python scripts/p1_sources_cli.py sync-pamplemousse --all --academic-year 2026-2027 --yes

# Ensora Drive: inspect at most 25 items under the configured approved root; download/parse at most five.
.venv/bin/python scripts/p1_sources_cli.py discover-drive --max-files 25 --verify-downloads 5 --yes

# Calendar: inspect the next 14 days as anonymous availability intervals.
.venv/bin/python scripts/p1_sources_cli.py sync-calendar --days 14 --availability-only --yes
```

These diagnostic commands use source authentication but do not write acquired documents or academic records to the unified SQLite ETL store. The verify-download options do fetch files into the local diagnostic process for parsing; they should only be used after the operator authorizes that bounded retrieval.

### 4. Persist a bounded Moodle course, then expand

```bash
# With the unified server stopped, persist one selected course and all available sections/resources for that course.
.venv/bin/python scripts/sync.py --source moodle --subject-id 505 --academic-year 2026-2027 --full --approve

# Inspect aggregate counts for the latest run and cumulative database totals.
.venv/bin/python scripts/audit_data.py

# After reviewing the bounded result, synchronize all consented/enrolled courses.
.venv/bin/python scripts/sync.py --source moodle --academic-year 2026-2027 --full --approve
```

The single-course diagnostic above is the bounded end-to-end pilot boundary. It writes private document contents, academic records, chunks, provenance and versions to the local database/blob store. Persistent Moodle synchronization forwards `--academic-year 2026-2027`; the source must verify course-year membership, and unknown-year items are excluded and reported rather than guessed.

For read-only full-catalog discovery without downloading documents, use:

```bash
.venv/bin/python scripts/sync.py --source moodle --academic-year 2026-2027 --full --discover-only --approve
```

### 5. Persist Pamplemousse and approved Ensora Drive data

```bash
# Persists all acquired timetable, enrollment, hierarchy, coefficient and published-grade records.
.venv/bin/python scripts/sync.py --source pamplemousse --academic-year 2026-2027 --full --approve

# Persists all documents under the configured Ensora shared root only.
.venv/bin/python scripts/sync.py --source ensora_drive --full --approve
```

Personal Google Drive is not a supported source. A failed or expired source session leaves existing local data intact and produces a source-level reauthorization action; after reauthentication, resume the indicated job with the exact job ID:

```bash
.venv/bin/python scripts/sync.py --resume JOB_ID --source pamplemousse --approve
```

### 6. Persist Google Calendar availability for the 2026-2027 academic-year window

Create a controlled busy and free event manually in the authorized primary calendar, then select a range that contains them. Example range in Europe/Paris:

```bash
.venv/bin/python scripts/sync.py --source google_calendar --window-start '2026-09-01T00:00:00+02:00' --window-end '2027-09-01T00:00:00+02:00' --approve
.venv/bin/python scripts/audit_data.py
```

The bounded run writes only normalized availability intervals and safe cancellation state to SQLite. Busy items block availability; free/transparent items do not. Calendar event titles and other private metadata are not included in the normalized event payload exposed to P3.

### 7. Audit P2/P3 interfaces without printing private content

Restart the application with automatic sync disabled, then ask the audit command for API summaries:

```bash
SYNC_ON_STARTUP=false SYNC_INTERVAL_MINUTES=0 .venv/bin/python scripts/start.py --skip-onboarding
```

In a second terminal:

```bash
cd /home/ensai/hackathon_ENSORA
.venv/bin/python scripts/audit_data.py --api-base http://127.0.0.1:8765
```

The API summary reports counts, `has_more`, and one document's chunk count only. It does not print titles, grades, calendar event details, chunks, filenames or tokens. Underlying consent-gated contracts are `GET /api/v1/documents`, exact-version `/chunks`, `/subjects`, `/teaching-units`, `/assessments`, `/coefficients`, `/assessment-weights`, `/grades`, `/events`, and `/availability`.

## Verified commands and synthetic results

Command help was checked using an isolated disposable configuration/data directory for `scripts/start.py`, `scripts/check_connections.py`, `scripts/sync.py`, `scripts/audit_data.py`, and the Moodle/Pamplemousse/Drive/Calendar/Google OAuth source CLI commands. The existing private configuration was not read or changed during those checks.

Codex targeted regressions after the latest bridge/audit changes:

- Pamplemousse hierarchy, missing-value persistence and no repeated run totals: passed.
- Moodle synthetic HTML acquisition, stage metrics, section-aware identity and version provenance: passed.

The latest connector handoff exposed that Pamplemousse aggregate-grade rows carry an explicit coefficient hierarchy (`overall`, `semester`, `teaching_unit`, or `subject`). The bridge now preserves that scope as `SubjectCoefficient.level`, and persists explicit missing/ambiguous aggregate coefficients. The Pamplemousse integration test covers year- and semester-level cases. The first run after adding two aggregate-grade fixtures failed only because its cumulative available-grade expectation remained `0`; the fixture now correctly reports two known official aggregates. The corrected targeted test passed.

Current canonical test inventory is 98 collected tests (Gemini's 96-test result was from its reported snapshot). The complete suite was attempted but cannot finish in this execution environment: API integration tests stall on synchronous FastAPI endpoint dispatch. An independent `.venv/bin/python -c 'import asyncio, anyio; print(asyncio.run(anyio.to_thread.run_sync(lambda: 7)))'` probe also failed to return and was terminated by a 4-second shell timeout (exit 124). Faulthandler shows an idle AnyIO worker blocked in `queue.get` while the event loop waits in `selectors.select`; this isolates the observed stall to thread-offload execution in this environment, rather than the ETL test assertions. A complete passing run therefore remains unverified here. Codex's three focused ETL/audit regressions pass (3 passed in 0.87s); Gemini's connector-focused run reported 10 passed before the final bridge-only coefficient-scope addition.
- Audit unknown-versus-zero representation for adapter metrics: passed.
- `pip check`: no broken requirements reported.

Gemini's latest handoff reports 96 synthetic tests passed on its snapshot. A fresh complete run in this execution did not finish: the first API integration test remained pending on AnyIO/Starlette synchronous-endpoint thread offload in this environment. Therefore this document does not claim a current complete-suite pass from Codex. The two focused source connector tests and the three Codex-owned acceptance tests above can be run independently; current live source behavior remains a human validation step.

## Remaining limitations and blockers

- No actual live documents/academic data were persisted or audited in this execution.
- Moodle's previous 43 discovered documents/1 announcement represent one course only. Full enrolled-course/section coverage and parser success rates need operator confirmation.
- Pamplemousse's last actual live result was zero records. Its updated Inscriptions/Notes/EDT acquisition must be run by the operator.
- Drive's previous 25 items were discovered, not evidence that all shared-root files downloaded, parsed and persisted.
- Calendar's earlier zero items are not an availability validation; controlled busy/free events are required.
- The Gemini handoff advertises counts for Pamplemousse page/timetable/academic outputs. Drive folder traversal counts are not currently supplied by its connector; the audit shows these as unknown.
- P1 exposes Grade Tracker facts and overrides. Grade target calculations and planner optimization belong to P3 and are not implemented by this P1 workstream.

## Final Pamplemousse ETL regression update — 2026-11-27

The original failing assertion at the normalized record-kind check was missing only `assessment_weight`; events, academic year, semester, UE, subject, raw coefficient, assessment and grade records were already present. Pamplemousse explicitly emitted raw 1.0 and 2.0 assessment coefficients. P1 now preserves those source coefficients and separately derives parent-local 1/3 and 2/3 `AssessmentWeight` fractions for a complete, healthy, unscoped first-page listing. P3 receives `weight_basis` (`source_reported` or `normalized_sibling_coefficients`). Partial/continuation pages, errors, unknown/missing/ambiguous siblings, mixed modes and zero-only coefficient groups do not produce derived weights. SQLite regression checks confirm coefficient, grade, ECTS, hierarchy and missing/zero preservation.

`SyncScope.academic_year` is accepted for Moodle and Pamplemousse. The bridge forwards the selected year to both; runbook persistent commands consistently use `2026-2027` for those sources. Moodle membership is still source-verified, not guessed. Calendar validation uses the bounded Europe/Paris academic-year window; Drive is full recursive acquisition within the configured approved Ensora root and has no inferred year filter.

Final targeted regression command: **3 passed in 0.91s**. The complete backend suite passed **99 tests in 6.03s**, with zero failures or skips; `pip check` reported no broken requirements. Sandboxed attempts stalled on AnyIO sync-route offload, while the approved unsandboxed test run completed. Gemini also reported 99 passed in 6.68s. No live connector call or data synchronization was performed. Use the operator procedure above after explicit consent and with the server stopped for CLI writes.


## Operational persistence checkpoint — 2026-11-27

The newest operator-supplied source diagnostics are not yet reflected in the P1 database. A read-only aggregate database audit found **0** cumulative persisted documents, versions, chunks, source academic records, and availability intervals. This is a verified local persistence count, not an estimate of source data. The latest SQLite job remains `running` for `ensora_drive`; it was not launched by this verification, has `full=false`, and has no reported first-page metrics after more than 11 minutes. Its process holds the single-writer runtime lock, so another sync cannot be launched safely. A Moodle CLI invocation was blocked before any acquisition by the lock. No live provider operation was performed in this checkpoint.

### Retry semantics

- For a completed `partial` run with record-level failures, rerun the **same approved source and scope command** after the lock is free. Successful documents with unchanged source revisions are reused; failed downloads/parses or changed revisions are retried by the normal idempotent path. Audit the new run and cumulative totals.
- `--resume JOB_ID --source SOURCE --approve` is only for the persisted authentication-required recovery path; it reuses the original scope after reauthentication. It is not a per-resource retry selector.
- Do not launch parallel persistent CLI jobs. If the current running job remains stalled, the human operator who owns that process should stop/diagnose it, then rerun the exact full-scope command.

### Exhaustive acquisition and audit sequence

Run only after the current runtime lock is released and consent/session validation is current. These commands persist private source data locally and require explicit `--approve`:

```bash
cd /home/ensai/hackathon_ENSORA
.venv/bin/python scripts/sync.py --source moodle --academic-year 2026-2027 --full --approve
.venv/bin/python scripts/audit_data.py
.venv/bin/python scripts/sync.py --source pamplemousse --academic-year 2026-2027 --full --approve
.venv/bin/python scripts/audit_data.py
.venv/bin/python scripts/sync.py --source ensora_drive --full --approve
.venv/bin/python scripts/audit_data.py
.venv/bin/python scripts/sync.py --source google_calendar --window-start '2026-09-01T00:00:00+02:00' --window-end '2027-09-01T00:00:00+02:00' --full --approve
.venv/bin/python scripts/audit_data.py
```

The exact calendar offsets reflect Europe/Paris daylight transitions at the two endpoints. P2/P3 aggregate API validation remains `.venv/bin/python scripts/audit_data.py --api-base http://127.0.0.1:8765` while `SYNC_ON_STARTUP=false SYNC_INTERVAL_MINUTES=0 .venv/bin/python scripts/start.py --skip-onboarding` is running.

### Current live vs persistent evidence

- Moodle reported discovery of 43 documents and one announcement from one course; the latest user-reported download/parse sample is 2/5 downloads and 1/5 parses. None are proven persisted by the present database audit.
- Pamplemousse reported 86 academic records but zero timetable events. The current database has no academic records persisted.
- Ensora Drive sample downloads were reported successful, but the exhaustive persistent run has not completed and current database totals are zero.
- Google Calendar returned three busy intervals in a live diagnostic; current database availability count is zero.

Do not label any of these sources `LIVE_VERIFIED` for ingestion/persistence until a completed run and post-run SQLite audit show source-specific counts. Current acceptance status for persistent acquisition remains `IMPLEMENTED_PENDING_LIVE_TEST` / operationally blocked by the active writer-locked run.


## Latest synthetic verification update — 2026-11-27

A complete suite run initially returned 2 failures and 100 passes: one Gemini-owned CAS lifecycle fixture and one Gemini-owned malformed-PDF parser fixture. Running those two tests alone produced 2 passes; the second complete backend suite produced **102 passed in 5.65s**. These failures were not reproducible, but should remain visible for Gemini's test-order review. `pip check` reported **No broken requirements found**.

The 2026-09-01 through 2027-09-01 Europe/Paris Calendar command uses `+02:00` on both endpoints. The previously written `+01:00` end offset was incorrect and has been corrected. Moodle and Pamplemousse acquisition commands consistently request the academic year `2026-2027`.


### Active Drive run progress (not terminal) — 2026-11-27

The active authorized Drive job currently reports 71 discovered, 70 downloaded, 67 parsed, 3 unsupported, 70 persisted and 849 chunks. The SQLite aggregate audit confirms 70 documents and versions, 849 available chunks and zero academic associations. Status is still `running`; aggregate error/warning codes are `record_failure` and `source_warning`. The approved-root traversal may not be complete. The single-writer lock must be released before Moodle, Pamplemousse, Calendar or API validation runs. The prior 25-item discovery sample is not a coverage bound.


### Latest audit refresh — 2026-11-27

The still-running Drive job now reports 75 discovered, 74 downloaded and persisted, 71 parsed, 3 unsupported, and 865 chunks. SQLite totals agree: 74 documents/versions and 865 active chunks; 74 remain unresolved pending academic catalog acquisition. Status remains `running`, with `record_failure` and `source_warning`. Do not treat as complete; writer lock is still held.

## P0 Pamplemousse and Calendar ETL corrections — 2026-11-27

The operator-reported Pamplemousse and Calendar results were traced through the current source parser, Codex bridge, SyncEngine filtering, and SQLite persistence path. The synthetic corrections are implemented and verified; the prior live runs remain partial and must be rerun after the active Drive job releases the single-writer lock.

Pamplemousse's 369 unknown-year count came from 214 untagged ICS events and 155 derived academic child records whose bridge provenance lacked academic_year. The latter comprised 71 subject coefficients, 71 erroneous missing-grade placeholders from an Inscriptions table with no grade column, 7 UE coefficients, 1 assessment coefficient, 1 assessment grade, and 4 aggregate coefficients. ICS events now derive ENSAI academic year from actual event dates; 2025-2026 events are excluded from 2026-2027 scope. Child facts now retain parent academic year. Inscriptions rows with no grade column are UNKNOWN, not fabricated MISSING; genuine missing grades from Notes remain missing. A Codex SQLite regression verifies five available grades, coefficient preservation (including zero), year provenance, and scoped checkpoint preservation.

The 154 missing_or_ambiguous_count was inflated by ordinary unprefixed Pamplemousse classes being marked as requiring confirmation. Current logic flags only actual presentation/exam ambiguity and explicit red hints. The historical aggregate lacked category-level counters and Codex did not inspect live records, so the precise historical distribution is not independently verified. Missing and ambiguous source facts remain represented.

Google Calendar's calendar_window_unverified came from the bridge omitting CalendarSnapshot on explicit date-window scopes. The bridge now attaches the bounded snapshot and leaves the durable global sync token untouched. The synthetic run verifies three busy intervals and one free event, exact UTC API window conversion for Paris +02:00 endpoints, event UTC conversion, no unverified-window error, and global checkpoint preservation. The availability API derives intervals from normalized event records; it does not duplicate Calendar events into the manual-availability table.

scripts/sync.py now emits a privacy-safe aggregate progress snapshot every ten seconds during long runs. It includes allowlisted counts only; no titles, grades, event metadata, source payloads, or credentials are emitted.

Evidence: Gemini's source-owned targeted regressions 2 passed in 0.71s; Codex's five-grade, three-busy-calendar and safe-progress regressions 3 passed in 0.90s; complete backend suite 108 passed in 6.31s; pip check clean. No live provider call or live database access occurred. After Drive completes, rerun Pamplemousse for 2026-2027, then Google Calendar for 2026-09-01T00:00:00+02:00 through 2027-09-01T00:00:00+02:00, and audit each result before Moodle bulk synchronization.

# Codex P1 handoff

Import contracts from backend.app.contracts.authentication and backend.app.contracts.sources. These files are available in the shared branch working directory.

```python
adapter.authorize(inputs: Mapping[str, SecretStr]) -> AuthenticationResult
adapter.validate() -> AuthenticationResult
adapter.disconnect() -> None
store.get(key: str) -> SecretStr | None
store.set(key: str, value: SecretStr) -> None
store.delete(key: str) -> None
sessions.lease() -> AbstractContextManager[Path]
connector.list_changes(scope: SyncScope, cursor: str | None) -> ChangePage
connector.fetch_blob(record: Document) -> bytes
parser.parse(document: Document, content: bytes) -> ParsedContent | None
classifier.classify(document: Document, parsed: ParsedContent | None) -> ClassificationResult
matcher.match(document: Document, classification: ClassificationResult) -> EntityMatch
```

Use ReauthenticationRequired for expired/revoked access, ProfileBusy for an already leased browser profile and RetryableSourceError for transient upstream failures. Use credential-store keys scoped to the source, except explicitly shared ENSAI session state. Close Chromium before releasing the profile lease. Only wrap existing adapter DTOs where needed.

A page with complete=False requires a next_cursor. A complete page may carry a durable resume cursor. The engine advances only after durable processing. Provide normalized provenance with a stable external_id and a remote_revision when reliable. Add academic parents before children in a batch. The discriminated SourceRecord union documents the supported payloads.

Codex owns backend/app/core and backend/app/contracts. Please publish your adapter composition entry point in GEMINI_HANDOFF.md for registration.

## Composition entry point

Set ADAPTER_FACTORY=package.module:factory in sources.env for a trusted local registration module. factory(settings: Settings, credentials: CredentialStore, sessions: BrowserSessionManager) -> Components. Components is defined in backend.app.core.runtime and accepts adapters, connectors, parser, classifier and matcher. Settings is in backend.app.core.settings. Sync owns consent, authorization checks and checkpoints. Adapters own source-specific OAuth/browser interaction and refresh.

The runtime composition entry point is now present. Use one factory per runtime, keyed by the shared SourceKind. Registration must remain inert at factory creation; perform authorization, validation and acquisition only on explicit calls. The runtime is single-user and single-process. HTTP binds to loopback through scripts/start.py and uses a process-lifetime bearer token in the private runtime directory.

P2 can call GET /api/v1/documents and GET /api/v1/documents/{document_id}/versions/{version_id}/chunks. P3 can read /api/v1/events, /availability, /subjects, /assessments, /teaching-units, /coefficients, /assessment-weights and /grades. Imported records include normalized payload and source linkage. Source-specific synchronization uses POST /api/v1/sync and GET /api/v1/jobs/{id}.

Please confirm the factory path and map Gemini's browser locking to the shared lease, or use a single compatible lock across both entry points.

## Remediation contract update

- CalendarEvent adds event_type, teacher, confirmation_required, confirmation_reason, recurrence_key, original_timezone and busy. An ID-only cancellation is CalendarEvent(provenance=..., title=..., status="cancelled") with no endpoints. P3 availability derives anonymous busy intervals from persisted Google Calendar events and excludes cancelled or transparent events.
- Provenance.source is optional. Document.access_status accepts active, tombstoned and revoked. Tombstones retain history and require no blob fetch.
- ChangePage.resume_cursor is the durable incremental checkpoint returned on the terminal page. next_cursor is the next page token for nonterminal pages; a terminal next_cursor remains supported for legacy adapters. error_codes make a run partial and prevent checkpoint advance. warning_codes appear in the report. Please send bounded constant codes, never upstream messages or private content.
- ParsedContent.warning_codes and DocumentChunk.block_kind/structured are optional additions. The latter carries JSON-compatible parser evidence such as tables or code metadata.
- Academic assessments, recursive weight nodes, coefficients and published grades should have distinct stable external IDs. Emit parent records first. A coefficient or weight must never replace the underlying assessment or subject fact.
- Add optional adapter.reauthorize(inputs: Mapping[str, SecretStr]) -> AuthenticationResult for forced OAuth re-consent. The manager falls back to authorize for existing adapters.
- These DTO additions use existing JSON-backed columns and require no data backfill. Migration 0002 will add database indexes and guard active version integrity. Bridge field propagation remains Gemini-owned.

## Cross-agent review closure

**Review status — COMPLETE** for the available canonical Gemini snapshot. Scope covered all ingestion modules, the standalone CLI, synthetic tests, shared runtime integration and the P1 requirement set. The closure rerun passed 24 tests in 1.60 seconds. Separate fake-object probes confirmed three open defects. The [review](CODEX_REVIEW_OF_GEMINI.md) records all 20 findings with owner corrections and evidence.

No outstanding review tasks remain for this snapshot. Live source behavior, credentials and access permissions remain unverified. The review package is ready for the remediation phase, which is paused at the user's request.

## Resumed integration

The engine now consumes Gemini's resume_cursor and safe page warning/error codes. It advances a durable checkpoint only after a successful terminal page and preserves the old checkpoint for partial runs. Terminal next_cursor remains a compatibility fallback. Scoped sync cannot advance a global cursor. Migration 0002 adds lookup indexes and SQLite triggers enforcing active-version ownership on existing databases. Standalone migration uses `.venv/bin/alembic -c backend/alembic.ini upgrade head` with the server stopped.

The API now exposes version-scoped concept candidates at `GET /api/v1/documents/{id}/versions/{version_id}/concept-candidates` using Gemini's offline extractor. Candidates do not grant or confirm catalog membership. Upload accepts optional form field external_id for a stable logical identity across renames. Academic import supports dry_run and returns safe per-record status or error codes.

## Remediation closure

**REMEDIATION_COMPLETE**. All 16 Codex findings are FIXED_PENDING_REVIEW, with regression evidence in [CODEX_REMEDIATION](CODEX_REMEDIATION.md). The canonical combined suite passed 42 tests in 3.87 seconds. The second formal review has not started.

The default runtime rebuilds the matcher from persisted subjects currently covered by source read consent. Withdrawn catalog sources do not influence new matches. Version-scoped concept extraction returns candidates without persisting a confirmed curriculum relationship. Existing databases upgrade automatically on startup; standalone Alembic supports a SQLAlchemy URL override for isolated verification.

Start with `cd /home/ensai/hackathon_ENSORA` and `python3 scripts/start.py`. Local status can run while the backend holds its writer lock. Interactive onboarding and CLI mutations use that same lock. Live institutional and Google flows remain subject to the user's authorization.

## Second formal review handoff

SECOND_REVIEW_COMPLETE. See [CODEX_REVIEW_ROUND_2](CODEX_REVIEW_ROUND_2.md) for all 20 determinations, six new defects, and source-level evidence. The verified total is 9. The complete suite passed 42 tests in 4.09 seconds. The most urgent gaps span Calendar query/restore semantics, durable consent for standalone onboarding, safe logs, and local private-directory exclusion. Shared cursor capacity, sync recovery and local-root policy require Codex/Gemini coordination. This phase made coordination-document edits only. Live validation remains gated on explicit human authorization.

## Round 2 contracts for Gemini

- ChangePage.next_cursor and resume_cursor are opaque strings bounded at 16 KiB. Preserve bytes/characters exactly and keep these out of logs.
- ChangePage.calendar_snapshot is an optional CalendarSnapshot(external_id_prefix: str, starts_at: AwareDatetime | None = None, ends_at: AwareDatetime | None = None). For unscoped Google Calendar full listings and 410 recovery, attach this marker to the first replacement page, including a terminal single-page snapshot. The prefix identifies the calendar namespace (for example google_calendar:primary:). Both bounds are set for bounded snapshots and both omitted for unbounded snapshots. Repeat the same marker on later snapshot pages if convenient. For 410 mid-run emit it on the replacement's first page. The backend retires unseen overlapping events only after the replacement is complete, error-free and still authorized. The marker is additive and never used for ordinary deltas, ICS or selected sync.
- SourceConnector.fetch_blob may continue returning bytes or return FetchedDocument(content: bytes, mime_type: str | None = None, filename: str | None = None). ETL uses supplied metadata for the parsed version and download. Fetch display filenames must be basenames.
- Concept.evidence optionally accepts a list of ConceptEvidence(chunk_ordinal: int, locator: dict[str, str | int]). The version endpoint resolves ordinals to persisted chunk IDs. Preserve legacy concept record fields.
- Runtime.validate_local_import_path(path) checks protected roots before scans or structured file imports. Unified SyncEngine uses the same check for local folder IDs. Standalone ingestion code should use the runtime check before reading structured imports and enforce protected subpaths in its contained-open policy as applicable.

Round 2 in-flight suite observed two Gemini-owned fixture failures while ingestion files were being updated. The legacy noninteractive onboarding fixture expects a ready status and the Calendar 410 fixture expects a raw provider token in internal sync_token. Please reconcile those fixtures with your current authorized/offline behavior and the chosen internal cursor contract. Codex does not require a specific prefix for the shared opaque resume cursor.

The two earlier in-flight fixture failures cleared on the next combined run. At that point the new Gemini fetched-metadata fixture still omitted the required internal ChangePage.next_cursor argument; its fake should pass next_cursor=None for a terminal page. This is a Gemini-owned fixture.

## Round 2 closure — REMEDIATION_ROUND_2_COMPLETE

The runtime consumes Gemini's CalendarSnapshot, FetchedDocument, consent-scoped SQLite matcher, and chunk-level evidence. Published interfaces now include uniform chronological availability, exact-version concepts and prerequisite hints at /api/v1/documents/{id}/versions/{version_id}/concept-candidates, and consent-scoped review candidates at /api/v1/documents/exam-correction-candidates. These candidate APIs leave curriculum and document associations under review. Migration 0003 stores fetched filenames on document versions. All previously noted in-flight fixture mismatches cleared on the final run.

The full synthetic suite passed 62 tests in 6.39 seconds. Isolated startup and pipeline registration passed. [Round 2 remediation and human-operated validation commands](CODEX_REMEDIATION_ROUND_2.md) are ready for targeted final verification. Google OAuth and authorized source access remain separate live gates. No new formal review started.

## Targeted final verification handoff

FINAL_VERIFICATION_COMPLETE. The offline runtime and P2/P3 contract evidence passed with 62 synthetic tests in 6.22 seconds. [CODEX_FINAL_VERIFICATION](CODEX_FINAL_VERIFICATION.md) records all 17 determinations and exact operator commands. Live readiness remains NO_GO pending FINAL-BLOCK-001 OAuth fresh-grant precedence, FINAL-BLOCK-002 Moodle REST metadata redirect policy, and FINAL-BLOCK-003 Drive durable paging and persisted membership reconciliation. No application code or Git state was written during verification.

## ENSAI CAS lifecycle integration in progress

- Keep using `Settings`, `LockedBrowserSession` and the common ENSAI profile from the runtime factory. Source adapters must never prompt, open a headed window or submit credentials in `validate()`, connector listing, or blob fetch. Interactive work belongs only in `authorize(inputs)` and `reauthorize(inputs)`.
- Shared `AuthenticationResult` will accept optional `session_state` (unknown, active, reauth_required) and `service_access` (unknown, verified, denied), plus the safe reasons `cas_login_required`, `service_access_denied` and `service_verification_required`. Use verified only after a protected, source-specific probe (Moodle course acquisition path or Pamplemousse timetable/authorized endpoint). A CAS login page, landing page or cookie alone is insufficient. Moodle REST can verify service access while leaving CAS state unknown.
- Translate expected CAS redirects and expired sessions on both listing and fetch to shared `ReauthenticationRequired(reason="cas_login_required")`. It carries a safe code and must not include URLs, credentials, cookies or response text. An explicit source permission denial may use the shared `ServiceAccessDenied` exception.
- Codex will persist the blocked connection, report a per-source recovery action, and add an explicit resume operation. Resume replays the stored selection from the last durable checkpoint and never launches interactive auth. No connector-side automatic CAS retry.

The default bundled Runtime now requires positive service evidence before accepting institutional CONNECTED, including configured Moodle REST. Custom injected adapters remain compatible, but an explicit active CAS session with unknown service access never establishes connectivity. The existing old fixture `test_ensai_shared_session_fallback_and_profile_lock` temporarily failed while Gemini was switching its probe to the protected Moodle URL. Please use the protected URL fixture.

Central settings now expose moodle_protected_url, pamplemousse_protected_url, ensai_shared_session, ensai_auto_login (with ENSAI_ALLOW_AUTO_CREDENTIAL_SUBMIT alias), and ensai_timeout_ms. Use these fields in runtime_config_from_codex_settings rather than reopening sources.env for their values. Environment and explicit config path selection are handled by Settings.load().

### ENSAI lifecycle entry points

- Auth adapter results distinguish CAS session state from protected service access. The default factory requires service evidence before it persists CONNECTED. On an acquisition challenge, the backend records REAUTH_REQUIRED before releasing the source operation lock.
- GET /api/v1/jobs/{id} includes connection_status, auth_reason and next_action per source. POST /api/v1/connections/{id}/reauthorize is an explicit interactive action. POST /api/v1/jobs/{id}/resume (optional source selector) requires a connected, read-consented source. The response identifies an idempotent successor job. Resume replays the original scope without storing a transient page cursor. The API never starts an interactive operation from scheduling or job processing.
- Human-operated recovery with the API server stopped:

```sh
cd /home/ensai/hackathon_ENSORA
python3 scripts/check_connections.py --validate --source moodle
python3 scripts/onboarding.py --source moodle --reauthorize
python3 scripts/check_connections.py --validate --source moodle
python3 scripts/sync.py --resume JOB_ID --source moodle --approve
```

Use `pamplemousse` instead of `moodle` for its own saved job. If no interrupted job exists, replace the resume line with `python3 scripts/sync.py --source moodle --subject-id COURSE_ID --approve` or, after agreeing to its selected acquisition, `python3 scripts/sync.py --source pamplemousse --approve`. Use `python3 scripts/onboarding.py --source SOURCE` once for missing read consent. Complete the official CAS browser flow yourself. For offline UI startup use `SYNC_ON_STARTUP=false SYNC_INTERVAL_MINUTES=0 python3 scripts/start.py --skip-onboarding`. All commands use the same Settings and configured profile.

### ENSAI integration gate and evidence

The combined suite passed 70 tests in 4.72 seconds. The Codex contracts, serialized shared-profile orchestration and recovery are implemented. The operator CLI flags were checked with disposable configuration. The current bundled bridge's _to_codex_auth_result still returns a bare CONNECTED from an authenticated AuthStatus. A fake protected probe through that factory correctly remained REAUTH_REQUIRED / service_verification_required. Before human acceptance, the Gemini bridge must report explicit service_access="verified" only for a successful protected source probe, including its authorized REST mode, and map CAS and permission failures to the shared statuses. Scripts should continue to reuse the single configuration and profile. Live auth/acquisition remains human-operated.

## Live CAS completion contradiction

The duplicate transition is in `AuthenticationManager._run`: after receiving an interactive CONNECTED result, it discarded it and called `CodexEnsaiAuthBridge.validate()`. That opens a freshly acquired profile context through `EnsaiAuthAdapter.check_source_connection(headless=True)`. `_to_codex_auth_result(..., institutional=True)` then returns REAUTH_REQUIRED/cas_login_required for NEEDS_LOGIN or raises ReauthenticationRequired("cas_login_required") for EXPIRED, and the manager persists that replacement. The onset of the fresh-context CAS challenge must still be verified with human-run diagnostics.

Codex will consume an already verified protected-service result without immediately opening a second context. Unverified success still requires a protected-service probe; explicit validation and acquisition in a separate run keep reporting any fresh-context challenge. Gemini should set service_access=verified only after confirming a positively authenticated protected resource, or a successful authorized REST probe. In particular, the browser converter must not infer protected verification from missing diagnostic fields, CAS completion or cookie existence. Keep interactive and headless probes on the same configured service/profile and report only redacted phase/state metadata.

### Targeted CAS split-probe correction

The manager now consumes verified protected-service access in the interactive result without immediately reopening Chromium. A legacy CONNECTED with unknown evidence still triggers a protected fallback probe. An explicit validate or an acquisition can still move CONNECTED to REAUTH_REQUIRED. The CLI consolidates duplicate pre-authorization checks and `--debug-auth` logs only source, operation, stage, status and allowlisted evidence codes.

The reproduction used both bundled bridges and mocked browser entry points. Before the correction, authorize/reauthorize called authenticate successfully, then check_source_connection returned NEEDS_LOGIN; _to_codex_auth_result produced cas_login_required. After the correction the initial call stays CONNECTED on protected evidence, while the separate validation still reports REAUTH_REQUIRED when the fresh context sends the same challenge. An EXPIRED response takes the equivalent shared-exception path. The manager does not use old SQLite state as proof of connectivity.

Gemini owns the remaining browser edge. Both paths derive settings and profile selection from the same runtime. The browser acquires/releases the profile around context launch/closure, then the detached probe selects it again. In the observed snapshot, open_context restored the allowlisted cookie snapshot and closed the context, while _save_session_cookies had no call site yet. The cookie/persisted booleans in the interactive diagnostic describe the existing context, so please verify effective save and reload from a new context. The browser bridge should require a positive protected-resource diagnostic instead of defaulting missing origin proof to true.

Run the following in separate terminal commands with the server stopped and existing source consent. Temporary overrides isolate manual session reuse and leave private configuration unchanged.

```sh
cd /home/ensai/hackathon_ENSORA
ENSAI_AUTH_MODE=browser ENSAI_AUTO_LOGIN=false python3 scripts/onboarding.py --source moodle --reauthorize --debug-auth
ENSAI_AUTH_MODE=browser ENSAI_AUTO_LOGIN=false python3 scripts/check_connections.py --validate --source moodle --debug-auth
ENSAI_AUTH_MODE=browser ENSAI_AUTO_LOGIN=false python3 scripts/check_connections.py --validate --source pamplemousse --debug-auth
ENSAI_AUTH_MODE=browser ENSAI_AUTO_LOGIN=false python3 scripts/onboarding.py --source pamplemousse --reauthorize --debug-auth
ENSAI_AUTH_MODE=browser ENSAI_AUTO_LOGIN=false python3 scripts/check_connections.py --validate --source pamplemousse --debug-auth
```

Only run Pamplemousse reauthorization if its first fresh probe requires it. An independent fresh probe failure should be investigated as browser persistence or service authorization. There is no automatic retry loop in the manager. Keep actual credentials, cookies and private URLs out of the coordination notes.

Update after rechecking the shared workspace: Gemini's browser now has calls to _save_session_cookies; the earlier observation applied to the in-flight snapshot. The combined suite passed 88 tests in 5.11 seconds, including separate same-profile interactive/headless results on both institutional services and an unverified-success negative case. Handling a fresh-context challenge remains explicit. Live browser cookie and service authorization behavior requires the human-operated sequence above.

## Live ETL acceptance contract

Codex owns additive DTOs, persistence, audit and P2/P3 routes. Gemini can emit the following through existing SourceRecord and ChangePage signatures:
- Provenance.academic_year. Document.course_id, course_title, academic_category, section_id, section_path, teaching_unit_id and resource_kind (document, announcement, educational_html). Keep a section-aware stable resource ID where distinct occurrences carry distinct provenance. For educational HTML return FetchedDocument bytes and text/html through fetch_blob. Do not place raw HTML in metadata. Assessment keeps deadline_at plus instructions and assessment_kind.
- AcademicRecord.record_status (known, missing, ambiguous), source_code. Subject and TeachingUnit accept ects; Subject accepts aliases and optional_activity. SubjectCoefficient retains weight as the raw coefficient plus level (unspecified, year, semester, teaching_unit, subject, assessment) and mode (raw, relative, fraction). PublishedGrade accepts grade_kind (unspecified, official_aggregate, assessment), minimum, maximum and rank. Missing values are null or absent, with record_status=missing when explicit. CalendarEvent.duration_minutes and optional provenance academic year are available.
- ParsedContent.status accepts ready or partial (default ready). ChangePage.observations accepts per-page count deltas for courses_discovered, sections_discovered, sections_traversed, folders_traversed and resources_excluded. Omit unavailable counts. These counters must not contain labels, IDs or private values.
- SyncScope.academic_year selects a source-backed academic year. SyncScope.window_start/window_end are an optional explicitly approved Google Calendar range; forward them to provider time_min/time_max and keep scoped jobs separate from durable global cursors. The backend will fail closed on unverified academic-year membership rather than guessing from an academic label.


## Live ETL integration update — metrics and explicit missing academic facts

Gemini's latest `GEMINI_STATUS.md` and `GEMINI_LIVE_VALIDATION.md` report full Moodle tab/course traversal, Pamplemousse EDT/Inscriptions/Notes extraction and page-level diagnostics. I reviewed those claims against the actual bridge and current shared runtime. The integration boundary now has these additional rules:

- `ChangePage.observations` accepts `courses_traversed`, `resources_discovered`, `pages_visited`, `timetable_events`, `enrolled_subjects`, `teaching_units`, `semester_associations`, `subject_coefficients`, `ects_values`, `available_grades`, `assessment_records`, `aggregate_grades`, and `missing_or_ambiguous_count` alongside course/section/folder/exclusion metrics. All values are non-negative integer counts only.
- The current Moodle connector reports `courses_discovered` as a whole-list total on every page; Pamplemousse `last_stats` are whole-run totals repeated during page slicing. The Codex bridge now emits these run totals on the first page only. Moodle course/section/resource traversal counts remain page-local and are accumulated across pages. This bridge normalization was selected over changing the connector's page contract because it keeps compatibility with the just-published Gemini adapters and prevents duplicate counts.
- `SyncEngine` initializes only processing stages that it actually measures. Source coverage metrics are not fabricated as zero. `audit_data.py` returns `null` for an adapter metric that is not emitted. Drive currently has no folder traversal counter; do not infer one from documents.
- If the source explicitly sets `weight_status` or `grade_status` to `missing` or `ambiguous`, the bridge persists a nullable `SubjectCoefficient`/`PublishedGrade` record with that status. A known numeric zero remains distinct. Subject and UE ECTS stay independent from coefficient values.

Codex regression `test_pamplemousse_bridge_persists_academic_hierarchy_and_missing_values` drives two connector pages through `CodexConnectorBridge`, `SyncEngine`, SQLite and `audit_database`; it verifies the academic parent chain, missing UE/subject coefficient and grade facts, and once-only run totals. `test_audit_leaves_adapter_unreported_metrics_unknown` protects against false zeroes.

**Gemini coordination request:** please retain these aggregate field names and non-negative integer semantics in Moodle/Pamplemousse connector `last_stats`. The backend will continue to tolerate absent counters. Please re-run the Pamplemousse test fixtures against explicit missing UEs/subjects and ensure zero values are still emitted as known values. Avoid changing shared field names without coordination.

**Alternatives reviewed:** per-page connector counter deltas would be ideal if every adapter can reliably report them; current Moodle and Pamplemousse counters are whole-list totals, so the shared bridge normalizes repeated totals instead. Missing values are emitted only on explicit source status rather than generating one for every absent grade row, to avoid treating an unobserved assessment as a confirmed missing grade.

### Aggregate coefficient hierarchy clarification (2026-11-27)

Review of Gemini's current Pamplemousse parser found `provenance.aggregate_scope` on official aggregate rows (`overall`, `semester`, `teaching_unit`, `subject`) and `weight_mode="raw"` where an institutional coefficient exists. The Codex bridge now maps those scopes to `SubjectCoefficient.level` (`year`, `semester`, `teaching_unit`, `subject`) and preserves explicitly missing/ambiguous aggregate coefficients with null weight. Unrecognized scope remains `unspecified`; no hierarchy is inferred from a label. Assessment rows with explicitly relative/fractional values continue to become `AssessmentWeight`, while institutional raw coefficients remain raw coefficients.

Regression: `test_pamplemousse_bridge_persists_academic_hierarchy_and_missing_values` verifies year/semester scope, missing aggregate coefficient, assessment raw coefficient, and explicit fractional assessment weight through SQLite. The combined Codex targeted set passed **3 passed in 0.87s**. The first test run had an assertion mismatch because two new aggregate grades made the old expected cumulative grade count of zero invalid; the fixture metric and expected cumulative count were updated to two, after which the test passed.

**Gemini coordination request:** please keep the explicit `provenance.aggregate_scope` values and `weight_mode="raw"` semantics stable. The bridge now consumes these directly. If connector changes introduce another scope name or a hierarchy level absent from the current contract, please publish it here before emission so the mapping can remain exact and lossless.

**Prior finding, superseded by the correction below:** the fixture expected `assessment_weight` for raw sibling coefficients. Codex now retains those raw values and emits separately identified sibling-normalized relative weights only for complete, healthy, fully known sibling groups. The source facts are never relabeled or discarded.

**Prior sandbox limitation, superseded:** sandboxed full-suite attempts stopped in synchronous FastAPI integration routes because AnyIO thread offload hung. An approved unsandboxed rerun of the same complete suite passed after the final bridge guards. This is not a remaining test blocker; no API route changes were made.

### Pamplemousse sibling assessment-weight integration correction — 2026-11-27

The latest Gemini-owned `test_live_scraping_completeness.py` now requires persisted sibling-derived assessment weights while retaining raw source coefficients, explicit basis, ECTS, official/assessment grades and missing-vs-zero facts. The current parser fixture confirms both sibling rows (`TP Distillation`, coefficient 1.0; `Final Homework`, coefficient 2.0) carry `weight_mode="raw"`; neither reports a relative fraction. The initial missing kind was solely `assessment_weight` (all other expected kinds were present). This was a normalized-contract gap: raw `SubjectCoefficient` values were retained, but the P3 Grade Tracker had no relative weights for a fully enumerated sibling group.

Codex now derives `AssessmentWeight` records only on an unscoped first page (`cursor is None`) that is complete, warning-free, and error-free, grouped by the same immediate parent, and only when every sibling has a known raw coefficient. This avoids deriving from a final continuation page that is complete only for its slice or from a listing with a failed subpage. Values are normalized within that sibling group (1/3 and 2/3), never across the academic catalog. The original raw coefficients (1 and 2), source provenance, coefficient level, grades, ECTS and nullable facts remain separate and unchanged. Incomplete, warned, errored, missing/ambiguous, or mixed explicit-relative/raw groups produce no derived denominator/weights; explicit source-relative values still flow through directly. All-zero groups do not divide by zero. New additive `AssessmentWeight.weight_basis` distinguishes `source_reported` (default) from `normalized_sibling_coefficients`; P3 consumers can display/choose either semantic explicitly. Codex asks P3 to preserve this additive basis in its typed integration model.

Regression evidence: the Gemini end-to-end test now checks SQLite persistence of raw assessment coefficients, two normalized sibling weights summing to one, explicit basis, ECTS independence, official vs assessment grades, missing grade nulls and zero vs missing coefficients. Codex added a synthetic bridge regression for 1:2 raw sibling values, source provenance, Pamplemousse academic-year scope forwarding, and no derivation from incomplete or continuation pages. Latest targeted run after the continuation guard: **3 passed in 0.92s** (Gemini end-to-end Pamplemousse case plus both Codex academic bridge regressions).

**Gemini coordination request:** please retain the current updated end-to-end assertions and add this basis field to any typed P3 consumer if it validates strict payloads. I adopted the latest `GEMINI_STATUS.md` recommendation to require `cursor is None` in addition to a complete group. Codex additionally requires a warning-free page. After these final guards, the canonical full suite passed **99 tests in 6.03s**, and `pip check` reported no broken requirements. Gemini independently reported 99 passed in 6.68s. No Gemini-owned implementation file was changed by Codex.

The `SyncScope.academic_year` validator now accepts Moodle and Pamplemousse, and `CodexConnectorBridge` forwards the requested year to either connector. `scripts/sync.py` help/error text reflects both sources. The synthetic Pamplemousse bridge regression verifies the exact `2026-2027` value reaches the connector. Moodle still fails closed on unverified year membership; Pamplemousse parser applies the requested year consistently to the selected academic acquisition.

Verified CLI help in an isolated disposable config/data directory confirms:
- Moodle exhaustive diagnostic: `scripts/p1_sources_cli.py sync-moodle --all-courses --academic-year 2026-2027 --yes`.
- Pamplemousse full diagnostic: `scripts/p1_sources_cli.py sync-pamplemousse --all --academic-year 2026-2027 --yes`.
- Ensora Drive root recursive diagnostic: `scripts/p1_sources_cli.py discover-drive --all --yes`.
- Google Calendar diagnostic availability window: `scripts/p1_sources_cli.py sync-calendar --days 14 --availability-only --yes`.
- Persistent ETL: `scripts/sync.py --source moodle --academic-year 2026-2027 --full --approve`; Pamplemousse `scripts/sync.py --source pamplemousse --academic-year 2026-2027 --full --approve`; Drive `scripts/sync.py --source ensora_drive --full --approve`; Calendar uses explicit `--window-start/--window-end` with `--approve`.
- SQLite and P2/P3 aggregate audit: `scripts/audit_data.py`; while the local server runs with automatic sync disabled, `scripts/audit_data.py --api-base http://127.0.0.1:8765`.

The full exact operator procedure is in `CODEX_LIVE_VALIDATION.md`; help checks used disposable paths because this environment cannot chmod the operator's private `~/.config/ensora-plus` directory. No live validation was run.

## Live acceptance document

Operator commands, requirement statuses, synthetic/live evidence, exact data persistence boundaries and current source limitations are maintained in [CODEX_LIVE_VALIDATION.md](CODEX_LIVE_VALIDATION.md). No live requests were made by Codex.


## Persistent ETL operational coordination — 2026-11-27

**New live evidence for Gemini:** the user reports Moodle 2/5 downloads and 1/5 parses, Pamplemousse 86 academic records but zero timetable events, Drive sample downloads, and three Google Calendar busy intervals. Those are discovery/diagnostic metrics, not SQLite persistence. A privacy-preserving read-only audit of the configured local database currently reports cumulative zero documents, versions, chunks, academic source records, and availability intervals.

**Active runtime finding:** the latest `sync_runs` row is still `running` for `ensora_drive`; its stored scope is `full=false`, no academic year, and all source metrics remain null. The associated `scripts/sync.py` process holds the runtime writer lock after 11+ minutes. A Moodle full-sync invocation was refused by `RuntimeBusy` before it began. Codex did not start another job, terminate the process, or call a live source. Please check why this Drive run has not yielded its first page and ensure connector/listing waits are bounded and produce actionable progress/errors. Keep connector changes in Gemini-owned files.

**Backend integration position:** successful `ChangePage` records are persisted immediately and idempotently by `SyncEngine`/`ETLPipeline`; academic 2026-2027 is forwarded to Moodle and Pamplemousse; missing and zero facts remain distinct; normalized academic records are available through P3 routes and active-version chunks through P2. Synthetic coverage is not live persistence evidence. Existing `--resume JOB_ID` is for reauthentication. For isolated resource failures, rerun the exact same approved source/scope command after the run ends: successful unchanged revisions are reused while failed/new revisions are retried. Do not claim a single-resource retry CLI exists; it does not.

**Sequential operator rule:** stop the unified server and ensure any existing `scripts/sync.py` job has ended before starting another persistent CLI sync. Use the full commands in `CODEX_LIVE_VALIDATION.md`; audit after each source. Moodle and Pamplemousse scopes must both specify `--academic-year 2026-2027`; Drive stays confined to the approved shared root; Calendar uses explicit timezone-aware window dates.


## Test and runbook refresh — 2026-11-27

- Full suite transiently returned **2 failed, 100 passed**: (1) `test_session_expiry_bounded_reauth_and_acquisition_resume_moodle_and_pamplemousse` raised `AuthenticationRequiredError` from Gemini-owned ENSAI adapter reauthentication, and (2) `test_document_parser_registry_all_formats` treated malformed bytes `NOT_A_PDF` as plain text. Both targeted tests passed immediately when isolated, and a subsequent full run passed **102 tests in 5.65s**. No assertions were weakened. Please check why these two Gemini-owned tests can fail in a full-suite run.
- `pip check` clean.
- Corrected Europe/Paris calendar annual range in `CODEX_LIVE_VALIDATION.md`: both 2026-09-01 and 2027-09-01 midnight use `+02:00`.
- The current full Drive process still holds the single-writer lock and has not emitted first-page metrics; the sanitized local audit remains zero. No concurrent acquisition should start.


### Live progress update — 2026-11-27

Latest read-only audit while the existing Drive job continues: 71 discovered, 70 downloaded, 67 parsed, 3 unsupported, 70 persisted, 849 chunks. Cumulative SQLite confirms 70 document versions and 849 active chunks; current subject associations are zero until academic catalog acquisition. Run remains `running` with aggregate codes `record_failure` and `source_warning`; process elapsed time exceeds 25 minutes. Do not treat this as full-root completion. Other source sync and local API smoke remain blocked by the held single-writer lock.


### Latest audit refresh — 2026-11-27

The still-running Drive job now reports 75 discovered, 74 downloaded and persisted, 71 parsed, 3 unsupported, and 865 chunks. SQLite totals agree: 74 documents/versions and 865 active chunks; 74 remain unresolved pending academic catalog acquisition. Status remains `running`, with `record_failure` and `source_warning`. Do not treat as complete; writer lock is still held.

## P0 Live integration findings and shared contract coordination — 2026-11-27

Codex traced the operator-reported Pamplemousse partial run and Calendar calendar_window_unverified through the current bridge/runtime. No live DB was opened or modified.

**Confirmed Codex academic-year defect:** CodexConnectorBridge._to_codex_records() derives SubjectCoefficient and PublishedGrade children from an academic parent, but the child Provenance constructors omit academic_year=prov.academic_year. SyncEngine._pages() then drops them under SyncScope.academic_year=2026-2027 and counts absent provenance as academic_year_unknown. This directly explains all 71 missing subject coefficients and is consistent with one missing subject-level grade. Codex will fix child provenance using an additive local helper and test parent-to-child year preservation through SQLite.

**Confirmed scoped Calendar defect:** the bridge attaches CalendarSnapshot only when Calendar scope is unscoped (if not selective), while an explicit date window makes the scope selective. SyncEngine intentionally requires a snapshot whose exact aware start/end match that window before writing any event; absent a marker it skips records and reports calendar_window_unverified. Codex will emit a bounded snapshot for explicit windows only when connector last_snapshot_info exactly matches the requested instants. Existing contract is sufficient; no schema change is needed. This preserves UTC instant comparison, bounded retirement, and keeps the global syncToken cursor untouched.

**Gemini coordination request before connector changes:** Pamplemousse list_changes() initializes detected_year from the request and therefore loses the distinction between user-selected scope and year observed on the portal. Please expose a separate source-confirmed year (or confirmation status) in last_stats; for iCalendar events, only stamp an academic year after confirming their dates fall in the source-selected academic-year interval. Parsed JSON/FullCalendar event metadata already has an academic_year argument, but direct ICS import does not. Please also verify the exact source of the 214 live events, whether 154 ambiguous/incomplete rows are primarily event ambiguity versus academic missing rows, and whether all five acquired grades include one subject-level grade.

The acquisition counters are discovery/extraction counts, not proof of persistence. Codex will preserve the reported 154 ambiguity/missing semantics and will not convert missing coefficients to zero. No shared contract change is currently proposed.

## P0 ETL regression independent verification and Codex corrections — 2026-11-27

This section supersedes the earlier provisional diagnosis above, which was written before Gemini's latest shared-checkout corrections were read.

### Verified Pamplemousse root cause

Gemini's exact live accounting is consistent with the current code and fixture: 214 ICS events + 155 academic child facts = 369 records without a matching year. The child facts were 71 subject coefficients + 71 fabricated missing grades from the Inscriptions page (which has no grade column) + 7 UE coefficients + 1 assessment coefficient + 1 assessment grade + 4 aggregate coefficients. The bridge dropped academic_year when constructing the derived Provenance; the sync filter correctly rejected them. The ICS parser also lacked event-year provenance. The connector now derives the ENSAI September–August year from each event's actual date, so an out-of-year event is excluded rather than blindly tagged. The bridge now copies the academic year to every child coefficient, weight and grade. Inscriptions-only grade status is now UNKNOWN, while genuine missing Notes grades remain MISSING.

The earlier missing_or_ambiguous_count=154 was a normalization issue: ordinary unprefixed classes were classified as OTHER but incorrectly marked confirmation_required. The current classifier reserves that flag for genuinely ambiguous presentations, ungrouped exams and red-color hints. The historical aggregate itself did not include per-category counters, so Codex did not inspect or decompose live records; connector code and synthetic cases establish the defect class, while the exact live per-category split remains unverified. The count must not be cleared by suppressing genuine missing/ambiguous facts.

### Verified Calendar root cause

The bridge previously suppressed CalendarSnapshot whenever the scope was selective, including a complete bounded Calendar range. SyncEngine correctly refused to persist events from a window without a snapshot matching the exact requested instants. The bridge now attaches a snapshot for an explicit date window while keeping the global durable sync token unchanged. The existing CalendarSnapshot contract is sufficient; no shared schema change was required.

### Independent evidence and Codex additions

- Gemini-owned Pamplemousse and Calendar regression cases passed independently: 2 passed in 0.71s.
- Codex-owned end-to-end SQLite test verifies five acquired subject grades and coefficients, a known zero coefficient, 2026-2027 provenance on every persisted fact, and that a year-scoped run leaves a pre-existing global checkpoint unchanged.
- Codex-owned bounded Calendar test verifies the requested Paris endpoints 2026-09-01T00:00:00+02:00 and 2027-09-01T00:00:00+02:00 reach Google as 2026-08-31T22:00:00Z and 2027-08-31T22:00:00Z; three busy and one transparent/free event persist; the free event does not count as busy; event times convert to UTC; and the global sync token remains unchanged.
- scripts/sync.py now prints a safe aggregate progress JSON snapshot every ten seconds during a long job. A test confirms unknown/private report keys are excluded.
- Codex targeted regressions: 3 passed in 0.90s. Full backend suite: 108 passed in 6.31s. pip check: clean.

### Human-operated selective recovery

No live DB was accessed in this verification. Wait until the already-running Drive sync releases the single-writer lock, then rerun the failed source scopes sequentially. For a data-related partial Pamplemousse result, replay the same year-bounded command; upserts are idempotent and the scope does not replace the global cursor:

    cd /home/ensai/hackathon_ENSORA
    .venv/bin/python scripts/sync.py --source pamplemousse --academic-year 2026-2027 --full --approve
    .venv/bin/python scripts/audit_data.py

Replay the exact bounded Calendar window to re-fetch and reconcile only that date range while preserving the global sync token:

    .venv/bin/python scripts/sync.py --source google_calendar --window-start '2026-09-01T00:00:00+02:00' --window-end '2027-09-01T00:00:00+02:00' --full --approve
    .venv/bin/python scripts/audit_data.py

For an authentication-required job only, reauthorize the source interactively, then use the exact failed job ID:

    .venv/bin/python scripts/onboarding.py --source pamplemousse --reauthorize
    .venv/bin/python scripts/sync.py --resume JOB_ID --source pamplemousse --approve

Do not run these while the Drive writer is active. The updated CODEX_LIVE_VALIDATION.md retains the full source sequence and P2/P3 aggregate API validation command.

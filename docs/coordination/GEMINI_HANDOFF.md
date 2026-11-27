# Gemini P1 Handoff — Source Integration, Authentication & Document Processing

- **Ticket / owner / branch**: P1 Source Integration / Gemini / `feat/p1-data-engineering`
- **Base commit**: `89ad3d8`
- **Current status**: **`P0_METADATA_AND_ETL_CONTRACTS_FIXED`** — combined canonical synthetic suite passes **105 tests in 6.91s** (`pip check` clean; 35 Codex integration + 70 Gemini unit/bridge/remediation/CAS/live-completeness tests; 0 failed, 0 skipped). Zero Codex core/contract files modified; active Drive sync undisturbed; zero Git write operations; zero unauthorized live source calls.
- **Live Validation Report & Operator Runbook**: See [GEMINI_LIVE_VALIDATION.md](GEMINI_LIVE_VALIDATION.md) for the exact post-Drive sequential persistent sync and verification commands.
- **Composition Factory Entry Point** (default in `backend/app/core/settings.py`):

```python
ADAPTER_FACTORY = "backend.app.modules.ingestion.codex_bridge:build_components"
```

## P0 Metadata & ETL Contract Corrections — Coordination Note for Codex (2026-11-27)
1. **Exact Root Cause of Pamplemousse 369 `academic_year_unknown` / 0 Events / 0 Coefs / 4-vs-5 Grades**:
   - **214 ICS timetable events**: `parse_ics_events()` omitted `provenance["academic_year"]`. Fixed via `_academic_year_from_event_time()` (derives ENSAI Sept–Aug academic year directly from each event's `DTSTART`/date so out-of-year events like 2025-2026 are accurately excluded rather than blindly tagged `2026-2027`).
   - **155 child academic records (`71 subject coefs + 71 fabricated missing grades from Inscriptions + 7 UE coefs + 1 assessment coef + 1 assessment grade + 4 aggregate coefs = 155`; `214 + 155 = 369`)**: Every child `Provenance(...)` constructed inside `_to_codex_records()` and sibling-weight derivation in `codex_bridge.py` omitted `academic_year=prov.academic_year`. The 4 persisted grades were `aggregate_grade` records (which reused `prov`), while the 5th grade was the `assessment` grade (which built a fresh `Provenance(...)` without `academic_year`).
   - **Bridge change for Codex review (`backend/app/modules/ingestion/codex_bridge.py`)**: Propagated `academic_year=prov.academic_year` onto all child `SubjectCoefficient`, `AssessmentWeight`, `PublishedGrade`, and sibling-derived `AssessmentWeight` `Provenance(...)` objects. Zero guessing: `None` remains `None`.
2. **Exact Root Cause of Pamplemousse 154 `missing_or_ambiguous_count`**:
   - `classify_timetable_event()` flagged every normal unprefixed class (`CalendarEventType.OTHER` without `category_hint`) as `confirmation_required=True`, inflating ambiguity across normal classes. Reserved `confirmation_required=True` strictly for presentations (`Sout.`/oral), ungrouped exams, and red-color hints.
   - `parse_pamplemousse_inscriptions_html()` (`p=107`, which has no grade column) previously emitted `grade_status=ValueStatus.MISSING`, causing 71 bogus empty `PublishedGrade(value=None, record_status="missing")` rows. Changed Inscriptions `grade_status` to `ValueStatus.UNKNOWN` while preserving genuine missing/locked grades on `Notes` (`p=1052`) as `ValueStatus.MISSING`.
3. **Exact Root Cause of Google Calendar `calendar_window_unverified` / 0 Persisted Busy Intervals**:
   - `SyncEngine` (`backend/app/core/sync.py:225-259, 304`) requires `ChangePage.calendar_snapshot` with `starts_at == scope.window_start` and `ends_at == scope.window_end` to set `window_verified=True`.
   - `codex_bridge.py:1175` previously gated `cal_snapshot` on `not selective`, where `selective` included `scope.window_start`, causing every bounded Google Calendar sync to return `calendar_snapshot=None` → `calendar_window_unverified` → 0 persisted records.
   - **Bridge change for Codex review (`codex_bridge.py:1175-1205`)**: Now attaches `CalendarSnapshot` for BOTH unscoped full/410 runs AND explicitly window-bounded Google Calendar runs (`window_scoped`), while still keeping `durable_cursor=None` on window-scoped runs so global `syncToken` is never overwritten.
4. **Moodle Course 505, Exhaustive 2026-2027 Discovery & Dataset ZIP Parsing**:
   - `MoodleConnector._discover_courses_playwright()` now issues a read-only same-origin `core_course_get_enrolled_courses_by_timeline_classification` (`classification="all"`, `limit=0`) AJAX query via `M.cfg.sesskey` to capture 100% of enrolled courses, derives source-backed `academic_year` from explicit year labels, `enddate`, `startdate` (`month >= 7`), and course breadcrumbs, and guarantees explicitly requested `course_ids` (such as `505`) are resolved directly from `course/view.php?id=505` even if omitted by `my/courses.php`.
   - `ZipArchiveDocumentParser` (`parsers/office_parser.py`) skips binary dataset/media/cache entries (`.npy`, `.pt`, `.png`, `.pyc`, `__pycache__`, etc.) before reading, enforces per-member zip-bomb bounds (up to 5,000 entries), and emits a clean archive manifest block if a valid ZIP contains only binary files.
   - `codex_bridge.py` also guards Drive's `folders_traversed` and `resources_discovered` to emit once on page 1 (`cursor in (None, "0")`).
5. **Response to Codex's `CODEX_HANDOFF.md:228` Note on Transient Test Failures**:
   - Those two failures (`test_session_expiry_bounded_reauth_and_acquisition_resume_moodle_and_pamplemousse` and `test_document_parser_registry_all_formats`) occurred briefly during our previous intermediate edit turn and were immediately fixed (not suite-order flakiness); the full suite has passed cleanly (**102 → 105 passed in 6.91s**) ever since.
   - Request for Codex: please review the 3 targeted updates in `codex_bridge.py` and confirm whether `Settings.max_document_bytes` (`25 MiB` default) should be bumped or documented as `MAX_DOCUMENT_BYTES=104857600` for large Moodle/Drive course files.

## P0 Real-Source Acquisition Fixes & Active Collaboration Notes for Codex (2026-11-27)
1. **Moodle Authenticated Downloads, URL Resolution & Exhaustive 2026-2027 Traversal (`connectors/moodle.py`)**:
   - **Root causes fixed**: (a) `/mod/resource/view.php` embedded files (`<object data=...>`, `<iframe src=...>`, `<embed>`, `<div class="resourceworkaround">`) were not unwrapped when `href=` matched an icon or didn't match; (b) `Settings.max_document_bytes` (`25 MiB`) caused `ValueError` on large course files without reporting `size_limit_exceeded`; (c) per-file Chromium launches were slow; (d) `my/courses.php` could miss courses hidden behind grouping filters or lack source-backed `academic_year` for Codex's fail-closed `--academic-year 2026-2027` filter.
   - **Implemented**: Cookie-backed streaming downloads (`_cookie_session_download_once` with strict same-origin pre-follow & CAS guards, Playwright fallback), `&redirect=1` + `_extract_moodle_pluginfile_url()`, HTML-not-binary login guard, clean `_extract_moodle_main_html()` for `/mod/page/`/`/mod/book/`/`/mod/forum/`/`/mod/folder/`, inert HTML pointer for `resource_kind="url"`, `DownloadSizeLimitError(ValueError)` with `size_bytes`/`limit_bytes`, passive `lib/ajax/service.php` course discovery (`startdate`/`coursecategory` -> `academic_year`) + course breadcrumb year extraction + `/mod/assign/` introattachment expansion.
2. **Pamplemousse Real Timetable Acquisition (`connectors/pamplemousse.py`)**:
   - **Root causes fixed**: (a) `_fetch_html()` (`page.goto()`) crashed with Chromium `Download is starting` on `.ics` attachments; (b) `edt_url` fallback to `index.php?p=121` was skipped whenever `Inscriptions`/`Notes` appeared in `discovered_explicit`; (c) passive FullCalendar XHR listener dropped PHP JSON served as `text/html`/`text/plain` and missed FullCalendar v3/v4 (`fc-list-heading`/`fc-list-item`) and inline `<script>` `events: [...]`.
   - **Implemented**: `_fetch_ical_payload()` (cookie HTTP + `context.request.get()`, never `page.goto()`), `extract_ical_feed_urls()` (`webcal://`, `https://`, `<input>`, `<textarea>`, `<code>`, `onclick`, allowlisted `*.leos.fr`/`*.ensai.fr`), guaranteed `index.php?p=121` fallback on Leos portals, permissive XHR/fetch JSON+ICS interception, French/Leos JSON key support, inline `<script>` JSON parsing, FullCalendar v3/v4+v5/v6 DOM extraction, and bounded multi-month stepping (1 back + 3 forward).
   - **Contract stability**: All Codex-agreed `last_stats` counters remain unchanged; additive diagnostic keys `timetable_strategy`, `ical_candidates_tried`, `edt_xhr_captured`, `edt_months_scanned` are non-sensitive.
3. **Google Drive & Moodle Document Parsing Coverage (`parsers/`, `connectors/ensora_drive.py`)**:
   - Added pure-Python zip-bomb-safe `XlsxDocumentParser` (`.xlsx`/`.xlsm`), `OpenDocumentParser` (`.odt`/`.odp`/`.ods`), strictly inert `ZipArchiveDocumentParser` (`.zip`), `.rmd`/`.qmd` Markdown, `.rtf`/`.tsv`/`.bib`/`.xml`/`.toml`/`.log` text/code parsing, and magic-byte sniffing (`sniff_mime_from_bytes`) in `DocumentParserRegistry` (preserving strict error reporting on malformed `.pdf`/`.docx`/`.pptx`!).
   - `EnsoraDriveConnector` now skips unexportable Google Workspace items (`form`, `site`, `jam`, `map`, `script`), falls back to `text/plain` on Google Docs `exportSizeLimitExceeded`, and records `last_stats` (`folders_traversed`, `resources_discovered`).
4. **Brainstorming & Requests for Codex (No Concurrent Edits to Codex Files)**:
   - **File size limit (`backend/app/core/settings.py:55`)**: `max_document_bytes` defaults to `25 MiB`, which can reject large Deep Learning course slide decks/datasets on Moodle (`course 505`) or Drive. Recommend either raising the default to `50–100 MiB` or explicitly setting `MAX_DOCUMENT_BYTES=104857600` in live sync commands.
   - **Drive `folders_traversed` metric (`codex_bridge.py` / `audit_data.py`)**: `EnsoraDriveConnector.last_stats` now exposes `folders_traversed` and `resources_discovered` as non-negative ints; Codex can enable propagation in `codex_bridge.py` when convenient so Drive folder coverage is no longer `null` in `audit_data.py`.
   - **ETL-side selective retry (`scripts/sync.py` / `core/sync.py`)**: `scripts/p1_sources_cli.py` now supports `--verify-all-downloads`, `--only-external-id`, and `--retry-failed <REPORT_JSON>`. Adding a `--retry-failed-documents` option (or retrying `versions.status == 'failed'` / failed downloads selectively) on `scripts/sync.py` would make recovering partial live runs fast and idempotent without re-traversing healthy courses.
   - **Real-source ETL note**: `CodexParserBridge.parse()` automatically inherits all new parsers (`xlsx`, `odt`/`odp`/`ods`, `zip`, `rmd`/`qmd`, `rtf`, magic-byte sniffing) because `etl.py:97` already updates `record.title` and `record.mime_type` from `FetchedDocument` before calling `parser.parse()`.

## Final Pamplemousse Integration Regression — Findings & Collaboration Note for Codex
1. **Root Cause Confirmed**:
   - `parse_pamplemousse_notes_html()` emits Pamplemousse `BULLETIN DÉTAILLÉ` assessment coefficients (`TP Distillation` = `1.0`, `Final Homework` = `2.0`) with `weight_mode="raw"` (never fabricating normalized weights in the connector).
   - Codex's bridge update rightly preserved bare institutional coefficients as `SubjectCoefficient(level="assessment", mode="raw")`, initially causing `test_pamplemousse_live_nav_edt_inscriptions_notes_reconciliation_and_codex_etl:400` to miss `assessment_weight`.
   - Codex subsequently added sibling-set derivation in `codex_bridge.py:1096-1137` emitting explicitly tagged `AssessmentWeight(mode="fraction", weight_basis="normalized_sibling_coefficients")` alongside the untouched raw `SubjectCoefficient(level="assessment", mode="raw")` when all siblings (`>= 2`) have known raw coefficients with positive sum.
2. **Independent P3 SQLite Persistence Verification**:
   - Strengthened `test_pamplemousse_live_nav_edt_inscriptions_notes_reconciliation_and_codex_etl` to query persisted SQLite `records` directly and verify:
     - Raw assessment-level `SubjectCoefficient(level="assessment", mode="raw")` preserved (`1.0`, `2.0`).
     - Subject-level zero coefficient (`"0"`, `record_status="known"`) distinct from missing coefficient (`None`, `record_status="missing"`).
     - Derived sibling `AssessmentWeight` records carry `mode="fraction"`, `weight_basis="normalized_sibling_coefficients"`, and sum to `1`.
     - `Subject.ects` (`4`) preserved independently of coefficients.
     - `PublishedGrade` separates `official_aggregate` (`16.5`) from `assessment` (`17.5`) and missing (`None`, `record_status="missing"`, never `0`).
3. **Contract Commitments & Technical Review Feedback to Codex**:
   - Gemini confirms Codex's 21:29 handoff requests: the updated end-to-end SQLite assertions (including the null guard on grade values) are retained, `weight_basis` (`"source_reported"` vs `"normalized_sibling_coefficients"`) is documented as an additive P3 contract field, and `provenance.aggregate_scope` (`overall`, `semester`, `teaching_unit`, `subject`), `weight_mode="raw"`, and all Moodle/Pamplemousse `last_stats` counter names remain stable.
   - **Independent Full-Suite Confirmation**: Verified the complete 99-test canonical suite (35 Codex integration + 64 Gemini unit/bridge/CAS/live-completeness tests) plus `pip check` cleanly after Codex's 21:29 commit-free workspace update (**99 passed in 6.68s**, zero AnyIO stall in this environment).
   - **Edge-case note for Codex**: In `codex_bridge.py:1063-1137`, sibling-set normalization currently checks `page.complete and not page.errors and not conversion_error_codes` over `page.items`. While Pamplemousse runs currently fit inside a single 500-item page, also checking `cursor is None` (or grouping across a full subject across pages) will prevent partial-page mis-normalization if a large bulletin ever paginates across multiple pages. Also note P3 UI/Grade Tracker should treat `weight_basis="normalized_sibling_coefficients"` as derived/provisional because Pamplemousse hides unevaluated assessments/grades until course evaluations are completed.

## Live ETL Acceptance Alignment (Shared with Codex)
1. **Moodle (`connectors/moodle.py`, `codex_bridge.py`)**:
   - Full `my/courses.php` discovery (preferring `block_myoverview` over `block_recentlyaccessedcourses`, waiting for dynamic rendering, bounded read-only pagination), configurable `academic_year` & `include_archived`, tab-based `&section=N` traversal (respecting disabled/unavailable sections), inline HTML instructions/code extracted as `Document(resource_kind="educational_html")` (HTML bytes returned strictly through `fetch_blob` -> `FetchedDocument`, never raw HTML in metadata), and per-page `ChangePage.observations` (`courses_discovered`, `sections_discovered`, `sections_traversed`, `folders_traversed`, `resources_excluded`).
2. **Pamplemousse (`connectors/pamplemousse.py`, `codex_bridge.py`)**:
   - Discovers read-only Mon Espace links by link text & href (`iCalendar`, `EDT` `p=121`, `Inscriptions` `p=107`, `Notes` `p=1052`), detects academic year (`2026-2027`), prefers iCalendar `.ics` export supplemented by FullCalendar passive JSON XHR + `Liste mois`/`Liste semaine` DOM parsing (`Europe/Paris` -> UTC, `duration_minutes`), parses `Inscriptions` (`UE|Matière|Code|Coef.|ECTS|Sem.`, `sans UE`, zero vs missing coef, separate `ects`, distinct repeated names) and hierarchical `BULLETIN DÉTAILLÉ` (`official_aggregate` vs `assessment` grades, French decimals, missing != 0, `Classement` ignored), reconciles cleanly into Codex's `AcademicYear -> Semester -> TeachingUnit -> Subject -> Assessment -> PublishedGrade` DTOs with `record_status`, `source_code`, `ects`, `level`, and `mode="raw"`.
3. **Google Calendar & Ensora Drive (`codex_bridge.py`, `scripts/p1_sources_cli.py`)**:
   - `CodexConnectorBridge` forwards `SyncScope.window_start`/`window_end` and keeps scoped window queries isolated from durable global `syncToken` cursors and `CalendarSnapshot`; `sync-calendar` reports anonymous `busy_intervals` / `free_intervals_count` (zero private titles/locations/descriptions).
   - `discover-drive --all --verify-downloads N` traverses all pages within approved root `1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO` and verifies downloads/Workspace exports + `DocumentParserRegistry.parse_bytes()`.
   - `_to_codex_auth_result(..., institutional=True)` now strictly requires `meta.get("origin_verified") is True` per Codex's handoff recommendation.

## Post-`phase=completed` Session Failure Root Cause & Fix (`auth/ensai.py`, `scripts/p1_sources_cli.py`)
1. **Exact Root Cause Proven**:
   - **Cause A (Chromium session-cookie purge)**: `MoodleSession`, `PHPSESSID`, and CAS `TGC` are session cookies (`expires == -1`). Chromium purges all `expires == -1` cookies on `context.close()`, so reopening Chromium in `adapter.validate()` or in a separate CLI process had zero session cookies and redirected immediately back to `ssocas.ensai.fr/cas/login`.
   - **Cause B (Codex transition check)**: `backend/app/core/auth.py` previously called `adapter.validate()` unconditionally right after `reauthorize()` returned `CONNECTED`, immediately hitting Cause A and overwriting `CONNECTED` with `REAUTH_REQUIRED cas_login_required`. Codex updated `AuthenticationManager` to preserve verified interactive protected-service evidence while keeping fresh-context `validate()` and acquisition strictly verified.
   - **Cause C (Headed vs Headless User-Agent consistency)**: `open_context()` now passes a consistent desktop Chrome `user_agent` (`DEFAULT_ENSAI_USER_AGENT`) to both headed and headless Chromium contexts.
2. **Secure Session-Cookie Persistence (`0o600`, `O_NOFOLLOW`, Allowlisted Domains Only)**:
   - `_save_session_cookies()` persists live allowlisted institutional cookies (including `expires == -1`) atomically to `<profile_dir>/.ensora_session_cookies.json` before closing verified contexts; `_restore_session_cookies()` restores them in `open_context()`.
   - Third-party cookies are strictly excluded; cookie names/values are never logged; `disconnect()` / `clear_shared_session()` deletes the file cleanly.
3. **5-Stage Human-Operated Debug Verification (`--debug-session`, `--keep-open`)**:
   - `verify_session_lifecycle()` keeps Chromium open until the user confirms (`keep_open_until_confirmed=True`), persists & closes cleanly, reopens the same persistent profile headlessly (without auto-reauth), verifies the same protected resource, and reports distinct stages: `LOGIN_COMPLETED`, `PROTECTED_RESOURCE_VERIFIED`, `SESSION_PERSISTED`, `SESSION_REOPENED`, and `SESSION_REUSE_VERIFIED`.

### Exact Human-Operated Debug & Live Validation Commands
```bash
cd /home/ensai/hackathon_ENSORA

# 1. Human-operated 5-stage debug verification (keeps Chromium open until Enter, persists, reopens headlessly, verifies reuse):
.venv/bin/python scripts/p1_sources_cli.py auth-ensai --source moodle --debug-session
.venv/bin/python scripts/p1_sources_cli.py auth-ensai --source pamplemousse --debug-session

# 2. Onboarding reauthorize (must now report CONNECTED) & separate-process validation:
.venv/bin/python scripts/onboarding.py --source moodle --reauthorize
.venv/bin/python scripts/check_connections.py --validate --source moodle --source pamplemousse

# 3. Protected resource acquisition using the persisted session:
.venv/bin/python scripts/p1_sources_cli.py sync-moodle --single-course
.venv/bin/python scripts/p1_sources_cli.py sync-pamplemousse --max-items 20
```

## Persistent Authentication & Automatic Reauthentication (`credentials.py`, `config.py`, `auth/ensai.py`, `codex_bridge.py`, `scripts/p1_sources_cli.py`)
1. **Canonical OS Keyring Unification & Lifecycle**:
   - `KeyringCredentialStore` aligns with Codex's exact OS keyring layout (`service="ensora-plus"`, keys `ensai_cas:username` / `ensai_cas:password`), rejects non-OS/plaintext backends, enforces explicit user consent + masked input (`GetPassWarning` treated as error), and supports atomic credential replacement (`--replace-credentials`) and complete deletion (`--delete-credentials`).
2. **Non-Destructive `ENSAI_AUTO_LOGIN` Policy Flag**:
   - `SourcesRuntimeConfig.set_non_sensitive_env_flag()` updates ONLY `ENSAI_AUTO_LOGIN=true|false` in `~/.config/ensora-plus/sources.env` (`0o600`, atomic replace) preserving all existing private configuration verbatim and strictly refusing any sensitive key name.
3. **Cross-Restart Reuse + Headless Auto-Reauth + Interactive MFA Fallback**:
   - `CodexEnsaiAuthBridge.validate()` and `EnsaiAuthAdapter.check_source_connection(..., allow_auto_reauth=True)` reuse valid persistent Chromium sessions in `~/.config/ensora-plus/sessions/ensai/` across restarts for both Moodle and Pamplemousse.
   - If the CAS session expires AND `ENSAI_AUTO_LOGIN=true` AND OS-keyring credentials exist, a single bounded **strictly headless** reauthentication refreshes the persistent Chromium profile automatically (including CAS `"Connexion réussie"` without redirect).
   - If MFA/CAPTCHA or invalid credentials block headless reauth, MFA is never bypassed and interactive login remains the clean fallback.

### Exact Human-Operated Commands for Persistent & Automatic Authentication
```bash
cd /home/ensai/hackathon_ENSORA

# One-time secure setup (masked OS-keyring storage with explicit consent + ENSAI_AUTO_LOGIN=true + persistent session):
.venv/bin/python scripts/p1_sources_cli.py auth-ensai --configure-auto-login

# Verify persistent reuse (and headless auto-reauth on expiry) across separate executions:
.venv/bin/python scripts/p1_sources_cli.py auth-ensai --check-only
.venv/bin/python scripts/check_connections.py --validate --source moodle --source pamplemousse

# Securely replace or delete stored OS-keyring credentials at any time:
.venv/bin/python scripts/p1_sources_cli.py auth-ensai --replace-credentials
.venv/bin/python scripts/p1_sources_cli.py auth-ensai --delete-credentials --disable-auto-login
```

## Post-Login Interactive ENSAI CAS Lifecycle Fixes (`auth/ensai.py`)
1. **No Premature CAS Ticket Interruption (`is_transient_sso_url`, `_settle_navigation`, `_wait_for_interactive_login`)**:
   - Previously, `_returned_to_service()` returned `True` immediately on `?ticket=ST-...`, and `authenticate()` called `page.goto(protected_url)` mid-flight, aborting Moodle's `/login/index.php?authCAS=CAS&ticket=...` -> `?testsession=...` handshake and Pamplemousse's `?ticket=...` validation before session cookies were set.
   - Now `is_transient_sso_url()` explicitly detects `?ticket=`, `?testsession=`, and `/login/index.php?authCAS=CAS` and waits for redirects to finish before inspecting or navigating.
2. **Real Interactive Polling + Explicit Human Confirmation + CAS-Success Bridge**:
   - Real browser interactive runs keep Chromium open in a bounded polling loop (up to `wait_timeout_ms`, default 180s) rather than skipping or exiting prematurely on target-host login forms.
   - If CAS displays `"Connexion réussie"` / `"Log In Successful"` without redirecting back, the loop automatically navigates to `protected_url` to complete silent SSO via the active CAS `TGC` cookie.
   - In an interactive TTY, operators can either let automatic detection finish once Moodle/Pamplemousse loads OR press `Enter` to trigger immediate protected-resource verification (or type `q` + `Enter` to cancel).
3. **Same-Origin Iframe & Modern Moodle / Pamplemousse Detection + Session Flush**:
   - `_collect_page_html()` inspects both the main document and same-origin child `page.frames` (handling Leos/Pamplemousse framesets/iframes).
   - `classify_page_state()` supports modern Moodle dashboard/userid markers and cookie-backed Pamplemousse shells while strictly rejecting guest, login-form, and unauthenticated pages.
   - Real browser sessions flush persistent profile storage briefly before context close.
4. **Strictly Privacy-Preserving Diagnostics (`EnsaiAuthDiagnostics`)**:
   - Reports ONLY `phase`, `navigation_state`, `current_host`, `current_path` (all query strings, fragments, and CAS tickets stripped), `redirect_completed`, `session_persisted`, `service_cookies_present` (boolean only), and exact `failure_reason`.
   - Emitted to `stderr` during real interactive runs (leaving JSON `stdout` clean) and included in `AuthStatus.metadata["diagnostics"]`. Zero passwords, cookies, CAS tickets, tokens, or private page contents are ever logged.

## ENSAI CAS/SSO Lifecycle Alignment (Completed for Codex Runtime & Standalone CLI)
1. **Protected-Resource Validation & Shared Auth Evidence (`auth/ensai.py`, `codex_bridge.py`)**:
   - `EnsaiAuthAdapter.protected_url_for(SourceKind.MOODLE)` targets `moodle_protected_url` or `urljoin(moodle_base_url, "my/courses.php")` (never public front page `/`). `classify_page_state()` rejects guest/login-form/public landing pages and requires explicit authenticated shell proof (`logout.php` / `sesskey` / `usermenu`).
   - `_to_codex_auth_result(..., institutional=True)` emits `AuthenticationResult(status=ConnectionStatus.CONNECTED, session_state="active", service_access="verified")` ONLY after a verified protected probe (`session_state="unknown", service_access="verified"` for Moodle REST WS), `REAUTH_REQUIRED` with `reason="cas_login_required"` on CAS redirect/expired session, and `ACCESS_DENIED` with `reason="service_access_denied"` on HTTP 403.
   - `_CodexCredentialStoreAdapter.get_secret()` and `_optional_account_hint()` safely handle unavailable OS keyrings without turning a valid browser session into `ConnectionStatus.ERROR`.
   - `runtime_config_from_codex_settings(settings)` consumes Codex's centralized `Settings` fields directly (`ensai_auto_login`, `ensai_shared_session`, `ensai_timeout_ms`, `moodle_protected_url`, `pamplemousse_protected_url`) without reopening `sources.env`.
2. **CAS `?service=` Preservation, Silent SSO Reuse & Runtime Safety (`auth/ensai.py`, `codex_bridge.py`)**:
   - `_probe_on_page()` settles SSO/ticket redirects (`networkidle` / `?ticket=`) and follows official service-provided CAS links (`authCAS=CAS` or `ssocas.ensai.fr/cas/login?service=...`) preserving exact service redirect parameters; fallback constructs `cas_login_url?service=<encoded protected_url>`.
   - Inside Codex `Runtime` / `SyncEngine`, `CodexConnectorBridge` strictly sets `interactive=False, allow_auto_reauth=False` (zero headed windows or connector-side CAS retries during listing/fetch) and translates expired sessions to `ReauthenticationRequired("cas_login_required")` and explicit denials to `ServiceAccessDenied("service_access_denied")`.
   - In standalone interactive CLI (`scripts/p1_sources_cli.py sync-moodle` / `sync-pamplemousse`), bounded 1-attempt interactive reauth resumes interrupted acquisition automatically; `--non-interactive` (or non-TTY) never opens a visible browser window and outputs clean redacted `REAUTH_REQUIRED` JSON (`exit 2`).
3. **Moodle & Pamplemousse Acquisition Hardening (`connectors/moodle.py`, `connectors/pamplemousse.py`)**:
   - **Moodle**: Strips Moodle 4.x screen-reader course title prefixes (`Nom du cours`), falls back `my/courses.php` -> `my/` -> `user/profile.php`, expands nested `/mod/folder/` modules (capped at 20 per course), differentiates CAS redirects (`AuthenticationRequiredError`) from untrusted external redirects (`PermissionDeniedError`) during downloads, unwraps `/mod/resource/view.php` HTML wrappers to `/pluginfile.php/...`, and extracts real filenames from `Content-Disposition`.
   - **Pamplemousse**: Prevents academic grade tables (`Matière/Note/Coef` with no date/time column) from being misparsed as timetable events; conservatively discovers up to 4 same-origin authenticated timetable/planning/ICS/notes subpages when the landing page is a portal menu; preserves ambiguity markers (`confirmation_required=True`) and missing values (`ValueStatus.MISSING`).

## Exact Human-Operated Live Validation Commands (Stop `scripts/start.py` First)
```bash
cd /home/ensai/hackathon_ENSORA

# 1. Inspect stored status (offline, read-only)
.venv/bin/python scripts/check_connections.py

# 2. Authenticate Moodle & Pamplemousse interactively on the shared persistent profile
#    (complete official ENSAI CAS login & any MFA directly in the Chromium window)
.venv/bin/python scripts/onboarding.py --source moodle --reauthorize
.venv/bin/python scripts/check_connections.py --validate --source moodle

#    Verify whether Pamplemousse reuses the shared CAS SSO session; reauthorize if needed:
.venv/bin/python scripts/check_connections.py --validate --source pamplemousse
.venv/bin/python scripts/onboarding.py --source pamplemousse --reauthorize

# 3. Bounded redacted discovery (interactive TTY auto-resumes if CAS expired; add --non-interactive to forbid popups)
.venv/bin/python scripts/p1_sources_cli.py sync-moodle --single-course
.venv/bin/python scripts/p1_sources_cli.py sync-pamplemousse --max-items 20

# 4. Persistent unified Runtime sync (and resume an interrupted job after reauth)
.venv/bin/python scripts/sync.py --source moodle --subject-id COURSE_ID --approve
.venv/bin/python scripts/sync.py --source pamplemousse --approve
# If a Runtime job stopped on CAS expiry, reauthorize above and resume the exact same scope:
.venv/bin/python scripts/sync.py --resume JOB_ID --source moodle --approve
```

## Verification Command
```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pip check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -v backend/tests
```

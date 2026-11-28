# Gemini P1 Handoff — Source Integration, Authentication & Document Processing

- **Ticket / owner / branch**: P1 Source Integration / Gemini / `feat/p1-data-engineering`
- **Base commit**: `89ad3d8`
- **Current status**: **`ENSORA_DRIVE_GLOBAL_EXCLUDED_CONNECTOR_READY`** — all Gemini unit & bridge tests pass (`compileall` clean, `pip check` clean); combined backend suite reports **130 passed, 1 failed** solely at Codex-owned `backend/tests/integration/test_p1.py:718` (see review note below). Active live Drive sync untouched. Zero Codex-owned files modified; zero Git write operations; zero live DB access.
- **Live Validation Report & Operator Runbook**: See [GEMINI_LIVE_VALIDATION.md](GEMINI_LIVE_VALIDATION.md).
- **Composition Factory Entry Point** (default in `backend/app/core/settings.py`):

```python
ADAPTER_FACTORY = "backend.app.modules.ingestion.codex_bridge:build_components"
```

## Exclude `Global` from Ensora Google Drive (`1A`/`2A`/`3A` Only) — Connector Contract & Mutual Review for Codex (2026-11-28)

### 1. Connector Enforcement (`connectors/ensora_drive.py`, `codex_bridge.py`, `scripts/p1_sources_cli.py`)
- **Authorized scope**: Root `1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO`, strictly top-level folders `1A`, `2A`, `3A` (`DRIVE_SCOPE_POLICY_VERSION = 2`, `DRIVE_SCOPE_POLICY_ID = "academic_cohorts_v2:1a,2a,3a"`). Top-level `Global`, non-academic folders, and stray root files are **excluded completely**.
- **Pre-traversal top-level resolution**: `resolve_authorized_top_level_folders()` lists direct children of root once (`1` `files.list` call) and seeds BFS ONLY with resolved `1A`/`2A`/`3A` folder IDs. `Global` and its descendants are **never queued and never listed** (0 wasted API requests). Legitimate nested subfolders like `Ensora/2A/Finance/Global` remain included.
- **Boundary enforcement across all paths**: `_resolve_folder_ancestry()` / `_validate_target_folder_boundary(..., require_approved_cohort=True)` enforces `1A`/`2A`/`3A` containment via live Drive `files.get(fields="id,name,mimeType,parents,trashed")` on:
  - Full & incremental (`drvmod:`) synchronization
  - Direct subfolder sync (`folder_id=...`) and explicit `cohorts=["Global"]` (raises `PermissionDeniedError`)
  - Direct downloads & selective retries (`fetch_blob()` blocks `Global`, root-stray, trashed, and personal files before any `get_media`/`export_media` call)
  - Shortcuts pointing to `Global`, `Global` descendants/files, root, or personal Drive (blocked & counted in `excluded_shortcuts_skipped`)
- **Checkpoint invalidation & filtering**: Legacy `v1` `drvpage:` snapshots (created before Global exclusion) are rejected & purged on load; resumed `v2` snapshots defensively filter `queue`/`pending_items`/`membership_additions` and revalidate live ancestry across restarts.
- **Metadata preservation**: `normalize()` preserves `folder_path`, explicit `academic_year` (when present, never guessed), `specialization_hint`, `semester_hint`, `cohort_hint`, `subject_hint`, `academic_category`, `top_level_folder_id`, `top_level_folder_name`, and `scope_policy`.
- **Measured API savings (`test_sprint7_*`)**: Controlled 35-folder fixture (root + `1A`/`2A`/`3A` + 5 academic subfolders + `Global` + 25 `Global` nested folders + `Divers`) used `<= 4` batched `files.list` calls with **0 calls** to `Global` or any of its 25 descendants (**>= 27 folder listings saved**).

### 2. Mutual Code Review & Coordination Notes for Codex (`backend/app/core/drive_scope.py`, `etl.py`, `sync.py`, `test_p1.py`)
1. **Codex-owned test failure to fix (`backend/tests/integration/test_p1.py:718`)**:
   - `test_catalog_sync_reconciles_prior_documents_without_reprocessing_and_preserves_overrides` ingests `drive:linear:lesson` and `drive:linear:manual` with `Provenance(external_id=..., source=SourceKind.ENSORA_DRIVE)` and `folder=None`.
   - Codex's new fail-closed `drive_document_in_scope()` in `backend/app/core/drive_scope.py` skips `drive:linear:lesson` during `reconcile_documents()`, causing `assert report["metrics"]["catalog_documents_matched"] == 2` to fail (`1 == 2`).
   - Adding `folder="Ensora/2A/Linear Algebra"` to those synthetic Drive fixtures in `test_p1.py` will bring the full 131-test suite to **131/131 passed**. (Gemini did not modify `test_p1.py`.)
2. **Two cross-agent integration fixes applied in Gemini-owned files to align cleanly with Codex's parallel edits**:
   - `ensora_drive.py`: Treated passing the full approved set `cohorts=["1A", "2A", "3A"]` (now sent by Codex's `CodexConnectorBridge.drive_allowed_cohorts`) identically to unscoped default so `authoritative_complete=True` and Drive membership reconciliation/reactivation (`test_final_block_003_*`) work cleanly.
   - `codex_bridge.py:1173`: Scoped Codex's new `folder_scope(item.folder_path) != "allowed"` filter to `item.access_status == AccessStatus.ACTIVE` so tombstoned Drive records still reach `ETL._document()`.
3. **Previously imported `Global` documents (non-destructive exclusion)**:
   - Confirmed Codex's `drive_scope.py` (`folder_scope` + `drive_document_in_scope`) matches Gemini's exact top-level `Global` provenance semantics (root-relative first descendant segment `Global`, never false-excluding `Ensora/2A/.../Global`) and excludes previously imported `Global` docs from active downstream queries/reconciliation without deleting DB rows or modifying the live DB.
   - `_load_active_drive_documents()` retains Codex's `if folder_scope(folder) == "allowed"` filter AND preserves `folder` for active `1A`/`2A`/`3A` tombstones. Note for Codex review: `backend/app/core/sync.py:347` also checks `not drive_document_in_scope(...)` before `pipeline.ingest()`, which is consistent with keeping historical `Global` rows immutable and read-excluded via `drive_document_in_scope()`.

## User-Configurable Incremental Synchronization — Connector Contract for Codex (2026-11-28)

Codex owns scheduling, user-configurable per-source intervals, enable/disable toggles, startup sync policy, and settings API contracts. Gemini implemented **zero scheduler logic and zero hardcoded intervals** inside connectors, and ensured every connector cleanly distinguishes **initial acquisition**, **incremental scheduled update**, **explicit `--full` sync**, and **`--retry-failed` selective recovery**:

| Source / Slice | Default Recommendation (User-Configurable in Codex) | Connector Incremental & Sync-Mode Primitives Implemented |
|---|---|---|
| **Ensora Drive** | Every **7 days** (`604800s`) | Incremental `drvmod:` watermark + 15-folder batched `files.list` (`pageSize=1000`) + `page_size=25` streaming BFS + crash-safe 24h `drvpage:` snapshots (surviving 65+ evictions) + `etl.py` unchanged-revision skip |
| **Moodle** | Every **7 days** (`604800s`) | Durable `moodle_sync:<iso>` watermark on clean completion + read-only SQLite `known_versions` skip wired in `build_components()` (`not scope.full and not scope.retry_failed`) + explicit `last_stats["sync_mode"]` (`initial`/`incremental`/`full`/`selective`) & `resources_unchanged_skipped` + cookie-backed HTTP fetch + `/user/profile.php` fallback |
| **Pamplemousse — Timetable** | Every **6 hours** (`21600s`) | `sections="timetable"` visits ONLY `iCalendar` / `p=121` EDT; deterministic wall-clock-free `remote_version` (`_stable_version_payload()`); unchanged events skipped individually via read-only `_load_known_record_revisions()` |
| **Pamplemousse — Grades** | Every **24 hours** (`86400s`) | `sections="grades"` visits ONLY `Notes` (`p=1052`); deterministic `remote_version` (strips `observed_at`/`retrieved_at`/`imported_at`); skips academic records as a consistent whole when zero facts changed so hierarchy & sibling-weight derivation stay intact on updates |
| **Pamplemousse — Catalog & Coefs** | Every **7 days** (`604800s`) | `sections="catalog"` visits ONLY `Inscriptions` (`p=107`); deterministic `remote_version` + consistent whole-tree incremental skip; safe trigger for `ETL.reconcile_documents()` |
| **Google Calendar** | Every **30 minutes** (`1800s`) | Live-verified incremental `syncToken` + 410 recovery + bounded windows (preserved untouched) |
| **Local files** | **On change** (`None`) | Fast metadata-only `LocalFileConnector.scan_fingerprint(root_path)` (SHA-256 over `rel_path|size|mtime_ns`, symlink/protected-root safe, zero file reads) + `known_hashes` skip |

### Shared Contract Coordination Notes for Codex (`backend/app/contracts/sources.py`)
- **Proposed additive `SyncScope` field**: `pamplemousse_scope: Literal["all", "timetable", "grades", "catalog"] | None = None` (or `sync_slice` / `dataset` — `CodexConnectorBridge.list_changes()` already forward-reads `pamplemousse_scope`, `sync_slice`, `dataset`, and `sections` and forwards them to `PamplemousseConnector.list_changes(scope={"sections": ...})`!).
- **Recommended scheduler rules for Codex**:
  - Treat weekly (Drive/Moodle/Pamplemousse catalog), 24h (grades), 6h (timetable), and 30m (Calendar) values in `RECOMMENDED_SYNC_POLICY_SECONDS` (`connectors/base.py`) as **defaults only**; persist user overrides (custom interval, disabled, manual immediate trigger) in Codex's settings/DB layer.
  - Treat slice-scoped Pamplemousse runs (`timetable` / `grades` / `catalog`) as non-destructive upserts so a 6-hour `timetable` run never retires `grades` or `catalog` records, and only trigger `ETL.reconcile_documents()` after `catalog` or `all` runs.
  - Keep application startup non-blocking / free of automatic full acquisitions; existing locally persisted documents, extracted chunks, and original blobs (`local_data_dir/blobs/<sha256>`) remain immediately accessible between scheduled cycles.
- **Note on `MoodleConnector.sync_token`**: Clean zero-error complete Moodle runs now return `sync_token = "moodle_sync:<iso>"` so Codex can distinguish initial vs incremental Moodle runs via `cursor`. `MoodleConnector.list_changes()` accepts `moodle_sync:` cursors cleanly (`offset = 0`) and never breaks pagination cursors (`"25"`, etc.). Verified compatible with all existing Codex CAS checkpoint/resume integration tests.

### Verification (Synthetic Only)
- `.venv/bin/python -m compileall -q backend/app scripts` — **OK**
- `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests -q` — **127 passed in 19.56s** (0 failed, 0 skipped; includes `test_sprint5_*` and `test_sprint6_incremental_deterministic_revisions_moodle_checkpoint_and_sync_modes`)
- `.venv/bin/python -m pip check` — **No broken requirements found.**

## Partial-Parsing Investigation, Codex 64-Eviction Fix & Selective Reprocessing Proposals (2026-11-28)

### 1. Investigation of Live 98/233 Partially Parsed Drive Documents (Expected vs Defect)
- **Original files verified preserved**: `backend/app/core/etl.py:168` writes 100% of downloaded original bytes verbatim to `local_data_dir/blobs/<sha256>` before parsing; partial/unsupported status never discards original files.
- **Root cause of the 98/233 partial count**:
  1. **Over-flagging defect fixed (`parsers/pdf_parser.py:319-340`)**: Previously, ANY PDF with `empty_pages > 0` (even 1 blank/diagram slide in a 30-page Beamer/PowerPoint PDF!) appended `scanned_pages_without_ocr_engine` to `unsupported_elements`, forcing `ParsedContent.status = "partial"`. Now: text-rich PDFs with `<30%` empty pages stay `status="ready"` with `warning_codes=["mixed_media_pages_present"]`, while genuinely scanned/image-heavy PDFs (`blocks == 0` or `>=30%` empty pages) remain `status="partial"` with `scanned_pages_without_ocr`.
  2. **Expected non-defect partial categories** (now distinguished via specific privacy-safe allowlisted tokens in `CodexParserBridge.parse()`, replacing opaque `"parse_warning"`):
     - `scanned_pages_without_ocr`: Scanned/image-heavy PDF without installed `tesseract` (systematic OCR intentionally avoided during hackathon; original PDF preserved for future selective OCR).
     - `ocr_unavailable`: Standalone `.png`/`.jpg`/`.webp`/`.tiff` images/diagrams preserved as original blob + figure metadata.
     - `legacy_binary_office`: Legacy OLE2 `.doc`/`.xls`/`.ppt` heuristic UTF-16LE/Latin-1 text extraction (`quality=0.55`).
     - `non_textual_archive`: Valid `.zip` containing only binary datasets/weights (`.npy`/`.pt`/`.png`) emitting an archive manifest block (`quality=0.25`).
     - `archive_limit_truncated`: ZIP member skipped/truncated by zip-bomb or max-member security guard.
     - `notebook_visual_outputs`: Jupyter `.ipynb` with embedded matplotlib/image outputs preserved as figure metadata (keeps `status="ready"`).

### 2. Confirmed Fix for Codex's Drive Snapshot Eviction Finding (`CODEX_HANDOFF.md:343`)
- Fixed `EnsoraDriveConnector._store_bfs_paging_state()` to evict **only** the 64-entry in-memory `_paging_states` key and retain unexpired (`<24h`) on-disk `drvpage:<hash>.json` files until TTL expiry.
- Added Codex's exact requested regression `test_sprint4_drive_durable_snapshot_survives_65_evictions_codex_review` (retains first cursor, creates 65 later distinct handles, instantiates fresh connector, replays first cursor cleanly -> **PASSED**).

### 3. Collaboration Responses & Proposals for Codex (`CODEX_HANDOFF.md:331-345`)
1. **Catalog-arrival reconciliation (`ETL.reconcile_documents()` in `etl.py:249-290`)**:
   - **Reviewed & confirmed safe**: Consent gating (`allowed` + `is_authorized`), skipping tombstoned/revoked/unversioned docs, preserving `user_override` and `match.confirmed`, and per-document short transactions are clean.
   - `CodexEntityMatcherBridge.match()` is strictly metadata-only, deterministic, and idempotent (safe to repeat after Pamplemousse sync / academic import without touching blobs, hashes, versions, or chunks).
2. **Additive Ranked-Candidate DTO Proposal for P2 Review UI (`backend/app/contracts/sources.py`)**:
   - Propose extending `EntityMatch` additively (default empty lists, zero breaking changes) once Codex approves:
     - `SubjectCandidate(subject_id: Key, teaching_unit_id: Key | None = None, confidence: float, method: Key)`
     - `EntityMatch.candidates: list[SubjectCandidate] = Field(default_factory=list)` (top <=3 candidates)
     - `EntityMatch.evidence_codes: list[Key] = Field(default_factory=list)` (strictly allowlisted privacy-safe tokens e.g. `exact_code`, `confirmed_alias`, `folder_segment_match`, `initialism_review`, `tie_conflict`)
3. **Selective Local Blob Reprocessing Proposal (Zero Redownloads)**:
   - Because original files are already preserved at `local_data_dir/blobs/<sha256>`, propose a Codex-owned local reprocessing command (`scripts/sync.py --reprocess-stored [--source SOURCE] [--status partial|unsupported|failed] [--parser-mismatch]`) that reads stored blobs locally, re-runs `parser.parse()` -> `classifier.classify()` -> `matcher.match()` for selected versions, and replaces `versions.status`/`parser_version`/`parser_warning_codes` + `chunks` atomically without making any network/Drive/Moodle requests and without overwriting `user_override` or `match.confirmed`.
   - Also recommend surfacing aggregate `parser_warning_codes` counts (by allowlisted token & MIME type, zero filenames) in `scripts/audit_data.py` so operators immediately see how many of the 98 `partial` docs are `ocr_unavailable` vs `scanned_pages_without_ocr` vs `legacy_binary_office` vs `non_textual_archive`.

### 4. Verification (Synthetic Only)
- `.venv/bin/python -m compileall -q backend/app scripts` — **OK**
- Targeted Sprint 4 regressions (`test_sprint4_*`): **2 passed in 1.27s**
- `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests -q` — **124 passed in 20.10s** (0 failed, 0 skipped)
- `.venv/bin/python -m pip check` — **No broken requirements found.**

## Final Content Processing, Durable Paging Guarantees & P2/P3 Integration (2026-11-28)

### 1. Confirmed Response to Codex (`CODEX_HANDOFF.md:321`) — Durable Drive Paging Guarantees & Limitations
- **Confirmed & Implemented (`connectors/ensora_drive.py`)**: `_load_durable_paging_state()` and `list_changes()` no longer delete `drvpage:<key>.json` on read. Prior `0o600` (`O_NOFOLLOW`) snapshot files are retained on disk until their 24h TTL expires (expired snapshots `>24h` are pruned opportunistically on save), and in-memory state lookups deep-copy mutable containers so replaying a cursor is strictly idempotent.
- **Exact Guarantees vs Remaining Limitations (Connector Continuation vs Full ETL Job Recovery)**:
  - **What is guaranteed**:
    1. If a process terminates mid-page *before* Codex commits the returned `next_cursor` to SQLite, replaying the job from Codex's last committed `drvpage:<prev_key>` cursor safely reloads the exact pre-page BFS frontier without losing progress or duplicating committed pages.
    2. Every cross-process resume verifies `root_folder_id`, `scope_fingerprint`, and 24h TTL, and runs a live approved-root ancestry check (`_validate_target_folder_boundary`) before fetching any child folder.
    3. Global durable `drvmod:<iso_utc>` watermark and authoritative tombstone reconciliation are emitted **only** once the full approved tree completes cleanly with zero errors and zero depth truncation.
  - **Explicit limitations**:
    1. If a paused/interrupted job is replayed **after >24h** (snapshot expired) or with a modified scope, `drvpage:<key>` rejects cleanly (`ValueError`); full ETL recovery falls back to a fresh full-tree scan (where Codex ETL skips all unchanged `active_version_id` + `remote_revision` files with zero redownloads and preserves the prior durable `drvmod:` cursor until clean completion).
    2. Authoritative deletion/move-out tombstone reconciliation still requires a complete uninterrupted logical traversal across the approved root (cannot be inferred from a partial/failed listing).

### 2. P1 Content Processing, Classification, RAG Chunking & Cross-Source P2/P3 Matching
- **Classification (`contracts.py`, `classification/classifier.py`, `codex_bridge.py`)**:
  - Added `DocumentCategory.DATASET` (`"dataset"`) for tabular/statistical datasets (`.csv`, `.tsv`, `.parquet`, `.rds`, `.rdata`, `.sav`, `.dta`, `.arff`, `.feather`, `.h5`, `.npy`, `.npz`), dataset folders, and binary-only dataset ZIP manifests, while preserving explicit `practical_work`/`assignment`/`examination` precedence on instruction PDFs.
  - `CodexClassifierBridge.classify()` now forwards `document.resource_kind` (`announcement`, `educational_html`, `document`), `course_title`, and `chapter_id`/`section_path`; explicit Moodle announcements (`resource_kind="announcement"`) carry `0.90` administrative weight so exam-related forum announcements do not misclassify as exam papers.
- **RAG-Ready Parsing & P2/P3 Concept Signals (`codex_bridge.py`)**:
  - `CodexParserBridge.parse()` now deterministically splits oversized non-table blocks (`MAX_RAG_CHUNK_CHARS = 1800`, `RAG_CHUNK_OVERLAP_CHARS = 180`) on paragraph/newline/sentence boundaries while preserving `locator` + `sub_chunk`/`sub_chunks_total` + `heading_path` + `structured["language"]`.
  - Sets `ParsedContent.status = "partial"` whenever `unsupported_elements` is non-empty or `0 < extraction_quality < 0.70` (e.g. legacy `.doc` or scanned PDFs without OCR), aligning with Codex's `documents_partially_parsed` audit metric.
  - `extract_concept_candidates()` now copies `academic_year=document.provenance.academic_year` onto `Concept.provenance`; `extract_prerequisite_hints()` adds French cues (`pré-requis`, `avant de commencer`, `notions requises`, `chapitres requis`).
- **Cross-Source Academic Association (`resolution/entity_resolver.py`, `codex_bridge.py`)**:
  - `AcademicEntityResolver.resolve_record()` splits non-generic `folder_path` segments (so Drive `Ensora/2A/Econometrie/Annales` auto-matches Pamplemousse `Économétrie`) and adds conservative acronym hints (`PPDP`, capped at `0.82` -> `needs_review=True`).
  - `CodexEntityMatcherBridge.load_from_sqlite_engine()` hydrates `code`/`source_code`, `aliases`, and `parent_key` (`teaching_unit`); `match()` passes `course_title` & `section_path` and populates `EntityMatch.teaching_unit_id`; `find_exam_correction_pairs_from_engine()` excludes `ingestion_state == "download_failed"` shells.

### 3. Verification (Synthetic Only)
- `.venv/bin/python -m compileall -q backend/app scripts` — **OK**
- Targeted Sprint 3 regressions (`test_sprint3_*`): **2 passed in 2.06s**
- `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests -q` — **120 passed in 22.01s** (0 failed, 0 skipped)
- `.venv/bin/python -m pip check` — **No broken requirements found.**

## Accelerated Sprint — Drive 40x Discovery Speedup, Durable Paging, Stale Lock Cleanup & Cookie-HTTP Auth (2026-11-28)

### 1. Drive Discovery Bottleneck Proof & Resolution (`connectors/ensora_drive.py`, `codex_bridge.py`)
- **Why Job `2a7a5408` showed `Documents: 0` for minutes**:
  1. `CodexConnectorBridge` previously defaulted to `page_size = MAX_PAGE_CACHE_ENTRIES = 500`, so `_advance_bfs_page()` had to traverse ~250+ folders before emitting Page 1.
  2. `EnsoraDriveConnector` inherited `Settings.request_delay_seconds = 1.0s` (designed for institutional HTML scraping, NOT Google Drive API v3's 12,000 req/min quota).
  3. `_list_folder_children()` queried 1 folder at a time with `pageSize=100` (492 sequential calls = ~11 min).
- **Fixes implemented**:
  - `build_components()` now wires `CodexConnectorBridge(drive_conn, ..., page_size=DRIVE_STREAMING_PAGE_SIZE)` (`25`), while keeping `CodexConnectorBridge`'s default `MAX_PAGE_CACHE_ENTRIES` (`500`) intact for all other sources/tests.
  - `EnsoraDriveConnector` now batches up to `15` validated folders per `files.list` call (`pageSize=1000`, `('f1' in parents or ...) and trashed = false`) on real Drive API v3 (automatic single-folder fallback on error), cutting 492 folder queries to ~33 API calls (`last_stats["api_list_calls"]`).
  - Default polite pacing for Drive is capped at `min(config.rate_limit_min_interval_sec, 0.12)` (still <= 8 req/sec vs Google's 200 req/sec quota, with exponential backoff on HTTP 429).
  - **Durable `drvpage:<hash>` continuation implemented per Codex's `CODEX_HANDOFF.md:303`**: metadata-only BFS state is now persisted atomically (`0o600`, `O_NOFOLLOW`, 24h TTL) to `<config_dir>/state/drive_paging/<key>.json`, resumes seamlessly across process restarts with scope-fingerprint + approved-root revalidation, and cleans up on completion without losing the prior `drvmod:` watermark.
- **Request for Codex**:
  - If Codex adds a backend page-budget yield boundary on long Drive jobs, `drvpage:<hash>` is now restart-safe on disk and ready to integrate.
  - Reminder from earlier handoff: consider adding `"DriveResourceNotFoundError"` -> `"not_found"` and `{"DriveUnexportableError", "DriveExportSizeLimitError"}` -> `"unsupported_export"` alongside `"DownloadSizeLimitError"` in `backend/app/core/etl.py:_download_error_code()` (note `DriveSizeLimitError` is already mapped to `DownloadSizeLimitError` in `codex_bridge.py`).

### 2. Moodle Timeout & Pamplemousse `adapter_error` Resolution (`auth/ensai.py`, `connectors/pamplemousse.py`, `codex_bridge.py`)
- **Stale Chromium `SingletonLock` cleanup (`auth/ensai.py:open_context`)**: inside the exclusive `.ensora_profile.lock` lease, dead-PID `SingletonLock`/`SingletonSocket`/`SingletonCookie` files left behind by a system restart are safely unlinked (live PIDs are never touched).
- **Fast cookie-backed HTTP validation (`auth/ensai.py:_probe_via_cookies_http`)**: `check_source_connection()` now validates persisted allowlisted session cookies over strict same-origin HTTP FIRST (with automatic `/user/profile.php` fallback for Moodle if `/my/courses.php` times out), validating live sessions in <0.5s with zero Chromium launch and falling back to headless Playwright whenever cookies are missing/expired or CAS silent SSO is needed.
- **Playwright `/user/profile.php` fallback & actionable diagnostics**: `_probe_on_page()` also falls back to `/user/profile.php` if Moodle `/my/courses.php` times out after `commit` retry; `TransientSourceError`/timeout now maps to `errorcode="transient_failure"` -> `ConnectionStatus.UNAVAILABLE` (`reason="transient_failure"`) in `codex_bridge.py` instead of opaque `adapter_error`, and unexpected errors emit strictly redacted `exc_type` notices (zero secrets/cookies/URLs).
- **Pamplemousse cookie-backed HTML fetch**: `PamplemousseConnector._fetch_html()` now uses `_cookie_session_fetch_html()` for `index.php`, `p=107` (Inscriptions), and `p=1052` (Notes) before falling back to Playwright.

### 3. Response to Codex's Latest Review (`CODEX_HANDOFF.md:311-319`) & Mutual Code Review
- **`test_r2_bridge_fetched_document_metadata_and_page_size_cap` (500 vs 25) resolved cleanly without weakening any test**: `DRIVE_STREAMING_PAGE_SIZE = 25` is now passed explicitly inside `build_components()` for `EnsoraDriveConnector`, while `CodexConnectorBridge` keeps its generic `MAX_PAGE_CACHE_ENTRIES = 500` default for non-Drive/generic connectors. `test_r2_bridge_fetched_document_metadata_and_page_size_cap` continues verifying the generic 500 bound intact, while `test_sprint2_drive_batched_listing_durable_paging_across_restarts_and_small_bridge_batch` verifies `build_components()` Drive `page_size=25`, exhaustive multi-page coverage with zero drops/duplicates, durable `0o600` restart-safe `drvpage:<hash>` snapshots, and per-page delta counters.
- **Per-page Drive discovery progress counters (`CODEX_HANDOFF.md:313`)**: `EnsoraDriveConnector._advance_bfs_page()` now emits per-page deltas (`page_folders_traversed`, `page_resources_discovered`) alongside cumulative `folders_traversed`/`resources_discovered`; `CodexConnectorBridge` publishes those per-page deltas on **every page** (including incomplete pages) so `SyncEngine`'s additive `count()` reports non-zero discovery progress from Page 1 and sums to the exact cumulative tree totals at completion with zero double-counting.
- **Mutual Review of Codex's `main.py` & `etl.py` (`CODEX_HANDOFF.md:315,319`)**:
  - Verified `backend/app/main.py:291-375`: `active_document()` (`coalesce(access_status, "active") == "active" & active_version_id.is_not(None)`) is enforced consistently on `/api/v1/documents`, `/api/v1/documents/{document_id}`, `/versions`, `/content`, and `/chunks` — failed-download shells (`active_version_id IS NULL`) stay hidden from P2 reads while previously successful documents remain visible if a later update fails.
  - Verified `backend/app/core/etl.py:85-119`: maps all Gemini Drive exception classes (`DriveSizeLimitError`, `DriveResourceNotFoundError`, `DriveUnexportableError`, `DriveExportSizeLimitError`, `TransientSourceError`) to safe categories without provider messages/URLs and preserves `user_override` and `match.confirmed`.
  - Confirmed Codex's `test_interrupted_job_replay_preserves_original_academic_year_scope` fix passes cleanly.

### 4. Verification
- `.venv/bin/python -m compileall -q backend/app scripts` — **OK**
- Targeted regressions (`test_r2_bridge_fetched_document_metadata_and_page_size_cap` + `test_sprint2_*`): **3 passed**
- `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests -q` — **117 passed in 20.27s** (0 failed, 0 skipped)
- `.venv/bin/python -m pip check` — **No broken requirements found.**

## Hackathon Final Source Acquisition Sprint — Mutual Review & Coordination for Codex (2026-11-27)

### 1. Mutual Code Review of `backend/app/core/sync.py` & `test_p1.py` (Read-Only)
- **Verified `sync.py:225-228` syntax/transaction fix**: Confirmed `IndentationError` is fixed. `with self.engine.connect() as db:` properly indents `cursor = None if scope.full or selective else db.scalar(...)` and releases the read connection before entering the pagination loop. Terminal calendar retirement and cursor persistence (`sync.py:373-382`) run atomically inside `with self.engine.begin() as db:` under `self.auth.locks[source]`.
- **Verified `test_p1.py` green**: Independently confirmed Codex's full integration suite now passes **40/40 in 13.26s** (including `test_interrupted_job_replay_preserves_original_academic_year_scope` and `test_failed_download_is_private_retryable_and_replay_is_idempotent`).

### 2. Responses to Codex's 4 Design & Contract Requests (`CODEX_HANDOFF.md:297-309`)
1. **Drive streaming pagination & durable continuation (`CODEX_HANDOFF.md:303`)**:
   - Converted `EnsoraDriveConnector.list_changes()` from upfront full-tree traversal to **streaming / incremental BFS pagination** (`_advance_bfs_page` + `_store_bfs_paging_state`). Page 1 now yields as soon as `> page_size` files are discovered (seconds instead of 11+ minutes), while FINAL-BLOCK-003 authoritative membership reconciliation, tombstones, and durable `drvmod:` sync tokens remain strictly gated on terminal completion with zero errors and zero truncation.
   - `codex_bridge.py` now emits Drive's `folders_traversed` and `resources_discovered` observations on `page.complete` so `SyncEngine` records exact cumulative totals once at tree completion.
   - **Next step if Codex adds a backend page-budget yield**: Gemini agrees with connector-owned private `0o600` (`O_NOFOLLOW`) JSON persistence of `drvpage:<hash>` BFS state (with scope fingerprint, 24h TTL expiry, approved-root revalidation on resume, and zero loss of the prior durable `drvmod:` watermark) whenever Codex finalizes the backend yield contract.
2. **Drive safe failure categories (`CODEX_HANDOFF.md:305`)**:
   - Added typed exceptions in `connectors/ensora_drive.py`: `DriveSizeLimitError`, `DriveResourceNotFoundError` (404), `DriveUnexportableError` (400/403 non-exportable Workspace), and `DriveExportSizeLimitError` (10MB export cap after multi-step fallbacks), plus comprehensive `_classify_http_error()` retrying network timeouts (`TimeoutError`, `ConnectionError`, `ssl.SSLError`, `http.client.HTTPException`) and HTTP 403 `downloadQuotaExceeded`/`rateLimitExceeded`/`backendError`, plus transparent one-time 401 OAuth token refresh and `service` client reuse.
   - Mapped `DriveSizeLimitError` -> `DownloadSizeLimitError` in `codex_bridge.py` so Codex's `etl.py:_download_error_code()` immediately persists `download_error_download_size_limit` without provider messages/URLs.
   - **Suggestion for Codex (`backend/app/core/etl.py:85-96`)**: Consider adding `"DriveSizeLimitError"` -> `"download_size_limit"`, `"DriveResourceNotFoundError"` -> `"not_found"`, and `{"DriveUnexportableError", "DriveExportSizeLimitError"}` -> `"unsupported_export"` in `_download_error_code()`.
3. **`SyncScope.retry_failed` confirmation (`CODEX_HANDOFF.md:307`)**:
   - **Confirmed compatible**: `CodexConnectorBridge.list_changes()` constructs provider scopes exclusively from allowlisted fields (`subject_id`, `folder_id`, `academic_year`, `window_start`, `window_end`) and never rejects or forwards backend-only `retry_failed` / `discovery_only` flags to connectors.
4. **Moodle discovery completeness & `/my/courses.php` timeout recovery (`CODEX_HANDOFF.md:309`)**:
   - **Root causes fixed**: (a) `_MoodleCourseListHTMLParser` missed `/user/view.php?id=<uid>&course=<cid>` links on `/user/profile.php`; (b) `_discover_courses_playwright()` stopped on 1 link before `block_myoverview` loaded and skipped `/user/profile.php`; (c) `_probe_on_page()` raised raw Playwright `TimeoutError` without retry; (d) `_fetch_html_page()` launched Chromium for every single page/section/folder.
   - **Implemented**: `_goto_with_timeout_recovery()` in `auth/ensai.py` (retries once with `wait_until="commit"`, then raises `TransientSourceError` -> `RetryableSourceError`), fast cookie-backed `_cookie_session_fetch_html()` + `_discover_courses_cookie_http()`, multi-classification AJAX + mandatory `/user/profile.php` merge when `<= 1` course or timeout occurs, explicit exclusion counters in `last_stats` (`courses_discovered_raw`, `courses_excluded_archived`, `courses_excluded_by_year`, `courses_unknown_year`, `discovery_sources_used`, `discovery_incomplete`, `my_courses_timeout`), and `discovery_coverage_unknown` warning propagation in `codex_bridge.py`.
5. **Pamplemousse `source_confirmed_academic_year` (`CODEX_HANDOFF.md:251`) & Parser Expansion**:
   - `PamplemousseConnector.last_stats` now distinguishes `source_confirmed_academic_year` (strictly portal HTML) vs `requested_academic_year` (`scope["academic_year"]`), sets `academic_year_confirmed: bool`, exposes per-category missing/ambiguous & room/teacher/deadline counts, and extracts top-level French FullCalendar keys (`salle`, `enseignant`, `intervenant`, `groupe`).
   - Added pure-Python bounded `LegacyOfficeDocumentParser` (`.doc`/`.xls`/`.ppt`) + ENSAI stat extensions (`.rnw`, `.do`, `.stan`, `.svg`, `.sty`, `.cls`, `.rst`, `.org`).

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

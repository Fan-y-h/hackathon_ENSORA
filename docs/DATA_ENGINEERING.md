# Data engineering and source integration

Requirements R02–R08, R19, R31–R32. Sources are read-only. API access at ENSAI remains unverified. Use authorized exports or synthetic demo inputs if permissions are unavailable; never bypass SSO, scrape private data without permission or ask users to store institutional passwords in source code.

## Connector contract [RECOMMENDATION]
Each adapter exposes `authorize(config)`, `list_changes(scope, cursor) -> ChangePage(items, next_cursor, complete)`, `fetch_blob(item)`, `normalize(item) -> SourceRecord`, `disconnect()`. SourceRecord includes source kind, external ID, remote version, modification time, title, MIME, folder path, course/section metadata, source URL, event/assignment payload and access status. Capabilities report document/event support and incremental/full-scan support. Use bounded pagination, timeouts, retry with exponential backoff and jitter for 429/5xx, no retry for invalid permissions until reauthorized. Store cursors only after durable processing; repeat pages safely.

| Source | Authorized primary approach | Fallback / mapping |
|---|---|---|
| Moodle ENSAI | Confirm enabled REST web services: site info, enrolled courses, course contents, calendar, assignments, optional submission status | Authorized ZIP/PDF/CSV/manual import; map course ID and sections, deadlines and announcements; retain original URL |
| Ensora Google Drive | OAuth least-privilege read, user-selected folder; Drive v3 list/get/export and change tokens if accessible | Authorized local folder/ZIP, preserve 1A/2A/3A/Global tree, track folder IDs, historical year and types |
| Pamplemousse | Confirm official API with ENSAI/LEOS; event identifiers, titles, category, room, instructor, group, start/end | ICS if available, structured authorized export, CSV/JSON manual fallback; browser automation only with explicit permission |
| Google Calendar | OAuth read-only selected calendars, sync tokens, recurrence instances and cancellations | ICS import; respect time zone, all-day events, personal busy intervals; minimize sensitive event descriptions |
| Local files | Explicit file/folder import, recursive scan within selected root | Manual upload; no arbitrary filesystem traversal; validate symlinks and archives |

S2 examples: PPDP alias across sources; red Pamplemousse event is a hint, not proof of written exam; `Sout.ID` indicates possible presentation, two slots may belong to different groups. Use category, keywords (Partiel, Examen, Sout., Contrôle, Cours, TD, TP), subject code and group; uncertain assignments require confirmation. No assumptions about student's actual slot.

## Pipeline and idempotence
1. Discover changes into staging. Compare source identity + remote revision; if revision unknown compute hash. Normalize timestamps and type. Preserve source metadata even when bytes deduplicate.
2. Fetch authorized changed bytes to temporary file, check MIME/size/hash, commit blob and version; unchanged hash skips parsing/indexing.
3. Resolve subject using explicit source ID/code → confirmed alias → normalized token match → fuzzy similarity → optional semantic match. Restrict candidates by cohort/track/year; proposed thresholds >=0.90 auto only with no conflicts, 0.65–0.90 review, below unassigned. Thresholds are hypotheses, tune on fixtures. Never overwrite confirmed mappings.
4. Classify by folder, filename, metadata and text: course, TD, TP, notebook, assignment, exam, solution, sheet, note, other. Detect chapter from section/title; historical year separate from student's year. Match exam and solution by canonical subject/year/session/name and content similarity; require confirmation on ties.
5. Parse blocks; retain page/slide/cell/line locator, heading hierarchy, bounding box when available, language, source hash and extraction warnings. Build concepts and provenance; publish new index only after successful indexing. Mark old generated artifacts stale, not deleted.
6. Emit source/assessment change events; update sync counters and checkpoint. Manual/startup/selective/periodic sync share same pipeline; single writer per connection. Show partial errors and retry only failed items.

## Parser matrix
PDF: PyMuPDF or compatible parser, page text, figures, tables; OCR only scanned pages with confidence flags. DOCX: paragraphs/tables/images and heading anchors; PPTX: slide text, notes/tables/images. IPYNB: markdown and code cell index, language and outputs as inert text; never execute. Code: text with language and line spans. HTML/Markdown/TXT: sanitize and retain hierarchy. Images: metadata and optional OCR; formula recognition to LaTeX only when reliable, preserve original crop. Preserve tables as structured rows and code indentation; no invented equations. Unsupported/corrupt files remain previewable when safe and report failure. Test accented French, mixed English, formulas, table and scanned page.

## Change policy
| Change | Downstream action |
|---|---|
| New subject/chapter/course | Create catalog space, match Ensora, extract concepts, refresh plan needs |
| New TD/TP | Link concepts, course and correction candidates |
| New assignment/project | Create source-linked task; propose decomposition from instructions, user confirms |
| New exam/solution | Link historical pair, extract assessed concepts |
| New/moved/cancelled assessment | Confirm scope/slot if unclear, update agenda, mark plan dirty |
| Timetable/personal event/deadline changed | Recompute availability and impacted remaining sessions |
| Document changed | Version, reparse/reindex only affected content, mark artifacts stale |
| Source removed/revoked | Tombstone, block inaccessible content and evaluate local rights; do not infer deletion from incomplete listing |
| No change | No new downloads, embeddings, tasks or duplicated events |

Personal notes, attempts, history, user edits and existing artifacts never silently overwritten. Source mirrors are separate from user overrides. For demo, normalized fixture and local PDF import must work without external accounts. See [RAG_ARCHITECTURE](RAG_ARCHITECTURE.md), [DATA_MODEL](DATA_MODEL.md).

## Academic source extension (R35)
Pamplemousse or another authorized academic platform may expose program/UE/subject IDs, assessment dates, weights, marks and rules; access not confirmed. Extend connector port with capability discovery, normalized academic records and provenance; manual JSON/YAML/CSV import or UI remains functional without it. Map to existing catalog/event IDs, deduplicate by source+external ID+owner, retain versions and manual overrides, stage conflicts for confirmation. Never infer official rules from calendar color or missing marks. No upstream changes. Grade/copy access requires explicit scope; redact logs and never include real records in fixtures. See [GRADE_TRACKER](GRADE_TRACKER.md).

## P1 local runtime

Run `python3 scripts/start.py` from the project root. Python 3.11 or later is required. The launcher reuses `.venv`, installs missing dependencies, prepares private directories, and opens the CLI onboarding flow on a terminal until it is completed. `python3 scripts/onboarding.py` reopens it. `python3 scripts/bootstrap.py --browser` installs Chromium only when Playwright cannot find its existing executable.

Runtime settings come from the environment and `~/.config/ensora-plus/sources.env`, in that precedence order. Existing files are preserved. New config uses mode 0600 and private directories use mode 0700. Docs, blobs and the SQLite database live under `~/.local/share/ensora-plus/` by default. Institutional sign-in uses the official browser flow and the shared ENSAI profile, with an OS keyring for optional authorized credential entry. Google uses scoped OAuth via the Gemini adapters. Keep OAuth client and token files in the private secrets directory. The Ensora Drive connector is scoped to the Ensora shared root. Personal Google Drive is excluded.

From an operator terminal, `python3 scripts/check_connections.py` reads saved state. Add `--validate` to contact explicitly approved sources. `python3 scripts/sync.py --source moodle --approve` requests an incremental run. Add `--full` or `--subject-id ID` for selective operation. For local files or directories, select the local source in onboarding and run `python3 scripts/sync.py --source local --folder-id /path/to/selected/folder --approve`. For structured academic data, select academic import in onboarding and run `python3 scripts/sync.py --academic-json /path/to/records.json --approve`. The JSON file is an array of discriminated `SourceRecord` objects. Order parent records before children. All CLI data commands require the server to be stopped because one process owns the local database and scheduler at a time.

Automatic runs require per-source approval and successful validation. Startup and periodic runs share the manual pipeline. `SYNC_ON_STARTUP` and `SYNC_INTERVAL_MINUTES` configure their timing. A failed record leaves its checkpoint retryable. Authentication loss preserves cached data. Explicit withdrawal removes that source from local API reads.

## Google developer setup

Use `python3 scripts/p1_sources_cli.py gcp-check` to inspect Google Cloud CLI, project access, API and desktop-client readiness. Authorized project developers can run `python3 scripts/p1_sources_cli.py gcp-check --enable-apis` to enable the supported services after confirmation. The existing project is `ensora-516015`. Use its [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent?project=ensora-516015) and [credentials page](https://console.cloud.google.com/apis/credentials?project=ensora-516015) to configure branding, add test users where needed and download the desktop OAuth client. Place the JSON in the configured credential location. Connecting an end-user Google account uses the supplied client and the official browser consent screen.

## Current implementation scope

FastAPI, SQLite/Alembic, CLI onboarding, source consent, connector orchestration, content hashing, versioned documents, parsed chunks and imported academic facts are part of P1. Gemini owns source authentication, connectors, parsers, classification, entity resolution and Google provisioning. Codex owns the shared contracts, persistence, jobs, CLI, API and integration tests. See the [technical handoff](coordination/CODEX_HANDOFF.md). Live Moodle, Pamplemousse, Drive and Calendar access requires human authorization and remains unverified. P2 consumes chunks and immutable document versions. P3 consumes the normalized calendar and academic facts and owns calculated results and planning.

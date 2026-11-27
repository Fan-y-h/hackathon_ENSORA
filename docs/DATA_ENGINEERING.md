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
| Personal Drive | Opt-in selected folders, same adapter separate connection and rights | Local document import; do not enumerate whole account by default |
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

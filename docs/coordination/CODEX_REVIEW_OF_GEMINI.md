# Codex review of Gemini source ingestion

Reviewed the canonical shared checkout on feat/p1-data-engineering at base 89ad3d8. Scope included source authentication, provisioning, connectors, parsers, classification, entity resolution, the standalone CLI, unit tests and the bridge into the normal runtime. Findings below come from source inspection. Live CAS pages, institutional authorization, Google project permissions and real provider behavior remain unverified. No private files were opened and no live source operation was run.

The shared Chromium lease and official, read-only workflows are present in the reviewed source. The review found concrete gaps at the bridge boundaries, where source-level behavior does not always survive integration.

## GEMINI-REV-001 · HIGH

- **Location** — `backend/app/modules/ingestion/connectors/ensora_drive.py:207, 393, 487 — _validate_target_folder_boundary, list_changes, fetch_blob`
- **Evidence** — Previously seen folder IDs are trusted without checking ancestry again. fetch_blob requests the trashed flag but does not enforce it.
- **Consequence** — A folder moved outside the Ensora root after discovery can stay trusted for the process lifetime. Trashed files can remain eligible for download.
- **Correction** — Revalidate live ancestry on scoped listings and each download. Reject trashed files and verify moved-folder cases with the fake Drive API.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-002 · HIGH

- **Location** — `backend/app/modules/ingestion/connectors/google_calendar.py:345–392, 424–435 — GoogleCalendarConnector.list_changes`
- **Evidence** — The page cursor contains only pageToken and discards the original syncToken. Initial requests recalculate their time bounds and incremental requests change the singleEvents and ordering parameters.
- **Consequence** — An incremental multi-page run loses its query context. Page requests can be rejected or return a different event set.
- **Correction** — Carry the original sync token and a stable query fingerprint with paging. Use provider-compatible invariant query options and test initial and incremental pagination separately.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-003 · HIGH

- **Location** — `backend/app/modules/ingestion/codex_bridge.py:197–232, 405–416 — _to_codex_record, CodexConnectorBridge.list_changes`
- **Evidence** — The event conversion returns None when start_at is absent, including cancelled entries represented solely by ID and status. It also fabricates a one-hour duration when a timed end is absent.
- **Consequence** — Cancellations can silently leave stale busy events and fabricated durations can affect planning.
- **Correction** — Map ID-only cancellations to an explicit tombstone. Preserve incomplete event evidence for review and never invent an authoritative duration.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-004 · HIGH

- **Location** — `backend/app/modules/ingestion/codex_bridge.py:197–232, 380–391 — _to_codex_record, CodexConnectorBridge.list_changes`
- **Evidence** — The bridge omits event type, busy/transparency, teacher, recurrence, timezone and confirmation flags. It never asks Calendar for availability-only output.
- **Consequence** — P3 cannot distinguish invitations, free events and uncertain ENSAI slots. Personal titles flow into the event cache even when availability would suffice.
- **Correction** — Carry typed event fields through the shared contract. Default to privacy-preserving availability or gate personal metadata with separate consent.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-005 · HIGH

- **Location** — `backend/app/modules/ingestion/codex_bridge.py:181–195, 234–278 — _to_codex_record`
- **Evidence** — Assignment parent keys use course IDs but Moodle does not emit parent Subject records. Weighted assessment rows become AssessmentWeight instead of producing assessment and weight facts separately. Coefficients on subject or grade rows are discarded by earlier branches. Missing grades can fall through to Subject.
- **Consequence** — Assignments can fail referential validation. Academic facts and coefficient hierarchy are incomplete in P3 even when acquisition succeeds.
- **Correction** — Convert a source record to an ordered sequence of typed facts with stable distinct IDs. Add parents before children, preserve independent coefficients and keep missing grades explicit or absent.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-006 · HIGH

- **Location** — `backend/app/modules/ingestion/codex_bridge.py:405–431; connectors/ensora_drive.py:399–405, 457–463 — list_changes, fetch_blob`
- **Evidence** — The bridge drops connector warnings, errors and separate sync_token values. Conversion exceptions abort the whole page. Drive records folder listing failures in the omitted errors field.
- **Consequence** — The runtime can mark incomplete collection successful, loses durable incremental marks and skips healthy records in a malformed page.
- **Correction** — Separate next page cursor from durable resume cursor, propagate safe completeness and record errors, and isolate conversion failures without advancing the durable checkpoint.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-007 · HIGH

- **Location** — `backend/app/modules/ingestion/auth/google_oauth.py:105–131, 208–218, 394–399 — _load_stored_credentials, _save_credentials, authorization flow, disconnect`
- **Evidence** — Stored credentials are reconstructed with requested scopes, and the fallback scope check consults that requested field. Tokens are written to a fixed temporary path before chmod, and revocation sends the token in a URL parameter.
- **Consequence** — Stored grants can be overstated. Token handling is vulnerable to permissive umask, concurrent replacement and accidental URL disclosure.
- **Correction** — Persist verified grants independently, use exclusive restriction at file creation and an atomic rename, and send the revocation token in a form body.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-008 · HIGH

- **Location** — `backend/app/modules/ingestion/codex_bridge.py:355–370; auth/google_oauth.py:377–379 — CodexGoogleAuthBridge.validate, validate_connection`
- **Evidence** — The bridge checks local credential usability only. The Calendar and Drive connectors implement separate remote probes, but the runtime never calls them for validation.
- **Consequence** — A valid local token can produce CONNECTED for a disabled API, inaccessible Ensora root or inaccessible calendar. Automatic-sync consent can then be offered prematurely.
- **Correction** — Use a source-specific read-only remote probe after consent and token validation. Map permission denials and transient failures distinctly.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-009 · HIGH

- **Location** — `backend/app/modules/ingestion/parsers/office_parser.py:70–105, 168, 230–265 — PPTX and DOCX parse`
- **Evidence** — The Office parsers read expanded ZIP members directly. The upstream document byte limit applies to compressed bytes, with no aggregate expansion budget in these functions.
- **Consequence** — An unusually large compressed document can exhaust worker memory before error recovery.
- **Correction** — Bound ZIP member count, expanded aggregate and member size before allocation. Reject unsafe inputs and cover limits with small synthetic archives.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-010 · MEDIUM

- **Location** — `backend/app/modules/ingestion/connectors/moodle.py:417–431, 610–695, 701–745 — _validate_ws_token, list_changes, fetch_blob`
- **Evidence** — REST support validates a token, while catalog acquisition still uses Playwright HTML. Download content is materialized before its limit is checked, and the initial URL origin is checked without a corresponding bounded redirect check.
- **Consequence** — REST-only sessions do not acquire data and large or redirected payloads can bypass the intended acquisition boundaries.
- **Correction** — Implement authorized REST reads when configured. Validate the final origin and enforce size while streaming where the transport supports it.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-011 · MEDIUM

- **Location** — `backend/app/modules/ingestion/connectors/local_import.py:258–289, 342–387; codex_bridge.py:197–232 — normalize, list_changes, fetch_blob, event conversion`
- **Evidence** — The local ID uses only a relative path, which collides across selected roots. The blob read does not recheck the selected root after discovery. Local ICS is tagged as an event without normalized event fields.
- **Consequence** — Two unrelated folders can version the same document. A changed filesystem entry can invalidate discovery-time containment. The unified path can drop ICS imports.
- **Correction** — Namespace IDs by stable root identity, use contained bounded opens at fetch time, and parse ICS into event records before entering the bridge.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-012 · MEDIUM

- **Location** — `backend/app/modules/ingestion/codex_bridge.py:405–431, 446–487 — list_changes, fetch_blob, parse, classify`
- **Evidence** — Record cache grows with every page and source run. FetchResult MIME and filename are discarded. Parser warnings, asset metadata and block kinds disappear, and classification ignores parsed content.
- **Consequence** — Long sessions retain source metadata in memory. Workspace exports can have MIME/title mismatches and P2 loses structured evidence.
- **Correction** — Keep only page-scoped fetch state. Align exported metadata before parsing and carry bounded structured blocks, warnings and classification evidence through shared DTOs.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-013 · MEDIUM

- **Location** — `backend/app/modules/ingestion/codex_bridge.py:500–578; resolution/entity_resolver.py:175–210 — match, build_components, subject registration`
- **Evidence** — The factory creates the resolver without persisted catalog subjects or confirmed aliases. Source kind is forced to local when matching and exam/correction pairing stays in the separate standalone pipeline.
- **Consequence** — Cross-source matching and durable confirmed overrides are absent from the normal runtime path.
- **Correction** — Provide a catalog-backed resolver interface and preserve source identity, confirmed mappings and linked-match outputs through persistence.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-014 · MEDIUM

- **Location** — `scripts/p1_sources_cli.py:191–210, 254–365; auth/google_cloud.py:189–295 — main, inspect`
- **Evidence** — The standalone CLI uses a separate onboarding/connector path and prints connector records, parsed documents, academic grades and active Google account status. Its imports do not go through the unified ETL persistence.
- **Consequence** — Private source data can appear in captured output. Setup state and consent can differ between entry points.
- **Correction** — Keep the CLI as a redacted developer diagnostic, route normal sync/import through the unified runtime, and return sanitized counters unless a local export is explicitly requested.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-015 · MEDIUM

- **Location** — `backend/app/modules/ingestion/codex_bridge.py:98–135, 302–319, 524–578; config.py:204–277 — runtime_config_from_codex_settings, authorize, build_components, load`
- **Evidence** — Two settings and credential composition paths interpret overlapping keys. The bridge can replace an explicit missing filename with a fallback and does not carry source-specific modes. Keyring write errors are swallowed while automated submission remains enabled for that attempt.
- **Consequence** — Runtime and diagnostics can use different credentials or modes, and a failed secure-store write can trigger a confusing second prompt or old credential reuse.
- **Correction** — Share one settings adapter, preserve explicit paths, disable automated submission on write failure and expose a safe user-action code.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## GEMINI-REV-016 · LOW

- **Location** — `backend/tests/unit/ingestion/test_connectors.py:243, 359, 449; backend/tests/unit/ingestion/test_auth_and_setup.py — synthetic coverage`
- **Evidence** — The existing happy-path tests cover ancestry, one 410 recovery, parsers and basic runtime wiring. They do not exercise moved folders, multi-page incremental Calendar requests, cancelled ID-only events, cross-root identities, or partial page error handoff.
- **Consequence** — Passing current tests cannot establish the required edge-case guarantees.
- **Correction** — Add synthetic contract fixtures for these cases and verify complete bridge-to-SQLite replay and cancellation. Keep all live tests separately authorized.
- **Resolution** — Open in the reviewed code. Gemini owns the source changes.

## Synthetic verification

The combined suite passed 24 tests in 2.34 seconds on the canonical checkout at the reviewed baseline. This included 19 Gemini ingestion tests and five Codex integration tests. The baseline tests do not cover the open cases above. No claim of live integration is made.

## GEMINI-REV-017 · HIGH

- **Location** — `scripts/p1_sources_cli.py:186–196,254–264` and `backend/app/modules/ingestion/auth/onboarding.py:74–124,175–213`, standalone status, validate and setup.
- **Evidence** — These paths probe enabled ENSAI sources and inspect or refresh OAuth state without consulting durable per-source read consent. The command called status can contact external services.
- **Consequence** — A developer diagnostic can perform live operations using previously available credentials without the unified runtime's consent decision.
- **Correction** — Make status local-only by default. Require explicit source selection and the shared consent decision for live validation. Route ordinary onboarding through the unified runtime.
- **Resolution** — Open. Confirmed by source inspection; external calls were not executed.

## GEMINI-REV-018 · MEDIUM

- **Location** — `backend/app/modules/ingestion/auth/ensai.py:322–326,886–909` and `backend/app/modules/ingestion/codex_bridge.py:348–352`, profile selection and disconnect.
- **Evidence** — Both institutional sources can select the same shared profile. Source disconnect clears that entire profile.
- **Consequence** — Disconnecting one source can remove the other source's session despite retained consent. The other persisted connection may still show CONNECTED until validation.
- **Correction** — Withdraw source-specific consent without deleting shared browser state while other active sources depend on it. Expose a separately confirmed operation for clearing the shared institutional session.
- **Resolution** — Open. Shared profile behavior is confirmed in code; live institutional logout was not executed.

## GEMINI-REV-019 · MEDIUM

- **Location** — `scripts/p1_sources_cli.py:200–210` and `backend/app/modules/ingestion/auth/google_cloud.py:313–335`, API provisioning.
- **Evidence** — The CLI confirms the reported missing subset. The helper builds its command from all enabled source settings. The CLI then returns zero even if provisioning fails.
- **Consequence** — The command can request a broader operation than shown in the approval prompt and scripts cannot reliably detect failure.
- **Correction** — Pass the exact approved, allowlisted missing subset into the helper and return a nonzero diagnostic exit on failure.
- **Resolution** — Open. Confirmed by the argument paths in source; no project change was attempted.

## GEMINI-REV-020 · MEDIUM

- **Location** — `backend/app/modules/ingestion/codex_bridge.py:440–521` and `backend/app/modules/ingestion/pipeline.py:92–138`, parser and catalog handoff.
- **Evidence** — The normal runtime produces document chunks, category and subject/chapter guesses. No published adapter emits evidence-linked Concept records for extracted definitions, formulas, theorems or prerequisites.
- **Consequence** — R07 concept acquisition remains incomplete in P1. P2 can consume text with locators, while P3 has no extracted concept evidence from these adapters.
- **Correction** — Agree a provenance-linked concept extraction contract and ownership with P2/P3. Publish reviewable candidate records from parsed evidence and retain the original locators.
- **Resolution** — Open. Confirmed coverage gap in the reviewed ingestion surface.

## Review closure

The closure pass checked every Gemini Python source path under the ingestion package, the standalone CLI, its synthetic tests and the shared runtime boundary. Requirements were traced through the project baseline, source and ETL specification, architecture, data model, API contracts, Grade Tracker and testing strategy. Out-of-scope implementation such as RAG generation and grade optimization was treated as a downstream integration boundary.

Latest canonical synthetic suite: **24 passed in 1.60 seconds**. Additional isolated fake-object probes reproduced the dropped ID-only Calendar cancellation (GEMINI-REV-003), missing partial-page diagnostics and resume cursor (GEMINI-REV-006), and trust of a cached Drive folder without a fresh ancestry check (GEMINI-REV-001). The other findings identify source-confirmed behavior or static coverage gaps. Live permissions, provider responses and actual institutional page structure remain unverified. Review complete for this snapshot. Findings remain open for owner remediation.

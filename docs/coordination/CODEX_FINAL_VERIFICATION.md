# P1 targeted final verification

## Drive return-to-root recheck — FINAL_SIGN_OFF: GO

**FINAL-BLOCK-003 is VERIFIED.** This targeted recheck supersedes the earlier NO_GO below. OAuth and Moodle blocker dispositions remain verified from the prior recheck. The current combined suite passed **66 tests in 5.55 seconds**, with no failures or skips.

The default bridge now distinguishes an empty successfully loaded active-membership baseline from an unavailable one and passes the empty baseline to the connector. A file absent from that baseline can be a membership addition even when its modification timestamp precedes the watermark. Old-timestamp membership additions and tombstones are emitted only after complete approved-root discovery. The connector rechecks ancestry and trash status before fetching bytes.

The new regression passed and covers re-entry from an empty active SQLite set, a first-seen old file, and incomplete discovery that leaves tombstones intact. An independent test used a moved parent folder with a child whose timestamp stayed unchanged. With the folder outside the root, the document and version content endpoints returned 404, and an attempted stale download was denied before the fake media call. The next incremental run after return restored both endpoints using the same document ID and immutable version ID, preserved the watermark, and created no duplicate version. A subsequent no-change run performed no additional download. Read-consent withdrawal hid the content again.

The operator steps below were checked against the CLI help and Settings/onboarding entry points. Run them in order after explicit authorization. Stop the server before write-side CLI commands. The loader reads the existing private sources.env directly.

```bash
cd /home/ensai/hackathon_ENSORA
python3 scripts/bootstrap.py --test --browser
python3 scripts/check_connections.py
.venv/bin/python scripts/p1_sources_cli.py gcp-check
python3 scripts/onboarding.py
```

Use the official ENSAI CAS browser window for Moodle/Pamplemousse and the official Google OAuth screen for the Ensora shared Drive and personal Calendar. Select only approved sources and decline automatic and initial bulk sync during this first validation. If the readiness report identifies missing APIs, an authorized developer can run `.venv/bin/python scripts/p1_sources_cli.py gcp-check --enable-apis`. Reuse project `ensora-516015` and the configured desktop OAuth client JSON. If that client is missing, create or obtain it in the existing project's Google Cloud Console and place it at the configured private path.

```bash
.venv/bin/python scripts/p1_sources_cli.py validate --source moodle
.venv/bin/python scripts/p1_sources_cli.py validate --source pamplemousse
.venv/bin/python scripts/p1_sources_cli.py validate --source ensora_drive
.venv/bin/python scripts/p1_sources_cli.py validate --source google_calendar
.venv/bin/python scripts/p1_sources_cli.py sync-moodle --single-course
.venv/bin/python scripts/p1_sources_cli.py sync-pamplemousse --max-items 20
.venv/bin/python scripts/p1_sources_cli.py discover-drive --max-files 25
.venv/bin/python scripts/p1_sources_cli.py sync-calendar --days 14 --availability-only
```

After separately approving persistence, substitute an authorized Moodle course ID and run the following. Run each other source command only if separately approved. The Drive command persists the configured shared root.

```bash
python3 scripts/sync.py --source moodle --subject-id COURSE_ID --approve
python3 scripts/sync.py --source pamplemousse --approve
python3 scripts/sync.py --source ensora_drive --approve
python3 scripts/sync.py --source google_calendar --approve
SYNC_ON_STARTUP=false SYNC_INTERVAL_MINUTES=0 python3 scripts/start.py --skip-onboarding
```

No actual credential access, live source activity, application edits or Git writes occurred in the recheck. GO authorizes readiness for human-operated live verification after approval. Provider access remains unverified.

## Earlier blocker verification record

## Final blocker recheck — FINAL_SIGN_OFF: NO_GO

The full current suite passed **65 tests in 7.08 seconds**, with zero failures or skips. This recheck covers only the three final blockers. The earlier verification below is retained as the original record and its OAuth/Moodle verdicts are superseded by this recheck.

| Finding | Current status | Independent evidence |
| --- | --- | --- |
| FINAL-BLOCK-001 | VERIFIED | Reviewed fresh-grant precedence, state persistence and both refresh paths in `backend/app/modules/ingestion/auth/google_oauth.py:176–245,355–378`. The targeted regression passed. An independently supplied fake refresh narrowed Drive to Calendar-only; Drive was denied and the saved grant marker became Calendar-only. |
| FINAL-BLOCK-002 | VERIFIED | Reviewed shared pre-follow handling and both callers in `backend/app/modules/ingestion/connectors/moodle.py:417–479,480–555,608–638`. The targeted regression passed. An independent fake 302 for both credential-bearing validation and listing produced two same-origin calls with automatic redirects disabled and no call to the external destination. |
| FINAL-BLOCK-003 | PARTIALLY_FIXED | Multipage watermark continuity and move-out/trashed-cache retirement are verified. The supplied regression checks persisted tombstones and API visibility. An independent one-item-page probe reproduced the original incremental listing scenario and now returned each changed ID exactly once. The same fake listing exposed the remaining return-to-root defect below. |

### Remaining blocker — Drive does not restore unchanged files that re-enter the approved root

An independent fake-provider probe first listed an old file inside the root, then omitted it with its ID marked active. Reconciliation emitted the expected tombstone. On the next incremental run, the file returned to the approved root with the same earlier `modifiedTime`, as can happen to a child when its parent folder is moved. The connector produced zero records and retained the watermark. A full listing detected the file. The incremental path therefore leaves a returned cached file tombstoned and also misses first-seen approved files whose content timestamps precede the watermark.

The filter in `backend/app/modules/ingestion/connectors/ensora_drive.py:555–561` applies the timestamp cutoff before checking membership in the active SQLite set. The set supplied by the default bridge excludes tombstoned documents by design (`backend/app/modules/ingestion/codex_bridge.py:724–734,781–790`). Treat an ID absent from a successfully loaded active membership set as a membership addition even when its content timestamp is old. Keep standalone incremental calls without a membership baseline distinct, and add a persisted regression for move-out, return-without-content-change, incremental rerun, and restored API visibility. A full scan works around the symptom for a single run.

No application source or Git state changed. Probes used fake OAuth credentials and fake transports only. The operator live procedure below remains gated by the Drive correction and explicit authorization.

## Prior targeted verification record

**FINAL_VERIFICATION_COMPLETE** · **Live validation readiness: NO_GO**

Verified the canonical checkout at `/home/ensai/hackathon_ENSORA` on `feat/p1-data-engineering`. Of the 17 items carried forward, **14 are VERIFIED and 3 are PARTIALLY_FIXED**. No items were BLOCKED or REOPENED. The three partial items contain confirmed live sign-off blockers. The application is ready for synthetic/offline use.

**Current suite** — 62 passed in 6.22 seconds, with no failures or skips.

```bash
cd /home/ensai/hackathon_ENSORA
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q backend/tests
```

Verification used disposable configuration, temporary SQLite databases, fake provider responses and a loopback-only startup smoke test. No private credentials were accessed. No live source requests, cloud mutations, Git writes or application edits were performed.

## Findings carried forward

Source paths below are relative to `backend/app/modules/ingestion/` unless otherwise stated. Test names refer to the actual combined suite.

| Finding | Status | Verification evidence |
| --- | --- | --- |
| GEMINI-REV-002 | VERIFIED | `connectors/google_calendar.py:439–459` keeps `singleEvents=True` on initial and incremental pages while excluding incompatible window and ordering parameters from token requests. Both pagination tests verify token and query continuity. |
| GEMINI-REV-005 | VERIFIED | `codex_bridge.py:366–503` preserves distinct assessment, coefficient, weight and grade facts and copies source provenance on derived facts. The nested academic test verifies the persisted group/child chain and derived provenance. The P3 API test checks explicit nested weights and a published zero. |
| GEMINI-REV-007 | PARTIALLY_FIXED | `auth/google_oauth.py:106–126,174–215` rejects legacy requested-only scopes and retains private atomic storage. After refresh, the old `_verified_granted_scopes` marker takes precedence over a newer provider-reported grant. A synthetic fixture with conflicting grants confirmed that precedence. See FINAL-BLOCK-001. |
| GEMINI-REV-010 | PARTIALLY_FIXED | `connectors/moodle.py:891–1045` checks attachment redirect destinations before following, and REST-only composition avoids CAS. The existing test covers those paths. A separate mocked invocation confirmed that REST validation and listing omit redirect controls. See FINAL-BLOCK-002. |
| GEMINI-REV-011 | VERIFIED | `connectors/local_import.py:335–390` pins a directory descriptor, walks relative components without following symlinks, rejects nonregular files and bounds reads. Cross-root IDs, intermediate symlink swaps, FIFO rejection and ICS normalization are covered. |
| GEMINI-REV-012 | VERIFIED | `codex_bridge.py:739–770,853–872` bounds connector pages and preserves original fetch metadata. `backend/app/core/etl.py` and migration 0003 persist fetched MIME/filename per version. The bridge and runtime parser fixtures check both the stored values and parsed content. |
| GEMINI-REV-013 | VERIFIED | `codex_bridge.py:1120–1244` prefers explicit source provenance and loads SQLite subjects, overrides and exam-pair candidates under consent. `backend/app/core/runtime.py:144–165` rebuilds from current consent. Runtime tests verify override preservation across re-sync and disappearance of candidates on withdrawal. |
| GEMINI-REV-014 | VERIFIED | `scripts/p1_sources_cli.py:49–81,399–458` emits aggregate counters by default, routes imports through shared persistence and uses private atomic export. Redaction tests and the symlink/0600 export test passed. |
| GEMINI-REV-016 | VERIFIED | The suite includes thirteen Gemini round-two regressions plus Codex integration cases for pagination, scope denial, replacement recovery, metadata, P2/P3 and confirmed overrides. The three blocking observations below came from independent probes outside the existing fixtures. |
| GEMINI-REV-017 | VERIFIED | `scripts/p1_sources_cli.py:201–223` makes noninteractive onboarding local-only and delegates interactive onboarding to the shared consent flow. `auth/onboarding.py` gates its separate probes behind explicit network and source consent inputs. Both routing tests and the isolated offline CLI smoke passed. |
| GEMINI-REV-020 | VERIFIED | `codex_bridge.py:951–1048` emits exact chunk ordinals/locators for concepts and prerequisite hints. The version endpoint resolves them to stored chunk IDs. Adapter extraction and P2 API tests passed. |
| ROUND2-NEW-001 | VERIFIED | Calendar uses compact, process-local paging handles and the shared durable cursor supports up to 16 KiB. The long-token, multipage Runtime/SQLite test and the independent Codex long-cursor recovery case passed. |
| ROUND2-NEW-002 | VERIFIED | The Calendar connector emits bounded replacement scope after a 410 and `backend/app/core/sync.py:159–216` commits retirement and the checkpoint after an error-free authorized terminal page. Fake 410, partial replacement, history retention, other-calendar isolation, outside-window isolation and omission from availability all passed. |
| ROUND2-NEW-003 | PARTIALLY_FIXED | `connectors/ensora_drive.py:385–398,466–519` consumes a durable watermark on the first page and supports tombstones when callers provide known IDs. The paging cursor drops the watermark on the next page. The default engine supplies no known IDs for reconciliation. See FINAL-BLOCK-003. |
| ROUND2-NEW-004 | VERIFIED | `codex_bridge.py:763–813` reports constant conversion codes rather than IDs or exception text. The log-capture regression verifies that invalid synthetic academic and document fields are absent from logs and reports. |
| ROUND2-NEW-005 | VERIFIED | `backend/app/core/settings.py:95–106`, the sync engine and `connectors/local_import.py:273–294,335–390` reject private-root overlap and enforce contained reads. API/CLI tests deny direct private paths, symlinks, enclosing roots and private data directories. |
| ROUND2-NEW-006 | VERIFIED | `scripts/p1_sources_cli.py:49–75` refuses a symbolic-link target/immediate parent and publishes a 0600 temporary file by atomic replacement. The regression checks both refusal and preservation of the existing linked file. |

## Blocking defects before live sign-off

### FINAL-BLOCK-001 · HIGH · OAuth grant narrowing is ignored after refresh

`auth/google_oauth.py:174–215` prioritizes the persisted verified marker even when the current credential object contains a newer provider-reported grant. The refresh path then uses that check to accept and persist credentials. A fake credential with an older Drive grant and a newer Calendar-only grant still passed the Drive scope check. Prefer an explicitly supplied fresh provider grant, update its verified marker, and reject or request consent when required scopes are missing. Add a narrowed-scope refresh regression. No Google account or token was used in the probe.

### FINAL-BLOCK-002 · HIGH · Moodle REST validation and listing do not enforce the redirect boundary

`connectors/moodle.py:417–440,536–569` sends credential-bearing validation/listing requests using default redirect handling. Attachment fetches use a separate guarded path. A fake HTTP transport confirmed that neither REST endpoint call disables redirects. Use one same-origin pre-follow policy across token validation, metadata listing and downloads. Add a synthetic redirect case for both metadata entry points. The probe dispatched no HTTP traffic.

### FINAL-BLOCK-003 · HIGH · Persistent Drive sync cannot reconcile moved or revoked cached files

`connectors/ensora_drive.py:385–398,466–519` separates offset and watermark tokens but does not carry the watermark through pagination. A stable fake three-file listing with a one-item page produced a duplicated changed item as the second page reverted to an unfiltered set. More importantly, `backend/app/core/sync.py:153–216` and the bridge never pass the active stored Drive external IDs into an authoritative scan. Cached files moved outside the approved tree or deleted can therefore stay API-visible. Preserve fixed listing context across continuation pages and reconcile approved-root membership only after a complete, error-free scan or an authorized provider change feed. Test persisted listing, move/delete, rerun and API visibility. Reconciliation must treat depth truncation and other incomplete discovery as incomplete because the current connector can warn and continue.

## Non-blocking limitations and live gates

- Browser-mode Moodle attachment retrieval checks declared and actual size, but its response buffering is weaker than the REST streaming cutoff. Validate small authorized samples first after the blockers clear.
- Prerequisite, concept and exam-correction records are review candidates. Confirmed curriculum and associations remain separate.
- The smoke test validates the local backend and contract surfaces. Google Calendar recurrence, real Moodle/Pamplemousse shapes and permissions, the Ensora shared root's actual access, OAuth grant/refresh behavior, and Cloud project/API readiness require human authorization. An authorized developer must supply or confirm the existing project's desktop OAuth client when required. Optional institutional credential storage needs an OS keyring.

## Runtime and integration verification

The isolated smoke ran noninteractive onboarding with a disposable config and home, then started the real unified launcher against a temporary database. It returned ready, exposed five connector registrations, and the read-only CLI reported six unconfigured sources while the backend held its writer lock. Integration tests covered configuration precedence and CORS, migrations to head, source consent, save/reload, partial checkpoint replay, 410 recovery, document versions and stored chunks, P2 exact-version evidence, and P3 availability and academic hierarchies.

## Exact operator commands

Offline checks and local startup can be run now. Runtime startup with scheduler settings forced off prevents automatic acquisition while the live gate is closed. The service uses a local bearer credential for protected routes. Keep that credential in the private runtime directory.

```bash
cd /home/ensai/hackathon_ENSORA
python3 scripts/bootstrap.py --test --browser
python3 scripts/check_connections.py
SYNC_ON_STARTUP=false SYNC_INTERVAL_MINUTES=0 python3 scripts/start.py --skip-onboarding
```

From a second terminal, local readiness is available without credentials.

```bash
curl --fail --silent http://127.0.0.1:8765/api/v1/health
```

After the blockers are corrected and the human explicitly authorizes live work, stop the backend before running CLI setup. Check the existing project `ensora-516015`. Run the second command only if an authorized project developer approves enabling missing APIs. Use the existing project's Console for consent-screen or desktop OAuth client setup when needed.

```bash
cd /home/ensai/hackathon_ENSORA
.venv/bin/python scripts/p1_sources_cli.py gcp-check
.venv/bin/python scripts/p1_sources_cli.py gcp-check --enable-apis
python3 scripts/onboarding.py
```

Select authorized sources separately in onboarding, complete the official login or OAuth screens, and decline automatic sync and the initial bulk sync for this first session. Then run only the validation commands for sources explicitly authorized.

```bash
.venv/bin/python scripts/p1_sources_cli.py validate --source moodle
.venv/bin/python scripts/p1_sources_cli.py validate --source pamplemousse
.venv/bin/python scripts/p1_sources_cli.py validate --source ensora_drive
.venv/bin/python scripts/p1_sources_cli.py validate --source google_calendar
```

With separate human approval for acquisition, use small redacted diagnostics first. Leave export flags off.

```bash
.venv/bin/python scripts/p1_sources_cli.py sync-moodle --single-course
.venv/bin/python scripts/p1_sources_cli.py sync-pamplemousse --max-items 20
.venv/bin/python scripts/p1_sources_cli.py discover-drive --max-files 25
.venv/bin/python scripts/p1_sources_cli.py sync-calendar --days 14 --availability-only
```

# Codex remediation round 2

**REMEDIATION_ROUND_2_COMPLETE**. Codex corrections are ready for targeted final verification. Gemini retains ownership of the provider corrections. No new formal review was started.

## Corrections and verification

| Finding | Codex correction | Evidence |
|---|---|---|
| CODEX-NEW-001 | Direct ASGI startup and the unified launcher use the same loaded frontend origin. Default app creation remains inert before startup. | Synthetic CORS preflight allowed the configured origin and rejected the previous default. |
| CODEX-NEW-002 | A successful incremental terminal page with no cursor keeps the stored checkpoint. Partial, cancelled and unauthorized runs do not retire records or advance a checkpoint. A successful full replacement may clear its obsolete checkpoint. | Long-cursor recovery fixture verified failure retention, successful replacement and subsequent incremental no-change retention. |
| CODEX-NEW-003 | Availability lists both manual and calendar records as {id, source, payload}, ordered by UTC start and ID. Private Calendar titles stay out of the projection. | Mixed-source pagination, timezone offsets, all-day conversion and consent withdrawal verified. |
| ROUND2-NEW-001 | Added a dedicated, unmodified opaque cursor type bounded at 16 KiB. | Synthetic cursor longer than 512 characters passed through the sync engine; Gemini connector paging fixtures passed. |
| ROUND2-NEW-002 | A complete authorized Calendar replacement snapshot retires only unseen events in its calendar and overlap window, appends history, and commits the new checkpoint in the same transaction. | Controlled partial and complete snapshots, then a fake provider 410 through the actual Gemini bridge, passed. |
| ROUND2-NEW-005 | Unified runtime rejects selected roots that overlap private configuration, sessions, credential paths or local data. The API returns 403, and command-line imports enforce the same policy. | Disposable private file, symlink, ancestor and data directory cases passed. |
| Shared P2/P3 interfaces | Added versioned fetched filename/MIME handling, exact chunk references for concepts and prerequisite hints, and consent-scoped exam correction candidates. | Default composition tests verified evidence IDs, academic parent IDs, published zero marks, candidate visibility and confirmed overrides across re-sync. |

Migration 0003 adds a nullable filename to immutable document versions and preserves the old-title fallback for versions imported earlier. Snapshot fields are optional and require no database migration. The protected-root policy intentionally rejects a broad selection containing private application directories. Select a dedicated academic folder.

## Final synthetic evidence

- Combined suite in the existing virtual environment passed **62 tests in 6.39 seconds**. It includes **20 Codex integration tests** and **42 Gemini tests**. Both teams' new cases ran.
- Isolated loopback smoke passed through `scripts/start.py`. Health returned ready, all five connectors registered, and read-only connection status worked with the backend running.
- `.venv/bin/python -m pip check` found no broken requirements. Actual configurations were not rewritten or printed.
- Scope of readiness is the local backend and published P2/P3 integration boundaries. ENSAI permissions, source behavior, Google OAuth and project/API state remain live validation gates. The Google Cloud CLI was unavailable on PATH in this session. Project diagnostics can be run by an authorized developer where it is installed or in the existing project's Console.


## Human-operated validation commands

Run the read-only local check first. The subsequent commands are for the explicitly authorized operator. Use the existing checkout and private configuration. Stop a running backend before CLI authorization or sync because the runtime takes one writer lock.

```bash
cd /home/ensai/hackathon_ENSORA
python3 scripts/bootstrap.py --test --browser
python3 scripts/check_connections.py
```

If a Google project developer needs to check current access or API state, run the readiness helper. An authorized developer can use the second command to offer provisioning of the exact missing APIs in project `ensora-516015`. Leave provisioning alone if the required APIs are already enabled.

```bash
.venv/bin/python scripts/p1_sources_cli.py gcp-check
.venv/bin/python scripts/p1_sources_cli.py gcp-check --enable-apis
```

With external access explicitly approved, use the unified interactive onboarding. Select each authorized source separately and decline automatic and initial bulk sync during the first bounded validation. Complete the official ENSAI login window and Google consent prompts yourself. A desktop OAuth client for the existing project must be available in the configured private location before Google OAuth. The OS keyring is needed only for optional institutional credential storage. Institutional connector use and curriculum access require authorization.

```bash
python3 scripts/onboarding.py
.venv/bin/python scripts/p1_sources_cli.py validate --source moodle
.venv/bin/python scripts/p1_sources_cli.py validate --source pamplemousse
.venv/bin/python scripts/p1_sources_cli.py validate --source ensora_drive
.venv/bin/python scripts/p1_sources_cli.py validate --source google_calendar
```

After validation and separate approval for acquisition, use bounded, redacted source diagnostics where applicable. The Calendar command requests busy/free information only. The Moodle command uses one course by default. Do not add export flags during initial validation.

```bash
.venv/bin/python scripts/p1_sources_cli.py sync-moodle --single-course
.venv/bin/python scripts/p1_sources_cli.py sync-pamplemousse --max-items 20
.venv/bin/python scripts/p1_sources_cli.py discover-drive --max-files 25
.venv/bin/python scripts/p1_sources_cli.py sync-calendar --days 14 --availability-only
```

Once the human approves a persistent sync, substitute an authorized Moodle course ID for COURSE_ID and run the following. Each full source sync needs a separate approval.

```bash
python3 scripts/sync.py --source moodle --subject-id COURSE_ID --approve
SYNC_ON_STARTUP=false SYNC_INTERVAL_MINUTES=0 python3 scripts/start.py --skip-onboarding
curl --fail --silent http://127.0.0.1:8765/api/v1/health
```

Do not paste runtime tokens, source records, browser data or student material into a coordination document. Developer Console actions remain human-operated in the existing project. No private credential content or live source was accessed in this remediation.

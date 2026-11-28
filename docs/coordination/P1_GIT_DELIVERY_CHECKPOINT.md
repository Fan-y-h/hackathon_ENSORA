# P1 Git Delivery Checkpoint

**Checkout:** `/home/ensai/hackathon_ENSORA`, branch `feat/p1-data-engineering`, current base `ed4cf44` (`Sauvegarde ID`). No files have been staged or committed.

## Delivery summary

The working tree contains the P1 backend and connector integration: typed source/authentication/sync contracts; local SQLite persistence, versioned document ETL, provenance, academic records and overrides; source synchronization, durable job recovery and progress; onboarding/authentication integration; parsing/classification/entity matching; P2 document/chunk endpoints; P3 academic, timetable, availability and Grade Tracker endpoints; source-specific scheduling; and strict approved Ensora Drive scope enforcement with a non-destructive Global cleanup utility.

## Verification evidence

Latest human-reported validation: **132 backend tests passed**, compilation and dependency checks succeeded. Human-reported live results include 958 Drive documents persisted before scope exclusion; 20 Global documents subsequently excluded without deleting files or versions; Pamplemousse persisted 214 timetable events, 71 subjects, 71 subject coefficients, 83 coefficient records and 5 grades; Google Calendar was live-validated earlier. These live counts are operator-reported, not independently re-read from the private database in this checkpoint review.

## Outstanding acceptance issues

- Moodle authentication works, but the current academic-year filter excludes 19 of 20 discovered courses. Course/year reconciliation requires correction and another authorized live validation.
- Restricted Drive discovery for only `1A`, `2A` and `3A` has not yet been fully live-validated.
- Academic reconciliation examined 938 Drive documents and matched zero. P2 retrieval and P3 academic associations therefore need additional evidence and owner review before claiming complete downstream integration.
- Gemini's status/handoff still contains an outdated test-fixture failure and duplicates the allowed-cohort constants. `connectors/base.py` also carries a recommendation table parallel to Codex's persisted schedule defaults. Codex has requested a shared source of truth and status refresh. Cross-agent acknowledgement is pending.

## Git hygiene review

The proposed source, tests and coordination documentation contain synthetic examples and aggregate metrics. No private credential/configuration values, OAuth token values, session-cookie values, browser profile, virtual environment, database, downloaded source document or private log is present in the proposed changed-file set. The untracked files are `backend/app/core/drive_scope.py` and `scripts/drive_scope_cleanup.py` (implementation) plus this checkpoint document. `.gitignore` is extended to cover local environment files (while allowing example templates), source configuration, SQLite/database artifacts and sidecars, common runtime/download directories, nested browser/session state, token/client-secret files, private keys and HAR captures.

## Readiness

This is a **reviewable development checkpoint**, not a claim of complete live acceptance. The operator-reported 132-test result is noted; Codex's local rerun during the preceding work stalled in several AsyncClient/ASGI tests, so that local rerun is not counted as a pass. Do not describe Moodle, restricted Drive scope, or academic entity reconciliation as fully live-verified. Human commit readiness remains **conditional on Gemini acknowledging the changed scope and the outstanding source/reconciliation status**.

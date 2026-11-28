# ENSORA+ final acceptance

Scope follows sections 1–12 of the ENSORA+ specification and the separate Grade Tracker requirements. Live checks use the authorized local account and a private SQLite backup and working copy. Synthetic browser checks run on separate stores. Counts in this report are aggregate evidence. No private course contents or credentials are included.

## Running instances

| Mode | URL | API | Evidence |
|---|---|---|---|
| Real | http://127.0.0.1:15180 | http://127.0.0.1:18080 | Runtime reports real through the frontend proxy. Chrome was opened on this URL. |
| Demo | http://127.0.0.1:15181 | http://127.0.0.1:18081 | Runtime reports demo. The store and index are separate. |

Use `bash scripts/run_local.sh real` or `bash scripts/run_local.sh demo` from the checkout. [Launch configuration and backup](README.md).

## Requirement evidence

| Section | Requirement | Code and API | Interface and evidence | Status and remaining work |
|---|---|---|---|---|
| 1 | Real local student workspace | `app/main.py`, `core/config.py`, `GET /runtime-mode` | Real Vite proxy reaches the real backend. Demo reaches a separate backend. | VERIFIED_LIVE |
| 2 | Centralized resources | `modules/ingestion/`, `GET /sources/documents` | Library reads authorized Moodle and Drive documents on the real store. | VERIFIED_LIVE for tested sources and courses |
| 2 | Personalized agenda | `modules/planner/source_bridge.py`, `GET /events` | All eight Pamplemousse entries in the reference week of 30 November 2026 matched the imported titles and local times. | VERIFIED_LIVE |
| 2 | Exam mode, answer visibility, timer | `modules/learning/`, `core/exam_guard.py`, `/attempts` | Fixture Chromium tests resume, submit and guard restricted pages. | VERIFIED_FIXTURE |
| 2 | Generated summaries, quiz and flashcards | `modules/ai/`, `POST /artifacts/generate` | A revision sheet from the selected Moodle PDFs was saved with seven citations. Quiz and flashcard generation and linking were tested with fixtures. | VERIFIED_LIVE for the sheet. VERIFIED_FIXTURE for quiz and flashcard output. |
| 2 | Podcasts, video, animation, infographics | `modules/ai/`, Studio | End-to-end multimedia production is absent. | NOT_IMPLEMENTED |
| 3 | Data separation and consent | `scripts/real_store.py`, `core/auth.py`, `modules/catalog/service.py` | Distinct real and demo SQLite stores, read-scoped sources and local single-student identity. | VERIFIED_LIVE for separation. Separate student accounts within one process are outside this implementation. |
| 3 | Shared identifiers and module bridges | `modules/catalog/service.py`, `modules/ingestion/codex_bridge.py`, `modules/rag/store.py`, `GET /subjects` | Canonical curriculum IDs connect scoped resource records to subject views and retrieval. | PARTIAL. Legacy ambiguous documents require confirmation. |
| 4 | Profile and configuration | `modules/account/router.py`, `PATCH /profile` | Cohort, year, track, status, goal and work windows save and reload in isolated Chromium. Actual P1 fields prefill the real profile. | VERIFIED_FIXTURE for first-use form. Display name and personal choices are entered by the student. |
| 4 | Learning and study navigation | `frontend/src/features/`, `/quizzes`, `/artifacts`, `/decks` | Tutor, Studio, Quiz and Flashcards use existing backend routes. | VERIFIED_FIXTURE. Some advanced output formats are unavailable. |
| 4 | Collaboration and sharing | Workspace and account modules | Local content storage and account export are available. | PARTIAL. Multi-user collaboration is absent. |
| 5 | Official source authentication | `core/auth.py`, `POST /sources/connections/{id}/validate` | Current authorized CAS access verified for Pamplemousse and Moodle, and Google read access for Drive and Calendar. | VERIFIED_LIVE. Expired sessions require the official login flow. |
| 5 | Pamplemousse inscriptions | `ingestion/connectors/pamplemousse.py`, `GET /results` | Verified published subject weights, distinct ECTS and UE grouping on the real copy. | VERIFIED_LIVE. Unpublished UE weights stay unknown. |
| 5 | Pamplemousse marks and scales | `grades/source_results.py`, `GET /results`, `POST /grades/import` | Five published marks persisted with unknown scale. Official values are read only. | VERIFIED_LIVE for reading. Values without a published scale stay out of normalized calculations. |
| 5 | Pamplemousse schedule and cancellations | `ingestion/connectors/pamplemousse.py`, `planner/source_bridge.py`, `GET /events` | Reference week matched 8/8 in Europe/Paris. | VERIFIED_LIVE for this week. Cancellation and DST regression coverage is fixture-based. |
| 5 | Moodle discovery and documents | `ingestion/connectors/moodle.py`, `POST /sources/sync` | Restricted synchronization of authorized course resources. Course 505 produced real PDFs and notebooks. | VERIFIED_LIVE for course 505. See delivery observations below for course 245. |
| 5 | Moodle deadlines into tasks | `planner/source_bridge.py`, `GET /tasks` | Course 505 persisted one real assignment. Moodle returned no deadline for it. The dated assignment-to-task path and duplicate prevention passed integration tests. | PARTIAL. No task with an assumed deadline was created. |
| 5 | Ensora Drive authorized annales | `ingestion/connectors/ensora_drive.py`, `/sources/sync` | Scoped upstream sync and real matched annale download and parsing. | VERIFIED_LIVE for the validated subject. Broad archival matching is partial. |
| 5 | Personal Calendar | `ingestion/connectors/google_calendar.py`, `GET /events` | Two actual syncs of the same window returned seven unchanged events. The imported schedule contributes occupied intervals. | VERIFIED_LIVE for the tested window. Personal labels remain opt-in. |
| 5 | Increments, progress and versions | `core/sync.py`, `core/etl.py`, `GET /sources/jobs/{id}` | Stable source IDs and versions. Sources UI exposes stage, result counts and errors. | VERIFIED_LIVE for Calendar and the scoped reads. Cross-source and version regressions are fixture-based. |
| 6 | Parsing and extraction | `ingestion/parsers/`, `GET /sources/documents/{id}/versions/{version_id}/chunks` | Real PDF headers, parsing and chunks verified on authorized documents. | VERIFIED_LIVE for selected PDFs. Scanned pages can remain partial without OCR. |
| 6 | Document classification and manual override | `ingestion/resolution/entity_resolver.py`, `PUT /sources/documents/{id}/match` | Scoped Moodle records bind to the canonical subject. Library offers manual reassignment. | PARTIAL. Automatic chapter and concept coverage is incomplete. |
| 6 | Local indexing and freshness | `rag/store.py`, `POST /rag/reindex`, `GET /search` | New chunks enter the authorized index and can be found by subject or document. | VERIFIED_LIVE for tested accessible Moodle material. |
| 7 | Context and provenance | `ai/service.py`, `POST /tutor/answer`, `GET /citations/{chunk_id}` | Chromium sent a question about backpropagation limited to the real course PDFs. The grounded response had four resolvable citations, one with a topic-bearing excerpt. | VERIFIED_LIVE. Extractive answers are identified in the interface. |
| 7 | Model availability and route selection | `core/ai.py`, `GET /ai/providers`, `PATCH /ai/preferences` | Provider states and allowlisted inline model choice are exposed. | PARTIAL. No paid provider was activated for acceptance. |
| 7 | Personalization and reuse | `ai/schemas.py`, `/artifacts`, `/conversations` | Generation is on demand, and conversation history reloads. Backend accepts chapter and document filters. | PARTIAL. Some Studio controls and output options remain limited. |
| 8 | Quiz evidence, mastery and error notebook | `learning/service.py`, `/attempts`, `/mastery`, `/errors` | Automatic grading, evidence isolation and error logging covered in tests. | VERIFIED_FIXTURE. No live user learning history altered for testing. |
| 8 | Spaced repetition and review | `learning/fsrs.py`, `GET /reviews/due` | Fixture tests cover reviews and repetition behavior. | VERIFIED_FIXTURE |
| 9 | Conflict-free scheduling and preferences | `planner/cpsat.py`, `planner/plan_service.py`, `POST /planner/preview` | CP-SAT found an optimal test session against real busy events with zero overlaps on an isolated planner database. | VERIFIED_LIVE for evaluated constraints and horizon. |
| 9 | Replanning and locked sessions | `planner/scheduling.py`, `/planner/preview`, `/plans` | Previews and planner constraints are covered in fixture regression tests. | VERIFIED_FIXTURE |
| 9 | Academic targets and simulation | `grades/domain.py`, `/grades/summary`, `/grades/required`, `/grades/simulate` | Weighted scenarios and objectives validated by fixture, and the authorized curriculum imported. | PARTIAL. Unknown scales and official rules remain explicit. |
| 10 | Task create, edit and persistence | `planner/service.py`, `/tasks` | Isolated real-mode Chromium verified create, edit and reload. | VERIFIED_FIXTURE |
| 10 | Notes and Focus Room | `workspace/service.py`, `/notes`, `/focus` | Note persistence and effective-time tracking are covered in fixture tests. | VERIFIED_FIXTURE. Full personal session acceptance kept out of the real profile. |
| 11 | Learning Analytics | `modules/analytics/`, `/analytics`, `/analytics/readiness` | Separate academic and practice measures rendered and tested. | VERIFIED_FIXTURE. Readiness is an estimate. |
| 12 | Closed-loop adaptation | `learning/service.py`, `planner/plan_service.py`, `analytics/service.py` | Task and assessment changes feed replanning and learner evidence feeds prioritization. | PARTIAL. Complete live quiz-to-next-plan and focus-to-replanning sequences remain unverified. |


## Delivery observations

- Moodle course 505 initially produced 34 ready documents, including seven PDFs, and 1,396 chunks. Three downloads exceeded the configured size limit. The final incremental run succeeded, traversed nine sections and persisted the previously missing course and assignment records. The working copy has 35 ready documents for the course.
- The final course 245 sync succeeded and traversed two sections. Its announcement and resource were already present. The modern section parser confirmed the course identity before processing links.
- Chromium visited 17 real screens with no uncaught errors or HTTP 5xx. The real display name and study preferences remain the student's choices. The isolated browser acceptance covers the first-use form and task persistence.
- The post-change checks passed 109 ingestion tests, 33 RAG and adaptation tests, 11 integration tests for the sources, modes and catalogue, and 31 frontend tests. Build and lint passed. The separate scoped Tutor browser check also passed in the demo store. These suites overlap.
- Official grade scales and unlisted UE weights are kept unknown. Full live replacement of a study plan, adaptive assessment and Focus history were not run against the student's personal data.

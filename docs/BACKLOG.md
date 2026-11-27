# Engineering backlog

Proposed ownership: C1 = Codex integration/data; G = Gemini frontend/AI; C3 = Codex planner/learning. Each ticket requires scoped branch, tests, review and documented blockers. P0/P1/P2 as in [MVP](MVP.md). Dependencies refer to ticket IDs.

| ID / priority | Owner | Dependencies | Deliverable and acceptance |
|---|---|---|---|
| T00 P0 | C1 | — | Audit actual local tree and permissions; reconcile ADRs; record commit, existing code, access limitations; human approves architecture |
| T01 P0 | C1 | T00 | Bootstrap config, DB, migrations, local auth/demo, health, CI; clean install and restart preserve profile |
| T02 P0 | C1 | T00 | Freeze API/DTO/event schema, generated client contract, synthetic fixture; G/C3 validate same fields and types |
| T03 P0 | C1 | T01,T02 | Local document + calendar/assignment fixture import, subject/alias classification, hashes; repeat import has zero duplicates |
| T04 P0 | C1 | T03 | Parse PDF/TXT, anchors, versioned chunks; malformed file visible error, unchanged skip |
| T05 P0 | C1 | T04 | FTS + vector retrieval, filter and citation; golden query resolves exact allowed span, missing evidence tested |
| T06 P0 | G | T02,T05 | Provider abstraction, one verified model if credentials, Tutor + sheet/cards/quiz structured artifacts, cache; source citations validate, repeated generation hits cache |
| T07 P0 | G | T02 | App shell, onboarding/dashboard/library/agenda, client and mocks; responsive navigation, explicit demo state, no secrets |
| T08 P0 | C3 | T02,T03 | Calendar/task service, assessment scope, availability and confirmation; event change updates revision and constraints |
| T09 P0 | C3 | T08 | Greedy planner/preview/accept, locks, infeasible warnings, deterministic no overlap and limits |
| T10 P0 | C3 | T02 | Quiz/card review, evidence, mastery, due date; duplicate submit idempotent, passive activity not mastery |
| T11 P0 | C3 | T09,T10 | Feedback events, replan/partial sessions, preserve completed/locks and show diff |
| T12 P0 | C3 | T08,T10,T11 | Analytics read models and session completion; values derived from persisted evidence/time |
| T13 P0 | G+C1+C3 | T05–T12 | Wire real endpoints, complete demo, restart test, citation/plan/mastery UI; smoke pass and documented actual vs mock |
| T14 P0 | C1 | T13 | README, release checklist, fixture licensing, setup instructions and test evidence; clean checkout usable |
| T15 P1 | C1 | T03 | Moodle authorized REST/export adapter, deadlines and incremental update; permission proof or explicit fallback |
| T16 P1 | C1 | T03 | Ensora Drive OAuth, recursive selected folder, exam/solution match; changes and revocation tested |
| T17 P1 | C1 | T08 | Pamplemousse official/API/ICS/CSV, group and ambiguity; no false assumption red=exam |
| T18 P1 | C1 | T16,T17 | Personal Google Calendar, recurring/all-day events and selected access; revoke excludes retrieval |
| T19 P1 | C1 | T04,T05 | DOCX/PPTX/IPYNB/code/OCR, table/formula/figure anchors, incremental reindex; no notebook execution |
| T20 P1 | C1+G | T05,T06 | Concept/prerequisite/exam-pattern extraction, source evidence and review; current syllabus priority, cycles flagged |
| T21 P1 | C3+G | T08,T06 | Project TXT/PDF decomposition, editable estimates/resources, task views and remaining work; user confirms proposals |
| T22 P1 | C3+G | T06,T10 | Smart Notes and error notebook, Markdown/LaTeX source excerpts, tutor save; preserve edits and provenance |
| T23 P1 | C3+G | T11 | Focus timer classic/long/deep/custom/free, pause/resume/reload, notes/authorized music/reflection; active minutes exclude breaks |
| T24 P1 | C3+G | T10,T06 | Exam trainer historical/mock, timed mode, no keys/AI during active attempt; post-submit rubric and errors |
| T25 P1 | C3+G | T12 | Readiness/heatmap/weak concept analytics and notifications incl. source changes; configurable delivery, dedupe |
| T26 P1 | C3 | T09,T10 | CP-SAT and FSRS adapters; baseline comparison, timeout fallback, versioned evidence and intervals |
| T27 P1 | G | T06 | Multi-model preference, quota/cost reservation, authorized fallback and cache invalidation; offline/denial tests |
| T28 P2 | G | T06,T27 | Podcast script + TTS, persisted playable audio/transcript; no fake media |
| T29 P2 | G | T28 | Explanatory video rendering, subtitles, formulas/illustrations; validate output or label storyboard |
| T30 P2 | G | T06 | Parameterized safe math/stat/algorithm animation templates, interactive controls; no generated code execution |
| T31 P2 | G | T06 | Interactive mind map and infographic, schema/edge validation, source citations and accessibility |
| T32 P2 | C1+G | T22,T27 | Account export/delete, backup/restore, opt-in sharing packages, later authorized moderated community service |
| T33 P2 | C3 | T26,T25 | Rich adaptive mastery/retention and readiness validation, historical exam pattern refinement, evidence calibration |
| T34 P2 | C1 | T32 | Evaluate PostgreSQL/shared deployment/institutional auth; privacy and permission review, migration plan |
| T35 P1 | G+C3 | T06,T10 | Expand all quiz types, personalized exercises and detailed correction, user editable rubric and provisional AI grading |
| T36 P2 | G | T27 | Conditional NotebookLM integration if approved documented access, otherwise standalone media pipeline; DeepSeek/Mistral/Claude/Ollama adapters as authorized |

## Coverage and queue management
R01/R30/R32/R33 → T00–T02,T14,T32,T34; R02–R08/R31 → T03–T05,T15–T20; R09/R10 → T07,T08,T12; R11–R13 → T09,T11,T20,T26; R14–R19 → T05,T06,T20,T27–T31,T36; R20–R23 → T10,T11,T24,T26,T33,T35; R24–R26 → T21–T23; R27/R28 → T12,T25; R29 → T32/T34; R34 → T13/T14. Full scope preserved. Status initially **planned**, not implemented. Integration owner assigns a single primary editor for each shared task before work. Scope changes require updated ADR and acceptance test.

## Academic extension tickets (S4)
| ID / priority | Owner | Dependencies | Deliverable and acceptance |
|---|---|---|---|
| T37 P0 | C1 | T02 | Freeze academic tree/record/goal/signal DTOs, migration and synthetic fixture; same fixture shared by G/C3 |
| T38 P0 | C3 | T37 | Curriculum and manual mark CRUD, nested weights, exact calculator; reject cycles and invalid sums; missing vs zero tested |
| T39 P0 | C3 | T38 | Objectives, required grades, feasible bounds/combinations and scenarios; no actual writes; impossible/fully assessed cases tested |
| T40 P0 | C3 | T39,T09 | Academic signals and planner priority preview; caps/locks preserved; unknown inputs use baseline |
| T41 P0 | G | T37 | Academic editor/dashboard/objective/simulator UI against mocks then real endpoints; show coverage and provenance; link planner preview |
| T42 P0 | C1+G+C3 | T38–T41 | Academic end-to-end demo, restart persistence, isolation and regression; required before T13/T14 release gate |
| T43 P1 | C1 | T37,T17 | Authorized academic source sync, conflicts/overrides and provenance; permission evidence or manual fallback |
| T44 P1 | C3 | T39 | Verified validation rules/compensation, risk and explanations; unknown for unsupported rules |
| T45 P1 | C3+G | T40,T44,T11 | Grade-triggered replan preview, reasons, diagnostic recommendations, trend UI; no aggregate mark mastery update |
| T46 P2 | G+C3 | T45,T06,T22 | Corrected-script analysis with consent and citations, confirmed evidence links |
| T47 P2 | C3 | T44,T26 | Calibrated uncertain forecasts and advanced time optimizer; compare baseline, no unsupported guarantees |
Coverage: R35→T37/T38/T43; R36/R37→T38; R38/R39→T39/T41; R40→T44; R41→T40/T45/T47; R42→T45/T46; R43→T41/T45; R44→T38/T39/T47. T42 is an additional dependency of T13; avoid circular dependency. All planned.

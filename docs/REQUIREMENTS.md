# Requirements baseline

All R identifiers below are **REQUIREMENT** unless explicitly marked. P0/P1/P2 in the backlog denote delivery priority, not deletion of scope. Source references S1–S4 are defined in [PROJECT_CONTEXT](PROJECT_CONTEXT.md). Detailed implementation belongs in the linked domain documents.

| ID | Full product requirement | Source / detail |
|---|---|---|
| R01 | Personal account, first name/email, academic year, 1A/2A/3A, specialization, subjects, goals, availability, preferences; editable profile, self-assessment and optional diagnostic quiz. | S1 profile/adaptive; S2 §3 |
| R02 | Connect Moodle, Ensora Drive, Pamplemousse, Google Calendar, personal Drive and local folders using authorized access; disconnect, view status, select scope. | S1 sources/sync |
| R03 | Initial, incremental, startup, manual, selective and regular synchronization; show last refresh and changes; no reprocessing unchanged data. | S1 sync |
| R04 | Download authorized courses, TD, TP, PDF, slides, notebooks, assignments, instructions, deadlines, announcements; archive historical exams, answers, revision sheets and tutoring resources. | S1 sync |
| R05 | Normalize and classify by cohort, specialization, subject, chapter, academic year and resource type; reconcile aliases across sources; match exam/solution; confirm ambiguity. | S1/S2 ETL |
| R06 | Parse PDF, DOCX, PPTX, IPYNB, code, notes, HTML, images; retain formulas/LaTeX when possible, code language/indentation, tables, illustrations and provenance; OCR where needed. | S1 extraction |
| R07 | Extract concepts, definitions, properties, theorems, formulas, hypotheses, proofs, algorithms, implementations, exercises, prerequisites, historical exam patterns. | S1 pedagogy |
| R08 | Local library with previews, downloads, keyword/content/semantic search, filters, selection, annotations, extracts and source links. | S1/S2 |
| R09 | Subject workspaces group chapters, concepts, resources, notes, generated artifacts, assessment results and progress. Dashboard shows daily timetable, goals, tasks, upcoming assessments/deadlines, sessions and progress. | S1 modules |
| R10 | Unified agenda: classes, assessments, presentations, teachers, rooms, group, Moodle deadlines, personal events, tasks, revisions; detect changes and confirmation for ambiguous assessment scope/slot. | S1/S2 |
| R11 | Smart Planner identifies current curriculum, format, difficulty, scope, prerequisites, readiness, estimated work, essential concepts and representative historical exam exercises. | S1 planner |
| R12 | Plan all subjects/tasks with objectives, resources, difficulty, activity, estimated duration, priorities, success criteria and future repetitions; respect availability, breaks, limits, deadlines, prerequisites, locks, no overlap. | S1 planner |
| R13 | Dynamically replan on new/moved/cancelled assessments, changed timetable/scope/resources/preferences, missed/partial sessions, new tasks and mastery; preserve completed/locked activities, minimize disruption and report infeasibility. | S1 planner |
| R14 | AI Tutor by subject or cross-subject, RAG-based explanations, math proofs, code assistance, detailed exercise/TP/exam correction, Socratic tutoring and exam preparation. | S1/S2 AI |
| R15 | Learning Studio: revision sheets, flashcards, single/multiple-choice quizzes, true/false, fill blanks, open questions, calculations, applied exercises, podcasts, explanatory videos, parameterized animations, infographics, interactive mind maps, personalized exercises, detailed corrections. | S1 features |
| R16 | Customize sources, scope, concepts/formulas/code/skills, objective, language, format, provider/model, instructions; earlier specification adds difficulty, duration and detail. Save, edit, organize by subject/chapter, reuse and regenerate. | S1/S3 |
| R17 | Multi-provider AI router: OpenAI, Gemini, Claude, Mistral, DeepSeek, Ollama; NotebookLM conditional on authorized integration. Select by capabilities, preferences, cost, quota; compatible authorized fallback only. | S1 AI |
| R18 | Hybrid retrieval with metadata filters, source citations at document/page/section, honest missing evidence; comprehensive summaries cover selected sections. | S1/S3 RAG |
| R19 | Generate on explicit request or session start only; cache and persist locally; detect stale sources; user can edit/update/regenerate; don't overwrite personal data on sync. | S1 persistence |
| R20 | Manual/AI flashcards with review feedback, adaptive quiz and item-level correction; spaced repetition across learning activities, not flashcards alone. | S1 adaptive |
| R21 | Exam Trainer: original exams and solutions, mock exams matching structure/level/current syllabus with rubric, timed mode without answers or AI, post-submit corrections, error analysis and recommendations. | S1 exams |
| R22 | Per-concept mastery dimensions: theory, memory, mathematical reasoning, application, implementation, autonomy and long-term retention; record evidence, confidence, errors, dates, count, risk and trend. Passive consumption is not proof. | S1 adaptive |
| R23 | Adapt next activities, difficulty, priority, duration and spacing to evidence, prerequisites, forgotten concepts, assisted success, recurring errors and impending assessment. | S1 adaptive |
| R24 | Task Manager: personal/imported/AI tasks, TXT/PDF project decomposition, deliverables, estimates, resources, editable priorities, status, filters/sort, daily/weekly/calendar/Kanban, move/delete/lock and replan remaining work. | S1 productivity |
| R25 | Smart Notes: notebooks by subject/chapter/concept, Markdown/LaTeX, code highlighting, tables/images/diagrams; extract and annotate source material preserving format; AI restructure/explain/summarize with references; save tutor output; error notebook. | S1 notes |
| R26 | Focus Room: classic/long Pomodoro, Deep Work, custom/free timer, pause/resume/extend/stop, resources and goals, notes, authorized audio, themes/background/fullscreen, actual active minutes; end-session self-evaluation and optional active recall. | S1 focus |
| R27 | Analytics: daily/weekly/monthly minutes by subject/chapter/session, completion, adherence, regularity, quiz/exam results, card mastery, risk, concept progress and assessment readiness; feedback to planner. | S1 analytics; S2 heatmap |
| R28 | Notifications for new documents/assessments, timetable changes, deadlines, planned work, repetitions, overdue tasks; configurable in-app and message/email reminders. | S1 notifications |
| R29 | Community: maintain inter-cohort tutoring, share sheets/decks/resources, collaborative corrections, peer recommendations and exchange; shared infrastructure or explicit export/import needed. | S1 module; S2 §16 |
| R30 | Settings: source connections, keys/providers, quotas, storage, sync, backups, display and work preferences; export personal data and delete account. | S1/S2 |
| R31 | Local storage of documents, metadata, index, profile, agenda, tasks, notes, artifacts, chats, results/progress; survives restart; offline access to compatible features. | S1/S3 |
| R32 | Authorized source access, least privilege, secret protection, user-controlled external disclosure, data isolation, read-only originals, copyright/rights checks, no forced cloud dependency. | S1/S2/S3 |
| R33 | Modular, responsive, maintainable and reproducible; incremental performance, error recovery, traceability, installation documentation and tests. | S2/S3 |
| R34 | Hackathon deliverables: runnable local app, documented repo, functional imports/connectors, unified database, ingestion, RAG, planner, quizzes/cards, dashboard, demo dataset, architecture, tests, README and jury pitch. | S2 §27 (proposed deliverables) |

## Cross-cutting acceptance
Every feature must handle empty/loading/error states, persist authoritative state, validate input, prevent cross-profile leakage and provide a reproducible test. External integration cannot be claimed working without a successful authorized test. No source material is redistributed merely because it is accessible. Source deletions are tombstoned and cached access evaluated; revoke access when required. See [TESTING](TESTING.md) and [BACKLOG](BACKLOG.md) for measurable checks.

## Explicitly not decided
Provider credentials, API availability, institutional permission, exact mastery/scheduling algorithms, event duration and jury rubric, Community moderation model. Personal Drive metadata is unspecified in S1; treat it as an opt-in selected-folder connector analogous to local personal resources. These are not reasons to remove requirements.

## S4 extension — Grade Tracker & Academic Optimizer [REQUIREMENT]
| ID | Requirement | Source |
|---|---|---|
| R35 | Authorized academic data retrieval and manual entry/edit of formation, semester, UE, subjects, assessment/date, grades, coefficients and regulations; configuration separate from calculator. | S4 §1 |
| R36 | Recursive coefficient hierarchy and weight validation, editable evaluation components. | S4 §2 |
| R37 | Deterministic weighted means at assessment/subject/UE/semester/year levels; provisional and forecast; distinguish actual, estimated, simulated, missing. | S4 §3 |
| R38 | Personal targets at all levels, official validation separate, detect incompatibilities and compute necessary remaining grades/feasible combinations. | S4 §4–5 |
| R39 | Non-mutating what-if scenarios, marginal impact, feasibility, compensation analysis. | S4 §6 |
| R40 | Versioned official validation/compensation/eliminatory rules, risk identification, unknown when unavailable. | S4 §1,4,6,8 |
| R41 | Integrate academic weights, grades, goals, risk and deadlines into planner; explain priorities, adapt following updates without violating limits. | S4 §8,10–11 |
| R42 | Diagnostic/adaptive feedback from grades, corrected papers, teacher comments and error types; never infer precise mastery from overall mark alone. | S4 §9 |
| R43 | Academic Dashboard, grade trend, coverage, goals and risk by academic hierarchy; connected to Learning Analytics. | S4 §7,10 |
| R44 | Deterministic calculation/feasibility; optional OR-Tools and uncertain future-gain modeling; manual fallback and local privacy. | S4 §11–12 |
See [GRADE_TRACKER](GRADE_TRACKER.md) for detailed rules and acceptance.

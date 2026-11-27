# Person 3 — Codex Planner + Learning (C3): coding guidelines and delivery order

Status: **PROPOSED**, awaiting human approval. Scope is the C3 workstream only, as defined in [AGENT_TEAM](AGENT_TEAM.md) and [BACKLOG](BACKLOG.md), plus the grade calculator (T38–T40), delivered as **P0** as in `main` (see [P3_PLAN](P3_PLAN.md) §1). This file adds no scope and overrides nothing in [AGENTS.md](../AGENTS.md): Plan Mode, worktree, ticket and human-approval rules apply.

Required reading: [AGENTS.md](../AGENTS.md), [SMART_PLANNER](SMART_PLANNER.md), [ADAPTIVE_LEARNING](ADAPTIVE_LEARNING.md), [GRADE_TRACKER](GRADE_TRACKER.md), [GRADE_CALCULATOR_DESIGN](GRADE_CALCULATOR_DESIGN.md), [ARCHITECTURE](ARCHITECTURE.md), [DATA_MODEL](DATA_MODEL.md), [API_CONTRACTS](API_CONTRACTS.md), [TESTING](TESTING.md).

---

## 1. Role and ownership

| Item | Value (from [AGENT_TEAM](AGENT_TEAM.md)) |
|---|---|
| Human / agent | Person 3 / Codex sessions (only Codex and Gemini are development agents) |
| Responsibility | calendar/tasks, planner, adaptive/spaced repetition, quiz/exam scoring, focus/notes backend, analytics and notifications backend, grades backend (calculator, simulator, objective/rule logic, planner and learning integration) |
| Allowed paths | `backend/app/modules/planner/**`, `learning/**`, `workspace/**`, `analytics/**`, `account/**` (profile contract coordinated with C1), `grades/**`; matching tests under `backend/tests/{unit,integration,contract}/` |
| Consumes | catalog/identity DTOs and the calendar fixture (C1); generated artifacts, quiz definitions and AI feedback (G); academic DTOs and migration (C1, T37) |
| Produces | planner, learning, workspace, analytics and grades endpoints consumed by the frontend (G) |
| Not owned | `frontend/**` (G), `main.py`, `contracts/**`, migrations, lockfiles, root config, shared docs (integration owner C1), `modules/ai`, `catalog`, `ingestion`, `rag` |

A change to a shared file (contract field, migration, dependency) is requested through the handoff template with the schema and the reason. Never edit another owner's files, and never write another module's tables ([ARCHITECTURE](ARCHITECTURE.md) § Boundaries).

## 2. Principles (from the domain docs)

1. **Deterministic scheduling.** Hard constraints are checked deterministically, and the same inputs give the same plan, with stable tie-breaks. The LLM never schedules arbitrary dates (ADR-05). The planner makes no provider calls.
2. **Feasibility over coverage.** Never exceed limits to cover infeasible work. Every need is either scheduled or returned as `unscheduled` with a reason.
3. **Preview → accept.** Plans are versioned previews on an `input_revision`. Accept is atomic, and returns 409 if stale. Completed, in-progress and locked sessions and manual decisions are never overwritten.
4. **Evidence-based mastery.** Passive activity is never proof of mastery. Assisted success weighs less. Unknown is distinct from zero (ADR-06).
5. **No eager generation.** Sessions carry a `generation_spec`; artifacts are generated only when a session starts or on explicit request ([AI_ENGINE](AI_ENGINE.md)).
6. **Exam integrity.** While an attempt is active, the payload contains no answer key, no solution citation and no AI access ([ADAPTIVE_LEARNING](ADAPTIVE_LEARNING.md) § Assessment).
7. **Deterministic grade arithmetic.** Official status stays `unknown` unless rules are verified. Scenarios never modify actual marks. An aggregate mark never sets concept mastery ([GRADE_TRACKER](GRADE_TRACKER.md)).
8. **Versioned and explainable.** Store the algorithm version, evidence IDs and input revisions, so results can be recomputed and explained.

## 3. Code structure

Each module uses `router.py`, `schemas.py`, `service.py`, `domain.py`, `repository.py` *as needed*. Do not create empty layers ([PROJECT_STRUCTURE](PROJECT_STRUCTURE.md)).

| Layer | Rule |
|---|---|
| `router.py` | validate DTOs and call the service. No logic. User resolved from the session, never from the body |
| `schemas.py` | Pydantic DTOs matching `contracts/`. No frontend-only or invented fields |
| `service.py` | application use cases: load the snapshot through repositories and query interfaces, call the domain, persist in one transaction, write outbox events |
| `domain.py` (or `domain/`) | pure Python: no FastAPI, SQLAlchemy or provider SDK imports. Injected clock |
| `repository.py` | SQLAlchemy behind typed interfaces. Module-owned tables only |

Cross-module reads use small query interfaces (for example catalog concepts for an assessment), never direct ORM access. Events go through the transaction + outbox: `AssessmentChanged`, `EvidenceRecorded`, `MasteryUpdated`, `SessionFinished`, `PlanAccepted`, `grade.*` ([ARCHITECTURE](ARCHITECTURE.md) § Consistency).

Stack (ADR-02/03): Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic (migrations owned by C1), SQLite. OR-Tools only for T26/T47.

## 4. Data conventions

| Topic | Rule (source) |
|---|---|
| IDs and time | UUID strings; ISO 8601 UTC; Europe/Paris for rendering; all-day dates separate from instants ([DATA_MODEL](DATA_MODEL.md)) |
| Durations | integer minutes; half-open intervals `[start,end)`; recurrence and DST normalized in local time before UTC ([SMART_PLANNER](SMART_PLANNER.md)) |
| Mutations | `revision` on mutable rows, `expected_revision`/If-Match; `Idempotency-Key` on evidence, review and submit ([API_CONTRACTS](API_CONTRACTS.md)) |
| Evidence | append-only, unique on activity/item/concept/dimension; mastery unique on user/concept/dimension |
| Grades | `Decimal`, serialized as strings; missing = null, 0 is a real mark; round only for display ([GRADE_TRACKER](GRADE_TRACKER.md)) |
| Privacy | every row user-scoped; no grade values, answers or document text in logs |

## 5. Algorithms to implement

**Planner (T09, T11) — [SMART_PLANNER](SMART_PLANNER.md):**
- Hard constraints: no overlap; work windows, meals, rest; daily and weekly caps; min/max session length; confirmed prerequisites; deadlines; no past slots; locks honored, with impossible locks reported; split only splittable tasks.
- Priority (soft rank, editable assumptions): `0.30 U + 0.25 I + 0.20 G + 0.15 F + 0.10 E`, with `U = 1/(1+days_to_deadline)` and unknown mastery G set to a 0.5 prior for ranking only (flagged).
- Greedy MVP in 4 steps: availability → needs + topological prerequisite order → slot search by slack, priority and stable ID → independent invariant recheck. Output is `PlanPreview` with sessions, unscheduled needs, warnings and per-day load.
- Replan: mark the plan dirty, debounce, recompute only impacted unstarted sessions; freeze completed, in-progress and locked sessions; return a diff with reasons. Partial sessions schedule only the remaining work.

**Learning (T10) — [ADAPTIVE_LEARNING](ADAPTIVE_LEARNING.md):**
- Mastery: first objective evidence initializes; then `m_new = (1-α·w)·m_old + α·w·score`, `α = 0.35`; assisted factor 0.5; confidence `min(1, n_effective/5)`; statuses unknown/learning/weak/acquired/consolidate. No double count on retries.
- Repetition: Again → 1 day; Hard → `max(1, round(prev·1.2))`; Good → `max(2, round(prev·2))`; Easy → `max(3, round(prev·3))`. Clamp before the exam and keep a long-term review after it. Store `due_at`, interval, rating, history and version. Do not label it FSRS (FSRS comes with T26).
- Scoring: exact-set for single/multiple choice, T/F, normalized fill, numeric with tolerance; open/code answers by rubric, with AI feedback labeled provisional (G). No code execution. Immutable attempts; server-side timer; submit/expire once.
- Output to the planner: `StudyNeed(concept_id, dimension, activity_type, priority_factors, estimated_min, due_window, reason, resource_ids, success_criteria)`.

**Grades (T38–T40, P0) — [GRADE_CALCULATOR_DESIGN](GRADE_CALCULATOR_DESIGN.md):**
- Recursive tree, normalized sibling weights, leaf weight = product along the path.
- Provisional mean with coverage; required mean `(T − S)/Σ_R a_i`, unclamped, with feasibility.
- Multi-objective "12 in every UE"; non-mutating scenarios.
- Academic signals blended into planner priority with `α = 0.25` only when inputs are sufficient; otherwise the baseline priority applies.

## 6. Endpoints owned (from [API_CONTRACTS](API_CONTRACTS.md))

| Ticket | Endpoints |
|---|---|
| T08 | `GET/POST /events`, `PATCH/DELETE /events/{id}`, `GET /assessments`, `PATCH /assessments/{id}`, `GET/POST /tasks`, `PATCH/DELETE /tasks/{id}` |
| T09/T11 | `POST /planner/preview`, `POST /planner/accept`, `GET /plans/current`, `PATCH /sessions/{id}`, `POST /sessions/{id}/start`, `POST /sessions/{id}/finish`, `POST /planner/replan` |
| T10 | `GET/POST /decks`, `/decks/{id}/cards`, `GET /reviews/due`, `POST /cards/{id}/review`, `GET/POST /quizzes`, `POST /quizzes/{id}/attempts`, `PATCH /attempts/{id}/responses`, `POST /attempts/{id}/submit`, `GET /attempts/{id}/result`, `GET /mastery`, `GET /study-needs` |
| T12/T25 | `GET /analytics`, `GET /notifications`, `PATCH /notifications/{id}` |
| T22/T23 | `/notes`, `/annotations`, `/errors`, `/focus/start`, `/focus/{id}/transition`, `/focus/{id}/finish` |
| T21 | `POST /tasks/decompose` (with G's AI) |
| T38–T40, T44–T45 | `/grades/programs`, `/curriculum`, `/rules`, `/grades/records`, `/grades/objectives`, `/grades/summary`, `/grades/required`, `/grades/simulate`, `/grades/scenarios`, `/grades/signals`, `/grades/records/{id}/feedback` (`/grades/import` is C1's) |

An endpoint that is not implemented is documented as planned; it never falsely returns success.

## 7. Delivery order

Planning, gates and the step-by-step breakdown (S0–S16) are in [P3_PLAN](P3_PLAN.md).

## 8. Tests (from [TESTING](TESTING.md))

- **Planner:** no overlap; daily/weekly caps; deadlines; rest; min/max session; prerequisite order and cycle; lock conflict; DST and all-day events; infeasible workload; partial/missed sessions; deterministic output; stale preview. Property tests with overlapping busy intervals and tight capacity. Golden example from SMART_PLANNER (60-minute busy event → 60 min unscheduled).
- **Learning:** unknown vs zero; objective vs passive; assisted weighs less; duplicate submit; correct and incorrect scoring; interval change; retention; exam expiry and key isolation; post-submit reveal. One attempt → exactly one evidence event per mapped item and dimension.
- **Workspace and analytics:** timer reload and pauses; error dedupe; actual vs estimated time; analytics denominators.
- **Grades:** the full academic acceptance matrix (3.125 %, 11.7142857, 10.6666667, missing vs 0, invalid configs, feasibility, scenario isolation, planner soft rank only).
- Services are tested with an injected clock and repositories; no live paid API. A skipped test is not a pass.

## 9. Definition of done and handoff

Follow [DEVELOPMENT_WORKFLOW](DEVELOPMENT_WORKFLOW.md):
- worktree `feat/planner-learning` (for example `../ensora-planner`) with its own `.env`, DB and ports;
- conventional commits; explicit `git add`; no force push or direct commit to main; the human authorizes push and PR.

Done means: acceptance met, tests run (or the limitation documented), no unauthorized files changed, docs updated for agreed decisions only, human review. Report with the [AGENT_TEAM](AGENT_TEAM.md) handoff template: ticket/owner/branch, base commit, files changed, contract additions, tests and results, blockers, integration instructions.

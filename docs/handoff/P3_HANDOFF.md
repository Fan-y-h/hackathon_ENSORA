# Person 3 (C3) — handoff to the integration owner (C1) and the team

Single source of truth for Person 3's handoff (replaces the block that used to live in `backend/README.md`).
Format: [AGENT_TEAM](../AGENT_TEAM.md) handoff template. Not a completion claim beyond the tests listed.

```text
Ticket / owner / branch: T08–T12, T21–T26, T33, T35, T38–T47 (C3 parts) / C3 / Fan-y-h
Base commit: 89ad3d8 (origin/main)
Files changed: backend/app/modules/{planner,learning,grades,workspace,analytics,account,notifications}/**,
  backend/tests/**, docs/P3_*.md, docs/GRADE_CALCULATOR_DESIGN.md, docs/handoff/**
  + C1-owned files written as an interim bootstrap (see §1)
Contract additions or deviations: §2 and generated P3_ENDPOINT_INVENTORY.md (36 routes outside the contract)
Tests executed and results: cd backend; .venv/Scripts/python.exe -m pytest → 128 passed (unit, integration,
  contract guards, E2E + restart); live uvicorn scenario 31/31 checks. Lint: ruff (E,F,W,B,UP,SIM,I) clean on P3 modules.
Known blockers / unverified assumptions: §3 (risk register) and P3_HEURISTICS.md (O08)
Integration instructions and next dependency: §4
```

## 1. Files owned by C1 that Person 3 wrote (risk 6) — C1 decides

| File | What it is | Why it exists | C1 options |
|---|---|---|---|
| `backend/app/core/db.py` | engine, `Base`, `UTCDateTime`, `create_all`, `_add_missing_columns` | nothing to run on | keep, or replace by Alembic (T01); the column helper must then go |
| `backend/app/core/identity.py` | `users` table + local single-user `get_current_user_id` | no auth yet | replace by local session auth (T01); modules only depend on the dependency |
| `backend/app/core/errors.py`, `common.py`, `events.py`, `config.py` | error envelope, pagination/clock/revision helpers, outbox, settings | API_CONTRACTS conventions | keep or merge into C1's core |
| `backend/app/main.py` | composition root (`create_app` factory) | | C1 owns router registration |
| `backend/pyproject.toml` | deps (`fastapi`, `sqlalchemy`, `pydantic`, `uvicorn`, `tzdata`), extras `dev`, `optim` (ortools) | | C1 owns lockfiles/pinning |
| `backend/app/__init__.py` | sets `DISABLE_SQLALCHEMY_CEXT_RUNTIME=1` | Windows Smart App Control blocks SQLAlchemy's DLL | keep or document in setup |
| `.gitignore` (root) | adds `data/runtime/`, `*.db*` | never commit local DBs | keep |
| `data/demo/agenda_fixture.json`, `scripts/seed_demo.py` | synthetic, labeled demo data and idempotent seed | demo/tests before T03 | merge into C1's fixture (T02/T37) |
| `learning.models.ConceptEdge` (`concept_prerequisites`) | interim prerequisites table | planner needs R12 before the catalog | move to catalog `concept_edges`; swap `learning/catalog_port.py` adapter |

## 2. Schema / contract deviations to review (risk 5)

Routes: see [P3_ENDPOINT_INVENTORY.md](P3_ENDPOINT_INVENTORY.md) (generated; `tests/contract` fails if stale) and
[p3_openapi.json](p3_openapi.json). Field/table deviations from [DATA_MODEL](../DATA_MODEL.md):

- `tasks.splittable`; `events.busy`, `events.source_hash`, `events.notes`, `events.group_name` (`group` in DTOs); `agenda_state`.
- `study_sessions.concept_ids` JSON instead of `session_concepts`; `plans.preview` JSON; `study_sessions.need_key/kind/title/deadline_at/reflection`.
- `mastery.subject_id`, `mastery.interval_days`; `evidence.predicted`, `evidence.derived_from`; `flashcards.fsrs_*`, `last_reviewed_at`; `questions.partial_credit`; `attempt_items.grader/feedback`; `quizzes.origin`.
- Grades: nodes addressed by a stable `key` + UUID `id`; `academic_objectives` unique per node; `POST /grades/objectives` accepts `node_kind` (bulk "12 in every UE"); `academic_feedback`, `corrected_papers`, `academic_rule_sets` (rules JSON).
- Workspace: `focus_sessions`, `work_intervals`, `notebooks`, `notes`, `note_versions`, `error_entries` (with `dedupe_key`, `occurrences`).
- `preferences`: `repetition_algorithm`, `desired_retention`, `planner_algorithm`, `notification_settings` (incl. optional `email_address`); `profiles.self_assessment`.
- Sync where the contract says job: `POST /account/export`, `POST /tasks/decompose` (heuristic, returns a proposal).
- Subject/concept IDs are free, format-checked strings behind `learning/catalog_port.py` (no FK until the catalog exists).

## 3. Risk register — who resolves what

| # | Risk | Status after this handoff | Responsable de la résolution |
|---|---|---|---|
| 3 | Code not committed | committed locally on `Fan-y-h` in scoped commits | **Human**: authorize push / PR / merge |
| 9 | Contradictory docs | P3 docs aligned (grades = P0, status §6 of P3_PLAN, this single handoff) | P3 (done); shared DECISIONS/BACKLOG/MVP unchanged → C1 if wording needed |
| 10 | P2 before P0 integration | feature freeze declared (P3_PLAN §6) | P3 (done) → support T13 |
| 11 | Uncalibrated heuristics | register + drift test ([P3_HEURISTICS](../P3_HEURISTICS.md)) | **Human team**: validate weights (O08), C1 records in DECISIONS |
| 5 | Contracts not frozen | OpenAPI + generated inventory + contract test | **C1**: freeze T02/T37, accept/reject 36 extra routes |
| 6 | C1 files written by P3 | listed in §1 with options | **C1**: keep or rewrite |
| 7 | No catalog | references isolated behind `catalog_port` (interim adapter) | **C1**: build catalog, provide adapter |
| 4 | "PLAN ONLY" mode in AGENTS.md | code exists although the policy says plan only | **Human**: authorize; then C1 updates AGENTS.md |
| 8 | No Alembic, simplified auth | `create_all` + column helper; local single user | **C1** (T01) |
| 1 | No frontend | APIs ready; P3 can do delegated screens only if agreed | **G** (or human delegation of `frontend/src/features/{planner,grades}`) |
| 2 | No ingestion / RAG / AI | P3 AI-dependent paths return honest "not available"/heuristic | **C1** (T03–T05), **G** (T06) |
| 12 | ENSAI rules & source access unknown | T44 ready to receive verified rules with provenance | **C1 + human** (O03–O05, O12) |

## 4. Integration instructions (T13)

1. `cd backend; py -3.13 -m venv .venv; .venv/Scripts/python.exe -m pip install -e ".[dev,optim]"`; `pytest` must be green.
2. Regenerate artifacts after any API change: `.venv/Scripts/python.exe -m tests.contract.handoff`.
3. Catalog: implement `CatalogPort` (`check_subject`, `check_concepts`, `prerequisites`) over the catalog and return
   it from `learning.catalog_port.get_catalog`.
4. Auth: replace `core.identity.get_current_user_id`; every P3 route already resolves the user server-side and
   returns 404 for other users' resources (tested).
5. Migrations: generate an initial Alembic revision from `Base.metadata`, then delete `Database._add_missing_columns`.
6. Frontend: consume `p3_openapi.json`; demo data via `python scripts/seed_demo.py` (synthetic, labeled).

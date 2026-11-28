# Ensora+ backend — C3 P0 slice (agenda, profile, planner, learning, grades, analytics)

FastAPI modular monolith (ADR-02/03), SQLite, Python ≥ 3.12. Implements the C3 (Person 3)
P0 tickets of [BACKLOG](../docs/BACKLOG.md) / [P3_PLAN](../docs/P3_PLAN.md) S1–S7:
T08 agenda + learning profile, T09 greedy planner, T10 learning, T11 feedback/replan,
T12 analytics, and the academic P0 of [MVP](../docs/MVP.md) S4 (T38 calculator, T39
objectives/required marks/what-if, T40 academic signals in the planner). Where
`P3_PLAN` (Fan-y-h) moves the grade calculator to P1, `main` (MVP/BACKLOG: P0) prevails.

## Run (Windows PowerShell, from the repository root)

```powershell
py -3.13 -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e "backend[dev]"
backend/.venv/Scripts/python.exe scripts/seed_demo.py          # optional SYNTHETIC demo data
backend/.venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
```

OpenAPI UI: <http://127.0.0.1:8000/docs>. Tests: `cd backend; .venv/Scripts/python.exe -m pytest`.
Runtime DB in `data/runtime/` (git-ignored; override with `DATA_DIR` / `DATABASE_URL`).

### One server, two parts (integration of `feat/p1-data-engineering`)

The same process now also serves the P1 data-engineering API (source connections, sync jobs,
documents, academic records), mounted under **`/api/v1/sources/...`** from
`backend/app/modules/ingestion/api.py` (its former `backend/app/main.py`, routes unchanged).

| Part | Routes | Storage | Auth |
|---|---|---|---|
| Planner, learning, grades… | `/api/v1/*` | `data/runtime/ensora.db` | local single user |
| P1 sources | `/api/v1/sources/*` | `~/.local/share/ensora-plus/ensora.sqlite3` (`LOCAL_DATA_DIR`) | `Authorization: Bearer <api-token>` (file regenerated at each start; the Vite dev proxy adds it) |
| Bridge | `POST /api/v1/agenda/import-from-sources` | P1 events → agenda | — |

* The bridge copies P1 calendar events (Pamplemousse, Google Calendar, imports) into the agenda,
  idempotently: cancelled events skipped, tentative ones flagged for confirmation, P1-only types
  folded (`project`→`deadline`, `sport`→`personal`). Imported exams become assessments.
* `GET /api/v1/health` reports `"sources": true|false`. If the P1 runtime cannot start, the
  planner keeps working and `/api/v1/sources/*` answers 503.
* Tests and any `create_app(database_url=...)` call run **without** sources unless
  `enable_sources=True` (they never touch the real P1 store, browser profile or keyring).
* The standalone P1 launcher still works: `python scripts/start.py` (port 8765).
* Windows: `app/core/portable.py` replaces `fcntl`/`O_NOFOLLOW` (P1 was Linux-only) and blob
  writes close the temp file before renaming. Remaining P1 tests failing on Windows assert Unix
  file modes (`0o600`), symlink creation (needs admin) or local-import directory descriptors:
  they fail identically on the original branch with only the portability fix.

> Windows note: if Smart App Control blocks SQLAlchemy's compiled extension
> ("DLL load failed … stratégie de contrôle d'application"), `app/__init__.py` sets
> `DISABLE_SQLALCHEMY_CEXT_RUNTIME=1` so the pure-Python implementation is used.

## Modules and endpoints (`/api/v1`)

| Module | Endpoints | Key behaviour |
|---|---|---|
| account | `GET/PATCH /profile` | cohort/track/year/locale/timezone, goals, weekly work & rest windows, daily/weekly caps, min/max session, formats, provider consent |
| planner (agenda) | `/events`, `/events/{id}/confirm`, `/assessments`, `/tasks`, `/availability` | unified agenda; imported events read-only; ambiguous slots confirmed explicitly; exam → assessment with scope/concepts |
| planner (smart planner) | `POST /planner/preview`, `POST /planner/accept`, `POST /planner/replan`, `GET /plans/current`, `PATCH /sessions/{id}`, `POST /sessions/{id}/start`, `POST /sessions/{id}/finish` | deterministic greedy, preview → atomic accept (409 if stale), diff, sticky slots, locks, partial/missed sessions, no AI call (sessions carry a `generation_spec`) |
| learning | `/decks`, `/decks/{id}/cards`, `PATCH /cards/{id}`, `GET /reviews/due`, `POST /cards/{id}/review` (Idempotency-Key), `/quizzes`, `/quizzes/{id}/attempts`, `PATCH /attempts/{id}/responses`, `POST /attempts/{id}/submit`, `GET /attempts/{id}/result`, `GET /mastery`, `GET /study-needs` | objective scoring (choice exact-set, T/F, normalized fill, numeric tolerance, open = pending review), evidence → EMA mastery (α 0.35, assisted ×0.5), interval heuristic, exam mode with server timer and hidden keys |
| grades | `/grades/programs`, `/grades/programs/{id}/curriculum`, `/grades/records`, `/grades/objectives`, `GET /grades/summary`, `POST /grades/required`, `POST /grades/simulate`, `GET /grades/signals` | recursive tree, Decimal arithmetic as strings, coverage-weighted provisional mean, missing ≠ 0, required mark + feasibility, "12 in every UE" (`node_kind`), non-mutating what-if, official status always `unknown` |
| analytics | `GET /analytics?start&end[&subject_id]` | time planned/actual, adherence with denominators, practice quizzes, cards, mastery, academic results (separate), upcoming items, whether plan inputs changed since accept |

### P1 additions (Person 3 — P3_PLAN S9–S15)

| Ticket | Endpoints | Behaviour |
|---|---|---|
| T44 rules & risk | `GET/PUT /grades/programs/{id}/rules`, `GET /grades/validation` | immutable rule versions (`pass_threshold`, `eliminatory_minimum`, `compensation`) with provenance; `verified` requires `source_ref`; `official_status` stays `unknown` unless verified; outlook guaranteed/possible/impossible, risk none→high with reason codes |
| T39 scenarios | `GET/POST /grades/scenarios`, `PATCH/DELETE /grades/scenarios/{id}`, `GET /grades/scenarios/{id}/evaluate` | named overlays, never bump the program revision; values superseded by real marks are dropped with a warning; stale flag |
| T45 / R42 | `POST /grades/records/{id}/feedback`, `GET /grades/feedback`, `PATCH /grades/feedback/{id}`, `GET /planner/status` | mark below objective (or 10) → subject diagnostic need; reported difficulties → targeted activities (calculation→practice, formula→recall…); no mastery write; plan status lists changed inputs (grades/agenda/learning/prefs) |
| R43 trends | `GET /grades/trends` | provisional mean + coverage after each mark, chronological |
| T26 | `PATCH /profile` `repetition_algorithm=fsrs`, `desired_retention`; `planner_algorithm=cpsat` or `POST /planner/preview {"algorithm":"cpsat"}` | FSRS-4.5 (default params) for cards with heuristic baseline in each result; CP-SAT (15-min slots, 5 s limit) kept only if it beats greedy weighted coverage and passes the invariant check, else greedy fallback |
| T35 | `POST /attempts/{id}/items/{qid}/grade`, `partial_credit` on multiple choice | teacher = full evidence, self = half, `ai_provisional` = no evidence |
| T24 | `GET /exam-mode`, `POST /attempts/{id}/errors` | exam errors auto-logged, recommendations per failed concept, UI lock list |
| T23 | `/focus/start`, `/focus/active`, `/focus/{id}`, `/focus/{id}/transition`, `/focus/{id}/finish` | classic/long/deep/custom/free; server intervals (reload-safe), breaks/pauses excluded; finishing updates the linked study session and task |
| T22 | `/notebooks`, `/notes`, `/notes/{id}/versions`, `/errors` | every save snapshotted; error notebook dedupes by question, recurring (≥2) → planner consolidation need |
| T25 | `GET /notifications`, `PATCH /notifications/{id}`, `POST /notifications/read-all` | in-app only; outbox + deadlines/reviews/sessions/plan outdated/academic risk; per-type opt-out |

### P2 and remaining Person-3 features

| Ticket | Endpoints | Behaviour |
|---|---|---|
| T33 | `GET /learning/calibration`, `GET /analytics/readiness`, `GET /analytics/mastery-map`; quiz `origin: historical_exam` | retention evidence derived from delayed recall (≥ 1 day), autonomy from unassisted application/reasoning; each observation stores the prior prediction → Brier score, skill vs base rate, reliability bins (flagged insufficient < 20 samples); readiness per exam = importance-weighted weakest-dimension mastery, coverage and an uncertainty band; past-paper concept frequency boosts importance inside the confirmed scope only |
| T46 | `PUT/GET /grades/records/{id}/corrected-paper`, `POST /grades/comments/analyze` | student itemizes the paper (points per question ↔ concepts); only `confirmed: true` creates concept evidence (immutable afterwards); consistency check vs the recorded mark; comment analysis = keyword heuristic, `ai_used: false`; no external AI without consent/provider |
| T47 | `POST /grades/forecast`, `POST /grades/time-allocation` | seeded Monte-Carlo with marks fitted to the student's own results shrunk to 12±3, P(reach target), p10/p50/p90, labeled uncalibrated < 8 marks; time split maximizing Σw√m vs a proportional baseline, no grade gain promised |
| R12/R23 | `GET/PUT /concepts/prerequisites` (interim, catalog C1 will own it) | cycles rejected; planner places prerequisites first (hard, re-checked), blocks dependents when a prerequisite cannot be scheduled, warns on unscheduled out-of-scope prerequisites; CP-SAT defers to greedy when prerequisites exist |
| SMART_PLANNER | — | learned duration factor per activity type (EMA actual/estimated, ≥ 3 sessions, bounded 0.5–2) applied to non-task needs, reported in warnings |
| R01 | `PATCH /profile {"self_assessment": {...}}` | declared confidence 1–5 shifts the unknown-mastery ranking prior only, never mastery |
| T24 | `POST /quizzes/mock` | deterministic mock exam from the student's own bank: concept coverage first, past papers preferred, missing concepts named |
| T21 | `POST /tasks/decompose`, `POST /tasks/{id}/subtasks` | heuristic text split (numbered/bulleted/headings), deliverables flagged, effort proportional to text; proposal only until applied |
| R30 | `POST /account/export`, `DELETE /account {"confirm":"DELETE MY ACCOUNT"}` | full JSON export of every user-scoped table (no password hash); deletion of all user rows |
| R28 | `PATCH /profile {"preferences":{"notification_settings":{"email_address":…}}}` | opt-in email copy via SMTP env (`SMTP_HOST`, `SMTP_FROM`, …); status `not_configured`/`sent`/`failed`, never claimed when not configured |

Still not done (other owners): AI corrected-paper extraction and AI task decomposition (G's provider
adapter), authorized grade/timetable sync (C1), catalog-owned concept graph (C1), frontend (G),
fitted FSRS parameters (needs a review-log optimizer and enough history).

### Planner algorithm (docs/SMART_PLANNER.md)
Needs = open leaf tasks + confirmed-scope assessments (one need per concept, sized by
mastery) + learning needs (due reviews, weak concepts, due cards), minus minutes already
done/reserved. Priority `0.30U + 0.25I + 0.20G + 0.15F + 0.10E`, blended
`0.75·base + 0.25·A` with the academic signal A only when an objective applies. Order:
tight-slack needs first, then priority, slack, stable key. Placement into free time
(work windows − rest − busy events − kept sessions), within daily/weekly caps, min/max
session, one local day, before deadline, max one `max_session` per need per day; previous
slots reused when still valid. `check_invariants` re-verifies every hard constraint.

## Handoff

The Person-3 handoff (files owned by C1, contract deviations, risk register with owners, integration steps) is in
[`docs/handoff/P3_HANDOFF.md`](../docs/handoff/P3_HANDOFF.md). Generated contract artifacts:
[`p3_openapi.json`](../docs/handoff/p3_openapi.json), [`P3_ENDPOINT_INVENTORY.md`](../docs/handoff/P3_ENDPOINT_INVENTORY.md)
(`python -m tests.contract.handoff`). Uncalibrated values: [`docs/P3_HEURISTICS.md`](../docs/P3_HEURISTICS.md).
**Feature freeze:** no new Person-3 feature before the P0 integration (T13).

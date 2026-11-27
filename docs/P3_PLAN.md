# Person 3 (C3) — Delivery plan and step breakdown

Status: **PROPOSED**, awaiting human approval. Implementation status: see §6 (code on branch `Fan-y-h`, not yet merged). Scope: the C3 tickets from [BACKLOG](BACKLOG.md) and [AGENT_TEAM](AGENT_TEAM.md), plus the grade calculator. Coding rules: [P3_PLANNER_LEARNING_GUIDELINES](P3_PLANNER_LEARNING_GUIDELINES.md). Grade calculator design: [GRADE_CALCULATOR_DESIGN](GRADE_CALCULATOR_DESIGN.md). Existing documents are not modified by this plan.

## 1. Priority decision recorded here

| Item | Existing docs | This plan |
|---|---|---|
| Grade calculator core (T38 calculator, T39 objectives/required marks/scenarios, T40 academic signals, T42 academic E2E) | P0 ([MVP](MVP.md) S4, [BACKLOG](BACKLOG.md) T37–T42) | **P0 — follows `main`** |
| Everything else | unchanged | unchanged |

**Superseded (2026-11-27).** Person 3 first proposed moving the grade calculator to P1. The rule "when
documents disagree, `main` prevails" was then applied: the calculator stays **P0** as in MVP S4 and BACKLOG
T37–T42, and it was implemented as P0. No change to [DECISIONS](DECISIONS.md), [BACKLOG](BACKLOG.md) or
[MVP](MVP.md) is requested. Steps S9–S13 below therefore describe work that was in fact delivered with the P0
loop; their P1 label is historical.

Other owners are unchanged: C1 delivers T37 (academic contracts, migration, fixture) and G delivers T41 (`/grades` UI).

## 2. Ordering rules

1. P0 → P1 → P2. P1 starts only once the P0 acceptance in [MVP](MVP.md) is verified (step S8).
2. Within a priority, follow the ticket dependencies from [BACKLOG](BACKLOG.md).
3. Work against the T02 fixture until C1/G endpoints exist. Mocks are labeled.
4. Each step = one ticket or coherent slice, one branch/worktree, tests run, handoff written ([AGENT_TEAM](AGENT_TEAM.md) template).
5. Stop adding features before the final demo (G5); unfinished P1 stays documented as planned.

## 3. Timeline

Gate times are the illustrative 48 h from [ROADMAP](ROADMAP.md). Scale them to the confirmed event duration (O02), keeping the order.

| Gate | Time (example) | Priority | Steps | Gate exit |
|---|---|---|---|---|
| G0 | 0–2 h | P0 | S0 | contracts and fixture frozen |
| G1 | 2–6 h | P0 | S1, S2 | pure domain tests green on the fixture |
| G2 | 6–16 h | P0 | S3, S4, S5 | planner and learning work independently |
| G3 | 16–28 h | P0 | S6, S7, S8 | **end-to-end P0 loop passes** |
| G4 | 28–36 h | **P1** | S9, S10, S11, S12 | grade golden tests green; signals reach the planner preview |
| G5 | 36–48 h | P1 if time | S13, S14 | stable demo, known limitations documented |
| after the event | P1 → P2 | — | S15, S16 | — |

```text
S0 → S1 ─┬→ S3 → S4 ─┬→ S6 → S7 → S8 ══ P0 gate ══> S9 → S10 ─┬→ S11 → S13
         └→ S2 → S5 ─┘                               (needs T37)  └→ S12
```

## 4. Step breakdown

### P0 — MVP loop

| Step | Ticket | Goal | Deliverables | Depends on | Done when |
|---|---|---|---|---|---|
| **S0** | T02 review | agree calendar, task, session, evidence and mastery DTOs | handoff listing C3's contract needs (`PlanPreview`, `StudySession`, `StudyNeed`, `ReviewResult`, attempt/evidence DTOs) | C1 T02 | C1 has frozen the DTOs and the fixture used by C3 |
| **S1** | T09 prep | pure planner core | `planner/domain`: intervals `[start,end)`, availability (work windows, rest, caps, DST), priority score, greedy placement, invariant checker | S0 | unit + property tests: no overlap, caps, deadlines, determinism |
| **S2** | T10 prep | pure learning core | `learning/domain`: mastery EMA, interval heuristic, objective scoring (choice sets, T/F, fill, numeric tolerance) | S0 | unit tests: unknown vs 0, passive ≠ mastery, assisted weight, intervals |
| **S3** | T08 | calendar and task service | `planner` router/service/repository: events, assessments (scope confirmation), tasks, availability snapshot with revision | S1, C1 T03 (fixture until then) | an event change updates the revision and the constraints; imported events stay read-only |
| **S4** | T09 | greedy planner | `POST /planner/preview`, `POST /planner/accept`, `GET /plans/current`, `PATCH /sessions/{id}` (move/lock) | S3 | zero hard violations; lock conflict and infeasible cases reported; 409 on stale preview; no AI call |
| **S5** | T10 | learning persistence | decks/cards/review, quizzes/attempts/submit/result, evidence, mastery, `GET /mastery`, `GET /study-needs` | S2 | idempotent submit/review; one attempt = one evidence per item/dimension; no answer key in an active payload |
| **S6** | T11 | feedback and replan | `POST /sessions/{id}/start` and `/finish`, `POST /planner/replan` with a diff; partial sessions | S4, S5 | completed/locked/in-progress preserved; only remaining work rescheduled; diff with reasons |
| **S7** | T12 | analytics | read models: time, adherence, scores, mastery, session completion; `GET /analytics` | S3, S5, S6 | every value derived from persisted evidence/time; explicit denominators |
| **S8** | T13 | integration (with C1 and G) | real endpoints wired; demo path; restart test | S4–S7, C1 T05, G T06/T07 | import → plan → session → quiz → mastery → replan → restart passes. **P0 gate** |

### P1 — grade calculator first

| Step | Ticket | Goal | Deliverables | Depends on | Done when |
|---|---|---|---|---|---|
| **S9** | T38 | curriculum tree and calculator | `grades/domain`: tree validation, normalized weights, leaf weights, provisional mean + coverage; mark CRUD (actual/estimated) | S8, C1 T37 (the pure domain can start on the fixture) | 8 QCMs = 3.125 % each; 13 (coef 4) + 10 (coef 3) → 11.7142857; missing ≠ 0; invalid trees → 422 |
| **S10** | T39 | objectives, required marks, scenarios | objectives at any level (incl. "12 in every UE"), required mean, feasibility, bounds, assumptions, `POST /grades/required`, `POST /grades/simulate`, saved scenarios | S9 | target 12 → 10.6666667; guaranteed/impossible/no-remaining-weight cases; a simulation writes nothing |
| **S11** | T40 | academic signals in the planner | `GET /grades/signals`; blend into planner priority (α = 0.25) only with sufficient inputs | S10, S4 | soft rank only: locks, caps, deadlines and maintenance reviews unchanged; fallback to baseline |
| **S12** | T44 | verified validation rules | rule sets with provenance; official status `unknown` when rules are unverified | S10 | unknown rules never report a pass; an eliminatory minimum blocks compensation |
| **S13** | T42 | academic end-to-end (with C1 and G) | enter marks, set a goal, run a scenario, see the required mark and the planner preview change | S9–S11, G T41 | restart keeps actual data; scenario isolation; wrong user denied |
| **S14** | T23 / T24 / T26 | next P1, only if time | Focus Room backend; exam trainer with G; CP-SAT and FSRS adapters | S6 / S5 + G T06 / S4 + S5 | each ticket's acceptance in [BACKLOG](BACKLOG.md) |

### After the event

| Step | Priority | Tickets (dependencies) |
|---|---|---|
| **S15** | P1 | T35 quiz types (T06, T10), T22 notes/error notebook (T06, T10), T21 project decomposition (T08, T06), T25 notifications (T12), T45 grade-triggered replan (T40, T44, T11) |
| **S16** | P2 | T33 calibrated mastery (T26, T25), T46 corrected-script analysis (T45, T06, T22), T47 advanced optimizer (T44, T26) |

## 5. If time runs short

Cut in this order: S14 → S12 → S11 (keep the grade calculator without planner signals) → S13. Never cut S0–S8. They form the P0 loop of [MVP](MVP.md).

## 6. Delivery status (2026-11-27, branch `Fan-y-h`, not merged)

Evidence: `pytest backend/tests` and a live server scenario (see `backend/README.md`). "Done" means implemented
and tested against synthetic data in a local single-user setup; it is **not** an integration claim (T13).

| Step | Status | Notes / blocked by |
|---|---|---|
| S0 contract review | partial | P3 OpenAPI + inventory in [handoff/](handoff/P3_HANDOFF.md); **C1 must freeze T02/T37** |
| S1–S7 (T08–T12) | done | |
| S8 (T13) integration | **blocked** | needs C1 catalog/ingestion (T03–T05), G frontend/AI (T06/T07) |
| S9–S11 (T38–T40) | done (P0, see §1) | |
| S12 (T44) | done | ENSAI rules unknown (O12): only unverified rules can be entered today |
| S13 (T42) academic E2E | backend done | UI part is G (T41) |
| S14 (T23, T24, T26) | done | T24 mock generation is from the student's own bank (no AI) |
| S15 (T35, T22, T21, T25, T45) | done | T21 and T46 AI variants need G's provider adapter |
| S16 (T33, T46, T47) | done | uncalibrated; see [P3_HEURISTICS](P3_HEURISTICS.md) (O08) |

**Feature freeze (risk 10).** No new Person-3 feature until the P0 integration (T13) passes. Remaining Person-3
work: support T13, answer contract review, calibrate heuristics with the team.

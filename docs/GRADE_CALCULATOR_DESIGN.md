# Grade calculator — implementation design

Status: **PROPOSED**, awaiting human approval. This is the implementation design for the deterministic part of [GRADE_TRACKER](GRADE_TRACKER.md) (R35–R39, tickets T37–T41). It adds no new scope. It specifies the code architecture, algorithms and worked examples for:

- a curriculum tree: semester → UE → subject → assessment → sub-assessment, nested to any depth;
- coefficients at every level: the overall mark, inside each UE, inside each subject;
- **target marks** entered by the student (for example "12 in every UE");
- **actual marks** entered progressively, with the **required mark** recomputed for every assessment not yet graded.

Ownership follows [AGENT_TEAM](AGENT_TEAM.md): C3 (Person 3) owns `backend/app/modules/grades/**`; G owns `frontend/src/features/grades/**`; C1 owns the migration, contracts and fixtures. Delivery priority: **P0** (MVP S4, BACKLOG T37–T42); steps in [P3_PLAN](P3_PLAN.md) (S9–S13, delivered).

---

## 1. Domain model: one recursive tree

Everything is a **node**. Only leaves receive marks. Each non-root node has a weight relative to its siblings.

```text
Semester 5                                  (root)
├── UE Data Science            coef 5
│   ├── Machine Learning       coef 3
│   │   ├── Final exam         fraction 1/2
│   │   └── Continuous assessment  fraction 1/2
│   │       ├── Midterm        fraction 1/2
│   │       └── Quizzes        fraction 1/2
│   │           ├── QCM 1      fraction 1/8
│   │           ├── …
│   │           └── QCM 8      fraction 1/8
│   └── Statistics             coef 2
│       └── Final exam         fraction 1
└── UE Optimisation            coef 4
    └── …
```

| Field | Meaning |
|---|---|
| `kind` | `semester`, `unit` (UE), `subject`, `assessment_group`, `assessment` (leaf) |
| `weight` | positive `Decimal` |
| `weight_mode` (on the parent) | `coefficient`: any positive values, normalized by their sum. `fraction`: must sum to exactly 1 |
| `scale_min`, `scale_max` | leaf scale (default 0–20). Marks are converted to /20 before aggregation |
| `subject_id`, `event_id` | optional links to catalog and agenda (P1 entities, read-only) |

Normalized weight of a child: `ŵ = w / Σ(sibling weights)`. This is identical for both modes; `fraction` only adds the "sums to 1" validation.

**Leaf weight relative to a target node `n`:** `a_i = ∏ ŵ` along the path from `n` down to leaf `i`. For any node, `Σ a_i = 1`.

> Machine Learning example: Final `1/2`, Midterm `1/2·1/2 = 1/4`, each QCM `1/2·1/2·1/8 = 1/32 = 3.125 %`.

## 2. Three kinds of values, never mixed

| Value | Stored in | Can change actual results? |
|---|---|---|
| **actual** | `grade_records(kind=actual)` | is the actual result |
| **estimated** (personal forecast "I think I'll get 14") | `grade_records(kind=estimated)` | no |
| **simulated** (what-if) | `grade_scenario_values` | no. Never writes records, revision or plan |

A missing mark is `None` (absence). A mark of `0` is a real mark that counts.

## 3. Algorithms (pure functions in `calculator.py` / `objectives.py`)

For a target node `n`, with `K` = graded leaves, `R` = remaining (ungraded) leaves and `x_i` = mark on /20:

| Output | Formula | Edge cases |
|---|---|---|
| Coverage | `C = Σ_K a_i` | 0 when nothing is graded |
| Earned points | `S = Σ_K a_i·x_i` | |
| **Provisional mean** | `S / C` | `None` if `C = 0` |
| Final mean | `S` when `R = ∅` | |
| Forecast | include `estimated` leaves; null if any leaf has neither value | report forecast coverage |
| **Required mean** for target `T` | `r = (T − S) / Σ_R a_i` | if `Σ_R a_i = 0`: `achieved` or `impossible` from `S` |
| Feasibility | `r ≤ min` → `guaranteed`; `r > max` → `impossible`; otherwise `feasible`; missing weights or rules → `unknown` | `r` returned **unclamped** |
| Bounds | `[S + min·Σ_R a_i, S + max·Σ_R a_i]` | attainable interval |
| Marginal impact | +1 point on leaf `i` → `+a_i` on `n` | used by the planner (signals) |

**Never average child means.** A partially graded subject would be over-weighted. Always aggregate from leaves with coverage.

### 3.1 "Fix some marks, solve for the rest"
The student may set assumptions on some remaining leaves, for example "I expect 15 in the remaining QCMs". Treat those leaves as known (`S` += `a_i·assumed`) and solve `r` over the leaves still free. This is how the simulator answers "what do I need in the final?".

### 3.2 Several objectives at once ("12 in every UE" + "13 for the semester")
For targets of the form "mean ≥ T", every objective `O` gets its own `r_O`, computed independently. Then:

- **Joint feasibility = each objective feasible on its own.** Proof: give every remaining leaf `x_i = max(r_O for the objectives O above it)`. Each objective sees all its remaining leaves at ≥ `r_O`, so its mean is ≥ its target. If every `r_O ≤ 20`, every `x_i ≤ 20`. Conversely, an individually infeasible objective is jointly infeasible.
- **Per-leaf display "needed here" = `max r_O` over its ancestor objectives.** This is a *sufficient* uniform plan, labeled as such, not a unique minimum. Different combinations can trade one exam against another (simulator).
- An LP or CP-SAT solver is only needed later (P1/P2) to *optimize* combinations (for example minimal total effort). It is not needed for feasibility.

### 3.3 Number handling
- API input and output: `Decimal` serialized as strings (`"10.6666667"`), per [API_CONTRACTS](API_CONTRACTS.md).
- Internally, use `Decimal` arithmetic (as [GRADE_TRACKER](GRADE_TRACKER.md) requires) with a local context of at least 28 significant digits. Compare before any rounding, and quantize to 7 decimals only for output. The UI rounds to 2 decimals for display only.

## 4. Worked examples (these become golden tests)

**A — your UE example.** Target UE = 12. Subject A coef 2, actual 13. Subject B coef 1.5, not graded yet.
`a_A = 2/3.5 = 4/7`, `a_B = 3/7`. Provisional = 13, coverage = 57.14 %.
Required on B = `(12 − 4/7·13) / (3/7)` = **10.6666667** → `feasible`.
(This is the same ratio as the docs' 4/3 example, so it reproduces [TESTING](TESTING.md).)

**B — subdivided subject, coef 3.** Final 1/2; continuous assessment 1/2 = midterm 1/2 + 8 QCMs 1/2.
Marks so far: midterm 11; QCM 1–4 = 14, 16, 12, 18.
`C = 1/4 + 4/32 = 0.375`; `S = 11/4 + (14+16+12+18)/32 = 4.625`; provisional = **12.3333333**.
Target 12 → remaining weight 0.625 → required mean on the final + QCM 5–8 = **11.8**.
With the assumption "QCM 5–8 = 15", the required mark on the final = `(12 − 4.625 − 4/32·15) / 0.5` = **11**.

**C — "12 in every UE".** Two UEs (coef 5 and 4), each with its own `r`. The semester mean is then ≥ 12 automatically, because a weighted mean of values ≥ 12 is ≥ 12. A separate semester target of 13 adds a third `r`, and each leaf shows the max of the ones that apply to it.

## 5. Code architecture

Follows [ARCHITECTURE](ARCHITECTURE.md): the domain is pure Python, services orchestrate, routers only map DTOs. File list from [PROJECT_STRUCTURE](PROJECT_STRUCTURE.md).

```text
backend/app/modules/grades/
  domain/
    tree.py             # Node, CurriculumTree (frozen dataclasses), build + validate
    coefficients.py     # normalize siblings, leaf_weights(tree, node_id) -> dict[leaf_id, Decimal]
    calculator.py       # summarize(tree, marks, node_id) -> NodeSummary  (coverage, S, provisional, bounds, forecast)
    objectives.py       # required(tree, marks, objectives, assumptions) -> list[Requirement]
    simulator.py        # simulate(tree, actual, overlay) -> ScenarioResult  (actual untouched)
    validation_rules.py # official rules: MVP always returns "unknown" unless verified rule set present
    academic_optimizer.py # AcademicSignal per subject/assessment for the planner (impact, gap, r, feasibility)
  schemas.py            # Pydantic DTOs = contracts (Decimal as str)
  repository.py         # SQLAlchemy over academic_* tables (Protocol)
  service.py            # load tree+records → domain → persist/emit grade.* events
  router.py             # /api/v1/grades/* endpoints
  models.py             # ORM (migration authored by C1)
frontend/src/features/grades/
  api/                  # generated client hooks only
  components/           # CurriculumTreeEditor, WeightInput, MarkInput, ObjectiveBadge,
                        # RequiredMarkCell, CoverageBar, ScenarioPanel
  pages/                # GradesPage (/grades), ScenarioPage
```

Key signatures (all pure and deterministic):

```python
def leaf_weights(tree: CurriculumTree, node_id: NodeId) -> Mapping[NodeId, Decimal]: ...
def summarize(tree: CurriculumTree, marks: Mapping[NodeId, Decimal], node_id: NodeId) -> NodeSummary: ...
def required(tree: CurriculumTree, marks: Mapping[NodeId, Decimal],
             objectives: Sequence[Objective],
             assumptions: Mapping[NodeId, Decimal] = {}) -> RequirementReport: ...
```

`RequirementReport` contains a list of `{objective_id, node_id, target, required_mean, feasibility, bounds, reason_codes}` and a list of `{leaf_id, needed: max r_O, from_objective_ids}`.

**Validation (422)** happens in `tree.py`, before any computation: cycles, orphans, duplicate IDs, non-positive weights, `fraction` sums ≠ 1, out-of-scale marks, marks on non-leaf nodes, maximum depth (for example 8).

**Persistence:** use the tables `academic_programs`, `academic_nodes`, `grade_records`, `academic_objectives`, `grade_scenarios`, `grade_scenario_values` from [DATA_MODEL](DATA_MODEL.md). Every change bumps the program revision and emits `grade.recorded`/`curriculum.changed` through the outbox. The planner reads `academic_signals`.

## 6. API (already in the contract, [API_CONTRACTS](API_CONTRACTS.md) § Academic)

| Step in the user flow | Endpoint |
|---|---|
| Build or edit the UE/subject/assessment tree | `PUT /grades/programs/{id}/curriculum` (atomic, `expected_revision`) |
| Enter a received mark | `POST /grades/records` (`kind=actual`) |
| Set targets ("12 in each UE") | `POST /grades/objectives`, one per UE node. A bulk form `{node_kind:"unit", parent_id, target}` is a **contract request to C1** if needed |
| See means, coverage, required marks | `GET /grades/summary`, `POST /grades/required` |
| What-if | `POST /grades/simulate` (never writes records) |

## 7. UI flow (`/grades`)

1. **Configure:** an editable tree table (UE → subject → assessment → sub-assessment). For each parent, choose "coefficients" or "fractions"; the effective % of the subject and of the semester is shown live. Invalid sums are flagged inline.
2. **Targets:** "apply 12 to all UEs" fills each UE's objective. Override per node.
3. **Marks:** enter marks as they arrive. Each ungraded leaf shows **"needed: 11.8"** with a feasibility badge (guaranteed / feasible / impossible / unknown).
4. **Simulate:** fix hypothetical marks in a side panel and compare baseline vs projected. Reset or save as a named scenario. Actual marks stay untouched.
5. Missing, 0, estimated and simulated values are each visually distinct. "Official validation: unknown" stays visible until rules are verified.

## 8. Tests (from [TESTING](TESTING.md) § Academic)

- Golden: examples A, B and C above; 8 QCMs at 3.125 % each; path weights sum to 1.
- Missing vs 0; remaining weight 0 (achieved/impossible, no division by zero); `r < 0` → guaranteed; `r > 20` → impossible.
- Joint feasibility equals individual feasibility (Hypothesis property test on random trees).
- Monotonicity: raising any mark never lowers any mean or raises any `r`.
- A simulation leaves records, revision and plan unchanged.
- Invalid trees rejected (cycle, orphan, fraction sum ≠ 1, negative coefficient, out-of-scale mark).
- Wrong-user access denied; no mark values in logs.

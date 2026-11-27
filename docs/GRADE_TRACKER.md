# Grade Tracker & Academic Optimizer

Status: PLAN ONLY. Requirements source S4: user-supplied `Texte collé(20261127-131527).txt`, sections 1–12 (2026-11-27). All examples are synthetic, not official ENSAI regulations. This addition extends, not replaces, R01–R34. See [REQUIREMENTS](REQUIREMENTS.md), [DATA_MODEL](DATA_MODEL.md), [API_CONTRACTS](API_CONTRACTS.md), [SMART_PLANNER](SMART_PLANNER.md) and [ADAPTIVE_LEARNING](ADAPTIVE_LEARNING.md).

## 1. Academic records and configuration (R35–R36)
[REQUIREMENT] Track academic year, cohort, specialization, semester, teaching units (UE), subjects, coefficients, assessments (written exams, continuous assessment, quizzes, labs, projects, final exams), dates, actual marks, grading weights, validation thresholds, compensation and possible eliminatory marks. Authorized Pamplemousse or another permitted academic source is preferred; all fields must be manually configurable when unavailable. Keep institutional rules separate from personal targets. No upstream writes. Configure recursively nested assessment groups, add/edit/delete components and validate sibling weights. Persist user overrides and provenance; review conflicts with imported records.

[RECOMMENDATION] A versioned curriculum tree has kinds year, semester, unit, subject, assessment_group, assessment. Each non-root node has positive sibling-relative weight; normalize coefficients within each parent. Assessment groups may nest arbitrarily (bounded depth for safety). Link subjects to existing catalog IDs and assessments to existing calendar events. Use explicit weight mode `coefficient` or `fraction`; fractions sum to 1 within each group, coefficients need not. Reject cycles, missing parents, invalid scales, duplicate IDs and incomplete configurations; use Decimal arithmetic and configurable display rounding only. Store a JSON/YAML seed separately from deterministic calculation logic; SQLite remains the MVP store; PostgreSQL is a later migration option.

```yaml
academic_year: "2026-2027"
semester: 5
specialization: ID
units:
  - id: ue_data_science
    name: Data Science
    coefficient: 5
    subjects:
      - id: machine_learning
        coefficient: 3
        assessments:
          - {id: final_exam, weight: 0.5}
          - id: continuous_assessment
            weight: 0.5
            components:
              - {id: midterm, weight: 0.5}
              - id: quizzes
                weight: 0.5
                components:
                  - {id: qcm_1, weight: 0.125}
                  - {id: qcm_2, weight: 0.125}
                  - {id: qcm_3, weight: 0.125}
                  - {id: qcm_4, weight: 0.125}
                  - {id: qcm_5, weight: 0.125}
                  - {id: qcm_6, weight: 0.125}
                  - {id: qcm_7, weight: 0.125}
                  - {id: qcm_8, weight: 0.125}
```
Each quiz is 0.5 × 0.5 × 0.125 = 3.125% of the subject. A unit may have coefficient 5 and another 4; subjects inside the first unit may have coefficients 3 and 2. All coefficients are fictitious.

## 2. Deterministic calculator (R37)
For target node n, normalize each sibling weight and multiply along paths to derive leaf weights a_i (sum 1). Convert marks to a common scale (default /20; confirm official scale). Weighted mean M = sum(a_i x_i). For known leaves K, coverage C = sum_K a_i; provisional mean = sum_K(a_i x_i)/C if C > 0, else null. Do not average provisional child means without propagating coverage: that can overweight partially assessed branches. Forecast includes explicitly estimated leaves; return forecast coverage and null full forecast if any remain unspecified. Simulated values live only in scenario overlays; official actual marks are immutable by simulation. Zero is a valid actual mark; absence is null. Annual aggregation must follow configured semester weights and institutional rules, not assume equal weighting. Official rounding, retakes, exemptions, missing/absent handling and compensation are unverified.

## 3. Objectives, required grades and scenarios (R38–R39)
Targets at year, semester, UE, subject, or assessment level (e.g. pass year, semester 14/20, every UE 12/20, DL 15/20, next exam 14/20). Distinguish numeric personal objectives from institutional validation. For target T and remaining leaves R, required average = (T - sum_K a_i x_i) / sum_R a_i; if remaining weight is zero, classify achieved or impossible using actual mean. Return unclamped required value and feasibility; below scale minimum means target guaranteed under weighted-average model, above maximum means impossible. Bounds from assigning all remaining leaves min/max. With a single variable, hold other assumed values explicit; multiple remaining assessments require alternative combinations rather than arbitrary unique minimums. For multiple objectives, use linear inequalities with bounded variables (LP or integer-scaled CP-SAT if discretized); distinguish infeasible from unknown due to missing rules or weights. Return assumptions, feasible examples and trade-offs; scenarios must never persist as actual grades.

Synthetic check: A=13 weight 4, B=10 weight 3 gives 11.7142857; B needs 10.6666667 for target 12. This reconstructs the illustrative source values, not an official coefficient set. If B=0 actual, include it; if B missing, provisional=13 and coverage=4/7. No rounding before feasibility comparisons.

What-if use cases: minimum next exam, effect of 8/20, compensation under confirmed rules, subjects to prioritize, feasibility of 14/20, impact of two additional points, and UE validation risk. Allow saved named scenarios with separate revisions, reset/compare, and clear distinction actual/estimated/simulated/ungraded. Show gaps and attainable interval. Scenario selection alone must not alter the live plan.

## 4. Validation engine and academic risk (R40)
Rules are versioned, scoped by curriculum and include provenance, verification state, thresholds, compensation groups and eliminatory minima. Use deterministic rules, never LLM arithmetic or inferred official regulations. Return `validated`, `not_validated`, `pending`, `unknown` for official status; distinguish possible/guaranteed/impossible under remaining grade bounds from actual validation. If rules unavailable, only evaluate personal targets and weighted-average scenarios, label official status unknown. A low mark cannot be compensated unless verified rules permit it. P1 implements verified rule types and risk explanations; unsupported rules remain unknown, never silently pass. P2 probabilistic forecasts require calibrated evidence and uncertainty, not guessed grade gains.

## 5. Academic Optimizer and planner integration (R41)
For assessment e, impact I_e is the product of normalized UE, subject and assessment weights (include every nested level and semester weight if relevant). Under a linear mean, +1 point raises the target mean by I_e; official nonlinear conditions need separate validation checks. Emit academic signals by subject/assessment: effective weight, actual/forecast coverage, goal gap, required grade, feasibility, risk/status, deadline, uncertainty, rationale. Combine these with urgency, mastery, forgetting risk, workload and prerequisites; high weight alone is not high priority. Never equate estimated study time with guaranteed grade improvement.

[RECOMMENDATION] Use academic score only when sufficient validated inputs exist; otherwise retain original priority. E.g. A = 0.35 normalized impact + 0.35 normalized target gap + 0.30 verified risk (renormalize available components; unknown risk omitted); blend base planner score with A using configurable alpha=0.25, initially uncalibrated. Keep mandatory constraints, locked/completed sessions, rest, caps, deadline and minimum maintenance reviews unchanged. Infeasible targets trigger explanation and alternative personal targets, not excess study load. Reserve reviews for strong subjects; prioritize poorly mastered high-impact topics or threatened units; low-weight exam may still matter for an eliminatory rule. If capacity insufficient, show trade-offs and unscheduled effort. Recalculate on actual grade/weight/objective/rule update; P0 manual preview consumes signals, P1 automatic dirty/replan preview; accept via existing revision check. Strong results can release some unstarted capacity, poor results can add targeted exercises after diagnosis. Explain each change with concrete inputs, not invented probabilities.

## 6. Adaptive learning and analytics (R42–R43)
A global mark is not concept-level evidence. A poor grade creates a subject-level diagnostic recommendation; only linked rubric/question evidence, verified corrected paper or diagnostic attempts update specific mastery. Student may import corrected scripts, teacher comments or manually report difficulties. Route calculation errors to practice, forgotten formulas to recall/cards, conceptual issues to explanations/video/animations, code issues to debugging, repeated mistakes to Error Notebook, lack of autonomy to unassisted work. Good demonstrated performance increases difficulty and review intervals; preserve uncertainty. AI may extract tentative error-to-concept links with citations and user confirmation; never execute submitted code. Consent required before sending grade/copy data externally.

Academic Dashboard: year/semester/UE/subject/assessment drill-down; actual/provisional/forecast/simulated values, coverage, coefficients, goals, gaps, upcoming exams, weighted grade trends, risk and actionable planner explanations. Clearly label missing records and synthetic fixture data. Distinguish academic metrics from practice quiz scores and estimated mastery. Example synthetic priorities: ML 8/20 vs 12 target, 50% assessed; DL 14 vs 13, 60%; Optimization 10 vs 12, 40%; Statistics 16 vs 12, 80%; Data Engineering 15 vs 14, 75%. Source flat subject coefficient example (4,3,3,2,4) yields 12.25/20, not the illustrated 12.44; use calculated result and label flat provisional-subject average as distinct from hierarchical leaf coverage.

## 7. Module boundary and delivery
`grades` owns curriculum, marks, rule evaluation, targets and scenarios; `planner` owns time allocation; `learning` owns mastery; `analytics` builds read models; `ingestion` owns authorized adapters; `ai` owns optional explanations and corrected-script analysis. No cross-module writes. See [PROJECT_STRUCTURE](PROJECT_STRUCTURE.md) and [AGENT_TEAM](AGENT_TEAM.md).

P0 (explicit S4 requirement): manual curriculum/UE/coefficients, nested assessment weights, actual grades, means, goals, required grades, scenario simulation, planner integration. P1: automatic grade/weight sync, risk/validation, personalized recommendations, automatic grade-triggered replan and adaptive integration. P2: corrected-paper AI analysis, probabilistic forecast, advanced time optimization. P0 academic UI is minimal but usable; expanded charts P1. Do not drop baseline P0; expanded scope requires human capacity review. See [MVP](MVP.md), [BACKLOG](BACKLOG.md), [TESTING](TESTING.md).

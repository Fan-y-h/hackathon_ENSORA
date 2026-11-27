# Person 3 — register of uncalibrated heuristics (open decision O08)

Status: **PROPOSED values, awaiting human validation.** [DECISIONS](DECISIONS.md) O08 assigns C3 the job of
confirming mastery weights, repetition policy, planner priority and workload limits "on scenarios before
production claims". None of the values below has been calibrated on real student data. Every one of them
is a named constant in code; `backend/tests/unit/test_heuristics_register.py` fails if this table and the
code drift apart.

Source column: **doc** = value proposed in the shared design docs; **P3** = assumption introduced during
implementation (needs the most scrutiny).

| Area | Code (`app.modules.` …) | Value | Source | Effect / how to validate |
|---|---|---|---|---|
| Planner priority | `planner.scheduling.W_URGENCY` | `0.3` | doc (SMART_PLANNER) | weight of `1/(1+days)`; compare plans on synthetic weeks |
| Planner priority | `planner.scheduling.W_IMPORTANCE` | `0.25` | doc | |
| Planner priority | `planner.scheduling.W_GAP` | `0.2` | doc | unknown mastery uses a 0.5 prior (ranking only) |
| Planner priority | `planner.scheduling.W_FORGETTING` | `0.15` | doc | |
| Planner priority | `planner.scheduling.W_EFFORT` | `0.1` | doc | |
| Academic blend | `planner.scheduling.ACADEMIC_ALPHA` | `0.25` | doc (GRADE_TRACKER §5) | only when an objective or verified rule applies |
| Breaks | `planner.scheduling.BREAK_MIN` | `0` | P3 | breaks come from profile rest windows; 10 made the doc golden example infeasible |
| Prep estimate | `planner.plan_service.CONCEPT_ESTIMATE` | `{'unknown': 60, 'learning': 60, 'weak': 45, 'acquired': 25, 'consolidate': 25}` | P3 | minutes per confirmed concept before an exam |
| Prep estimate | `planner.plan_service.NO_SCOPE_ESTIMATE` | `{'easy': 120, 'medium': 180, 'hard': 300, None: 180}` | P3 | exam without concept list (flagged `ESTIMATE_HEURISTIC`) |
| Importance | `planner.plan_service.IMPORTANCE` | `{'easy': 0.6, 'medium': 0.75, 'hard': 0.9, None: 0.75}` | P3 | assessment difficulty → I |
| Duration learning | `planner.plan_service.ESTIMATE_ALPHA` | `0.3` | P3 | EMA of actual/estimated per activity type |
| Duration learning | `planner.plan_service.ESTIMATE_MIN_OBS` | `3` | P3 | observations before adjusting |
| Duration learning | `planner.plan_service.ESTIMATE_BOUNDS` | `(0.5, 2.0)` | P3 | clamp of the factor |
| Diagnostics | `planner.plan_service.DIAGNOSTIC_WINDOW_DAYS` | `7` | P3 | deadline given to grade diagnostics / error consolidation |
| Lookahead | `planner.plan_service.ASSESSMENT_LOOKAHEAD` | `datetime.timedelta(days=14)` | P3 | exams beyond the horizon still get preparation |
| CP-SAT grid | `planner.cpsat.SLOT_MIN` | `15` | doc | coarser grid can lose coverage vs greedy (then greedy is kept) |
| CP-SAT size | `planner.cpsat.MAX_CHUNKS_PER_NEED` | `8` | P3 | solver size bound |
| Mastery EMA | `learning.domain.ALPHA` | `0.35` | doc (ADAPTIVE_LEARNING) | |
| Mastery EMA | `learning.domain.ASSISTED_FACTOR` | `0.5` | doc | assisted success counts half |
| Mastery status | `learning.domain.WEAK_THRESHOLD` | `0.5` | doc | |
| Mastery status | `learning.domain.ACQUIRED_THRESHOLD` | `0.7` | doc | |
| Mastery status | `learning.domain.MIN_EVIDENCE_FOR_ACQUIRED` | `2.0` | P3 | "sufficient evidence" made concrete |
| Flashcards | `learning.domain.CARD_EVIDENCE_WEIGHT` | `0.7` | P3 | self-rated recall weaker than auto-graded items |
| Flashcards | `learning.domain.RATING_SCORE` | `{'again': 0.0, 'hard': 0.6, 'good': 0.85, 'easy': 1.0}` | P3 | rating → evidence score |
| Study needs | `learning.service.REVIEW_SESSION_MIN` | `20` | P3 | |
| Study needs | `learning.service.PRACTICE_SESSION_MIN` | `30` | P3 | |
| Study needs | `learning.service.REVIEW_WINDOW_DAYS` | `3` | P3 | review window, closes at local end of day |
| T33 | `learning.calibration.DELAYED_RECALL_DAYS` | `1.0` | P3 | delay that counts as retention evidence |
| T33 | `learning.calibration.DERIVED_WEIGHT` | `0.8` | P3 | weight of derived retention/autonomy evidence |
| T33 | `learning.calibration.MIN_CALIBRATION_SAMPLES` | `20` | P3 | below: calibration flagged insufficient |
| Academic signal | `grades.domain.IMPACT_WEIGHT` | `Decimal('0.35')` | doc | |
| Academic signal | `grades.domain.GAP_WEIGHT` | `Decimal('0.35')` | doc | |
| Academic signal | `grades.domain.RISK_WEIGHT` | `Decimal('0.30')` | doc | verified rules only |
| Rule risk | `grades.validation_rules.RISK_SCORE` | `{'none': Decimal('0'), 'low': Decimal('0.25'), 'medium': Decimal('0.5'), 'high': Decimal('1')}` | P3 | |
| Diagnostics | `grades.academic_service.DEFAULT_ALERT_THRESHOLD` | `Decimal('10')` | P3 | mark below → diagnostic when no objective |
| Diagnostics | `grades.academic_service.DIAGNOSTIC_MIN` | `30` | P3 | |
| Diagnostics | `grades.academic_service.DIAGNOSTIC_WINDOW` | `datetime.timedelta(days=30)` | P3 | marks older than this stop generating diagnostics |
| T47 forecast | `grades.forecast.PRIOR_MEAN` | `12.0` | P3 | shrinkage prior |
| T47 forecast | `grades.forecast.PRIOR_SD` | `3.0` | P3 | |
| T47 forecast | `grades.forecast.PRIOR_STRENGTH` | `3.0` | P3 | pseudo-marks of the prior |
| T47 forecast | `grades.forecast.MIN_MARKS_FOR_CALIBRATION` | `8` | P3 | label threshold only — not a validation |
| Error notebook | `workspace.service.RECURRING_THRESHOLD` | `2` | P3 | occurrences before a consolidation need |
| Error notebook | `workspace.service.CONSOLIDATION_MIN` | `25` | P3 | |
| Focus Room | `workspace.service.MODES` | `{'classic': (25, 5), 'long': (50, 10), 'deep': (90, 15), 'free': (None, None)}` | doc (R26) | |

FSRS uses the published FSRS-4.5 default parameters (`learning.fsrs.W`); fitting them needs a review-log
optimizer and enough history (not available).

## Validation protocol proposed to the team
1. Fix 3–5 synthetic student weeks (agenda, marks, quiz history) as golden scenarios.
2. For each, a human reviewer states the plan they would expect; compare ordering, unscheduled work and
   per-day load with the planner output; adjust one weight at a time.
3. Record accepted values and the date in [DECISIONS](DECISIONS.md) (C1 edits shared docs).
4. Once real (consented) usage exists, use `GET /learning/calibration` (Brier skill > 0 expected) before
   claiming any calibration.

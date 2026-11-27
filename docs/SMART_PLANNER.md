# Smart Planner — constrained scheduling

R10–R13, R23–R24. LLM may propose concepts and workload; deterministic scheduling owns feasibility. Plan all subjects and personal tasks together. Source assessment scope requires confirmation when uncertain.

## Inputs and outputs
Input snapshot: timezone, horizon, classes/assessments/personal busy intervals, work windows, breaks/meals/rest, daily/weekly maximum minutes, min/max session length, preferred activity formats/hours, locked and completed sessions, tasks (deadline/remaining effort), assessment scope, concept mastery/due dates, prerequisites, importance/difficulty, estimated work and revision numbers. Use integer minutes; intervals are half-open [start,end). Normalize recurrence and DST in local timezone before conversion to UTC. Reserve future repetitions within rolling horizon.

Output PlanPreview: input_revision, ordered sessions, unscheduled needs (reason, remaining_min, next best option), warnings, summary of workload/coverage. Session contains subject, assessment, concepts, objectives, activity type, resources, generation spec (not artifact generation), duration, priority, difficulty, success criteria, planned repetition/due date. Accept preview atomically only if snapshot still current.

## Hard constraints
- No overlap with busy intervals or other sessions; honor locked/completed sessions; report pre-existing impossible locks rather than silently moving them.
- Respect work windows, meals, breaks/rest, daily/weekly caps, min/max activity durations and confirmed prerequisites; deadline before assessment/assignment, never schedule past deadline as if valid.
- Respect personal exclusions; no schedule in past; split only splittable tasks, preserve minimum chunks and remaining effort. Do not exceed limits to cover infeasible workload.

## Priority and objective [RECOMMENDATION]
Normalize urgency U (e.g. `1/(1+days_to_deadline)`), importance I, mastery gap G (unknown flagged, use 0.5 prior only for ranking), forgetting risk F, and effort pressure E (remaining minutes / feasible available minutes clipped to 1). Initial score `0.30 U + 0.25 I + 0.20 G + 0.15 F + 0.10 E`. Weights are assumptions, editable and tested. User priority and mandatory task deadlines can override soft rank but not hard feasibility. Optimize weighted coverage, readiness, due-date adherence, workload balance, preferred hours, subject/method alternation, difficulty progression and minimal schedule churn. Historical exam frequency informs I only within current syllabus.

## MVP deterministic algorithm
1. Normalize calendar, subtract busy/rest/locked intervals, calculate daily and weekly remaining budgets. Validate all locks and emit conflicts.
2. Build study needs from confirmed assessment scope, remaining tasks and adaptive next activities. Topologically order prerequisite DAG; detect cycles and request resolution. Generate future review needs with earliest/latest desirable windows; cap estimate to realistic horizon.
3. Sort eligible needs by deadline slack, priority and stable ID, considering predecessor placement. Search feasible slots in preferred windows before deadline, with review due window and day budget. Split if allowed; reserve required break and update capacity. If no slot, record reason; never silently discard.
4. Recheck all invariants independently. Return preview, total/unscheduled minutes, per-day load, coverage and explanation. No provider calls during scheduling. Stable tie breaks ensure deterministic result for fixed inputs.

## CP-SAT evolution
OR-Tools optional after baseline: discretize to configurable 15-minute slots (round up durations); binary assignment x[need,slot], optional interval variables, no-overlap and capacity constraints, precedence, due windows, locks fixed. Maximize weighted scheduled minutes/coverage minus lateness relative to soft review windows, load imbalance, preference and displacement penalties. Deadline is hard; unscheduled indicator allows feasible partial plan. Bound solve time, retain feasible incumbent or use greedy fallback. Report solver status, not claim optimum when timed out.

## Dynamic replan
Triggers: source changes, new/moved/cancelled exam, new task, changed preference/availability, scope, missed/partial/interrupted session, new mastery evidence. Mark dirty, debounce bursts, calculate impacted unstarted sessions; freeze completed/in-progress and locked, penalize unnecessary moves. Partial sessions schedule only remaining work. User can preview diff (added/moved/removed/reason), accept or keep plan. Never auto-overwrite manual decisions. Work-duration update: compare active time vs estimate; proposed bounded EMA per activity type; require enough observations, do not treat breaks as work.

## Example and tests
Synthetic exam in two days, 90 minutes free today, 60 tomorrow, 120 minutes of learning needs and 30-minute review: feasible if no prerequisite/time conflicts. Add 60-minute personal busy event tomorrow; report 60 minutes unscheduled instead of violating daily cap. Test DST, all-day event, overlapping source events, lock conflict, insufficient capacity, cancelled exam, prerequisite cycle, stable repeat, duplicate replan, no AI generation at preview, missed/partial and completed preservation. See [API_CONTRACTS](API_CONTRACTS.md).

## Academic optimization extension (R41)
Consume a read-only academic signal snapshot from `grades`; never calculate official validation with an LLM. See [GRADE_TRACKER](GRADE_TRACKER.md) sections 3–5 for normalized path weights, required grade, feasibility, risk and proposed bounded blending formula. The previous score remains the fallback when data are unavailable; academic importance does not override hard constraints or spaced-review maintenance. P0 preview uses academic weights and goals; P1 grade/coefficients/objective/rule events mark plan dirty and trigger a revisioned preview, preserve locks/completed work, explain additions/moves and require existing acceptance flow. Strong subjects retain consolidation; poor grade without concept-level evidence schedules diagnostic activity, not a fabricated mastery deficit. If target is impossible, report it and propose alternatives without violating caps. Scenario overlays are opt-in preview inputs only, not live grades. P2 OR-Tools may optimize weighted coverage and risk proxies; do not promise grade gains per hour.

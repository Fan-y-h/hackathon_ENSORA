# Adaptive learning and spaced repetition

R20–R23, R26–R27. Keep knowledge estimation, repetition scheduling and calendar placement separate. All scores are pedagogical estimates, not validated psychometric measurements.

## Learning profile and evidence
Profile inputs: cohort/track/year/subjects/goals, preferred methods/formats/duration/hours, timetable/rest, assessment scope/date/format/difficulty, current material/prerequisites, historical exam patterns, declared confidence, diagnostic quizzes, flashcard reviews, quiz item scores and response time, exercise/TP/exam reasoning/calculation/code errors and independence, focus active time/objectives, error notebook and planned/missed/partial sessions. Retain per-concept/per-dimension evidence history, estimated mastery, confidence, last review, review count, trend, forgetting risk and due date.

Dimensions: theoretical understanding, memory, mathematical reasoning, practical application, implementation, autonomy, long-term retention. Not all apply to every concept. Map each question or activity to one or more applicable dimensions with explicit weights. Passive reading, video view, timer completion and self-confidence do not increase objective mastery; they affect recommendations only. Assisted success has lower evidentiary weight and triggers independent practice.

## MVP mastery estimator [RECOMMENDATION]
Unknown until assessed. Each objective observation `score ∈ [0,1]`, weight based on rubric quality, difficulty and independence (e.g. assisted factor 0.5). Maintain weighted evidence with bounded recency factor; use transparent weighted mean, or EMA `m_new=(1-alpha*w)*m_old+alpha*w*score` with alpha=0.35, w in [0,1]. First objective evidence initializes score, not self-assessment. Confidence based on count/diversity, e.g. `min(1,n_effective/5)`, independently display low evidence. Example status: unknown, learning (<0.5), weak (0.5–0.69), acquired (>=0.7 with sufficient evidence), consolidate if overdue/low retention. Thresholds are tunable and not scientifically calibrated. Record algorithm version and evidence IDs so recompute is deterministic. Item scores cannot double count on retries. Retention requires delayed recall evidence; autonomy needs unassisted task.

## Repetition engine
Learning stages: discovery (read/sheet/audio/video), comprehension (proof/examples/animation), active recall (cards/open/formulas/proof), application (TD/TP/calculation/code), consolidation (targeted quiz/error notebook), timed training (past/mock exam), long-term retrieval. Alternate stages rather than flashcards only.

Initial review heuristic: Again → 1 day, Hard → max(1, round(previous_interval*1.2)), Good → max(2, round(previous*2)), Easy → max(3, round(previous*3)); first successful review baseline 1 day. Repeated failures shorten and recommend explanation; clamp to assessment deadline for pre-exam activity but retain long-term review afterward. Store due_at, interval, rating, history and version. Risk proxy `1-exp(-elapsed_days/max(interval_days,1))` is scheduling heuristic, not true probability. Later integrate FSRS via version-pinned adapter and review logs; avoid claiming FSRS is implemented by heuristic. Planner reserves slots from due windows and may surface overdue reviews.

## Assessment and exam integrity
Single/multiple-choice exact set grading (partial credit only explicit rubric), T/F, normalized fill, numeric with specified tolerance/units, open/code answers use rubric and optional authorized AI feedback labeled provisional. No arbitrary code execution. Store question version and immutable attempts; server timer and expiration, no private answer, solution citation or AI access during active exam in app. Block tutor/studio and correction endpoints for active exam scope or globally in MVP; external tools cannot be controlled. Submit/expire once, reveal rubric only after completion, log errors and recommendations. Mock matches historical format/difficulty but current syllabus authority; verify marks sum.

## Adaptation rules
| Observation | Next action |
|---|---|
| New concept or missing prerequisite | Discovery/comprehension/application; prerequisite first |
| Misunderstanding | Alternate explanation, example/video/animation |
| Forgotten definition/formula | More recall, shorter interval |
| Proof difficulty | Break reasoning into progressive reconstruction |
| Application/programming difficulty | Targeted increasing difficulty, code explanation/debugging |
| Recurring error | Error notebook, targeted consolidation |
| Assisted success | Independent exercise |
| Stable successful evidence | Increase difficulty, extend interval |
| Previously acquired but forgotten | Shorten interval |
| Missed/partial or underestimated work | Replan remaining only, adjust future estimates |
| Near exam or changed syllabus | Prioritize essential current topics, typical exercises, refresh scope |

Return `StudyNeed(concept_id, dimension, activity_type, priority_factors, estimated_min, due_window, reason, resource_ids, success_criteria)` to planner. Analytics derive from persisted attempts and actual active intervals; no fabricated progress. Tests: passive completion, assisted vs unassisted, incorrect answer, repeat intervals, unknown mastery, duplicate submission, exam key protection, confidence distinction, overdue and prerequisite recommendations.

## Academic feedback extension (R42)
An official mark is subject-level evidence only unless rubric/question-to-concept evidence is available. A low result requests diagnostic quiz or user review of corrected script/comments; never directly decrease all concept mastery estimates. Confirm extracted errors and link to existing evidence IDs, then use existing update rules. Route calculation, formula recall, comprehension, programming, recurring errors and autonomy gaps to respective exercise/recall/explanation/debugging/error notebook/unassisted activities. Demonstrated strong performance supports longer intervals and harder tasks. Practice quiz grades are not official marks. See [GRADE_TRACKER](GRADE_TRACKER.md).

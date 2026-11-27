# Hackathon MVP

[RECOMMENDATION] Prioritize a working vertical slice, not breadth. Full requirements remain in [REQUIREMENTS](REQUIREMENTS.md). Duration and official judging criteria unverified; use this as proposed acceptance baseline. A local app with real ingestion/retrieval/planning and persistent assessment feedback must work even if institutional APIs are unavailable.

## P0 — must demonstrate
| Capability | Minimum acceptance |
|---|---|
| Local setup and profile | Clean install/seed, user/cohort/track preferences and work limits persist |
| Source ingestion | Local PDF/TXT and normalized calendar/assignment fixture or authorized ICS import; repeat import creates no duplicate; visibly distinguish fixture from live connector |
| Subject/library | At least two synthetic subjects, resources grouped, keyword/semantic retrieval and preview, provenance |
| Agenda | Classes, deadlines, exam and busy events; ambiguous assessment confirmation; update event changes plan inputs |
| RAG Tutor | Selected source question returns grounded response and resolvable citation; missing evidence reported; one verified provider if available, mock clearly labeled otherwise |
| Studio | Revision sheet + quiz/flashcard structured generation, save and reuse on restart; no generation during planning |
| Planner | Confirm scope, generate feasible schedule with tasks, review needs and no overlaps; lock and partial/infeasible case; preview/accept replan |
| Learning | Answer quiz or card, persist result, update concept mastery/due date; replan uses new evidence |
| Dashboard/analytics | Real session/attempt results, upcoming exam/tasks and changed plan visible |
| Reliability | Local persistence, smoke tests, no keys/private course content in Git, clear setup README and demo instructions |

## P1 — after P0 green
Authorized live Moodle/Drive/Pamplemousse connector(s), incremental sync controls, Google Calendar/personal Drive, more formats, stronger concept extraction and entity resolution, additional tutor modes, task project decomposition/Kanban, Smart Notes/Error Notebook, Focus Room, exam mode and mock generation, richer analytics and notifications, CP-SAT and FSRS, additional provider adapters. If connector access is unavailable, prioritize authorized imports and document the limitation rather than fake success.

## P2 — full-product extensions
Podcasts, rendered videos, parameterized animations, interactive mind maps, infographics, richer personalized exercises/corrections, additional provider routing and quotas, community sharing/moderation/server, institutional auth, advanced knowledge tracing, mobile-friendly enhancements. All remain scoped requirements or earlier suggested extensions; no silent removal.

## Jury demonstration (proposed)
1. Start local demo with labeled synthetic data, show subject and calendar import; add/change an assessment.
2. Confirm scope and inspect resources. Ask a source-grounded question; open cited passage.
3. Generate plan respecting busy times and daily caps. Show a warning for insufficient time and accept feasible plan.
4. Start a session, generate a revision sheet/quiz on demand; answer a question incorrectly; inspect correction/mastery/next review.
5. Preview and accept replan; show changed priority and preserved completed/locked sessions. Restart and verify retained state.

Deliverables from prior proposed brief: local application, GitHub repo/docs, connectors or imports, database/pipeline, RAG, planner, quizzes/cards, dashboard, demo dataset, architecture diagram, functional tests, README, presentation/pitch. Do not claim official rubric or live integration without evidence. If no AI credentials, demo deterministic fixture output labeled mock and identify live provider as incomplete; actual AI acceptance requires live verified call.

## Mandatory S4 academic scope amendment
P0 addition: manual UE/subject curriculum and recursive assessment weights, actual marks, weighted/provisional means, academic goals, minimum required marks, what-if scenarios and academic signals consumed by Smart Planner. Demonstrate a synthetic nested program, change a hypothetical score without altering actual results, show feasibility and a constraint-respecting priority change. No official validation claim absent verified rules. P1: authorized grade/weight sync, risk/compensation engine, personalized reasons, automatic replan on new grades, adaptive diagnostic integration. P2: corrected-script AI, probabilistic results, advanced study-time optimization. Existing P0 remains; expanded workload requires team review rather than silent scope reduction.

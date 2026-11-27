# Roadmap and gates

[ASSUMPTION] Illustrative 48-hour hackathon schedule from earlier proposal, not confirmed duration. Scale timeboxes to actual event; preserve dependency order and stop adding features before final demo. P0 is a vertical slice across streams.

| Gate | Example time | Codex Data / Integration | Gemini UI + AI | Codex Planner / Learning | Exit condition |
|---|---|---|---|---|---|
| G0 | 0–2h | inspect repo, approve ADRs, auth/provider/source checks | read docs, agree routes/DTOs | agree calendar/evidence constraints | contract and ownership frozen; no secrets shared |
| G1 | 2–6h | bootstrap DB/API, synthetic fixture, migrations | shell, typed mock client, provider adapter skeleton | pure domain tests, planner inputs/evidence types | all worktrees build against fixture |
| G2 | 6–16h | import/parse/catalog/index/search | library/agenda/tutor/Studio, structured artifacts | greedy scheduler, tasks, review and quiz grading | data import/search and planner work independently |
| G3 | 16–28h | integration, source change events, RAG quality | real API wiring, citations, job/error states | persisted attempts/mastery, replan, analytics | end-to-end loop passes |
| G4 | 28–36h | optional authorized connectors, harden restart | UI polish, accessibility, optional Focus/Notes | lock/partial/infeasible/exam mode if time | P0 acceptance verified; P1 only if green |
| G5 | 36–48h | merge, smoke, backup, release tag | demo rehearsal, pitch visuals | edge cases, demo evidence, regression | clean setup, stable demo, known limitations |

## Dependency graph
`T00 → T01/T02 → T03 → T04 → T05 → T06` (data to retrieval/AI); `T02 → T08 → T09/T10 → T11 → T12` (planner and learning); frontend shell T07 starts after T02 using mock DTOs and integrates T05/T06/T08–T12. T13 E2E depends on all P0. See [BACKLOG](BACKLOG.md). If time short, prioritize deterministic import + RAG + planner + one measured outcome over live connector or media expansion.

Post-hackathon phases: institutional connector validation and reliable sync → complete productivity/exam/notes/focus and personalization → robust adaptive optimization and multi-provider/media → authorized sharing and institutional deployment. Each phase requires rights/security review, migration, tests and documented feature readiness.

## S4 dependency and gate amendment
G0: agree academic contracts and ownership (T37 design); confirm source access/rules separately. G1: migration/fixture, C3 calculator tests and Gemini mock UI. G2: T38/T39 and T41 in parallel; T40 after calculator/required-grade output and T09. G3: integrate T42 before T13, show academic scenario and planner response. G4–G5: regression and demo; P1 only after full baseline + academic P0 green. Dependency: T02→T37→T38→T39→T40→T42→T13; T37→T41→T42. The previous 48h timetable is illustrative and not recalculated as a firm commitment; human must rebalance increased P0 workload.

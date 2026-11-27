# Testing and quality gates

[RECOMMENDATION] Use pytest for backend unit/integration/contract tests, Vitest + testing-library for components, Playwright for a local E2E flow. Test services with injected clock, repository and provider; no live paid API in CI. Separate live integration tests opt-in and require authorized credentials. Synthetic fixtures, no student documents or secrets in Git.

| Domain | Mandatory scenarios |
|---|---|
| Catalog/ETL | repeat import, changed hash, failed parse retry, alias ambiguity, exam-solution match, revoked source, incomplete listing not deletion, sync cursor retry |
| Parsing/RAG | PDF locator, notebook inert text, formulas/code/table retained, lexical/synonym query, selected-doc filter, French/English, no evidence, malicious source instructions, citation resolves exact version |
| AI | capability mismatch, budget exceeded, denied disclosure, authorized fallback, cache hit and stale source, malformed output, cancellation/restart, no unnecessary generation |
| Planner | hard no-overlap, daily/weekly cap, deadlines, rest, min/max session, prerequisite order/cycle, lock conflict, DST/all-day, infeasible workload, partial/missed, deterministic output, stale preview |
| Learning | unknown vs zero, objective vs passive, assisted lower evidence, duplicate submit, correct/incorrect score, interval change, retention, exam expiration/key isolation and post-submit reveal |
| Workspace | note version/citation, timer reload and pauses, error entry dedupe, actual vs estimated time, analytics denominators |
| Security | wrong-user access for docs/jobs/citations, path traversal, unsafe file, stored XSS, CSRF/CORS, secrets masked, source authorization and account deletion |
| UI | loading/empty/error/partial states, keyboard navigation, viewport, generated types, job polling, plan diff, no hidden exam answer key in network payload |
| Deployment | fresh DB/migrations, restart persistence, backup/restore, offline fixture, no accidental outbound calls in mock mode |

## Acceptance measurements (proposed)
- Import same synthetic dataset twice: second run 0 new versions/chunks/events; modify one source: only that source reprocessed.
- Small curated RAG evaluation set: report recall@5, MRR, citation precision and human-reviewed grounding; target >=80% recall@5 and 100% resolvable demo citations. No unsupported measured claims.
- Planner: zero hard violations across deterministic fixtures, report scheduled/unscheduled minutes and replan churn; property tests generate overlapping busy intervals and tight capacities.
- Learning: one submitted attempt creates exactly one evidence event per mapped item/dimension; incorrect answer lowers established mastery or initializes weak score; next review changes; no passive mastery increase.
- E2E: import → confirmed exam → grounded tutor → plan → activity → quiz/review → updated mastery → replan → restart; test empty and infeasible alternatives.

## CI and release
Proposed commands after project bootstrap: `python -m pytest backend/tests`, `npm --prefix frontend run lint`, `npm --prefix frontend run typecheck`, `npm --prefix frontend test`, `npm --prefix frontend run build`, E2E script. Pin dependencies and configure scripts before relying on these commands. Run contract diff and migration checks, secret scan, no fixture privacy leakage. Integration owner records commit, commands, pass/fail, environment and blockers. A skipped live test is not a pass; demo mocks must be labeled. No tests of application code were run during this documentation-only phase.

## Academic acceptance matrix
| Test | Expected |
|---|---|
| Nested weights | eight quizzes each 3.125% of subject; sum path weights 1 |
| Weighted scenario | A=13 weight4, B=10 weight3 → 11.7142857; target12 requires B=10.6666667 |
| Missing/zero | missing B → provisional13 coverage4/7; actual B=0 included |
| Invalid config | cycle, orphan, duplicate leaf, invalid fraction sum, negative coefficient/out-of-scale marks rejected |
| Feasibility | no remaining weight, impossible target above maximum, guaranteed target, conflicting objectives; no divide-by-zero |
| Rules | unknown rules never report official pass; configured eliminatory minimum blocks compensation when applicable |
| Scenarios | simulate/save/reset does not change actual records, revision or live plan |
| Planner | academic signals affect soft rank, not locks, deadlines, limits or maintenance; missing data fallback; stale preview rejected |
| Adaptation | poor aggregate mark creates diagnosis; only confirmed concept evidence changes mastery |
| Sync/security | duplicate import idempotent; override conflicts visible; wrong-user access denied; no grade values in logs |
| UI/E2E | enter marks, goal, scenario, minimum grade and preview; restart retains real data; fixture labeled |
Use Decimal comparison before display rounding, property tests for weighted bounds and monotonicity under linear rules, contract tests and synthetic fixtures. These tests are planned, not executed.

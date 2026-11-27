# Agent team and ownership

Only Codex and Gemini are development agents. Three humans remain accountable for reviews and merge authorization. Multiple sessions of the same agent are permitted; do not introduce Claude Code, Copilot, Cline, Roo or autonomous third-party agents. Runtime AI providers are unrelated (see [AI_ENGINE](AI_ENGINE.md)).

## Three parallel workstreams [RECOMMENDATION]
| Human / session | Ownership | Allowed paths | Dependencies |
|---|---|---|---|
| Person 1 / Codex Integration + Data | Architecture reconciliation, bootstrap, auth/db/contracts, connectors, ingestion, catalog, RAG indexing and search, CI/seed | backend/app/core, modules/catalog, ingestion, rag; migrations, contracts, infra, scripts, CI, root manifests | owns initial contract freeze; provides fixture/query interface |
| Person 2 / Gemini Frontend + AI | UI shell, routes, component system, typed client, Tutor/Studio provider/router, artifact viewer, all frontend integration | frontend/**, backend/app/modules/ai/** | uses mocks from contract; RAG interface from P1; assessment DTO from P3 |
| Person 3 / Codex Planner + Learning | Calendar/tasks, planner, adaptive/spaced repetition, quiz/exam scoring, focus/notes backend, analytics and notifications backend | backend/app/modules/planner, learning, workspace, analytics, account (profile contract coordinated with P1) | catalog/identity DTOs and calendar fixture; frontend endpoints consumed by P2 |

Load balancing: P2 owns broad UI and AI; after contract freeze, a separate Gemini session may implement specific frontend features in a distinct worktree (e.g. `frontend/src/features/planner`), with P3 human coordinating DTOs. Never assign simultaneous edits to same file. P1 delegates no shared migration changes without review. Cross-review: P1 validates P2 integration; P2 tests P3 flows; P3 tests P1 data impacts. Humans can redistribute tickets explicitly if workload differs; record owner changes in backlog.

## Shared-file policy
Integration owner alone edits main.py, contracts, base schema/migrations, dependency lockfiles, root config and shared documentation during feature work. Domain agent requests changes via handoff with schema and reason. Do not auto-edit another owner's files. Small domain migration proposals reviewed/merged sequentially. Frontend shared shell and API client owned by Gemini lead; feature-specific UI delegated only with explicit assignment.

## Agent startup protocol
Read root AGENTS.md (Gemini also GEMINI.md), PROJECT_CONTEXT, REQUIREMENTS, ARCHITECTURE, DATA_MODEL, API_CONTRACTS, MVP, BACKLOG and relevant domain docs. Inspect current branch/status/tree/manifests and existing code before proposing changes. State ticket, exact scope, dependencies, acceptance tests, expected files and unresolved questions. Wait for human authorization to leave Plan Mode. On implementation, use only assigned worktree, add tests, avoid secrets or destructive commands; no autonomous pushes or merges without explicit authorization.

## Handoff template
```text
Ticket / owner / branch:
Base commit:
Files changed:
Contract additions or deviations:
Tests executed and results:
Known blockers / unverified assumptions:
Integration instructions and next dependency:
```
A handoff is not a claim of completion unless tests actually ran. Review feedback becomes a new scoped change; agents do not rewrite unrelated modules. See [DEVELOPMENT_WORKFLOW](DEVELOPMENT_WORKFLOW.md).

## Academic extension ownership
C3 (Codex Planner/Learning) owns `backend/app/modules/grades/**`, calculator, simulator, objective/rule logic, planner and learning integration. G (Gemini) owns `frontend/src/features/grades/**` and Academic Dashboard UI; C1 (Codex Integration/Data) owns authorized academic connectors, schema migration, shared contracts and fixtures. G handles optional AI corrected-script adapter in assigned AI paths; C3 owns diagnostic evidence flow. Freeze DTOs and synthetic examples first; G develops against mocks while C3 builds deterministic domain tests. Rebalance C3 workload with human approval before implementation; no third agent type, no simultaneous edits to shared files.

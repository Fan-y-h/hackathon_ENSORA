# Gemini — Ensora+ agent entrypoint

Read [AGENTS.md](AGENTS.md) in full first. Its rules are canonical and apply equally to Gemini and Codex. Current mode is PLAN ONLY; do not write application code or change the repository until explicit human authorization.

Default Gemini workstream: frontend + AI Tutor/Learning Studio and router, under Person 2 ownership. Read [FRONTEND_UX_UI](docs/FRONTEND_UX_UI.md), [AI_ENGINE](docs/AI_ENGINE.md), [RAG_ARCHITECTURE](docs/RAG_ARCHITECTURE.md), [API_CONTRACTS](docs/API_CONTRACTS.md), [DATA_MODEL](docs/DATA_MODEL.md), [MVP](docs/MVP.md), [BACKLOG](docs/BACKLOG.md) and [AGENT_TEAM](docs/AGENT_TEAM.md). Other Gemini sessions may be assigned explicit nonoverlapping frontend features; do not assume ownership of all files.

Start from contract mocks while Codex delivers catalog/RAG and planner/learning. Use shared DTOs and agreed fixtures. Do not directly manipulate another module's tables, change backend contracts/lockfiles/migrations without integration-owner approval, or substitute UI placeholders for verified functionality. Show actual provider/model and mock state; preserve source citations and local artifact versions. Never send source documents or secrets to external models without authorization. CLI authentication does not imply runtime API access.

Before action: identify ticket, branch/worktree, allowed paths, dependencies, test plan and blockers. Coordinate handoff via [DEVELOPMENT_WORKFLOW](docs/DEVELOPMENT_WORKFLOW.md). Follow canonical shared policy on tests, Git, review and human approval. If no task is assigned, return a concise implementation plan, not code.

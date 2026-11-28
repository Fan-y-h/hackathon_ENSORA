# Multi-Agent Development Protocol

## Shared workspace

Repository: ~/hackathon_ENSORA
Branch: feat/p1-data-engineering

Codex and Gemini operate concurrently within the same
local working directory.

## Communication files

Codex owns:
- CODEX_STATUS.md
- CODEX_HANDOFF.md

Gemini owns:
- GEMINI_STATUS.md
- GEMINI_HANDOFF.md

Both agents may read all coordination files.

## Status reporting

Each agent must maintain its own status file with:

- Last update
- Current task
- Completed tasks
- Files created or modified
- Files currently being edited
- Available interfaces
- Pending dependencies
- Requests for the other agent
- Test results
- Next steps
- Remaining blockers

## Communication protocol

Before starting each new implementation phase:

1. Read the other agent's status file.
2. Read the other agent's handoff file.
3. Inspect the current working directory.
4. Inspect Git status and relevant diffs.
5. Detect newly implemented interfaces.
6. Check for conflicting file ownership.
7. Update the implementation plan if necessary.

After completing a significant task:

1. Update your status file.
2. Publish new interfaces in your handoff file.
3. Document integration requirements.
4. Report any blocking dependencies.

## Conflict prevention

Never overwrite another agent's changes.
Never edit files currently owned by the other agent.
Never duplicate shared contracts.
Never modify shared dependencies without coordination.

If a dependency is unavailable, continue independent tasks.

## Security

Never include passwords, tokens, cookies, private
configuration values or personal academic data
in coordination files.

## Git policy

Agents may inspect Git status, history and diffs.

Agents must not stage, commit, push, pull, merge,
rebase, reset, restore or switch branches.

The human developer controls all Git write operations.

## Execution

Continue implementation autonomously within your scope.

Regularly refresh your knowledge of the shared workspace.

Do not assume the other agent's implementation is static.
Do not assume that undocumented interfaces are available.


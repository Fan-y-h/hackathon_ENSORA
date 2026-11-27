# Parallel development and integration

[RECOMMENDATION] No implementation until human approval. Existing remote: `https://github.com/Fan-y-h/hackathon_ENSORA`. Preserve existing files and uncommitted work. Run commands below only in the user's local clone; they are instructions, not actions performed by this archive.

## Initial verification and documentation commit
```bash
git status --short
git remote -v
git branch -a
git ls-files
git log -5 --oneline
gh auth status
```
If dirty, inspect and preserve changes; do not reset/stash automatically. Extract archive into a temporary folder and copy only docs/, AGENTS.md, GEMINI.md after checking collisions. Review diff, create a docs branch, commit only intended paths, push after human approval and open PR. Integration owner reviews proposed stack/contracts and bootstraps shared foundation in a separate approved PR.

## Worktrees after foundation merge
```bash
git fetch origin
git worktree add -b feat/data-rag ../ensora-data origin/main
git worktree add -b feat/frontend-ai ../ensora-ui origin/main
git worktree add -b feat/planner-learning ../ensora-planner origin/main
git worktree list
```
If branch exists, use its existing branch without `-b`; never check out one branch in multiple worktrees. Each worktree has independent `.env`, runtime DB, ports and test data (e.g. backend 8001/8002/8003, frontend 5171/5172/5173). Shared immutable fixtures only. Run agents from corresponding directory in separate VS Code terminals (`codex` or `gemini`, assuming installed/authenticated). Do not share mutable SQLite/Chroma directories. Use a dedicated integration clone/worktree for merged tests. Agent CLI account access is not application API access.

## Branch policy
Short-lived feature branches off reviewed main, one ticket or coherent slice per commit. Use conventional commits (`feat(planner): ...`, `test(rag): ...`, `docs: ...`). `git add` explicit paths; inspect `git diff --cached` for secrets or unrelated changes. Pull/rebase only after coordinating and never rewrite others' branches; if conflict, stop and ask owner to resolve. No force push, hard reset, destructive clean or direct main commits by agents. Human authorizes push and PR. CI checks contract generation, lint/typecheck, unit/integration, smoke. Review migration order and lockfiles before merge.

## Integration order
1. Docs and foundation (config/auth/db/contracts/fixture and skeleton API/client) → main.
2. Data/catalog import and retrieval interface → integration; planner and UI build against fixtures concurrently.
3. Planner/learning and AI provider integration may merge independently if contracts stable; UI integrates actual endpoints as available.
4. End-to-end demo, migration, offline restart and regression tests; resolve feature flags; freeze new features before jury.

## Coordination rhythm
Use GitHub issues or a shared ticket log: ID, owner, dependency, status (todo/in-progress/review/blocked/done), branch and test evidence. Claim before edits; at handoff record new contract needs. Prefer small PRs. Shared integration owner resolves conflicts, never assumes agent-to-agent coordination occurs automatically. On blocker use stub/mock only if explicitly labeled and contract-compatible; file follow-up ticket. Definition of done: acceptance met, tests run or limitation documented, no unauthorized files changed, docs updated, human review.

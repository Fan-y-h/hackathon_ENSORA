# Repository structure

## Observed baseline (2026-11-27)
Public `Fan-y-h/hackathon_ENSORA` main: `.gitignore`, `LICENSE` (MIT), `README.md`, one commit. README is a short project description. No application folders visible on main. Unpublished local changes and other branches not audited. Do not delete or replace existing files. Before implementation, inspect `git status --short`, `git branch -a`, `git ls-files`, `git log -5 --oneline` and current manifests; record discrepancies in [DECISIONS](DECISIONS.md).

## Target layout [RECOMMENDATION]
```text
hackathon_ENSORA/
├── AGENTS.md                     # canonical agent policy
├── GEMINI.md                     # Gemini entrypoint
├── README.md                     # preserve, extend after approval
├── LICENSE                       # preserve
├── docs/                         # all design documents
├── frontend/
│   ├── src/
│   │   ├── app/                  # routes, providers, shell
│   │   ├── components/           # shared accessible UI
│   │   ├── features/             # dashboard, catalog, calendar, tutor,
│   │   │                         # studio, planner, learning, workspace, settings
│   │   ├── api/                  # typed client and generated contracts
│   │   ├── mocks/                # explicit demo/test adapters
│   │   └── styles/
│   ├── tests/
│   └── package.json
├── backend/
│   ├── app/
│   │   ├── main.py               # composition root
│   │   ├── core/                 # configuration, auth, db, errors, jobs, events
│   │   ├── modules/
│   │   │   ├── catalog/          # subjects, documents, concepts
│   │   │   ├── ingestion/        # connectors, parser, matching, sync
│   │   │   ├── rag/              # chunking, embedding, search
│   │   │   ├── ai/               # router, providers, tutor, studio
│   │   │   ├── planner/          # events, tasks, scheduling
│   │   │   ├── learning/         # mastery, review, quiz, exam
│   │   │   ├── workspace/        # notes, focus
│   │   │   ├── analytics/        # read models
│   │   │   └── account/          # profile, settings, notifications
│   │   └── contracts/            # public DTOs/event envelopes
│   ├── migrations/
│   ├── tests/{unit,integration,contract}/
│   └── pyproject.toml
├── contracts/openapi.json        # generated from approved API
├── data/demo/                    # synthetic, redistributable fixtures only
├── scripts/                      # setup, seed, smoke, backup
├── infra/                        # optional containers
├── .github/workflows/            # CI
├── .env.example                  # placeholders only
└── compose.yaml                  # optional local stack
```

Each backend module uses `router.py`, `schemas.py`, `service.py`, `domain.py`, `repository.py` as needed; do not create empty abstraction layers mechanically. Tests mirror module boundaries. Runtime `data/` (except demo), secrets, DB, indices, blobs, caches and worktrees stay ignored. `contracts/openapi.json`, `main.py`, migrations, lockfiles and shared docs are integration-owned. See [AGENT_TEAM](AGENT_TEAM.md). This archive intentionally contains documentation only.

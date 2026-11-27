# Architecture decisions and open questions

Status legend: REQUIREMENT = user baseline; PROPOSED = recommendation awaiting human approval; ASSUMPTION = unverified planning input; OPEN = unresolved. No application code changed in this phase.

| ADR | Status | Decision / rationale / consequence |
|---|---|---|
| ADR-01 | REQUIREMENT | Local-first, authorized read-only source mirrors, local persistence, generation on demand; external disclosure opt-in. |
| ADR-02 | PROPOSED | Modular monolith FastAPI + React/Vite/TS. Earlier proposals include Next.js; choose Vite for simple local SPA unless local repo has established framework. Avoid duplicate frontend frameworks. |
| ADR-03 | PROPOSED | SQLite WAL/FTS5 + Chroma/local files for hackathon; earlier PostgreSQL/pgvector proposal deferred to shared deployment. SQLAlchemy/Alembic and adapter boundaries ease migration. |
| ADR-04 | PROPOSED | Direct provider adapter for one verified runtime model + test fake; LiteLLM optional later. Keep complete multi-provider scope; Codex/Gemini exclusively for development agents. |
| ADR-05 | PROPOSED | Greedy deterministic planner baseline before OR-Tools CP-SAT. LLM does not schedule arbitrary dates. Explicit unscheduled needs, versioned previews. |
| ADR-06 | PROPOSED | Transparent mastery estimate and interval heuristic before FSRS/knowledge tracing. Earlier FSRS proposal retained as next step, not falsely claimed implemented. |
| ADR-07 | PROPOSED | `/api/v1` and shared DTOs, versioned artifacts/documents, durable jobs/events, user isolation. Existing repo has no application contract visible on main. |
| ADR-08 | PROPOSED | Three human workstreams using Codex, Gemini, Codex; single owner shared files; separate worktrees and PR review. |
| ADR-09 | PROPOSED | Authorized export/import fallback and synthetic fixture when live sources blocked. Community begins with rights-checked export/import; real-time collaboration needs shared service. |
| ADR-10 | PROPOSED | Local auth/session + optional institutional SSO later; avoid credential sharing and unauthorized browser automation. |
| ADR-11 | PROPOSED | Use safe parameterized animation templates, no model-generated executable code; media artifacts distinct from scripts/storyboards. |
| ADR-12 | ASSUMPTION | Illustrative 48h schedule, 3 humans; actual duration/rubric not verified. |

## Open decision register
| ID | Question / required evidence | Owner / gate |
|---|---|---|
| O01 | Actual local Git tree, branch and uncommitted code; compare public main snapshot, record base SHA | C1 before bootstrap |
| O02 | Official hackathon subject/rules, duration, grading and deliverables; supplied text includes proposed brief, not independent official rules | Human before roadmap freeze |
| O03 | Moodle web services enabled? Which scopes/token/SSO permitted? Obtain authorized test | C1 before live connector |
| O04 | Pamplemousse API/ICS/export and group assignment; confirm school permissions and personal slot | C1 before live connector |
| O05 | Ensora Drive folder ID, rights, OAuth consent and redistribution/AI permission; personal Drive scope | C1 before live connector |
| O06 | Which runtime model credentials and limits available? Verify actual provider access/pricing, local hardware and data consent | G before real AI call |
| O07 | Embedding model license, multilingual quality, memory/download; choose version after benchmark | C1 before indexing freeze |
| O08 | Mastery weights, repetition policy, planner priority and workload limits; validate on scenarios | C3 before production claims |
| O09 | Community moderation, ownership, retention, deletion and institutional GDPR/rights review | Human before shared deployment |
| O10 | Note export formats, media provider/renderers, notification delivery permissions | G/C3 before respective P1/P2 |
| O11 | Authentication mode, multi-profile isolation, backup and retention rules | C1 before data-bearing use |

## Known ambiguities and source reconciliation
Latest French LaTeX specification lists personal Drive but leaves its platform table blank: propose opt-in selected-folder semantics, not a confirmed detailed requirement. Community appears in navigation but no detailed current section; prior specification supplements sharing and collaboration. Earlier mockups, figures and schedule examples are illustrative, not live data. Notebook excerpt notes ambiguous PPDP presentation slots; never infer individual exam slot from color. Source APIs and model entitlements untested. No local clone available in authoring environment (network resolution failed); public main tree inspected through GitHub. Other branches/local changes not audited. Earlier notebook source only retrieved in excerpts, not executed; official organizer brief not independently retrieved. Human must resolve these gaps before implementation; do not block documentation or erase full product scope.

## Change procedure
Record date, owner, context, alternatives, decision, consequence and superseded ADR. Human approves changes affecting contracts, dependencies, access or scope; update [REQUIREMENTS](REQUIREMENTS.md), [API_CONTRACTS](API_CONTRACTS.md) or relevant domain docs and tests. Keep unresolved choices visible rather than treating assumptions as facts.

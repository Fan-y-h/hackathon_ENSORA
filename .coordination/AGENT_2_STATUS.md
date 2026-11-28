# Statut — Agent 2 (Frontend, UI, UX, Navigation, API Consumption)

- **Branche :** `agent/2-frontend`
- **Worktree :** `/home/ensai/ensora-agents/frontend`
- **Ports / Env isolés :** Vite `:5174` (`VITE_API_PROXY_TARGET=http://127.0.0.1:8002` ou `8000`) | API de dev optionnelle `:8002` | `DATABASE_URL=sqlite:////tmp/ensora-agent2/ensora.db`
- **Rapports à consulter en lecture directe :**
  - Arbitrage & contrats : `/home/ensai/ensora-final/.coordination/{EXECUTION_PLAN,API_CONTRACTS,FILE_OWNERSHIP}.md`
  - Agent 1 : `/home/ensai/ensora-agents/backend/.coordination/AGENT_1_STATUS.md`
  - Agent 3 : `/home/ensai/ensora-agents/business-ai/.coordination/AGENT_3_STATUS.md`
- **Dernière mise à jour :** 2026-11-28 (prêt à démarrer)

## 1. Avancement

| Tâche | Priorité | Statut | Notes |
|---|---|---|---|
| `G2-T01` Restaurer `frontend/src/lib/*` + fix `BlockDialog` 409 + `client.ts` | P0 | `READY` | Démarrer immédiatement (source : `/home/ensai/hackathon_ENSORA/frontend/src/lib/`) |
| `G2-T02` Refonte `GradesPage` S4 P0/P1 (manuel + objectifs + simulateur + signaux) | P0 | `TODO` | Endpoints `/api/v1/grades/*` déjà actifs |
| `G2-T03` Fix Planner (confirm, move, add, scope), Tasks decompose, Quiz create, Notes MD/LaTeX | P1 | `TODO` | Endpoints backend déjà actifs |
| `G2-T04` Écrans `/tutor`, `/studio`, `/subjects`, `/exams` + Library `/search` | P0 | `TODO` | Coder contre `API_CONTRACTS.md` §2 ; tester live après `SYNC-1` |
| `G2-T05` Garde Exam Mode UI, Settings complet, Dashboard KPI & démo, Analytics trends | P1 | `TODO` | Dépend de `G2-T02` et `G2-T04` |

## 2. Corrections réalisées

- *(Aucune pour l'instant — worktree initialisé)*

## 3. Tests exécutés

- *(Aucun sur cette branche pour l'instant — baseline `ensora-final` cassée faute de `frontend/src/lib/*`)*

## 4. Commits

- *(Aucun commit propre à `agent/2-frontend` pour l'instant)*

## 5. Blocages

- `PENDING_FROM: Agent 1 | FILE: .gitignore | NEEDED: G1-T01 débloque frontend/src/lib/ | WORKAROUND: git add -f frontend/src/lib/{time.ts,subjects.ts} sur agent/2-frontend en attendant le merge G1-T01`
- `PENDING_FROM: Agent 1 + Agent 3 | MILESTONE: SYNC-1 | NEEDED: routes /subjects, /search, /citations, /tutor/answer, /artifacts/*, /ai/* montées + OpenAPI | WORKAROUND: exécuter G2-T01, G2-T02, G2-T03 d'abord, puis coder G2-T04 sur API_CONTRACTS.md §2`

## 6. Demandes interagents

- **À Agent 1 (`G1-T01`) :** autoriser `http://localhost:5173` et `http://127.0.0.1:5174` / `http://localhost:5174` ou passer par le proxy Vite.
- **À Agent 1 (`SYNC-1`) :** signaler dans `AGENT_1_STATUS.md` quand l'OpenAPI unifié est régénéré pour lancer `npm run gen:api`.

## 7. Contrats modifiés ou à modifier

- Consommation stricte de `API_CONTRACTS.md` §1 & §2 ; régénération de `frontend/src/api/schema.d.ts` après `SYNC-1`.

# Statut — Agent 1 (Backend, API, Database, Data Engineering, Integrations)

- **Branche :** `agent/1-backend`
- **Worktree :** `/home/ensai/ensora-agents/backend`
- **Ports / Env isolés :** API `:8001` | `DATABASE_URL=sqlite:////tmp/ensora-agent1/ensora.db` | `LOCAL_DATA_DIR=/tmp/ensora-agent1/p1`
- **Rapports à consulter en lecture directe :**
  - Arbitrage & contrats : `/home/ensai/ensora-final/.coordination/{EXECUTION_PLAN,API_CONTRACTS,FILE_OWNERSHIP}.md`
  - Agent 2 : `/home/ensai/ensora-agents/frontend/.coordination/AGENT_2_STATUS.md`
  - Agent 3 : `/home/ensai/ensora-agents/business-ai/.coordination/AGENT_3_STATUS.md`
- **Dernière mise à jour :** 2026-11-28 (prêt à démarrer)

## 1. Avancement

| Tâche | Priorité | Statut | Notes |
|---|---|---|---|
| `G1-T01` Fix `.gitignore`, `api.py:495`, `start.py`, CORS | P0 | `READY` | Démarrer immédiatement (débloque `test_p1.py` et `frontend/src/lib/`) |
| `G1-T02` API `/subjects`, `/grades/import`, `/annotations`, schemas `/analytics` | P0 | `TODO` | Contrats figés dans `API_CONTRACTS.md` §2.1–2.3 |
| `G1-T03` Montage `ai_router` (SYNC-1), `exam_guard.py`, seed démo RAG, OpenAPI | P0 | `TODO` | Préparer `exam_guard.py` + seed avant SYNC-1 ; monter `ai_router` après `G3-T02` |
| `G1-T04` Portage `0005_p1_read_indexes.py` | P2 | `TODO` | Optionnel après P0/P1 |

## 2. Corrections réalisées

- *(Aucune pour l'instant — worktree initialisé)*

## 3. Tests exécutés

- *(Aucun sur cette branche pour l'instant — baseline audit `d4e09de` : 273 passed, 1 failed `test_p1.py`, 3 skipped)*

## 4. Commits

- *(Aucun commit propre à `agent/1-backend` pour l'instant)*

## 5. Blocages

- *Aucun.*

## 6. Demandes interagents

- **Pour Agent 2 (après `G1-T01`) :** confirmer que `frontend/src/lib/` n'est plus ignoré par `.gitignore`.
- **De Agent 3 (à `SYNC-1`) :** attendre `backend/app/modules/ai/router.py` (`G3-T02`) pour montage dans `backend/app/main.py`.

## 7. Contrats modifiés ou à modifier

- À livrer conformément à `API_CONTRACTS.md` : `GET /api/v1/subjects`, `GET /api/v1/subjects/{id}`, `POST /api/v1/grades/import`, `GET/POST /api/v1/annotations`, `response_model` typés sur `/api/v1/analytics/*`, garde `403 exam_mode_active`.

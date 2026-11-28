# Statut — Agent 3 (Business Logic, Algorithms, Planning, Academic, RAG & AI)

- **Branche :** `agent/3-business-ai`
- **Worktree :** `/home/ensai/ensora-agents/business-ai`
- **Ports / Env isolés :** API `:8003` | `DATABASE_URL=sqlite:////tmp/ensora-agent3/ensora.db` | `LOCAL_DATA_DIR=/tmp/ensora-agent3/p1`
- **Rapports à consulter en lecture directe :**
  - Arbitrage & contrats : `/home/ensai/ensora-final/.coordination/{EXECUTION_PLAN,API_CONTRACTS,FILE_OWNERSHIP}.md`
  - Agent 1 : `/home/ensai/ensora-agents/backend/.coordination/AGENT_1_STATUS.md`
  - Agent 2 : `/home/ensai/ensora-agents/frontend/.coordination/AGENT_2_STATUS.md`
- **Dernière mise à jour :** 2026-11-28 (prêt à démarrer)

## 1. Avancement

| Tâche | Priorité | Statut | Notes |
|---|---|---|---|
| `G3-T01` Portage `modules/rag/**`, `core/ai.py`, `contracts/ai.py` + adaptateurs transparents | P0 | `READY` | Source vérifiée : `/home/ensai/hackathon_ENSORA` (25/25 tests verts) |
| `G3-T02` Module API `modules/ai/` (`/search`, `/citations`, `/tutor/answer`, `/artifacts/*`, `/ai/*`) | P0 | `TODO` | Contrats figés dans `API_CONTRACTS.md` §2.4–2.5 |
| `G3-T03` `CatalogPort` hybride + test boucle E2E Grades→Planner→Studio→Learning→Replan | P0 | `TODO` | Inclut test de persistance après redémarrage DB |

## 2. Corrections réalisées

- *(Aucune pour l'instant — worktree initialisé)*

## 3. Tests exécutés

- *(Aucun sur cette branche pour l'instant — baseline `hackathon_ENSORA` RAG/AI : 25 passed)*

## 4. Commits

- *(Aucun commit propre à `agent/3-business-ai` pour l'instant)*

## 5. Blocages

- *Aucun blocage pour démarrer `G3-T01` et `G3-T02`.*

## 6. Demandes interagents

- `REQUEST_TO: Agent 1 | FILE: backend/app/main.py | MILESTONE: SYNC-1 | NEEDED: monter from app.modules.ai.router import router as ai_router dans api (prefix /api/v1) dès que G3-T02 est commité`
- `REQUEST_TO: Agent 1 | FILE: backend/app/core/exam_guard.py | NEEDED: exposer require_not_exam_mode (G3 utilise LearningService.exam_mode directement en attendant SYNC-1)`

## 7. Contrats modifiés ou à modifier

- À livrer conformément à `API_CONTRACTS.md` §2.4–2.5 : `GET /api/v1/search`, `GET /api/v1/citations/{chunk_id}`, `POST /api/v1/rag/reindex`, `GET/POST /api/v1/ai/providers*`, `GET/PATCH /api/v1/ai/preferences`, `GET/POST /api/v1/conversations*`, `POST /api/v1/tutor/answer`, `POST /api/v1/artifacts/generate`, `GET/PATCH /api/v1/artifacts*`, garde `403 exam_mode_active`.

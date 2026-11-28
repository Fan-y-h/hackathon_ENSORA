# Ensora+ — Propriété Exclusive des Fichiers

**Règle absolue :** Chaque fichier du repository a **un seul agent propriétaire**. Aucun autre agent ne peut modifier un fichier hors de son périmètre. Toute modification nécessaire sur un fichier partagé fait l'objet d'une demande explicite dans `AGENT_<N>_STATUS.md` adressée au propriétaire unique.

---

## 1. Matrice de propriété exclusive

| Chemin / Motif | Propriétaire unique | Justification |
|---|---|---|
| `.gitignore`, `README.md`, `backend/pyproject.toml`, `backend/alembic.ini` | **Gemini 1** | Configuration racine, dépendances et intégration |
| `scripts/**`, `data/demo/**` | **Gemini 1** | Lanceur unifié (`start.py`), bootstrap, onboarding, seed démo |
| `backend/migrations/**` | **Gemini 1** | Chaîne de migrations Alembic unique |
| `backend/app/__init__.py`, `backend/app/main.py` | **Gemini 1** | Racine de composition FastAPI (montage routers, CORS, lifespan) |
| `backend/app/contracts/authentication.py`, `sources.py`, `sync.py`, `users.py` | **Gemini 1** | Contrats P1 & identité |
| `backend/app/core/**` **SAUF** `backend/app/core/ai.py` | **Gemini 1** | DB, runtime, auth, security, sync, etl, config, errors, events, exam-mode dependency |
| `backend/app/modules/ingestion/**` | **Gemini 1** | Connecteurs, parsers, pipeline, classification, API P1 |
| `backend/app/modules/catalog/**` *(nouveau)* | **Gemini 1** | Façade lecture `/subjects` unifiant P1 + P3 |
| `backend/app/modules/account/**` | **Gemini 1** | Profil, droits données (export/delete), onboarding |
| `backend/app/modules/analytics/**` | **Gemini 1** | Endpoints analytics + schémas Pydantic `response_model` |
| `backend/app/modules/workspace/**` | **Gemini 1** | Notes, annotations, erreurs, focus + garde mode examen |
| `backend/app/modules/notifications/**` | **Gemini 1** | Notifications |
| `backend/app/modules/planner/source_bridge.py` | **Gemini 1** | Pont P1 événements → Agenda P3 |
| `backend/app/modules/grades/source_results.py` | **Gemini 1** | Pont P1 Pamplemousse → `/results` et `/grades/import` |
| `backend/tests/conftest.py`, `backend/tests/contract/**` | **Gemini 1** | Fixtures globales et contrat OpenAPI/inventaire |
| `backend/tests/unit/ingestion/**`, `backend/tests/unit/test_source_*.py` | **Gemini 1** | Tests P1 et bridges |
| `backend/tests/integration/test_p1.py`, `test_sources_integration.py`, `test_profile_api.py`, `test_catalog_api.py` *(nouveau)* | **Gemini 1** | Tests intégration P1, catalogue, bridges et auth/CORS |
| **`frontend/**`** (tout le dossier frontend sans exception) | **Gemini 2** | SPA React 19, client API, types, composants, pages, styles, tests Vitest/E2E |
| `backend/app/contracts/ai.py` *(nouveau — portage)* | **Gemini 3** | Contrats IA multi-fournisseurs |
| `backend/app/core/ai.py` *(nouveau — portage)* | **Gemini 3** | Routeur IA, registre providers, adaptateurs (Ollama, cloud opt-in, fallback extractif) |
| `backend/app/modules/rag/**` *(nouveau — portage)* | **Gemini 3** | Indexation FTS5/hybride, retrieval, reranking, contexte, moteur RAG, store d'artefacts |
| `backend/app/modules/ai/**` *(nouveau)* | **Gemini 3** | Router + service FastAPI pour `/search`, `/citations/{id}`, `/rag/reindex`, `/tutor/answer`, `/conversations`, `/artifacts/*`, `/ai/*` |
| `backend/app/modules/planner/**` **SAUF** `source_bridge.py` | **Gemini 3** | Algorithmes de planification (greedy, CP-SAT), contraintes, replan, décomposition |
| `backend/app/modules/learning/**` | **Gemini 3** | Maîtrise, FSRS/heuristique, quiz, exam mode, `CatalogPort` |
| `backend/app/modules/grades/**` **SAUF** `source_results.py` | **Gemini 3** | Calculateur déterministe `Decimal`, objectifs, notes requises, scénarios, règles, signaux, forecast |
| `backend/tests/unit/rag/**`, `backend/tests/unit/test_ai_*.py` *(nouveaux)* | **Gemini 3** | Tests RAG & routage IA |
| `backend/tests/unit/test_{grades,planner,p1,p2}_domain.py`, `test_scheduling.py`, `test_heuristics_register.py` | **Gemini 3** | Tests domaine métier |
| `backend/tests/integration/test_{agenda,grades,grades_p1,learning,p1_features,p2,planner}_api.py`, `test_rag_ai_api.py` *(nouveau)* | **Gemini 3** | Tests intégration métier, IA et RAG |
| `.coordination/AGENT_1_STATUS.md` | **Gemini 1** | Journal d'avancement G1 |
| `.coordination/AGENT_2_STATUS.md` | **Gemini 2** | Journal d'avancement G2 |
| `.coordination/AGENT_3_STATUS.md` | **Gemini 3** | Journal d'avancement G3 |
| `.coordination/{PROJECT_CONTEXT,AUDIT,REQUIREMENTS_MATRIX,EXECUTION_PLAN,FILE_OWNERSHIP,API_CONTRACTS,AGENT_*_TASKS,TEST_PLAN}.md` | **Tech Lead** | Documents directeurs de coordination |

---

## 2. Protocole pour fichiers partagés critiques

| Fichier partagé | Propriétaire | Demandes formalisées des autres agents |
|---|---|---|
| `.gitignore` | **Gemini 1** | **G2 demande :** remplacer `lib/` par `/lib/` (ou ajouter `!frontend/src/lib/`) dans `G1-T01` afin que `frontend/src/lib/time.ts` et `frontend/src/lib/subjects.ts` soient suivis par Git. |
| `backend/app/main.py` | **Gemini 1** | **G3 demande :** inclure `from app.modules.ai.router import router as ai_router` et initialiser `app.state.rag_runtime` (ou lazy resolver vers P1 `runtime.engine` + local artifact store) lors de `SYNC-1`.<br>**G2 demande :** autoriser `http://127.0.0.1:5173` ET `http://localhost:5173` dans CORS, et exposer un helper dev/local sécurisé pour `/api/v1/sources/*`. |
| `backend/app/core/exam_guard.py` *(nouveau)* | **Gemini 1** | **G3 demande :** fournir une dépendance FastAPI réutilisable `require_not_exam_mode` (vérifiant `LearningService.exam_mode(user_id).active`, renvoyant `403 exam_mode_active`) consommable par `workspace/router.py`, `ingestion/api.py` (optionnel) et `modules/ai/router.py`. |
| `backend/pyproject.toml` | **Gemini 1** | **G3 demande :** aucune dépendance obligatoire lourde (le RAG porté de `hackathon_ENSORA` fonctionne en SQLite FTS5 + embeddings locaux/hash sans `chromadb`/`torch`). Si `ortools` ou SDK cloud ajouté, uniquement en `optional-dependencies`. |
| `frontend/src/api/schema.d.ts` | **Gemini 2** | **G1 fournit** le schéma OpenAPI à jour à `SYNC-1` ; **G2 régénère** `schema.d.ts` via `npm run gen:api`. |

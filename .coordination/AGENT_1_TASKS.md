# Backlog Détaillé — Agent 1 (Gemini 1 : Backend Core, Data, API, DB & Intégrations)

**Worktree assigné :** `/home/ensai/ensora-agents/backend` (branche `agent/1-backend`, base `integration/ensora-final`)  
**Périmètre exclusif :** voir `.coordination/FILE_OWNERSHIP.md` (ne jamais modifier `frontend/**`, `backend/app/modules/{rag,ai,planner(sauf source_bridge),learning,grades(sauf source_results)}/**`, ni `backend/app/core/ai.py`).  
**Règle de suivi :** mettre à jour `.coordination/AGENT_1_STATUS.md` à chaque tâche démarrée/terminée ou blocage.

---

## G1-T01 [P0] — Corriger les 4 blocages P0 d'infrastructure (`.gitignore`, `ingestion/api.py:495`, `scripts/start.py`, CORS)

- **Identifiant unique :** `G1-T01`
- **Problème et cause racine :**
  1. `.gitignore` contient `lib/` (ligne 15), ce qui exclut `frontend/src/lib/` du suivi Git et casse le build frontend (`G2`).
  2. `backend/app/modules/ingestion/api.py:495` importe `.modules.ingestion.codex_bridge` (chemin relatif obsolète après déplacement depuis `main.py`), causant un `ModuleNotFoundError` HTTP 500 et l'échec de `test_p1.py::test_round2_p2_version_evidence_and_p3_nested_academics`.
  3. `scripts/start.py` lance encore l'ancienne app P1-only `backend.app.modules.ingestion.api:create_app` sur `:8765` au lieu du serveur unifié `app.main:create_app` sur `:8000` (ciblé par `frontend/vite.config.ts`).
  4. `backend/app/core/config.py` + `backend/app/main.py` n'autorisent que `http://127.0.0.1:5173` en CORS et rejettent `http://localhost:5173`.
- **Fichiers concernés :**
  - `.gitignore`
  - `backend/app/modules/ingestion/api.py`
  - `scripts/start.py`
  - `backend/app/core/config.py`
  - `backend/app/main.py`
- **Dépendances :** Aucune (Vague 0 — à faire immédiatement).
- **Correction attendue :**
  1. Remplacer `lib/` par `/lib/` (ou ajouter `!frontend/src/lib/` et `!frontend/src/lib/**`) dans `.gitignore`.
  2. Corriger ligne 495 de `backend/app/modules/ingestion/api.py` en `from backend.app.modules.ingestion.codex_bridge import extract_concept_candidates, extract_prerequisite_hints` et vérifier l'absence d'autres imports relatifs cassés dans `ingestion/`.
  3. Mettre à jour `scripts/start.py` pour démarrer par défaut `app.main:create_app` sur `127.0.0.1:8000` (avec option `--port 8000`, `PYTHONPATH` correct, et support `--skip-onboarding`).
  4. Étendre `allow_origins` dans `backend/app/main.py` pour accepter `settings.frontend_origin` ainsi que son équivalent `localhost`/`127.0.0.1` sur le même port (sans `*` avec `allow_credentials=True`).
- **Critères d'acceptation :**
  - `git check-ignore frontend/src/lib/time.ts` ne retourne rien (non ignoré).
  - `test_p1.py` passe intégralement (0 échec).
  - `python3 scripts/start.py --skip-onboarding` sert `/api/v1/health` → `{"status":"ok","database":true,"sources":true}` sur `:8000`.
  - Requêtes preflight `OPTIONS` depuis `http://127.0.0.1:5173` et `http://localhost:5173` retournent `200` avec le header CORS attendu.
- **Tests à exécuter :**
  - `cd backend && pytest tests/integration/test_p1.py tests/integration/test_sources_integration.py -v`
  - Vérification HTTP live sur `:8000` (`/api/v1/health` + preflight `OPTIONS` `localhost:5173`).

---

## G1-T02 [P0] — Façade Catalogue `/api/v1/subjects`, Bridge `POST /api/v1/grades/import`, `/api/v1/annotations` & Schémas `/analytics/*`

- **Identifiant unique :** `G1-T02`
- **Problème et cause racine :**
  1. Aucun endpoint `/api/v1/subjects` ni `/api/v1/subjects/{subject_id}` côté unifié : `/subjects` est un placeholder dans le frontend.
  2. `POST /api/v1/grades/import` est manquant : les résultats synchronisés depuis Pamplemousse (`/api/v1/results`) n'alimentent jamais les `AcademicProgram` P3 ni les `grades.signals` du Smart Planner.
  3. `GET/POST /api/v1/annotations` figurent au contrat (`docs/API_CONTRACTS.md`) mais ne sont pas implémentés.
  4. `GET /analytics/{readiness,heatmap,mastery-map}` n'ont pas de `response_model` Pydantic (types TS générés manquants).
- **Fichiers concernés :**
  - `backend/app/modules/catalog/__init__.py`, `backend/app/modules/catalog/schemas.py`, `backend/app/modules/catalog/service.py`, `backend/app/modules/catalog/router.py` *(nouveaux)*
  - `backend/app/modules/grades/source_results.py` (ajout `POST /grades/import` utilisant `GradesService` en lecture/écriture via API de service)
  - `backend/app/modules/workspace/models.py`, `schemas.py`, `service.py`, `router.py` (ajout `Annotation` + `GET/POST /annotations`)
  - `backend/app/modules/analytics/router.py` (ajout `response_model` typés)
  - `backend/app/main.py` (montage `catalog_router`)
  - `backend/tests/integration/test_catalog_api.py` *(nouveau)*
- **Dépendances :** `G1-T01`.
- **Correction attendue :**
  - Implémenter strictement les schémas gelés dans `.coordination/API_CONTRACTS.md` (§2.1, §2.2, §2.3).
  - `POST /api/v1/grades/import` doit supporter `source="pamplemousse"` (lit le tree P1 via `read_records` + `build_results`, crée/met à jour de façon idempotente un `AcademicProgram` via `GradesService` sans supprimer les objectifs/scénarios existants) ET `source="manual_json"` (dry-run puis commit) ; retourne `503 sources_unavailable` si P1 n'est pas actif pour `pamplemousse`.
  - `GET /api/v1/subjects` agrège sans erreur même quand P1 est désactivé (`sources_app=None`).
- **Critères d'acceptation :**
  - Tous les contrats §2.1–§2.3 répondent selon les schémas exacts (codes `200`/`201`/`404`/`503`).
  - Double appel à `POST /api/v1/grades/import` est idempotent et préserve les objectifs/scénarios existants.
  - `GET /api/v1/analytics/*` apparaissent typés dans `/openapi.json`.
- **Tests à exécuter :**
  - `cd backend && pytest tests/integration/test_catalog_api.py tests/integration/test_sources_integration.py tests/integration/test_grades_api.py tests/integration/test_p1_features_api.py -v`

---

## G1-T03 [P0] — Montage SYNC-1 (Routers G3 RAG/AI) + Garde Exam Mode Backend + Seed Démo RAG

- **Identifiant unique :** `G1-T03`
- **Problème et cause racine :**
  1. Les nouveaux endpoints RAG/AI de `G3` (`backend/app/modules/ai/router.py`) doivent être montés proprement dans `backend/app/main.py` avec accès au runtime P1 (pour lire les documents/chunks) et au dossier local d'artefacts.
  2. Le mode examen n'est pas appliqué côté backend sur `/notes`, `/notebooks`, `/annotations` ni sur un garde réutilisable.
  3. `scripts/seed_demo.py` ne sème pas de document synthétique de cours (nécessaire pour démontrer RAG/Search/Tutor/Studio hors-ligne) et le frontend en mode `vite preview` (sans dev proxy) échoue en `401` sur `/api/v1/sources/*`.
- **Fichiers concernés :**
  - `backend/app/core/exam_guard.py` *(nouveau)*
  - `backend/app/modules/workspace/router.py` (ajout `Depends(require_not_exam_mode)` sur `/notes*`, `/notebooks*`, `/annotations*`)
  - `backend/app/main.py` (montage `ai_router` de G3 à SYNC-1 + optionnel helper/session locale sécurisée ou documentation claire proxy)
  - `scripts/seed_demo.py`, `data/demo/sample_econometrie_cours.md` *(nouveau document synthétique 100% original et labellisé)*
  - `docs/handoff/p3_openapi.json`, `docs/handoff/P3_ENDPOINT_INVENTORY.md` (via `python -m tests.contract.handoff`)
  - `backend/tests/contract/test_handoff_up_to_date.py`
  - `README.md` (instructions exactes de lancement unifié en 2 commandes)
- **Dépendances :** `G1-T02`, `G3-T02` (SYNC-1 pour le montage final d'`ai_router`). Note : `exam_guard.py` et le document démo peuvent être préparés **avant** SYNC-1.
- **Correction attendue :**
  - Créer `require_not_exam_mode` dans `backend/app/core/exam_guard.py` qui appelle `LearningService(db, clock).exam_mode(user_id)` et lève `Forbidden("Disabled during an active exam attempt", code="exam_mode_active")` (`403`) si `active=True`.
  - L'appliquer sur les routes `notes`, `notebooks`, `annotations` dans `workspace/router.py` (jamais sur `focus` ou `errors` en lecture post-submit).
  - À SYNC-1, monter `ai_router` dans `backend/app/main.py`, mettre à jour les contrats (`python -m tests.contract.handoff`) et vérifier `test_handoff_up_to_date.py`.
  - Enrichir `scripts/seed_demo.py` pour importer de manière idempotente `data/demo/sample_econometrie_cours.md` (cours synthétique sur Gauss-Markov, MCO, biais/variance) et déclencher l'indexation RAG si P1 est disponible.
- **Critères d'acceptation :**
  - Tentative `mode=exam` active → `GET /api/v1/notes` renvoie `403 exam_mode_active` ; après submit → `200`.
  - `pytest backend/tests` passe à 100% (0 échec), y compris `test_handoff_up_to_date.py`.
  - `python scripts/seed_demo.py` s'exécute 2 fois sans erreur ni doublon.
- **Tests à exécuter :**
  - `cd backend && pytest tests -v --tb=short`
  - `cd backend && python -m tests.contract.handoff`
  - `python scripts/seed_demo.py && python scripts/seed_demo.py`

---

## G1-T04 [P2 — si temps restant] — Porter l'indexation de performance P1 (`0005_p1_read_indexes.py`)

- **Identifiant unique :** `G1-T04`
- **Problème et cause racine :** La migration `0005_p1_read_indexes.py` existant dans `/home/ensai/hackathon_ENSORA/backend/migrations/versions/` améliore les performances des lectures P1 (sur une base de 58 Mo).
- **Fichiers concernés :** `backend/migrations/versions/0005_p1_read_indexes.py`, `backend/tests/unit/test_read_indexes.py`.
- **Dépendances :** `G1-T03`.
- **Correction attendue :** Porter la migration et vérifier `alembic upgrade head` sur base vierge et sur copie de base `0004`.
- **Critères d'acceptation :** Une seule tête Alembic (`alembic heads`), migration réversible/idempotente, tests verts.
- **Tests à exécuter :** `cd backend && pytest tests/unit/test_read_indexes.py tests/integration/test_p1.py -v`

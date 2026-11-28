# Ensora+ — Audit Technique et Fonctionnel Réel

**Date :** 2026-11-28  
**Commit audité :** `d4e09de` (`integration/ensora-final`)  
**Méthodologie :** inspection complète de l'arbre Git, des worktrees locaux et branches distantes, exécution réelle de `pytest` (274 tests), démarrage réel d'Uvicorn + requêtes HTTP live, audit du frontend (`tsc`, `vitest`, inspection ligne par ligne des pages et appels API).

---

## 1. État Git et contributions locales découvertes

| Emplacement / Branche | Commit | État | Contenu critique |
|---|---|---|---|
| `/home/ensai/ensora-final` (`integration/ensora-final`) | `d4e09de` | Propre (base de référence) | Serveur FastAPI unifié (P3 + P1 monté sous `/api/v1/sources`), 274 tests backend, SPA React 19/Vite |
| `/home/ensai/hackathon_ENSORA` (`feat/p1-data-engineering`) | `d37e130` + **modifications non commitées massives** | Sale (non intégré) | **`frontend/src/lib/{time.ts,subjects.ts}`** (manquants dans `ensora-final` !), **`backend/app/modules/rag/`** (9 fichiers, 25/25 tests verts), **`backend/app/core/ai.py` + `contracts/ai.py`**, `frontend/src/features/local/LocalAi.tsx`, migrations `0005` & `0006` |
| `origin/Justin` | `f439fb1` | Non mergée | SPA React alternatif complet (Tutor, Studio, Subjects, Exams, GradeTree, Onboarding, Playwright E2E) basé sur contrats supposés + mocks MSW. **À utiliser uniquement comme référence UI**, sans écraser `ensora-final`. |
| `/home/ensai/ensora-agents/{ai-rag,data,frontend,planner-grades,qa}` | `d4e09de` | Propres (vides de modifications) | Worktrees pré-créés pointant sur `d4e09de` |

---

## 2. Résultats réels des vérifications exécutées (Phase 2)

### 2.1 Backend — Tests et démarrage live
- **Commande exécutée :** `.venv/bin/pytest backend/tests -q --tb=short` (via venv `/home/ensai/hackathon_ENSORA/.venv`)
- **Résultat :** **`273 passed, 1 FAILED, 3 skipped`** (64.89s)
  - **ÉCHEC RÉEL :** `backend/tests/integration/test_p1.py::test_round2_p2_version_evidence_and_p3_nested_academics`
  - **Cause racine :** `backend/app/modules/ingestion/api.py:495` contient un import relatif cassé après déplacement du fichier :
    `from .modules.ingestion.codex_bridge import extract_concept_candidates, extract_prerequisite_hints` → `ModuleNotFoundError: No module named 'backend.app.modules.ingestion.modules'`.
  - **3 tests skippés :** tests CP-SAT car `ortools` n'est pas installé dans le venv.
- **Démarrage serveur live (`uvicorn app.main:create_app --factory --app-dir backend --port 8099`) :**
  - `GET /api/v1/health` → `200 {"status":"ok","database":true,"sources":true}`
  - Génération de `api-token` P1 vérifiée (`True`)
  - `GET /api/v1/sources/connections` (avec Bearer token) → `200` (6 sources : `google_calendar`, `pamplemousse`, `local`, `moodle`, `ensora_drive`, `academic_import`)
  - `GET /api/v1/results` → `200` (arbre vide sur base P1 neuve)
  - `OPTIONS /api/v1/profile` (`Origin: http://127.0.0.1:5173`) → `200` CORS OK ; **MAIS `http://localhost:5173` est rejeté par CORS** (`allow_origins=[settings.frontend_origin]`).
  - **86 chemins OpenAPI** exposés sous `/api/v1/*` (la sous-app montée `/api/v1/sources/*` n'apparaît pas dans l'OpenAPI racine).

### 2.2 Frontend — Build, TypeScript et Tests
- **État dans `/home/ensai/ensora-final/frontend/` :**
  - **BLOQUANT P0 :** `frontend/src/lib/time.ts` et `frontend/src/lib/subjects.ts` sont **ABSENTS** du dépôt (importés par 18 fichiers de `src/` et `tests/` !).
  - **Cause racine :** la ligne `lib/` dans le `.gitignore` racine (issue du template Python) a exclu `frontend/src/lib/` lors des commits !
  - `frontend/node_modules` n'est pas installé dans le worktree `ensora-final` (présent dans `/home/ensai/hackathon_ENSORA/frontend/node_modules`, où `vitest run` passe 34/34 tests).
  - **Conséquence :** dans `ensora-final`, `npm run typecheck`, `npm run build`, `npm test` et `npm run dev` **échouent immédiatement**.

### 2.3 Dépendances et environnement local
- Python 3.12 (venv dans `/home/ensai/hackathon_ENSORA/.venv`), Node `v24.22.3`, Vitest `5.1.2`, Uvicorn installé.
- **Paquets absents du venv :** `ortools` (CP-SAT replie proprement sur greedy), `pymupdf/fitz` (`pdf_parser.py` utilise le fallback `pdftotext`/pure-Python), `python-docx`, `python-pptx`, `nbformat`, `chromadb`, `sentence_transformers`, SDKs cloud (`openai`, `google-genai`, `anthropic`, `mistralai`).
- **Ollama / clés API IA :** `ollama` non détecté en cours d'exécution sur `127.0.0.1:11434`, aucune variable `OPENAI_*`/`GEMINI_*`/`ANTHROPIC_*`/`MISTRAL_*` exportée dans le shell d'audit.
- **Données locales P1 existantes :** `~/.local/share/ensora-plus/ensora.sqlite3` (58 Mo) et `blobs/` existent sur le poste (ne jamais commiter ni exposer dans les logs/tests).

---

## 3. Inventaire des défauts critiques, bugs runtime et fonctionnalités manquantes

### P0 — Bloquants (application cassée, crash ou exigence P0 non démontrable)

| ID | Zone | Problème avéré | Preuve / Fichier | Cause racine |
|---|---|---|---|---|
| **BUG-01** | Frontend / Git | Le frontend ne compile ni ne démarre (`frontend/src/lib/time.ts` et `subjects.ts` manquants) | `ls frontend/src/lib` échoue ; 18 imports cassés ; `.gitignore:15` | Règle `.gitignore` générique `lib/` a ignoré `frontend/src/lib/` |
| **BUG-02** | Backend P1 | Crash HTTP 500 sur `GET /api/v1/sources/documents/{id}/versions/{vid}/concept-candidates` | `pytest`: `FAILED test_p1.py::test_round2_p2_version_evidence_and_p3_nested_academics` | Import relatif obsolète `backend/app/modules/ingestion/api.py:495` |
| **BUG-03** | Scripts / DevOps | `scripts/start.py` démarre l'ancienne app P1-only sur `:8765` au lieu de l'app unifiée sur `:8000` | `scripts/start.py:11,23` vs `frontend/vite.config.ts:31` (`8000`) | Script non mis à jour après fusion `fa54247` |
| **BUG-04** | Backend / AI+RAG | Aucun endpoint RAG ni AI dans `ensora-final` (`/search`, `/citations/{id}`, `/tutor/answer`, `/conversations`, `/artifacts/generate`, `/ai/providers`, `/ai/preferences`) | `app.openapi()` (86 paths, 0 RAG/AI) | Modules `rag/` (25 tests verts) et `core/ai.py` restés non committés dans `/home/ensai/hackathon_ENSORA` |
| **BUG-05** | Frontend / Grades (S4 P0) | `GradesPage.tsx` ignore complètement le backend `/api/v1/grades/*` : lecture seule Pamplemousse (`/results`), calcul en floats JS (`average.ts`), sims dans `localStorage`, **aucune** saisie manuelle UE/coef/notes, **aucun** objectif, **aucune** note requise, **aucun** scénario serveur, **aucun** statut officiel, écran vide sans Pamplemousse | `frontend/src/features/grades/GradesPage.tsx`, `average.ts` | Remplacement par une vue Pamplemousse-only en `d4e09de` sans conserver le flux P3/S4 P0 |
| **BUG-06** | Frontend / Routes MVP | 4 écrans majeurs renvoient `<PlannedPage />` ("Module à venir") : `/subjects`, `/tutor`, `/studio`, `/exams` | `frontend/src/app/App.tsx:52-56`, `PlannedPage.tsx` | Backends catalogue/RAG/AI non câblés + `/exams` non branché sur `POST /quizzes/mock` déjà existant |
| **BUG-07** | Frontend / Planner | **Crash 409 runtime** au clic sur « Terminée » ou « Partielle » sur une séance `planned` dans `BlockDialog.tsx` | `BlockDialog.tsx:124-135` vs `plan_service.py:672-673` (`session_not_started`) | `BlockDialog` appelle `/sessions/{id}/finish` sans appeler `/sessions/{id}/start` d'abord ; `client.ts` affiche un faux message « données modifiées ailleurs » |
| **BUG-08** | Sécurité / Exam Mode | Mode examen contournable : `AppShell.tsx` ne bloque que le lien `/notes` (pas `/tutor`, `/studio`, `/library`, ni la navigation URL directe), et le backend ne bloque pas les routes notes/RAG/AI pendant un examen actif | `AppShell.tsx:69`, `workspace/router.py`, `ingestion/api.py` | Vérification `exam_mode` absente côté backend et incomplète côté frontend |

### P1 — Fonctionnalités requises incomplètes, boutons sans action ou incohérences métier

| ID | Zone | Problème avéré | Preuve / Fichier |
|---|---|---|---|
| **BUG-09** | Frontend / Planner | `BlockDialog.tsx:41-45` affiche « confirmez-le dans l'agenda » mais **aucun bouton Confirmer** n'existe (alors que `POST /events/{id}/confirm` existe) ; la légende promet `⋮⋮ déplaçable` mais **aucune action de déplacement** n'existe (alors que `PATCH /sessions/{id}` existe) ; aucun formulaire d'ajout d'événement personnel ni de confirmation de périmètre d'évaluation (`PATCH /assessments/{id}`) | `PlannerPage.tsx:166`, `BlockDialog.tsx:41-45`, `CalendarBlock.tsx:97` |
| **BUG-10** | Backend / Grades | `POST /api/v1/grades/import` manquant (l'un des 3 endpoints contrat non implémentés) : les notes/UE synchronisées depuis Pamplemousse (`/api/v1/results`) ne sont jamais importées dans `/api/v1/grades/*` et n'alimentent donc pas `grades.signals` du Smart Planner ! | `docs/handoff/P3_ENDPOINT_INVENTORY.md:130`, `grades/router.py`, `grades/source_results.py` |
| **BUG-11** | Backend / CORS & Auth | CORS refuse `http://localhost:5173` (seul `127.0.0.1:5173` autorisé). Le proxy `api-token` pour `/api/v1/sources/*` n'existe que dans `vite dev` (en `vite preview` ou build statique, `/api/v1/sources/*` retourne `401`). | `backend/app/core/config.py:27`, `backend/app/main.py:113`, `frontend/vite.config.ts:40-50` |
| **BUG-12** | Frontend / Library | Recherche uniquement par `title.includes()` côté client : pas de recherche plein texte/sémantique (`GET /search`), pas de filtre par matière, pas d'aperçu de citation/extrait ni d'envoi vers Notes ou AI Tutor. | `frontend/src/features/library/LibraryPage.tsx:39-41` |
| **BUG-13** | Frontend / Quiz & Exams | Impossible de créer un quiz depuis l'UI (`POST /quizzes` non exposé) ni de générer un examen blanc (`POST /quizzes/mock` non exposé) ; sur base vide, écran bloqué sur « Aucun quiz ». | `frontend/src/features/learning/QuizPage.tsx` |
| **BUG-14** | Frontend / Tasks | Pas de décomposition de projet (`POST /tasks/decompose` + `POST /tasks/{id}/subtasks` non branchés), pas d'édition ni de verrouillage de tâche, pas de filtre par matière/priorité. | `frontend/src/features/tasks/TasksPage.tsx` |
| **BUG-15** | Frontend / Notes | Aucun rendu Markdown/LaTeX (malgré le libellé `formules LaTeX entre $…$`), pas de suppression de note (`DELETE /notes/{id}`), pas d'historique des versions (`GET /notes/{id}/versions`), pas de création ni résolution dans le carnet d'erreurs (`POST /errors`, `PATCH /errors/{id}`). | `frontend/src/features/workspace/NotesPage.tsx` |
| **BUG-16** | Frontend / Settings | Pas d'export des données personnelles ni suppression de compte (`POST /account/export`, `DELETE /account`), pas de configuration des fournisseurs IA / consentement cloud, pas de contrôle de planification de sync (`GET/PATCH /api/v1/sources/sync/schedule`), pas d'onboarding guidé. | `frontend/src/features/settings/SettingsPage.tsx` |
| **BUG-17** | Frontend / Dashboard & Analytics | Dashboard sans résumé académique (moyenne/objectif/risque), sans liste de tâches prioritaires, sans bandeau explicite « Données synthétiques de démonstration » quand le seed démo est actif. Analytics non relié aux tendances académiques (`GET /grades/trends`). | `DashboardPage.tsx`, `AnalyticsPage.tsx` |
| **BUG-18** | Backend / Contracts | `GET /analytics/*` (readiness, heatmap, mastery-map) sans `response_model` Pydantic (types TS écrits à la main) ; `GET/POST /annotations` non implémentés ; `schema.d.ts` et `p3_openapi.json` à régénérer après ajout RAG/AI/Catalog/Grades-import. | `backend/app/modules/analytics/router.py`, `frontend/src/api/schema.d.ts` |

### P2 — Améliorations techniques et ergonomiques non bloquantes

| ID | Zone | Problème |
|---|---|---|
| **BUG-19** | Frontend / A11y | `Dialog` dans `ui.tsx` sans focus trap complet ni IDs uniques en cas de modales multiples ; `TimeGrid` (min-width 720px) nécessite scroll horizontal sur mobile. |
| **BUG-20** | Backend / Perf & Déps | `ortools` absent du venv (CP-SAT replie proprement sur greedy) ; index de lecture P1 (`0005_p1_read_indexes.py` dans `hackathon_ENSORA`) non encore portés. |
| **BUG-21** | Tests E2E | Aucun test Playwright E2E dans `ensora-final` (uniquement tests unitaires Vitest). |

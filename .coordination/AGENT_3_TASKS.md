# Backlog Détaillé — Agent 3 (Gemini 3 : Business Logic, Algorithms, RAG & AI)

**Worktree assigné :** `/home/ensai/ensora-agents/business-ai` (branche `agent/3-business-ai`, base `integration/ensora-final`)  
**Périmètre exclusif :** voir `.coordination/FILE_OWNERSHIP.md` (`backend/app/contracts/ai.py`, `backend/app/core/ai.py`, `backend/app/modules/rag/**`, `backend/app/modules/ai/**`, `backend/app/modules/{planner(sauf source_bridge),learning,grades(sauf source_results)}/**` + tests associés).  
**Source de portage vérifiée (lecture seule) :** `/home/ensai/hackathon_ENSORA/backend/app/{contracts/ai.py,core/ai.py,modules/rag/**}` et `/home/ensai/hackathon_ENSORA/backend/tests/unit/{rag/**,test_ai_routing.py}` (25/25 tests déjà verts lors de l'audit).  
**Règle de suivi :** mettre à jour `.coordination/AGENT_3_STATUS.md` à chaque tâche ou besoin sur `main.py` (adressé à G1).

---

## G3-T01 [P0] — Portage et adaptation du moteur RAG + AI Router depuis `/home/ensai/hackathon_ENSORA`

- **Identifiant unique :** `G3-T01`
- **Problème et cause racine :** `ensora-final` ne contient aucun moteur RAG ni routeur IA alors qu'un module complet et testé (`rag/` : `contracts.py`, `embeddings.py`, `store.py`, `retrieval.py`, `reranking.py`, `context.py`, `engine.py`, `artifacts.py`, `evaluation.py` + `core/ai.py` + `contracts/ai.py`) existe dans `/home/ensai/hackathon_ENSORA`.
- **Fichiers concernés :**
  - `backend/app/contracts/ai.py` *(porté)*
  - `backend/app/core/ai.py` *(porté + enrichi)*
  - `backend/app/modules/rag/__init__.py`, `contracts.py`, `embeddings.py`, `store.py`, `retrieval.py`, `reranking.py`, `context.py`, `engine.py`, `artifacts.py`, `evaluation.py` *(portés)*
  - `backend/tests/unit/rag/**`, `backend/tests/unit/test_ai_routing.py` *(portés)*
- **Dépendances :** Aucune (Vague 0 — démarrage immédiat).
- **Correction attendue :**
  1. Porter tous les fichiers `rag/`, `core/ai.py`, `contracts/ai.py` et leurs tests en veillant à la double compatibilité d'imports (`app.*` et `backend.app.*`).
  2. Vérifier que le RAG fonctionne **sans dépendance externe obligatoire** (`SQLite FTS5` + vecteurs locaux déterministes déjà implémentés dans `store.py` / `embeddings.py`), et gère proprement les bases vides.
  3. Dans `core/ai.py`, conserver `OllamaProviderAdapter` (loopback sécurisé, pas de téléchargement auto) et ajouter :
     - Un adaptateur cloud optionnel **opt-in strict** (ex. `GeminiApiAdapter` / `OpenAICompatibleAdapter` lisant uniquement une variable d'environnement `GEMINI_API_KEY` / `OPENAI_API_KEY` si présente ET si `cloud_data_consent=True`, jamais de clé en dur, statut `credentials_missing` si absent).
     - Un générateur **`ExtractiveLocalProvider`** explicite et transparent (`is_fallback=True`, `generation_mode="extractive_fallback"`) qui assemble une réponse sourcée structurée (définitions, extraits pertinents, fiches, flashcards, QCM extractifs) à partir des chunks récupérés quand aucun LLM live n'est joignable — **toujours clairement étiqueté comme fallback extractif local**, jamais présenté comme un appel LLM distant !
- **Critères d'acceptation :**
  - 100% des tests RAG + AI routing portés passent (`pytest backend/tests/unit/rag backend/tests/unit/test_ai_routing.py`).
  - Aucun appel réseau sortant non autorisé ; aucun secret dans le code/logs.
  - Les textes malveillants dans les documents (« Ignore previous instructions ») restent inertes (délimiteurs untrusted-source).
- **Tests à exécuter :**
  - `cd backend && pytest tests/unit/rag tests/unit/test_ai_routing.py -v`

---

## G3-T02 [P0] — Créer le module API `backend/app/modules/ai/` (Search, Citations, Tutor, Conversations, Studio Artifacts, Providers)

- **Identifiant unique :** `G3-T02`
- **Problème et cause racine :** Le moteur RAG et le routeur IA portés en `G3-T01` doivent être exposés sur des endpoints FastAPI propres, persistés localement et alignés au centimètre près sur `.coordination/API_CONTRACTS.md` (§2.4 & §2.5), prêts à être montés dans `backend/app/main.py` par G1 à `SYNC-1`.
- **Fichiers concernés :**
  - `backend/app/modules/ai/__init__.py`
  - `backend/app/modules/ai/models.py` *(tables P3 ORM : `conversations`, `conversation_messages`, `ai_preferences` ; les artefacts peuvent utiliser `LocalArtifactStore` SQLite/JSON local ou table ORM dédiée)*
  - `backend/app/modules/ai/schemas.py` *(DTOs stricts conformes à `.coordination/API_CONTRACTS.md`)*
  - `backend/app/modules/ai/service.py` *(orchestration RAG ↔ P1 chunks ↔ AI Router ↔ `LocalArtifactStore` ↔ création optionnelle `Deck`/`Quiz` dans `LearningService`)*
  - `backend/app/modules/ai/router.py` *(expose `router = APIRouter()` avec `/search`, `/citations/{chunk_id}`, `/rag/reindex`, `/ai/providers`, `/ai/providers/refresh`, `/ai/preferences`, `/conversations`, `/conversations/{id}/messages`, `/tutor/answer`, `/artifacts/generate`, `/artifacts`, `/artifacts/{id}`, `/artifacts/{id}/versions`)*
  - `backend/tests/integration/test_rag_ai_api.py` *(nouveau — teste le router sur une FastAPI de test autonome avant même le montage dans `main.py`)*
- **Dépendances :** `G3-T01`. (Pour le montage dans `main.py`, notifier G1 dans `AGENT_3_STATUS.md` dès que `router.py` est prêt pour `SYNC-1`.)
- **Correction attendue :**
  1. **Indexation incrémentale depuis P1 :** `rag/reindex` (et auto-indexation lazy si index vide) lit les chunks actifs dans le moteur SQLite P1 (`request.app.state.sources_app.state.runtime.engine` si présent ; sinon index local des documents fournis) sans jamais crasher si P1 est arrêté (renvoie 0 chunks / `evidence_status="insufficient"` proprement).
  2. **Citations vérifiables :** chaque citation retournée par `/search`, `/tutor/answer`, `/artifacts/generate` correspond à un `chunk_id` réel avec `document_id`, `version_id`, `locator` et `content_url` — jamais de chemin filesystem.
  3. **Learning Studio relié à P3 Learning :**
     - `POST /artifacts/generate` avec `type="revision_sheet"` → fiche Markdown structurée (définitions, formules, méthodes, pièges, citations) avec cache SHA-256 (`cache_hit=True` au 2e appel identique).
     - `type="flashcards"` + `persist_to_learning=True` → crée un `Deck` + `Flashcard`s via `LearningService` (`to_p3_flashcard_deck_payload`) et renvoie `linked_deck_id`.
     - `type="quiz"` + `persist_to_learning=True` → crée un `Quiz` jouable via `LearningService` (`to_p3_quiz_payload`) et renvoie `linked_quiz_id`.
     - `type="exercise"` et `type="mind_map"` → payload structuré validé Pydantic (nœuds/arêtes pour `mind_map`).
     - `PATCH /artifacts/{id}` → crée une nouvelle version éditée par l'utilisateur sans écraser la version générée.
  4. **Garde Mode Examen :** chaque route sensible (`/search`, `/citations/*`, `/tutor/*`, `/conversations*`, `/artifacts*`) vérifie `LearningService(db, clock).exam_mode(user_id).active` et lève `403 exam_mode_active` si actif.
- **Critères d'acceptation :**
  - Tous les contrats `.coordination/API_CONTRACTS.md` §2.4 & §2.5 sont respectés et testés.
  - Question hors-corpus → `evidence_status == "insufficient"`.
  - Génération sur demande uniquement (zéro appel pendant `/planner/preview`).
  - Mode examen actif → `403 exam_mode_active` sur search, tutor et studio.
  - Artefacts, conversations et préférences IA survivent au redémarrage.
- **Tests à exécuter :**
  - `cd backend && pytest tests/integration/test_rag_ai_api.py -v`

---

## G3-T03 [P0] — `CatalogPort` réel, cohérence boucle Grades→Planner→Learning & non-régression métier

- **Identifiant unique :** `G3-T03`
- **Problème et cause racine :**
  1. `backend/app/modules/learning/catalog_port.py` utilise encore uniquement `InterimCatalog` (validation de format seulement).
  2. Il faut vérifier de bout en bout (avec test d'intégration dédié) que :
     - La boucle **Grades (`AcademicProgram` + objectifs + note faible) → `GET /grades/signals` → `POST /planner/preview` (priorité changée + raison académique) → `POST /planner/accept`** fonctionne sans violer aucune contrainte dure ni verrou ;
     - Une mauvaise note globale crée un besoin **diagnostique** (`academic_diagnostic_needs`) et **ne modifie jamais** la maîtrise d'un concept sans preuve objective ;
     - Un `Quiz` ou `Deck` généré par le Learning Studio (`G3-T02`) alimente bien `mastery`, `errors` et `study-needs` puis déclenche `planner/status -> dirty=True`.
- **Fichiers concernés :**
  - `backend/app/modules/learning/catalog_port.py` (ajout adaptateur `HybridCatalog` qui reconnaît les matières P1 si disponibles tout en gardant la compatibilité avec les matières manuelles/synthétiques)
  - `backend/app/modules/planner/plan_service.py` (vérification qu'aucun appel IA n'est fait au preview et que `generation_spec` est cohérent avec `/artifacts/generate`)
  - `backend/app/modules/grades/service.py` (vérification compatibilité avec `POST /grades/import` de G1)
  - `backend/tests/integration/test_full_adaptive_loop.py` *(nouveau : test complet Grades → Signals → Planner → Studio Quiz → Attempt → Mastery → Error Notebook → Replan → Restart)*
- **Dépendances :** `G3-T02` (et coordination légère avec `G1-T02` pour le format `GradesService` appelé par `/grades/import`).
- **Correction attendue :**
  - `test_full_adaptive_loop.py` valide toute la boucle pédagogique fermée du cahier des charges en un test reproductible + redémarrage DB.
  - Aucune régression sur les 225+ tests P3 existants.
- **Critères d'acceptation :**
  - `test_full_adaptive_loop.py` 100% vert.
  - Tous les tests domaine & intégration de G3 passent à 100%.
- **Tests à exécuter :**
  - `cd backend && pytest tests/unit tests/integration/test_agenda_api.py tests/integration/test_grades_api.py tests/integration/test_grades_p1_api.py tests/integration/test_learning_api.py tests/integration/test_planner_api.py tests/integration/test_rag_ai_api.py tests/integration/test_full_adaptive_loop.py -v`

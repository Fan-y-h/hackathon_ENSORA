# Ensora+ — Contrats API Partagés (Gelés pour Coordination)

**Base URL :** `/api/v1` (JSON UTF-8, `snake_case`, UUID strings, timestamps ISO-8601 UTC, minutes entières, notes/poids `grades` en chaînes décimales).  
**Enveloppe d'erreur unique :** `{"error": {"code": "...", "message": "...", "details": ..., "request_id": "..."}}`.  
**Codes HTTP standard :** `400` entrée invalide, `401` auth requise, `403` interdit / `exam_mode_active`, `404` absent (jamais de fuite cross-user), `409` révision obsolète / conflit, `422` validation, `503` service indisponible.

---

## 1. Contrats existants GELÉS (ne pas casser)

Les 86 chemins actuellement exposés par `app.main:create_app` (documentés dans `docs/handoff/P3_ENDPOINT_INVENTORY.md` et `docs/handoff/p3_openapi.json`) + la sous-app montée `/api/v1/sources/*` (`backend/app/modules/ingestion/api.py`) restent **rétrocompatibles** :
- **Account :** `GET/PATCH /profile`, `GET /profile/student-statuses`, `POST /account/export`, `DELETE /account`
- **Agenda & Tasks :** `GET/POST /events`, `GET/PATCH/DELETE /events/{id}`, `POST /events/{id}/confirm`, `GET /assessments`, `PATCH /assessments/{id}`, `GET /availability`, `GET/POST /tasks`, `PATCH/DELETE /tasks/{id}`, `POST /tasks/decompose`, `POST /tasks/{id}/subtasks`, `POST /agenda/import-from-sources`
- **Planner :** `POST /planner/preview`, `POST /planner/accept`, `POST /planner/replan`, `GET /planner/status`, `GET /plans/current`, `PATCH /sessions/{id}`, `POST /sessions/{id}/start`, `POST /sessions/{id}/finish`
- **Learning & Exams :** `GET/POST /decks`, `GET/POST /decks/{id}/cards`, `PATCH /cards/{id}`, `GET /reviews/due`, `POST /cards/{id}/review`, `GET/POST /quizzes`, `GET /quizzes/{id}`, `POST /quizzes/mock`, `POST /quizzes/{id}/attempts`, `GET /attempts/{id}`, `PATCH /attempts/{id}/responses`, `POST /attempts/{id}/submit`, `GET /attempts/{id}/result`, `POST /attempts/{id}/items/{qid}/grade`, `POST /attempts/{id}/errors`, `GET /exam-mode`, `GET /mastery`, `GET /study-needs`, `GET /learning/calibration`, `GET/PUT /concepts/prerequisites`
- **Grades :** `GET/POST /grades/programs`, `GET/PUT /grades/programs/{id}/curriculum`, `GET/PUT /grades/programs/{id}/rules`, `GET/POST /grades/records`, `PATCH/DELETE /grades/records/{id}`, `GET/POST /grades/objectives`, `PATCH/DELETE /grades/objectives/{id}`, `GET /grades/summary`, `POST /grades/required`, `POST /grades/simulate`, `GET /grades/signals`, `GET /grades/validation`, `GET/POST /grades/scenarios`, `PATCH/DELETE /grades/scenarios/{id}`, `GET /grades/scenarios/{id}/evaluate`, `GET/POST /grades/feedback` + `POST /grades/records/{id}/feedback`, `PATCH /grades/feedback/{id}`, `GET /grades/trends`, `GET/PUT /grades/records/{id}/corrected-paper`, `POST /grades/comments/analyze`, `POST /grades/forecast`, `POST /grades/time-allocation`, `GET /results`
- **Workspace / Analytics / Notifications :** `POST /focus/start`, `GET /focus/active`, `GET /focus/{id}`, `POST /focus/{id}/transition`, `POST /focus/{id}/finish`, `GET/POST /notebooks`, `GET/POST /notes`, `GET/PATCH/DELETE /notes/{id}`, `GET /notes/{id}/versions`, `GET/POST /errors`, `PATCH /errors/{id}`, `GET /analytics`, `GET /analytics/readiness`, `GET /analytics/heatmap`, `GET /analytics/mastery-map`, `GET /notifications`, `PATCH /notifications/{id}`, `POST /notifications/read-all`
- **P1 Sources (`/api/v1/sources/*`) :** `GET /health`, `GET /onboarding`, `POST /onboarding/complete`, `GET/PATCH /settings`, `GET/PATCH /sync/schedule`, `POST /sync/schedule/reset`, `GET/POST /connections`, `PUT /connections/{id}/consent`, `POST /connections/{id}/{authorize,reauthorize,validate}`, `DELETE /connections/{id}`, `POST /sync`, `GET /sync/runs`, `GET /jobs/{id}`, `POST /jobs/{id}/{cancel,resume}`, `GET /pipeline/status`, `GET /documents`, `POST /documents/import`, `GET /documents/{id}`, `GET /documents/{id}/versions`, `GET /documents/{id}/versions/{vid}/{content,chunks,concept-candidates}`, `PUT /documents/{id}/match`, `POST /academic/import`, `GET /subjects`

---

## 2. Nouveaux contrats GELÉS à implémenter avant SYNC-1

### 2.1 Catalogue & Matières (Propriétaire : G1 — Consommateur : G2, G3)

#### `GET /api/v1/subjects`
Agrège les matières issues du catalogue P1 (Pamplemousse/Moodle/documents), des événements/tâches/évaluations P3 et des programmes Grades.
```json
{
  "items": [
    {
      "id": "2AECO02",
      "code": "2AECO02",
      "title": "Économétrie linéaire",
      "source": "pamplemousse",
      "document_count": 12,
      "assessment_count": 2,
      "mastery_mean": 0.64,
      "next_deadline_at": "2026-12-07T08:30:00Z"
    }
  ]
}
```

#### `GET /api/v1/subjects/{subject_id}`
Vue détaillée pour `/subjects/:id` :
```json
{
  "id": "2AECO02",
  "code": "2AECO02",
  "title": "Économétrie linéaire",
  "documents": [{"id": "doc-1", "title": "CM1.pdf", "resource_type": "lecture", "active_version_id": "v1", "source": "moodle"}],
  "assessments": [{"id": "a1", "title": "Partiel", "deadline_at": "2026-12-07T08:30:00Z", "scope_status": "confirmed"}],
  "concepts": [{"concept_id": "gauss_markov", "score": 0.62, "status": "weak", "next_due_at": "2026-12-02T09:00:00Z"}],
  "quizzes": [{"id": "q1", "title": "Quiz OLS", "question_count": 5}],
  "decks": [{"id": "d1", "title": "Formules OLS", "due_count": 3}],
  "notes": [{"id": "n1", "title": "Hypothèses Gauss-Markov", "revision": 2}],
  "artifacts": [{"id": "art-1", "type": "revision_sheet", "title": "Fiche OLS", "stale": false}]
}
```

### 2.2 Bridge Grades Import (Propriétaire : G1 — Consommateur : G2, G3)

#### `POST /api/v1/grades/import`
Importe l'arbre UE/matières/coefficients/évaluations/notes synchronisé depuis Pamplemousse (ou un payload JSON structuré) dans un `AcademicProgram` P3 idempotent, sans écraser les objectifs ni les scénarios existants.
- **Requête :** `{"source": "pamplemousse", "dry_run": false}` (ou `{"source": "manual_json", "program": {...}, "dry_run": false}`)
- **Réponse (`200`) :**
```json
{
  "program_id": "uuid",
  "program_title": "ENSAI 2026-2027 (Pamplemousse)",
  "dry_run": false,
  "nodes_imported": 24,
  "grades_imported": 9,
  "conflicts": [],
  "revision": 3
}
```
- **Erreur si P1 indisponible :** `503 {"error": {"code": "sources_unavailable", ...}}`

### 2.3 Annotations (Propriétaire : G1 — Consommateur : G2)

- `GET /api/v1/annotations?document_id=&note_id=` → `list[AnnotationOut]`
- `POST /api/v1/annotations` → `201 AnnotationOut` (`{id, document_id, document_version_id, chunk_id, locator, excerpt, comment, note_id, created_at}`)

### 2.4 RAG & Recherche Hybride (Propriétaire : G3 — Monté par G1 — Consommateur : G2)

#### `GET /api/v1/search`
Paramètres : `q` (obligatoire, 1–500 chars), `mode` (`hybrid|lexical|semantic`, défaut `hybrid`), `subject_id?`, `document_ids?` (répétable ou virgules), `resource_type?`, `limit` (1–30, défaut 8).  
**Garde mode examen :** retourne `403 exam_mode_active` si un examen est en cours.
```json
{
  "query": "Gauss-Markov",
  "mode": "hybrid",
  "indexed_chunks": 142,
  "hits": [
    {
      "chunk_id": "chk-1",
      "document_id": "doc-1",
      "version_id": "ver-1",
      "title": "CM1 Économétrie.pdf",
      "subject_id": "2AECO02",
      "resource_type": "lecture",
      "score": 0.87,
      "excerpt": "Le théorème de Gauss-Markov stipule que...",
      "locator": {"kind": "page", "page": 4, "section": "2.1 Estimateur BLUE"},
      "citation_id": "chk-1"
    }
  ]
}
```

#### `GET /api/v1/citations/{chunk_id}`
Retourne le locator exact et l'extrait autorisé (jamais de chemin système local). `403` en mode examen, `404` si introuvable ou source révoquée.
```json
{
  "citation_id": "chk-1",
  "document_id": "doc-1",
  "version_id": "ver-1",
  "chunk_id": "chk-1",
  "title": "CM1 Économétrie.pdf",
  "locator": {"kind": "page", "page": 4, "section": "2.1 Estimateur BLUE"},
  "excerpt": "...",
  "content_url": "/api/v1/sources/documents/doc-1/versions/ver-1/content"
}
```

#### `POST /api/v1/rag/reindex`
Reconstruit/incrémente l'index FTS5/vecteurs locaux depuis les chunks P1 (et les documents importés).
- **Réponse (`200`) :** `{"documents_indexed": 12, "chunks_indexed": 142, "embedding_model": "local-hash-v1", "duration_ms": 84}`

### 2.5 AI Providers, Tutor & Learning Studio (Propriétaire : G3 — Monté par G1 — Consommateur : G2)

#### `GET /api/v1/ai/providers` & `POST /api/v1/ai/providers/refresh`
Liste réelle des fournisseurs et de leur disponibilité (jamais de secrets, jamais de disponibilité inventée) :
```json
{
  "items": [
    {"provider_id": "ollama-local", "kind": "ollama", "local": true, "configured": true, "available": false, "error_code": "unreachable", "models": []},
    {"provider_id": "gemini-api", "kind": "gemini", "local": false, "configured": false, "available": false, "error_code": "credentials_missing", "models": []},
    {"provider_id": "extractive-local", "kind": "local_extractive", "local": true, "configured": true, "available": true, "error_code": null, "models": [{"provider_id": "extractive-local", "model_id": "extractive-rag-v1"}]}
  ]
}
```
> **Transparence obligatoire :** si aucun LLM (Ollama/cloud) n'est disponible, `extractive-local` produit des synthèses/fiches/questions extractives strictement ancrées sur les chunks locaux avec `generation_mode: "extractive_fallback"` et `is_mock_or_fallback: true` — clairement affiché par l'UI !

#### `GET /api/v1/ai/preferences` & `PATCH /api/v1/ai/preferences`
```json
{
  "enabled": true,
  "cloud_data_consent": false,
  "allow_extractive_fallback": true,
  "preferred_provider": null,
  "preferred_model": null,
  "daily_token_budget": 100000
}
```

#### `GET/POST /api/v1/conversations` & `GET /api/v1/conversations/{id}/messages` & `POST /api/v1/tutor/answer`
- **Requête `POST /api/v1/tutor/answer`** (et `POST /api/v1/conversations/{id}/messages`) :
```json
{
  "question": "Explique le théorème de Gauss-Markov",
  "conversation_id": null,
  "subject_id": "2AECO02",
  "document_ids": [],
  "mode": "explain",
  "locale": "fr",
  "provider_override": null,
  "model_override": null
}
```
- **Réponse (`200`) :**
```json
{
  "conversation_id": "uuid",
  "message_id": "uuid",
  "answer_markdown": "...",
  "citations": [{"citation_id": "chk-1", "document_id": "doc-1", "version_id": "ver-1", "chunk_id": "chk-1", "title": "CM1.pdf", "locator": {"kind": "page", "page": 4}, "excerpt": "..."}],
  "evidence_status": "grounded",
  "provider": "extractive-local",
  "model": "extractive-rag-v1",
  "generation_mode": "extractive_fallback",
  "is_fallback": true,
  "fallback_reason": "No live LLM provider configured or reachable; answer grounded strictly on local retrieved excerpts.",
  "usage": {"tokens_in": 320, "tokens_out": 180}
}
```
**Garde mode examen :** `403 {"error": {"code": "exam_mode_active", ...}}`.

#### `POST /api/v1/artifacts/generate`, `GET /api/v1/artifacts`, `GET/PATCH /api/v1/artifacts/{id}`, `GET /api/v1/artifacts/{id}/versions`
- **Requête `POST /api/v1/artifacts/generate`** :
```json
{
  "type": "revision_sheet",
  "subject_id": "2AECO02",
  "document_ids": [],
  "concept_ids": [],
  "objective": "understand",
  "locale": "fr",
  "difficulty": "intermediate",
  "duration_min": 20,
  "detail_level": "detailed",
  "user_instructions": null,
  "force_regenerate": false,
  "persist_to_learning": true
}
```
Types supportés MVP : `revision_sheet`, `flashcards`, `quiz`, `exercise`, `mind_map` (types média `podcast`/`video` retournent `501 not_implemented` ou script explicitement étiqueté `script_only` — jamais de faux média).  
- Si `type == "flashcards"` et `persist_to_learning == true` : crée automatiquement un `Deck` + `Flashcard`s dans P3 `learning` et retourne `linked_deck_id`.
- Si `type == "quiz"` et `persist_to_learning == true` : crée automatiquement un `Quiz` jouable dans P3 `learning` et retourne `linked_quiz_id`.
- **Réponse (`200`/`201`) :**
```json
{
  "id": "uuid",
  "type": "revision_sheet",
  "title": "Fiche de révision — Économétrie",
  "subject_id": "2AECO02",
  "version": 1,
  "cache_hit": false,
  "stale": false,
  "provider": "extractive-local",
  "model": "extractive-rag-v1",
  "is_fallback": true,
  "content_markdown": "...",
  "structured_payload": {},
  "citations": [],
  "linked_deck_id": null,
  "linked_quiz_id": null,
  "created_at": "2026-11-28T08:00:00Z"
}
```
**Garde mode examen :** `403 exam_mode_active`.

---

## 3. Garde Mode Examen (Transversal)

Quand `GET /api/v1/exam-mode` retourne `active: true` :
- **Backend retourne `403 {"error": {"code": "exam_mode_active", "message": "Disabled during an active exam attempt"}}`** sur :
  - `/api/v1/notes*`, `/api/v1/notebooks*`, `/api/v1/annotations*` (G1)
  - `/api/v1/search`, `/api/v1/citations/*`, `/api/v1/tutor/*`, `/api/v1/conversations*`, `/api/v1/artifacts*` (G3)
  - `/api/v1/attempts/{id}/result` tant que l'attempt est `active` (déjà vérifié dans `learning/service.py`)
- **Frontend bloque** dans `AppShell.tsx` ET par garde de route directe : `/notes`, `/library`, `/tutor`, `/studio` avec bandeau explicite + bouton « Retourner à l'examen en cours ».

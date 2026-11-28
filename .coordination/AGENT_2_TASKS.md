# Backlog Détaillé — Agent 2 (Gemini 2 : Frontend, UX, UI & Consommation API)

**Worktree assigné :** `/home/ensai/ensora-agents/frontend` (branche `agent/2-frontend`, base `integration/ensora-final`)  
**Périmètre exclusif :** uniquement `frontend/**` (voir `.coordination/FILE_OWNERSHIP.md`).  
**Sources de référence autorisées en lecture seule :**
- `/home/ensai/hackathon_ENSORA/frontend/src/lib/{time.ts,subjects.ts}` (fichiers exacts manquants à restaurer !)
- `/home/ensai/hackathon_ENSORA/frontend/src/features/local/LocalAi.tsx` (référence AI preferences/providers)
- `origin/Justin` (`f439fb1`) uniquement pour inspiration ergonomique (Tutor, Studio, Subjects, Exams, Citations), en respectant strictement la charte graphique `tokens.css` et les contrats `.coordination/API_CONTRACTS.md`.  
**Règle de suivi :** mettre à jour `.coordination/AGENT_2_STATUS.md` à chaque étape.

---

## G2-T01 [P0] — Débloquer le build frontend (`frontend/src/lib/*`, `node_modules`) & corriger le crash 409 dans `BlockDialog.tsx`

- **Identifiant unique :** `G2-T01`
- **Problème et cause racine :**
  1. `frontend/src/lib/time.ts` et `frontend/src/lib/subjects.ts` sont absents de `ensora-final` (18 imports cassés → impossible de compiler ou tester).
  2. Dans `frontend/src/features/planner/BlockDialog.tsx:124-135`, cliquer « Terminée » ou « Partielle » sur une séance `planned` appelle `POST /sessions/{id}/finish` sans appeler `POST /sessions/{id}/start`, provoquant un crash HTTP `409 session_not_started` que `client.ts:142` traduit faussement en « Les données ont changé entre-temps ».
- **Fichiers concernés :**
  - `frontend/src/lib/time.ts`, `frontend/src/lib/subjects.ts` *(restaurés depuis `/home/ensai/hackathon_ENSORA/frontend/src/lib/`)*
  - `frontend/src/api/client.ts` (ne pas écraser `error.message` pour les 409 métier comme `session_not_started`, `session_in_progress`, `exam_mode_active`)
  - `frontend/src/features/planner/BlockDialog.tsx`
  - `frontend/tests/client.test.ts`
- **Dépendances :** `G1-T01` pour le commit Git de `frontend/src/lib/` (mais G2 peut restaurer localement et travailler immédiatement dès T=0, puis commiter avec `git add -f` si avant merge de `G1-T01`).
- **Correction attendue :**
  1. Copier `time.ts` et `subjects.ts` dans `frontend/src/lib/`, installer/lier `frontend/node_modules`.
  2. Dans `BlockDialog.tsx`, pour une séance `status === "planned"`, quand l'utilisateur clique « Terminée » ou « Partielle », chaîner `start.mutateAsync(session.id)` puis `finish.mutateAsync(...)` (pour `skipped`, appeler directement `finish`).
  3. Dans `client.ts`, préserver les messages serveur distincts pour `409` (`stale_preview` / `revision_conflict` vs `session_not_started` / `session_in_progress`) et `403` (`exam_mode_active`).
- **Critères d'acceptation :**
  - `npm run typecheck && npm run lint && npm test && npm run build` passent 100% vert.
  - Clôturer une séance `planned` dans `BlockDialog` réussit sans 409.
- **Tests à exécuter :**
  - `npm --prefix frontend run typecheck && npm --prefix frontend run lint && npm --prefix frontend test && npm --prefix frontend run build`

---

## G2-T02 [P0] — Refonte complète de `GradesPage` conforme S4 P0/P1 (Manuel + Pamplemousse + Objectifs + Simulateur + Signaux)

- **Identifiant unique :** `G2-T02`
- **Problème et cause racine :**
  `frontend/src/features/grades/GradesPage.tsx` ignore totalement les 28 endpoints `/api/v1/grades/*` déjà livrés dans le backend. Elle ne consomme que `GET /api/v1/results` (Pamplemousse read-only), calcule en floats JS côté client (`average.ts`), simule dans `localStorage`, n'offre ni création/édition manuelle de cursus/UE/coefficients/épreuves imbriquées/notes, ni objectifs, ni notes requises, ni scénarios serveur, ni statut de validation officiel (`unknown`), ni lien vers les signaux du Smart Planner. Sans Pamplemousse connecté, l'écran est vide et inutilisable.
- **Fichiers concernés :**
  - `frontend/src/api/queries.ts`, `frontend/src/api/types.ts` (hooks complets `/grades/programs`, `/curriculum`, `/records`, `/objectives`, `/summary`, `/required`, `/simulate`, `/scenarios`, `/rules`, `/validation`, `/signals`, `/trends`, `/feedback`, `/grades/import`)
  - `frontend/src/features/grades/GradesPage.tsx`
  - `frontend/src/features/grades/CurriculumEditor.tsx` *(nouveau : création programme, UE, matières, groupes d'épreuves imbriqués, coefficients/poids avec validation inline)*
  - `frontend/src/features/grades/SimulatorPanel.tsx` *(nouveau : simulateur non mutant `POST /grades/simulate`, sauvegarde/comparaison/reset scénarios nommés `GET/POST/DELETE /grades/scenarios`, calcul notes requises `POST /grades/required`)*
  - `frontend/src/features/grades/ObjectivesPanel.tsx` *(nouveau : CRUD objectifs par niveau + statut faisabilité `feasible|guaranteed|impossible` + règles officielles/provenance `unknown`)*
  - `frontend/src/features/grades/results.css`
  - `frontend/tests/grades.test.tsx` *(nouveau)*
- **Dépendances :** `G2-T01`. (Les endpoints `/api/v1/grades/*` existent déjà ! Seul le bouton « Importer depuis Pamplemousse » appelle `POST /api/v1/grades/import` de `G1-T02`, avec fallback propre si 404/503.)
- **Correction attendue :**
  - Source de vérité **100% serveur (`Decimal` en strings)** : afficher `GET /grades/summary` (provisoire, couverture, prévisionnel, fourchette min/max, statut officiel `unknown`/`validated`/`pending`/`not_validated`). Supprimer tout calcul autoritaire côté client.
  - Mode **Double accès fluide** :
    1. Si un programme P3 existe (seed démo, saisie manuelle, ou importé de Pamplemousse via bouton « Importer depuis Pamplemousse »), afficher l'arbre hiérarchique complet (`Année → Semestre → UE → Matière → Groupe/Épreuve`), saisie/édition de notes (`actual` vs `estimated`, jamais 0 pour manquant), éditeur de cursus/poids récursifs, objectifs, simulateur what-if serveur non mutant, et panneau **Impact Smart Planner** (`GET /grades/signals` + lien « Recalculer le planning »).
    2. Si aucun programme n'existe, proposer immédiatement 2 CTA clairs : **« Créer un programme manuel »** (pré-rempli ou vide) ET **« Importer depuis Pamplemousse »** (avec message clair si Pamplemousse non connecté).
  - Conserver un onglet/accordéon « Relevé Pamplemousse brut » si `/results` répond `200`, sans en faire une dépendance bloquante.
- **Critères d'acceptation :**
  - Fonctionne intégralement hors-ligne sans Pamplemousse.
  - Simuler une note ne modifie jamais les notes réelles (vérifiable après rechargement F5).
  - Les valeurs `Decimal` serveur, la couverture, les objectifs, la moyenne requise, la faisabilité et le statut `unknown` sont tous visibles et distincts.
- **Tests à exécuter :**
  - `npm --prefix frontend test` (incl. `grades.test.tsx` couvrant summary, objectives, simulate non-mutant, manquant ≠ 0).

---

## G2-T03 [P1] — Corriger tous les boutons manquants et compléter Planner, Tasks, Quiz et Notes

- **Identifiant unique :** `G2-T03`
- **Problème et cause racine :** Plusieurs actions promises par l'UI ou requises par le cahier des charges ont déjà leur endpoint backend mais aucun contrôle UI :
  - **Planner (`PlannerPage.tsx`, `BlockDialog.tsx`) :** pas de bouton « Confirmer ce créneau » (`POST /events/{id}/confirm`), pas de déplacement de créneau (`PATCH /sessions/{id}` malgré la légende `⋮⋮ déplaçable`), pas d'ajout d'événement personnel (`POST /events`), pas de confirmation de périmètre d'examen (`PATCH /assessments/{id}`).
  - **Tasks (`TasksPage.tsx`) :** pas de décomposition de projet (`POST /tasks/decompose` + `POST /tasks/{id}/subtasks`), pas d'édition/verrouillage de tâche, pas de filtre matière/priorité.
  - **Quiz (`QuizPage.tsx`) :** pas de création manuelle de quiz (`POST /quizzes`).
  - **Notes (`NotesPage.tsx`) :** pas d'aperçu Markdown/LaTeX sécurisé (sans `dangerouslySetInnerHTML` non sanitisé), pas de suppression (`DELETE /notes/{id}`), pas d'historique des versions (`GET /notes/{id}/versions`), pas d'ajout/résolution d'erreur (`POST /errors`, `PATCH /errors/{id}`).
- **Fichiers concernés :**
  - `frontend/src/api/queries.ts` (hooks manquants : `useConfirmEvent`, `useCreateEvent`, `usePatchAssessment`, `useDecomposeTask`, `useCreateSubtasks`, `useCreateQuiz`, `useCreateMockExam`, `useDeleteNote`, `useNoteVersions`, `useCreateError`, `useResolveError`)
  - `frontend/src/features/planner/PlannerPage.tsx`, `BlockDialog.tsx`, `EventDialog.tsx` *(nouveau)*
  - `frontend/src/features/tasks/TasksPage.tsx`
  - `frontend/src/features/learning/QuizPage.tsx`
  - `frontend/src/features/workspace/NotesPage.tsx`
  - `frontend/src/components/MarkdownView.tsx` *(nouveau : rendu Markdown + formules LaTeX sécurisé sans XSS)*
- **Dépendances :** `G2-T01`. (Tous les endpoints backend existent déjà !)
- **Correction attendue :**
  - Chaque promesse visuelle (ex. `confirmez-le`, `déplaçable`, `LaTeX entre $…$`) correspond désormais à une vraie interaction connectée à l'API.
  - Rendu Markdown/LaTeX sûr (échappement HTML strict, blocs code, titres, listes, tableaux, formules inline/bloc).
- **Critères d'acceptation :**
  - Aucun bouton factice ni promesse sans action sur Planner, Tasks, Quiz, Notes.
  - Décomposer un projet crée de vraies sous-tâches éditables ; créer un quiz permet de le jouer aussitôt ; résoudre une erreur met à jour le carnet d'erreurs.
- **Tests à exécuter :**
  - `npm --prefix frontend test && npm --prefix frontend run typecheck`

---

## G2-T04 [P0] — Remplacer les 4 `<PlannedPage />` (`/tutor`, `/studio`, `/subjects`, `/exams`) + Recherche Hybride `/library`

- **Identifiant unique :** `G2-T04`
- **Problème et cause racine :** 4 écrans P0/P1 majeurs affichent « Module à venir » (`App.tsx:52-56`), et `LibraryPage.tsx` ne fait qu'un filtre `title.includes()` côté client.
- **Fichiers concernés :**
  - `frontend/src/api/ai.ts` *(nouveau : hooks typés pour `/subjects`, `/search`, `/citations/{id}`, `/rag/reindex`, `/tutor/answer`, `/conversations`, `/artifacts/*`, `/ai/providers`, `/ai/preferences`, `/annotations`)*
  - `frontend/src/app/App.tsx`, `frontend/src/app/AppShell.tsx` (déplacer ces 4 écrans hors de « Bientôt » dans la navigation principale)
  - `frontend/src/features/subjects/SubjectsPage.tsx`, `SubjectDetailPage.tsx` *(nouveaux)*
  - `frontend/src/features/tutor/TutorPage.tsx` *(nouveau : sélection matière/documents, modes `explain|derivation|code_explain|correction|socratic|exam_prep|memorize`, historique conversations, panneau citations cliquables, badge provider/modèle + bandeau explicite si `is_fallback`, bouton « Enregistrer dans Mes notes »)*
  - `frontend/src/features/studio/StudioPage.tsx` *(nouveau : générateur à la demande `revision_sheet`, `flashcards`, `quiz`, `exercise`, `mind_map`, choix sources/objectif/langue/difficulté/instructions, historique & éditeur d'artefacts sauvegardés, badge cache/stale/fallback, lien direct vers le Deck ou Quiz créé)*
  - `frontend/src/features/exams/ExamsPage.tsx` *(nouveau : liste annales/quiz d'examen, générateur d'examen blanc `POST /quizzes/mock`, lancement en mode examen chronométré isolé, revue post-soumission et carnet d'erreurs)*
  - `frontend/src/features/library/LibraryPage.tsx` (recherche hybride `GET /search` + filtre matière/type + réindexation `POST /rag/reindex` + aperçu citation/extrait + action « Demander au Tuteur » / « Annoter »)
- **Dépendances :** `G2-T01` et contrats figés `.coordination/API_CONTRACTS.md` (consommation live dès `SYNC-1`).
- **Correction attendue :**
  - Tous les 4 écrans sont pleinement opérationnels, accessibles clavier, avec états loading/empty/error/exam-locked distincts.
  - Transparence totale sur le modèle/provider réel (`provider`, `model`, `is_fallback`, `evidence_status` `grounded|partial|insufficient`).
  - Régénérer `frontend/src/api/schema.d.ts` après `SYNC-1` (`npm run gen:api`).
- **Critères d'acceptation :**
  - Plus aucun lien principal ne mène à `<PlannedPage />` (seul `*` → 404 reste).
  - Recherche hybride dans `/library`, question sourcée + citation dans `/tutor`, génération + persistance dans `/studio`, examen blanc dans `/exams`, vue matière dans `/subjects/:id` fonctionnent de bout en bout.
- **Tests à exécuter :**
  - `npm --prefix frontend run typecheck && npm --prefix frontend run lint && npm --prefix frontend test && npm --prefix frontend run build`

---

## G2-T05 [P1] — Garde Mode Examen UI complet, Settings complet (IA/Sync/Export/Delete), Dashboard KPI & Analytics Grades

- **Identifiant unique :** `G2-T05`
- **Problème et cause racine :**
  1. `AppShell.tsx:69` ne bloque que le clic sur `/notes` en mode examen : un utilisateur peut toujours naviguer par URL directe vers `/notes`, `/tutor`, `/studio`, `/library`.
  2. `SettingsPage.tsx` n'expose ni l'export/suppression de compte (`POST /account/export`, `DELETE /account`), ni les préférences IA/providers (`/ai/providers`, `/ai/preferences`), ni la planification de sync (`/sources/sync/schedule`), ni la déconnexion d'une source (`DELETE /sources/connections/{id}`).
  3. `DashboardPage.tsx` n'affiche pas de résumé académique (moyenne/objectifs), ni les tâches prioritaires, ni d'étiquette explicite lorsque le jeu de données démo synthétique est actif.
  4. `AnalyticsPage.tsx` n'affiche pas les tendances de notes (`GET /grades/trends`).
- **Fichiers concernés :**
  - `frontend/src/app/AppShell.tsx`, `frontend/src/app/ExamRouteGuard.tsx` *(nouveau)*
  - `frontend/src/features/settings/SettingsPage.tsx`, `frontend/src/features/sources/SourcesCard.tsx`
  - `frontend/src/features/dashboard/DashboardPage.tsx`
  - `frontend/src/features/analytics/AnalyticsPage.tsx`
- **Dépendances :** `G2-T02`, `G2-T04`.
- **Correction attendue :**
  - `ExamRouteGuard` bloque `/notes`, `/library`, `/tutor`, `/studio` dès que `useExamMode().data?.active === true`, avec écran explicite et lien retour vers l'examen.
  - `SettingsPage` permet : export JSON téléchargeable, suppression de compte avec confirmation tapée, activation/choix provider IA + consentement cloud, schedule de sync et révocation de source.
  - `DashboardPage` affiche : carte « Résultats & Objectifs » (lien `/grades`), carte « Tâches prioritaires » (lien `/tasks`), et badge discret « Données synthétiques » si détecté.
  - `AnalyticsPage` ajoute une section « Tendances académiques » distincte de la maîtrise estimée.
- **Critères d'acceptation :**
  - En mode examen, l'accès direct à `/tutor` ou `/notes` est bloqué par l'UI (et l'API renvoie 403).
  - Export JSON fonctionne et télécharge un fichier valide ; tous les tests Vitest passent.
- **Tests à exécuter :**
  - `npm --prefix frontend run typecheck && npm --prefix frontend run lint && npm --prefix frontend test && npm --prefix frontend run build`

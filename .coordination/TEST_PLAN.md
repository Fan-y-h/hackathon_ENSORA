# Ensora+ — Plan de Tests et Validation Finale

**Règle absolue :** Aucun test n'est déclaré réussi sans exécution réelle et sortie vérifiée.

---

## 1. Commandes de validation automatisée

```bash
# 1. Backend — Suite complète (unit + contract + integration + RAG/AI)
cd /home/ensai/ensora-final/backend
/home/ensai/hackathon_ENSORA/.venv/bin/pytest tests -v --tb=short

# 2. Contrat OpenAPI & Inventaire à jour
cd /home/ensai/ensora-final/backend
/home/ensai/hackathon_ENSORA/.venv/bin/python -m tests.contract.handoff --check

# 3. Frontend — TypeScript, ESLint, Vitest, Build production
cd /home/ensai/ensora-final/frontend
npm run typecheck
npm run lint
npm test
npm run build
```

---

## 2. Scénarios de tests obligatoires par domaine

### A. Backend Core, Ingestion & Bridges (G1)
- **T-G1-01 (Fix P0 `test_p1.py`) :** `pytest backend/tests/integration/test_p1.py` → 100% vert (zéro `ModuleNotFoundError` sur `concept-candidates`).
- **T-G1-02 (Idempotence import) :** Importer le même document 2 fois (`POST /api/v1/sources/documents/import`) → 2e appel renvoie `result: "unchanged"`, 0 doublon de version/chunk.
- **T-G1-03 (Serveur unifié & CORS) :** Lancer `scripts/start.py --skip-onboarding` sur port `8000` → `GET /api/v1/health` renvoie `{"status":"ok","database":true,"sources":true}` ; `OPTIONS` depuis `http://127.0.0.1:5173` ET `http://localhost:5173` renvoient `200` avec `access-control-allow-origin`.
- **T-G1-04 (Catalogue `/subjects`) :** `GET /api/v1/subjects` et `GET /api/v1/subjects/{id}` renvoient matières, documents, évaluations, concepts, quiz, decks, notes et artefacts cohérents.
- **T-G1-05 (Bridge `POST /api/v1/grades/import`) :** Importer un arbre Pamplemousse (synthétique en test) → crée un programme P3, 2e appel idempotent, préserve objectifs/scénarios existants ; si sources arrêtées → `503 sources_unavailable`.
- **T-G1-06 (Garde Exam Mode backend) :** Démarrer tentative `mode=exam` → `GET /api/v1/notes`, `POST /api/v1/notes` retournent `403 exam_mode_active` ; soumettre l'examen → `200` rétabli.

### B. RAG, AI Router, Learning Studio & Domaine Métier (G3)
- **T-G3-01 (Indexation & Recherche Hybride) :** Importer 2 documents synthétiques (FR/EN avec formule `Gauss-Markov` et code Python) → `POST /api/v1/rag/reindex` → `GET /api/v1/search?q=Gauss-Markov` renvoie le bon chunk avec locator de page/section ; filtre `document_ids` respecté ; `GET /api/v1/citations/{chunk_id}` résout sans chemin système.
- **T-G3-02 (Hors-corpus & Injection) :** Question sans rapport avec les documents → `evidence_status == "insufficient"` ; document contenant « IGNORE PREVIOUS INSTRUCTIONS » traité comme données inertes.
- **T-G3-03 (AI Tutor & Transparence Provider) :** `GET /api/v1/ai/providers` rapporte l'état réel (Ollama indisponible ≠ disponible) ; `POST /api/v1/tutor/answer` retourne réponse sourcée avec `citations`, `provider`, `model`, `is_fallback` honnête ; persistance dans `conversations`/`messages`.
- **T-G3-04 (Learning Studio & Cache + P3 Link) :**
  - `POST /api/v1/artifacts/generate` (`type=revision_sheet`) → créé et persisté ; 2e appel identique → `cache_hit: true`.
  - `type=flashcards` (`persist_to_learning=true`) → crée un `Deck` visible dans `GET /api/v1/decks`.
  - `type=quiz` (`persist_to_learning=true`) → crée un `Quiz` jouable dans `GET /api/v1/quizzes`.
  - `POST /api/v1/planner/preview` → **0 appel** au générateur IA (`generation_spec` uniquement).
- **T-G3-05 (Garde Exam Mode RAG/AI) :** Pendant tentative `mode=exam` active → `GET /api/v1/search`, `POST /api/v1/tutor/answer`, `POST /api/v1/artifacts/generate` retournent `403 exam_mode_active`.
- **T-G3-06 (Grades S4 + Planner Signals) :**
  - 8 QCM imbriqués = `3.125%` chacun ; `A=13 (coef 4), B=10 (coef 3) → 11.7142857` ; cible 12 → B requis `10.6666667`.
  - Manquant ≠ 0 ; simulation n'altère ni les notes réelles ni `revision`.
  - Règles non vérifiées → `official_status == "unknown"`.
  - `GET /api/v1/grades/signals` modifie l'ordre de priorité dans `POST /api/v1/planner/preview` sans violer aucun plafond ni verrou.

### C. Frontend UX/UI & Consommation API (G2)
- **T-G2-01 (Build & Unitaires) :** `npm run typecheck && npm run lint && npm test && npm run build` 100% verts.
- **T-G2-02 (Fix Planner `BlockDialog` 409 + Confirm + Move) :**
  - Cliquer « Terminée » sur une séance `planned` dans `BlockDialog` → appelle `start` puis `finish` → statut passe à `completed` (0 erreur 409).
  - Cliquer « Confirmer ce créneau » sur un événement `confirmation_required` → disparaît des alertes.
  - Déplacer une séance vers un créneau libre → `PATCH /sessions/{id}` réussit et verrouille.
- **T-G2-03 (GradesPage S4 complet) :**
  - Fonctionne même si Pamplemousse est déconnecté (création programme manuel, UE, matière, épreuve imbriquée, note).
  - Objectif à 14/20 → affiche la note requise et la faisabilité calculées par le serveur (`Decimal`).
  - Simulateur what-if → affiche moyenne projetée + impact sans modifier les notes réelles (vérifié après F5).
  - Bouton « Importer depuis Pamplemousse » gère proprement `200` et `503`.
- **T-G2-04 (Écrans `/subjects`, `/library`, `/tutor`, `/studio`, `/exams`) :**
  - Aucun de ces 5 écrans n'affiche `<PlannedPage />`.
  - `/tutor` affiche les citations cliquables, le provider/modèle réel, le bandeau fallback le cas échéant, et le bouton « Enregistrer dans Mes notes ».
  - `/studio` génère une fiche, des flashcards ou un quiz et lie directement vers `/flashcards` ou `/quiz`.
  - `/exams` permet de lancer un examen blanc chronométré et active le verrouillage UI de `/notes`, `/library`, `/tutor`, `/studio` (y compris accès URL direct).

---

## 3. Parcours Démo Jury End-to-End (Validation Finale SYNC-2)

1. **Démarrage propre :** `scripts/seed_demo.py` (données synthétiques + doc démo) → serveur unifié `:8000` + frontend `:5173`.
2. **Dashboard :** bandeau « Données synthétiques » visible, emploi du temps du jour, prochain examen, KPI notes & tâches, bouton Synchroniser.
3. **Bibliothèque & Matières :** ouvrir `/subjects` → cliquer une matière → voir cours, évaluations, notes, maîtrise ; dans `/library`, importer un fichier texte/PDF, lancer une recherche hybride (`Gauss-Markov`), ouvrir la citation exacte.
4. **AI Tutor :** poser une question sur la matière → recevoir réponse sourcée avec citations ouvrables + badge provider réel → sauvegarder en 1 clic dans `Mes notes`.
5. **Grade Tracker (S4 P0) :** ouvrir `/grades` → inspecter UE/matières/épreuves imbriquées, ajouter un objectif (ex. 14/20), lancer une simulation what-if (voir la note requise & faisabilité sans altérer les notes réelles), constater que le Smart Planner reflète la priorité académique.
6. **Smart Planner :** confirmer un créneau ambigu → prévisualiser un plan respectant les plafonds et créneaux occupés → accepter → verrouiller une séance → clôturer une séance (sans erreur 409) ou démarrer la Focus Room.
7. **Learning Studio → Quiz / Exam Mode → Maîtrise → Replan :** générer un quiz ou des flashcards depuis `/studio` → répondre faux à une question → voir l'erreur ajoutée au carnet d'erreurs et la maîtrise mise à jour → démarrer un examen blanc chronométré (constater le blocage de `/notes` et `/tutor` en 403/UI) → soumettre → prévisualiser et accepter le replan (séances terminées/verrouillées intactes).
8. **Persistance redémarrage :** redémarrer Uvicorn et recharger le navigateur (F5) → toutes les données créées (note, objectif, artefact, séance, maîtrise, profil) sont intactes.

# Ensora+ (`hackathon_ENSORA`)
Améliorons l'ensora, améliorons la vie des étudiants !

## Démarrage de l'application unifiée (local-first)

### 1. Backend unifié (Planner, Grades, Learning, Workspace, Catalog + Sources P1)

Depuis la racine du dépôt :

```bash
# Optionnel : peupler la base locale avec des données synthétiques de démonstration
/home/ensai/hackathon_ENSORA/.venv/bin/python scripts/seed_demo.py

# Démarrer le serveur FastAPI unifié sur http://127.0.0.1:8000
/home/ensai/hackathon_ENSORA/.venv/bin/python scripts/start.py --skip-onboarding
```

Commande Uvicorn équivalente :

```bash
PYTHONPATH=backend:. /home/ensai/hackathon_ENSORA/.venv/bin/uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
```

- Documentation interactive OpenAPI : `http://127.0.0.1:8000/docs`
- Santé du serveur : `http://127.0.0.1:8000/api/v1/health`
- API sources P1 montée sous `/api/v1/sources/*` (jeton local lu côté serveur par le proxy Vite, jamais dans le bundle navigateur)

### 2. Frontend (React + TypeScript + Vite)

```bash
npm --prefix frontend install
npm --prefix frontend run dev   # http://127.0.0.1:5173 (proxy /api -> 127.0.0.1:8000)
```

### 3. Tests et vérifications

```bash
# Backend (unitaires, contrat, intégration, migrations)
cd backend && /home/ensai/hackathon_ENSORA/.venv/bin/pytest tests -v

# Frontend
npm --prefix frontend run typecheck && npm --prefix frontend run lint && npm --prefix frontend test && npm --prefix frontend run build

# Diagnostics connecteurs P1
python3 scripts/check_connections.py
```

Voir aussi [backend/README.md](backend/README.md), [frontend/README.md](frontend/README.md) et [docs/DATA_ENGINEERING.md](docs/DATA_ENGINEERING.md#p1-local-runtime).

# Ensora+

A local-first study workspace for ENSAI students, with a shared agenda, revision planner, academic grade tracker and document-grounded study tools.

## Start the integrated app

From `/home/ensai/ensora-final`, start the backend using the existing Python environment:

```bash
cd /home/ensai/ensora-final
PYTHONPATH=backend:. /home/ensai/hackathon_ENSORA/.venv/bin/python -m uvicorn \
  app.main:create_app --factory --host 127.0.0.1 --port 8000
```

In a separate terminal:

```bash
cd /home/ensai/ensora-final
npm --prefix frontend ci
npm --prefix frontend run dev
```

Open [the app](http://127.0.0.1:5173) or [the API documentation](http://127.0.0.1:8000/docs). The Vite proxy reads the source API token from the local source store. Configure a non-default source store by passing the same `LOCAL_DATA_DIR` to both processes. Authorize each source in Settings before syncing it. Live institutions and calendars require their own valid sessions and consent.

## Start an isolated demo

In the backend terminal, use a fresh directory for both databases and the source settings:

```bash
cd /home/ensai/ensora-final
ENSORA_DEMO_DIR=$(mktemp -d /tmp/ensora-demo.XXXXXX)
export DATA_DIR="$ENSORA_DEMO_DIR/runtime" LOCAL_DATA_DIR="$ENSORA_DEMO_DIR/sources"
export ENSORA_SOURCES_ENV="$ENSORA_DEMO_DIR/config/sources.env"
export SYNC_ON_STARTUP=false SYNC_INTERVAL_MINUTES=0 PYTHONPATH=backend:.
mkdir -p "$DATA_DIR"
printf 'Frontend LOCAL_DATA_DIR=%s\n' "$LOCAL_DATA_DIR"
/home/ensai/hackathon_ENSORA/.venv/bin/python scripts/seed_demo.py
/home/ensai/hackathon_ENSORA/.venv/bin/python -m uvicorn \
  app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Set `LOCAL_DATA_DIR` to the printed path in the frontend terminal and start Vite with the demo label:

```bash
VITE_DEMO_MODE=true npm --prefix frontend run dev
```

To demonstrate RAG, grant local-source read consent in Settings and import `data/demo/sample_econometrie_cours.md` from the Library. Ask the Tutor about Gauss–Markov. A configured model with appropriate consent can generate answers. When no model is reachable the tutor can display cited extracts from the local index. Official academic validation requires verified institutional rules.

## Tests

```bash
(cd backend && /home/ensai/hackathon_ENSORA/.venv/bin/python -m pytest -q)
npm --prefix frontend run build
npm --prefix frontend test
```

Point the Chromium suite at running isolated instances with `ENSORA_E2E_FRONTEND` and `ENSORA_E2E_BACKEND`, then run `python -m pytest frontend/e2e/test_e2e_browser.py -q` with Playwright and Chromium installed.

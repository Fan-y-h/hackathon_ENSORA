# Ensora+

Ensora+ runs as a local, single-student workspace. Real mode uses the student's authorized institutional sources. Demo mode owns separate databases, a separate source configuration and a separate RAG index.

## Open or launch ENSORA+

`bash scripts/run_local.sh real` checks the runtime mode through Vite before opening the real instance in Chrome. Use `bash scripts/run_local.sh demo` for the separate synthetic fallback. The real launcher uses the existing validation copy on this machine. Set `ENSORA_REAL_DIR` to reuse a different copy prepared below.

## Real mode

From the repository root, prepare a private copy of both existing stores. Stop any writer to the source stores while copying their document blobs. The command without `--copy-to` is a read-only provenance audit. It prints counts, never profile or document values.

```bash
cd /home/ensai/ensora-final
python3 scripts/real_store.py --planner data/runtime/ensora.db \
  --source-store "$HOME/.local/share/ensora-plus" \
  --copy-to /tmp/ensora-real-store
```

Use a new destination, or reuse an existing validation copy directly for subsequent launches. In the backend terminal:

```bash
cd /home/ensai/ensora-final
export DATA_DIR=/tmp/ensora-real-store/runtime
export LOCAL_DATA_DIR=/tmp/ensora-real-store/sources
export ENSORA_PAUSE_BACKGROUND_SYNC=true ENSAI_AUTO_LOGIN=false
unset DEMO_MODE ENSORA_DEMO_DIR DATABASE_URL
PYTHONPATH=backend:. /home/ensai/hackathon_ENSORA/.venv/bin/python -m uvicorn \
  app.main:create_app --factory --host 127.0.0.1 --port 18080 --no-access-log
```

In a second terminal, install the locked frontend dependencies if needed and start Vite:

```bash
cd /home/ensai/ensora-final
npm --prefix frontend ci
LOCAL_DATA_DIR=/tmp/ensora-real-store/sources VITE_API_PROXY_TARGET=http://127.0.0.1:18080 \
  npm --prefix frontend run dev -- --port 15180 --strictPort --bypass-lock
```

Open [real mode](http://127.0.0.1:15180). Complete the local student profile, then open **Paramètres → Sources**. Validate a saved connection. If it has expired, choose **Reconnecter**, complete the official CAS or OAuth flow in the opened browser and return to Ensora+. Select Pamplemousse before Moodle to establish the academic catalog. Keep ambiguous matches for manual review. Use source-specific sync controls and verify each individual result.

Google Calendar imports availability by default. Set `GOOGLE_CALENDAR_ID` for the selected calendar and optionally `GOOGLE_CALENDAR_INCLUDE_PERSONAL_DETAILS=true` in the private source configuration to include event labels. The backend keeps these settings and OAuth credentials server-side. Remove `ENSORA_PAUSE_BACKGROUND_SYNC` and restart only when scheduled reads are desired.

For a mixed legacy planner database, run the audit above first. With the app stopped, explicitly add `--remove-unlinked-fixture-events --backup-file /path/to/new-private-backup.sqlite3` to remove only fixture events without linked assessments. For records whose provenance cannot be determined, preserve the original and start with a clean planner database. Manual tasks and notes can represent real work.

## Demo mode

Use a new private directory. Seeding requires an explicit demo mode and rejects an existing non-demo directory. The backend always derives demo storage paths from the dedicated directory.

```bash
cd /home/ensai/ensora-final
export ENSORA_DEMO_DIR=$(mktemp -d /tmp/ensora-demo.XXXXXX)
export DEMO_MODE=true
printf 'Frontend LOCAL_DATA_DIR=%s/sources\n' "$ENSORA_DEMO_DIR"
/home/ensai/hackathon_ENSORA/.venv/bin/python scripts/seed_demo.py
PYTHONPATH=backend:. /home/ensai/hackathon_ENSORA/.venv/bin/python -m uvicorn \
  app.main:create_app --factory --host 127.0.0.1 --port 18081 --no-access-log
```

Use the printed `LOCAL_DATA_DIR` value in the frontend terminal.

```bash
LOCAL_DATA_DIR=/tmp/ensora-demo.REPLACE/sources VITE_API_PROXY_TARGET=http://127.0.0.1:18081 \
  npm --prefix frontend run dev -- --port 15181 --strictPort --bypass-lock
```

Open [demo mode](http://127.0.0.1:15181). The banner identifies synthetic records. For a cited tutor demo, grant read consent for local imports and use **Bibliothèque → Importer** to upload `data/demo/sample_econometrie_cours.md`. Ask the Tutor about Gauss–Markov. Demo uses the local extractive fallback.

## Checks

```bash
(cd backend && /home/ensai/hackathon_ENSORA/.venv/bin/python -m pytest -q)
npm --prefix frontend run build
npm --prefix frontend test
```

Set `ENSORA_E2E_FRONTEND` and `ENSORA_E2E_BACKEND` to a throwaway instance before running `python -m pytest frontend/e2e/test_e2e_browser.py -q`. The Chromium QA suite writes test profile and task data, so keep it off a real store.

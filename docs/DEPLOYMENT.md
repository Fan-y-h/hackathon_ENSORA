# Local deployment and configuration

[RECOMMENDATION] Local-first Linux development (other OS support later). Proposed Python 3.12, supported Node LTS compatible with chosen Vite version, npm, Git/GitHub CLI; confirm versions on actual machine. Docker optional; no database server required for SQLite MVP. All commands below are target runbook after bootstrap, not existing repository capabilities.

## Setup target
```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
# after pyproject and pinned dependencies exist
pip install -e 'backend[dev]'
npm --prefix frontend ci
cp .env.example .env
# after migrations and seed scripts exist
cd backend && alembic upgrade head
```
Start backend from backend directory: `uvicorn app.main:app --host 127.0.0.1 --port 8000`; frontend from root: `npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173`. Seed via approved script (`python scripts/seed_demo.py` target). Docker Compose optional with named runtime volumes, loopback port publishing, no secrets baked into images. Verify `/api/v1/health` and demo login. Do not expose to LAN/public internet without separate security configuration.

## Environment variable contract (proposed)
| Key | Purpose/default |
|---|---|
| APP_ENV | development/test/demo/production |
| DATA_DIR | local ignored runtime directory, per worktree |
| DATABASE_URL | sqlite local file URI |
| VECTOR_DIR | persistent index location |
| APP_SECRET | random local session signing secret; never commit |
| FRONTEND_ORIGIN | exact localhost origin for CORS |
| DEMO_MODE | false unless deliberately enabled; synthetic data only |
| AI_MODE | mock/local/external; explicit setting |
| OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY, MISTRAL_API_KEY, DEEPSEEK_API_KEY | optional server-only provider credentials |
| OLLAMA_BASE_URL | loopback authorized local model endpoint |
| EMBEDDING_MODEL_ID, EMBEDDING_MODEL_REVISION | pinned verified model |
| GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET | optional OAuth config; store refresh token securely separately |
| MOODLE_BASE_URL, MOODLE_TOKEN | only authorized token; no account passwords in repo |
| PAMPLEMOUSSE_BASE_URL | only approved interface; access method TBD |
| MAX_DAILY_TOKENS, MAX_JOB_TOKENS, MAX_DAILY_COST | configurable ceilings, deny unapproved unknown spend |
| SYNC_INTERVAL_MIN, JOB_TIMEOUT_SEC, MAX_UPLOAD_MB | bounded operational limits |
| VITE_API_BASE_URL | public URL only, never secrets |

`.env.example` placeholders only; `.env`, OAuth tokens, runtime databases, indices, files and logs ignored. User keyring preferred for persisted credentials. A CLI login is not a valid runtime key. Revoke any credential accidentally exposed; do not paste it into agent prompts. Google OAuth redirect/scopes and Moodle/Pamplemousse access must be confirmed separately.

## Persistence and recovery
Runtime directories: database, blobs/documents, artifacts/media, index, logs, temp. Restrict permissions; atomic writes, WAL backup via SQLite backup API, include blob manifest, no plaintext secrets. Test restore on isolated directory and rebuild vector index if necessary. Jobs left running on restart become retryable after lease timeout; idempotency prevents duplicate output. Offline mode serves cached files/artifacts and local features, visibly reports unavailable external sync/AI. No automatic cloud upload.

## Demo/release checklist
Clean clone, approved lockfiles, migrate/seed, test health and UI, verify real/mocked AI status, run smoke/E2E, restart, ensure no private data in fixtures, record known limitations and tag reviewed commit. Production deployment, multi-user auth, monitoring and shared storage remain separate approval; local demo configuration is not production-ready.

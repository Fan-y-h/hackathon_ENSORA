# Ensora+ frontend

React 19 + TypeScript + Vite SPA (ADR-02), connected to the FastAPI backend under `/api/v1`.
UI follows the Ensora+ mock-ups (light, dark and accessible modes) and
[docs/FRONTEND_UX_UI.md](../docs/FRONTEND_UX_UI.md).

## Run locally (Windows PowerShell, from the repository root)

```powershell
# 1. Backend (see backend/README.md)
backend/.venv/Scripts/python.exe scripts/seed_demo.py          # optional SYNTHETIC demo data
backend/.venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000

# 2. Frontend
npm --prefix frontend install
npm --prefix frontend run dev        # http://127.0.0.1:5173
```

In development Vite proxies `/api` to `VITE_API_PROXY_TARGET` (default `http://127.0.0.1:8000`),
so the browser makes same-origin calls. For a build served elsewhere, set the public
`VITE_API_BASE_URL` (see `.env.example`; never put secrets in `VITE_*` variables) and make
sure the backend `FRONTEND_ORIGIN` matches.

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Dev server on 127.0.0.1:5173 |
| `npm run typecheck` | `tsc --noEmit` (app + tests) |
| `npm run lint` | ESLint (typescript-eslint, react-hooks) |
| `npm test` | Vitest unit tests (`tests/`) |
| `npm run build` | Typecheck + production build to `dist/` |
| `npm run gen:api` | Regenerate `src/api/schema.d.ts` from the running backend's OpenAPI |

## Structure (docs/PROJECT_STRUCTURE.md)

```text
src/
  app/          App + router, AppShell (sidebar), display preferences, subject context
  api/          client.ts (fetch + error envelope), schema.d.ts (GENERATED), types.ts, queries.ts (TanStack Query hooks)
  components/   shared accessible UI (Card, Stat, Meter, Dialog, QueryState, Icon…)
  features/     dashboard, planner, analytics, focus, learning (flashcards, quiz), tasks,
                workspace (notes, error notebook), notifications, grades, settings, planned
  lib/          time (timezone-aware, pure) and subjects (colour palettes)
  styles/       tokens.css (light/dark/accessible), base.css, Inter fonts (local, OFL)
tests/          Vitest unit tests
```

## Conventions

- **Server is authoritative.** Mutations invalidate the read models they affect; no domain
  calculation is duplicated client-side (grades, mastery, plans are displayed as returned).
- **Contract.** `schema.d.ts` is generated: do not edit it. `types.ts` hand-types only the
  endpoints whose backend response model is still an untyped dict (`/analytics`,
  `/analytics/heatmap`, `/analytics/readiness`, `/analytics/mastery-map`). Replace them by
  generated types once the backend declares response models.
- **Errors.** `ApiError` carries status, code and `request_id`; 409 shows a "data changed,
  reload" state (stale plan preview, revision mismatch); network errors explain how to start
  the backend.
- **Idempotency.** Card reviews and quiz submissions send an `Idempotency-Key`.
- **Planner.** Preview first, nothing is committed until "Accepter ce planning". Supports for a
  session are generated only when it starts (the UI never triggers eager generation).
- **Focus Room.** The timer is display-only, derived from server intervals; it survives reloads.
- **Exam mode.** While `/exam-mode` is active, tutor/notes/studio/library links are disabled.
- **Accessibility.** Text ≥ 4.5:1, marks ≥ 3:1; every block shows an icon or shape + a word +
  the subject acronym, so nothing relies on colour alone. "Unknown" mastery is hatched,
  never shown as 0. Keyboard focus is visible; dialogs close on Escape.
- **No fake data.** Modules without a backend yet (subjects catalog, AI tutor, studio, exam
  trainer) are shown under "Bientôt" and display no simulated content.

## Not yet covered

- Subject names come from agenda titles (`subject_id` only) until the catalog API (C1) exists.
- Sources (P1): Bibliothèque (documents, import, passages), Profil › Sources (consent, connect,
  verify) and the Synchroniser button (sync job → agenda bridge) call `/api/v1/sources/*`. The dev
  proxy adds the P1 bearer token read from `ENSORA_P1_TOKEN_FILE` (default
  `~/.local/share/ensora-plus/api-token`); it never reaches the browser bundle.
- Grades: read-only summary; simulator/objectives screens still to build on the existing API.
- No Playwright E2E yet (docs/TESTING.md); unit tests cover the client, time, calendar layout,
  metrics, subject colours and the focus clock.

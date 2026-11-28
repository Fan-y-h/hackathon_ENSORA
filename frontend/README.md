# ENSORA+ frontend

React 19 + TypeScript + Vite single-page app. Routes and components follow
`docs/FRONTEND_UX_UI.md`; API calls follow `docs/API_CONTRACTS.md` (and the
domain docs it links to). The interface is in French.

## Run locally

```bash
npm install
npm run dev        # http://localhost:5173
```

| Command | Purpose |
|---|---|
| `npm run dev` | Dev server with the demo backend |
| `npm run typecheck` | TypeScript checks only |
| `npm run build` | Real deployment build (`.env.production`: calls the team backend, no demo code) |
| `npm run build:demo` | Jury demo build (`.env.demo`: labelled demo data, no backend needed) |
| `npm run preview` / `npm run preview:demo` | Serve the last build / build the demo and serve it |
| `npm test` | API contract + jury-journey tests against the demo backend (Vitest) |
| `npm run test:e2e` | Browser tests (Playwright, on the demo build): startup, jury journey, axe accessibility on every screen (light/dark), phone width. Set `PW_CHANNEL=msedge` or `chrome` to use an installed browser, or run `npx playwright install chromium` once |

During `npm run dev`, http://localhost:5173/dev/ui shows every design-system
component and a live API playground (never included in the production build).

## Demo data vs real backend

`VITE_USE_MOCKS` decides. In development (`npm run dev`) it defaults to demo data;
`npm run build` reads `.env.production` (`false`: real backend, the demo backend is not
even in the bundle) and `npm run build:demo` reads `.env.demo` (`true`). Override locally
in `.env.local`. During `npm run dev` and `npm run preview`, `/api` is proxied to
`http://localhost:8000`. If the demo backend fails to load, the page says so explicitly
instead of showing vague server errors; while the app starts, `index.html` shows
"Chargement d'Ensora+…" (never a blank page).

The demo backend (`src/mocks/`) reuses MSW request handlers, but answers
`window.fetch` directly instead of using a Service Worker: browsers stop idle
service workers and MSW then silently lets requests through (observed after ~6 s).
It follows the contract: `{items,next_cursor}` lists, error bodies with
`request_id`, 409 on stale revisions, 422 validation, jobs that progress when
polled, and 501 for anything not mocked yet (never a fake success). Data lives
saved in the browser (`localStorage`), so a reload keeps the demo state (the jury
demo ends with "restart and verify retained state"); Paramètres → "Réinitialiser la
démonstration" restores the initial data. The demo requires signing in: use
"Utiliser le compte de démonstration" on `/login` (any ENSAI-like username + password also works in the demo). Demo AI answers and generated
supports are labelled "Démonstration, sans IA"; model and provider names are never
shown to students (developer information only).

## Screens

| Route | Screen |
|---|---|
| `/` | Dashboard: today (classes + planned revisions), priority tasks, deadlines, weak concepts, goals, sources |
| `/subjects`, `/subjects/:id` | Subjects; tabs Programme & level, Resources (Notes / Supports / Results link out) |
| `/library` | Search (keyword / meaning / both), filters, preview with highlighted citation, import |
| `/agenda` | FullCalendar day/week/month/list, imported read-only events, assessment confirmation, unavailability |
| `/planner` | Confirm assessment scope, preview → accept plan (409 if stale), lock / start / finish sessions, replan diff |
| `/tasks` | List or Kanban (keyboard moves), remaining time, project decomposition into editable steps |
| `/tutor` | Grounded chat: citations, evidence status, modes, scope from a library passage, save to notes |
| `/studio` | Generate revision sheets / flashcards / quizzes (cache reuse, edit = new version, history) |
| `/flashcards` | Decks, spaced review (Space + 1–4 keys), card management |
| `/quiz` | Attempts with autosave; correction, mastery change, error notebook |
| `/exams` | Past papers + timed mock exam in an isolated full-screen mode |
| `/notes` | Markdown/LaTeX notes with preview; error notebook tab |
| `/focus` | Pomodoro / free timer from server-stored intervals (survives reload) |
| `/analytics` | Work time chart, plan adherence, quiz score, mastery per subject, weak concepts |
| `/grades` | Curriculum tree, inline marks, goals with required mean, what-if simulation, weights |
| `/login` | Sign in with the ENSAI username + password only; the backend reaches Moodle, Pamplemousse and the Ensora Drive with them and imports the student's data (progress screen). Demo account in mock mode. Every other screen requires a session |
| `/profile`, `/settings`, `/notifications` | Profile, semester, availability & work method; sources, privacy, theme, data; alerts |
| `/onboarding` | Setup in 5 steps (not in the menu, opened from Profil / Paramètres): schooling, sources + download folder, subjects by UE, goals & preferences (formats, per-UE goal, level, course language, starting difficulty), availability deduced from Pamplemousse + Google Agenda |
| `/community` | Planned for a future version (placeholder, by design) |

Product rules enforced in the UI: a missing mark or an unassessed concept is never
shown as 0; no official validation status without verified rules; the TD/TP group
is never asked, only read from Pamplemousse; exam attempts never receive the
answer key; demo data and demo AI output are always labelled.

## Open questions for the backend team

Fields or routes not named in the contract are marked `ASSUMPTION` in
`src/api/types*.ts` and `src/api/hooks*.ts` (e.g. `Profile.display_name`,
`Connection.is_fixture`, events range params `from`/`to`,
`POST /events/{id}/confirm`, `GET /search` returning `{citation, score}` hits,
`GET /artifacts/{id}` returning `{artifact, version}`, `GET /tasks/proposals/{id}`).
Run `grep -rn ASSUMPTION src/api` for the full list.

## Source layout

```
src/
  app/          routes, router (lazy-loaded screens), layouts, theme
  components/   shared blocks (Markdown + KaTeX, citations, query states)
    ui/         design system: import { Button, Card } from "@/components/ui"
    dev/        developer-only pages (never in the production build)
  features/     one folder per screen
  api/          types, HTTP client, React Query hooks (one file per domain)
  mocks/        demo backend: fixtures + handlers per domain
  lib/          helpers (class names, French formatting, labels)
  styles/       design tokens (light/dark), calendar, prose and chart styles
```

## Quality checks done

- `npm test`: 37 tests (contract behaviour + jury journey: grounded tutor, insufficient
  evidence, studio cache/versions, answer key hidden, idempotent submit, flashcard
  intervals, plan without overlaps or cap overflow, stale preview 409, missing ≠ 0,
  simulation writes nothing, weight validation).
- axe-core (WCAG 2 A/AA + best practices) on every screen, light and dark: no violation.
- No horizontal overflow at 390 px width; keyboard focus visible on every control.

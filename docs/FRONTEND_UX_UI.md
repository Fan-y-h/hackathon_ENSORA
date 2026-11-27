# Frontend UX/UI specification

[RECOMMENDATION] React/TypeScript SPA with responsive, accessible UI; French default interface, English locale available. Avoid external fonts/assets dependency for local demo. Use semantic design tokens, keyboard support, visible focus, text labels in addition to color, sufficient contrast and reduced motion. Prioritize desktop student workflow with responsive tablet/mobile layouts.

## Information architecture
| Route | Screen and principal actions |
|---|---|
| /onboarding | Profile, source setup/skip, preference configuration and diagnostic |
| / | Dashboard: today timeline, next exam, work goals, weak concepts, tasks, sync status |
| /subjects, /subjects/:id | Subject cards and tabs: curriculum, library, concepts, notes, artifacts, results |
| /library | Search, filters, file preview, source version, citation anchors, annotations and import |
| /agenda | Unified calendar, source vs personal vs planned indicators, event detail and confirmation |
| /planner | Horizon, preferences, exam scope, proposed sessions, warnings, preview diff, accept/lock/move |
| /tasks | Daily/weekly list, calendar, Kanban, project import/decomposition, remaining work |
| /tutor | Subject/global chat, document selector, modes, citation drawer, save to notes |
| /studio | Artifact type grid, generation configuration, jobs, saved versions and editor |
| /flashcards | Decks, due count, review with reveal and four rating buttons |
| /quiz | Diagnostic/adaptive questions, feedback, scores and concept mapping |
| /exams | Historical exam pairs, mock generation, timed attempt and post-submit correction |
| /notes | Notebook tree, Markdown/LaTeX editor, preview, excerpts, error notebook |
| /focus | Minimal full-screen timer, goal, resources, authorized audio, notes, reflection |
| /analytics | Time, adherence, results, mastery, review heatmap, readiness and recommendations |
| /community | Opt-in share/import/export; later shared service and moderation |
| /profile, /settings | Profile, source/model setup, budgets, local storage, backup/export/delete, theme |
| /notifications | Source changes, deadlines, review due, configurable channels and read state |

## Design system and component ownership
Suggested palette: neutral white/slate surfaces, deep navy text, teal/blue accent, amber warning, red error, green success; semantic colors not source category hard-coded. 4/8px spacing scale, 8–12px radii, clear heading hierarchy; avoid noisy dashboard cards. Components: AppShell (sidebar, topbar, content), SubjectBadge, ResourceCard, DocumentViewer, CitationLink, CalendarEvent, SessionCard, PlanDiff, MasteryIndicator (unknown distinct from zero), JobStatus, SourceStatus, EmptyState, ErrorPanel, ConfirmDialog, accessible form fields. Persist theme and locale. Use charts with labels and explicit denominators.

## Key journeys
Onboarding → choose cohort/track → connect or import demo → subject list → confirm assessment scope → preview/accept plan → start revision → open resources or generate on request → quiz/flashcard → see mastery change → accept revised plan. Second journey: library search → select source passage → ask tutor → open exact citation → save explanation to notes. Third: choose exam → timed session with no solutions or tutor controls → submit → rubric and error notebook → corrective session.

## State and API handling
Use typed generated OpenAPI client, query cache with explicit invalidation after mutations; local component state for transient form input, server as authoritative for persistence. No browser secrets, no duplicate domain calculations except display. Poll job status at bounded interval; cancel, retry and show stale/error details. Plan previews not committed until acceptance; use revision conflict UI. Timer computes elapsed time from server-persisted start/stop intervals and monotonic client display, survives reload; pauses excluded. Exam countdown server authority; hide answer key entirely (not just CSS). Render safe Markdown/KaTeX/code; no arbitrary HTML/JS from model. Distinguish mock/test results, actual provider results, and synthetic dataset. No hardcoded progress displayed as real.

## MVP UX acceptance
At 1280px desktop and narrow viewport, user can navigate the full MVP journey without broken links. Keyboard-accessible controls, error/empty/loading states, visible demo label, source citation navigation, planner warnings and durable session feedback. Non-MVP routes may be explicitly marked planned, not simulated as working. See [API_CONTRACTS](API_CONTRACTS.md) and [MVP](MVP.md).

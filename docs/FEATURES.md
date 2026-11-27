# Feature catalogue and interactions

All modules below preserve the full scope. Implementation order is independent; consult [MVP](MVP.md). Each feature consumes and produces domain objects defined in [DATA_MODEL](DATA_MODEL.md).

| Module | User workflow and behavior | Inputs → outputs / integration |
|---|---|---|
| Onboarding/Profile | Create account or local demo profile, select cohort/track/year/subjects, working hours, rest, goals and preferred study formats; connect sources; optional diagnostic. | Preferences + connections → curriculum scope, planner policy, router policy |
| Dashboard | Daily classes and revisions, goals, priority tasks, next deadlines, readiness, sync state; click through to subject or session. | Calendar/tasks/sessions/analytics → actionable overview |
| My Subjects | Subject/chapter tabs, concepts, resources, notes, generated materials, results; show linked historical exams and solutions. | Catalog + mastery + assessment → subject workspace |
| Library | Search keywords or meaning, filter cohort/track/type/year/chapter, preview original, select passages/formulas/code, annotate and add to notes or tutor. | Ingestion → documents/chunks; selection → notes/RAG |
| Agenda | Day/week/month views, distinguish imported read-only events from personal/planned items; resolve ambiguous assessments, add unavailability. | External events + tasks + sessions → availability snapshot |
| Smart Planner | Confirm exam scope, generate and preview multi-subject plan, accept, move or lock a session, complete/skip/partial, inspect unscheduled work. | Curriculum + estimates + mastery + calendar → versioned plan |
| Tasks | Import assignment; create or decompose TXT/PDF project instructions into editable steps, estimate effort and attach resources; daily/weekly/list/calendar/Kanban; track remaining time. | Assignments/AI/user → tasks → planner/focus |
| AI Tutor | Subject or global chat with explicit document selection, proof, code, explanation, correction, Socratic mode; cite and open source; save message to notebook. | Selected scope + RAG + router → grounded conversation |
| Learning Studio | Choose artifact type, source scope, objectives, language, model, difficulty/duration/detail and instructions; generate, edit, view history, regenerate and reuse. | RAG + router → persisted artifact version |
| Flashcards | Create/edit/deck by concept, reveal answer, rate Again/Hard/Good/Easy, show due count and review history. | Cards → review evidence + next due date |
| Quiz | Diagnostic/adaptive practice; single/multi-choice, T/F, fill, open, calculation and applied question; corrections and difficulty changes. | Questions/attempts → evidence per concept |
| Exam Trainer | Browse past papers, generate mock with rubric, choose duration, enter isolated exam mode, submit/expire, grade and inspect errors. | Exam + attempt → correction, error entries, mastery |
| Smart Notes | Markdown/LaTeX editor, source selection, code/table/image insertion, AI restructuring with citations; capture tutor corrections; maintain error notebook. | Notes + source anchors → optional study sources |
| Focus Room | Start planned or free activity, set timer and authorized music/theme, open resources, capture notes, pause/extend/stop, report objectives and difficulty. | Session → active work log + self-assessment |
| Learning Analytics | Daily/weekly/monthly time, adherence, heatmap, results, mastery map, readiness and weak concepts; link to corrective activity. | Persisted evidence/work logs → aggregates and recommendations |
| Community | Share explicitly selected decks, sheets and resources, peer correction, inter-cohort exchange; local package import/export first, later moderated shared service. | Opt-in share → versioned content with attribution and rights |
| Settings/Notifications | Manage profile, sources, quotas, models, local data, backups, theme, notification channels; mark alerts read. | Preferences and domain events → policy/notifications |

## End-to-end lifecycle
1. Sync identifies exam and resources; uncertain slot/scope is confirmed. Catalog maps aliases and revisions.
2. Analysis extracts syllabus and concepts with citations; assessment scope references current curriculum, not only historical exams.
3. Adaptive service creates study needs; planner reserves time and future review windows without generating artifacts.
4. Student accepts plan, starts session; Studio checks cache and retrieves/generates only requested content. Focus tracks actual time.
5. Quiz/card/exam outcome produces evidence and error entries; mastery and next due dates update transactionally.
6. Planner previews limited changes to remaining activities. Dashboard and analytics show new state. Original source data and completed history remain intact.

## State conventions
Document: discovered → downloaded → parsed → indexed, or failed/blocked/tombstoned. Generation job: queued → running → succeeded/failed/cancelled. Session: planned → in_progress → completed/partial/skipped/cancelled. Exam: draft → active → submitted/expired → graded. A lock is independent of session status. A failed job never creates a successful artifact; retries are explicit and idempotent. Source refresh must not silently reset a user's edit or review.

Domain details: [DATA_ENGINEERING](DATA_ENGINEERING.md), [AI_ENGINE](AI_ENGINE.md), [SMART_PLANNER](SMART_PLANNER.md), [ADAPTIVE_LEARNING](ADAPTIVE_LEARNING.md), [FRONTEND_UX_UI](FRONTEND_UX_UI.md).

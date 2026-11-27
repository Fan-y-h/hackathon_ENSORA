# API contracts — proposed v1

[RECOMMENDATION] Freeze these conventions before feature branches; generate OpenAPI from Pydantic and TypeScript types from the same schema. Contract changes require integration-owner review. Base `/api/v1`, JSON UTF-8, snake_case, UUID IDs, UTC timestamps, minutes integer. Authenticated user resolved from session, not request body. All list endpoints accept `limit` (default 50, max 200) and opaque `cursor`, return `{items,next_cursor}`. Mutations return resource and revision or 202 job. Errors use `{error:{code,message,details,request_id}}`; 400 invalid input, 401 auth, 403 forbidden/policy, 404 absent, 409 stale/conflict, 422 validation, 429 quota, 503 unavailable. No secret values in responses. POST jobs and evidence accept `Idempotency-Key`; revision checks use `expected_revision` or If-Match.

## Endpoint catalogue
| Method/path (relative to base) | Request / response and behavior |
|---|---|
| GET /health | service readiness, DB/index availability; no credentials |
| POST /auth/login, POST /auth/logout | local credentials/session; logout invalidates; explicit demo mode only |
| GET/PATCH /profile | Profile, Preferences; validate cohort and limits |
| GET/PATCH /settings | provider policy, sync preferences, notification channels; secrets write-only |
| GET /connections; POST /connections | list/create source config; authorization handshake separately |
| POST /connections/{id}/authorize; DELETE /connections/{id} | authorized OAuth flow or revoke locally |
| POST /sync | `{connection_id?,subject_id?,folder_id?}` → Job; selective or all |
| GET /sync/runs; GET /jobs/{id}; POST /jobs/{id}/cancel | sync history and job status; cancel cooperative |
| GET/POST /subjects; GET/PATCH /subjects/{id} | catalog and manual mapping |
| GET /subjects/{id}/chapters; GET /subjects/{id}/concepts | chapter/concept list with evidence |
| PATCH /concepts/{id}; POST /subjects/{id}/aliases | confirm mapping/prerequisites via approved DTO |
| GET /documents; POST /documents/import | filters subject/chapter/type/source/year; multipart file + metadata → Job |
| GET /documents/{id}; GET /documents/{id}/versions/{version_id}/content | metadata, authorized blob stream |
| PATCH /documents/{id}; POST /documents/{id}/links | classify/associate solution, expected revision |
| GET /search | q, subject_id, document_ids, mode lexical/semantic/hybrid → hits with citations |
| GET /citations/{id} | authorized exact locator and excerpt |
| GET/POST /events; PATCH/DELETE /events/{id} | calendar range, personal events; imported originals read-only; separate confirmation action |
| GET /assessments; PATCH /assessments/{id} | scope confirmation, format, concept IDs, revision |
| GET/POST /tasks; PATCH/DELETE /tasks/{id} | CRUD, status, remaining_min, priorities, user overrides |
| POST /tasks/decompose | instructions/document IDs → Job returning editable proposal |
| POST /planner/preview | horizon, optional overrides → PlanPreview; no plan commit |
| POST /planner/accept | preview_id, input_revision → accepted Plan; conflict 409 if stale |
| GET /plans/current; PATCH /sessions/{id} | get current, move/lock with constraint validation |
| POST /sessions/{id}/start; POST /sessions/{id}/finish | state transition, actual/remaining time, reflection; start can request selected artifact |
| POST /planner/replan | reason, base_plan_revision → preview/diff |
| GET/POST /conversations; GET /conversations/{id}/messages | scoped history |
| POST /conversations/{id}/messages | TutorRequest → response or Job, citation IDs, evidence status |
| POST /artifacts/generate; GET /artifacts | GenerationSpec → cached artifact or 202 Job; filters |
| GET/PATCH /artifacts/{id}; GET /artifacts/{id}/versions | content/version history; edits create new version |
| GET /providers; GET /usage | capabilities, permitted model IDs, budget consumption |
| GET/POST /decks; GET/POST /decks/{id}/cards | create/list decks/cards; patch card by ID |
| GET /reviews/due; POST /cards/{id}/review | rating + reviewed_at server-validated → due date, evidence |
| GET/POST /quizzes; POST /quizzes/{id}/attempts | create definition or attempt; active payload excludes answer key |
| PATCH /attempts/{id}/responses; POST /attempts/{id}/submit | response save and idempotent finalize; server checks expiry |
| GET /attempts/{id}/result | post-submit rubric, score, feedback; 403 while active |
| GET /mastery; GET /study-needs | subject/concept filter; estimates and next actions |
| GET/POST /notes; PATCH/DELETE /notes/{id} | Markdown, citations, revision, user-owned notes |
| GET/POST /annotations; GET/POST /errors | document anchors and error notebook |
| POST /focus/start; POST /focus/{id}/transition; POST /focus/{id}/finish | work/break intervals and reflection; idempotent state machine |
| GET /analytics | range, subject filter → work, adherence, scores, mastery, readiness |
| GET /notifications; PATCH /notifications/{id} | read state; configured delivery later |
| POST /shares/export; POST /shares/import | rights-checked package; later shared service |
| POST /account/export; DELETE /account | explicit confirmation, asynchronous export/deletion |

## Canonical DTO examples (illustrative UUID placeholders)
```json
{"id":"uuid","subject_id":"uuid","title":"PPDP","type":"exam","start_at":"2026-12-07T08:30:00+01:00","end_at":"2026-12-07T10:30:00+01:00","source":"pamplemousse","confirmation_required":true,"revision":1}
```
Calendar output normalizes timestamps to UTC; input accepts explicit offsets. Event type enum class/td/tp/exam/presentation/deadline/personal/other; planned sessions returned separately or with `origin=planner`.

```json
{"type":"revision_sheet","subject_id":"uuid","document_ids":["uuid"],"concept_ids":["uuid"],"objective":"understand","locale":"fr","difficulty":"intermediate","duration_min":20,"detail_level":"detailed","model":"auto","user_instructions":"Include formulas","force_regenerate":false}
```
```json
{"id":"uuid","kind":"generation","status":"queued","progress":0,"result_id":null,"error":null}
```
```json
{"preview_id":"uuid","input_revision":"snapshot-hash","sessions":[{"id":"uuid","subject_id":"uuid","concept_ids":["uuid"],"activity_type":"active_recall","start_at":"2026-12-06T13:00:00Z","end_at":"2026-12-06T13:30:00Z","estimated_min":30,"objective":"Recall definitions","success_criteria":"Answer independently","resource_ids":["uuid"],"locked":false}],"unscheduled":[{"need_id":"uuid","remaining_min":30,"reason":"CAPACITY"}],"warnings":[]}
```
```json
{"question":"Explain k-anonymity","subject_id":"uuid","document_ids":["uuid"],"mode":"explain","locale":"en","model":"auto"}
```
TutorResponse: `{message_id,answer_markdown,citations:[Citation],evidence_status,provider,model,usage}`. Citation defined in [RAG_ARCHITECTURE](RAG_ARCHITECTURE.md). ReviewRequest `{rating:"again|hard|good|easy", expected_revision:1}`; ReviewResult `{card_id,next_due_at,interval_days,mastery_updates}`. AttemptResponse includes question IDs, prompts, options, points, expires_at and status only; result adds score, corrections and evidence after submit. Job status enum queued/running/succeeded/failed/cancelled. Session status defined in [FEATURES](FEATURES.md).

## Contract tests
Validate schema examples, unauthorized and wrong-user requests, 409 stale preview, idempotent repeated review/submit, active exam response with no private key/citations, provider failure and quota, import result and job polling, empty lists, timezone handling, client generated types. Mock server uses same DTO fixtures; no frontend-only field inventions. Endpoints not implemented at MVP must be documented as planned, not falsely return success.

## Academic API extension [RECOMMENDATION]
All routes relative to `/api/v1`; existing auth, revision, pagination and error contracts apply. Decimal grades/weights serialized as strings to avoid binary rounding; frontend never authoritatively calculates validation.
| Method/path | Request / response |
|---|---|
| GET/POST /grades/programs | list/create academic program |
| GET/PUT /grades/programs/{id}/curriculum | versioned recursive node tree; atomic replace with expected_revision; validate references and weights |
| GET/PUT /grades/programs/{id}/rules | read/update versioned rule config and verification provenance; official status unknown unless verified |
| GET/POST /grades/records; PATCH/DELETE /grades/records/{id} | actual/estimated record CRUD, revision/provenance; deletion preserves audit history |
| GET/POST /grades/objectives; PATCH/DELETE /grades/objectives/{id} | node-level personal target CRUD |
| GET /grades/summary | program_id, optional node_id → hierarchical actual/provisional/forecast coverage, bounds, validation status |
| POST /grades/required | program_id, objective_ids, optional explicit fixed assumptions → necessary remaining mean, feasibility, example combinations, reasons |
| POST /grades/simulate | program_id, expected_revision, values [{node_id,value}] → isolated scenario projection, impact and warnings; no actual writes |
| GET/POST /grades/scenarios; PATCH/DELETE /grades/scenarios/{id} | optional persisted named overlays; owner isolation |
| GET /grades/signals | program_id → academic signals + input revision; planner uses internal typed equivalent |
| POST /grades/records/{id}/feedback | optional corrected_document_id, comments, difficulty flags → diagnostic proposal/job; no direct mastery write |
| POST /grades/import | authorized normalized academic import or fixture; dry-run/conflict report then explicit commit |
Example summary: `{"node_id":"uuid","provisional":"13.00","actual_coverage":"0.5714286","forecast":null,"remaining_weight":"0.4285714","official_status":"unknown","revision":4}`. Required response includes `required_mean`, `feasibility: feasible|guaranteed|impossible|unknown`, `assumptions`, `combinations`, `reason_codes`. Validation status independent of feasibility. Academic events: `grade.recorded`, `grade.updated`, `grade.deleted`, `curriculum.changed`, `academic.objective_changed`, `academic.rules_changed`; carry user/program/node IDs, revision and event ID, not raw private marks in logs. Planner preview includes academic snapshot revision, reason codes and existing constraints. Return 409 for stale inputs, 422 for cycles/invalid weights/scale/out-of-range grades. Test actual versus scenario isolation, no cross-user access and zero/missing distinction.

## Implemented P1 routes

The local FastAPI OpenAPI schema is served at `/openapi.json`, with Swagger at `/docs`. The readiness route is public. P1 data routes accept the bearer token from `api-token` in the private runtime data directory while the process is running. This is a local single-user session. Deploying a networked or multi-user service requires a separate identity design.

Use `GET /api/v1/onboarding` and `POST /api/v1/onboarding/complete` for onboarding state. `GET /api/v1/connections` lists source status and consent; `POST /api/v1/connections` resolves a source to its connection ID. Update that connection's consent with `PUT /api/v1/connections/{id}/consent`, with purpose `read` or `automatic_sync`. Use its `authorize`, `reauthorize` and `validate` actions for the adapter lifecycle. Use `DELETE /api/v1/connections/{id}` to withdraw access.

`POST /api/v1/sync` accepts `source`, `subject_id`, `folder_id` and `full`; it returns a job ID. Use `GET /api/v1/jobs/{id}`, `POST /api/v1/jobs/{id}/cancel`, `GET /api/v1/sync/runs` and `GET /api/v1/pipeline/status` for job tracking.

P2 can read the documents list, exact-version content and `GET /api/v1/documents/{id}/versions/{version_id}/chunks`. P3 can read `events`, `availability`, `subjects`, `teaching-units`, `assessments`, `grades`, `coefficients` and `assessment-weights` under `/api/v1`. Assessment-weight records include `weight_basis`: `source_reported` for a value explicitly supplied as relative/fractional, or `normalized_sibling_coefficients` when P1 derives a parent-local fraction from a complete, healthy set of known raw sibling coefficients. In the latter case the original raw `coefficient` records remain available unchanged. Use multipart `POST /api/v1/documents/import` for a local file or `POST /api/v1/academic/import` for normalized facts. The generated OpenAPI describes the currently callable routes. The earlier catalogue preserves the roadmap.

## Institutional sign-in and acquisition recovery

Institutional validation checks reuse the existing configured browser profile without opening an interactive login window. A protected service probe must confirm authorization before the bundled backend reports CONNECTED. CAS session state and service access are separate fields on the authorization and validation responses. An institutional service denial returns ACCESS_DENIED. A missing or expired session returns REAUTH_REQUIRED.

A sync that encounters a CAS challenge finishes as a partial job. Its source report includes connection_status, auth_reason and next_action. An operator can invoke POST /api/v1/connections/{id}/reauthorize to open an official sign-in flow. After a successful status check, POST /api/v1/jobs/{id}/resume accepts an optional source selector and returns a successor job ID. Supply the source when more than one source awaits reauthentication. Repeated requests for the same interrupted job and source return the same successor. Resuming a completed selective run uses the original subject and folder filters. An interrupted incremental run reuses its last durable checkpoint. Transient pagination state is discarded and successfully persisted facts replay idempotently.

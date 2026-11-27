# Data model and persistence

[RECOMMENDATION] Relational database is authoritative; vector index is rebuildable. All IDs UUID strings; timestamps ISO 8601 UTC (render Europe/Paris by default), calendar all-day dates separate from instants. Every private row carries user_id directly or through an enforced parent. FK checks enabled; migrations versioned; never expose internal file paths or credential values.

## Entities and keys
Notation: PK `id` implicit, FK `→`, UQ unique. All mutable records have created_at, updated_at, revision. Append-only facts have occurred_at. JSON fields validated against versioned schemas.

| Entity | Important columns / constraints |
|---|---|
| users | email UQ, display_name, password_hash nullable for explicitly local demo auth |
| profiles | user_id UQ → users, cohort enum, track, academic_year, locale, timezone, goals JSON |
| preferences | user_id UQ, work_windows JSON, rest_windows, daily/weekly limits, min/max session, formats, provider policy |
| connections | user_id, source_kind, remote_account_key, scope JSON, credential_ref, status, cursor, last_success_at; no token plaintext |
| sync_runs | connection_id, scope, status, counts, checkpoint, safe_error, started/finished |
| subjects | user_id, canonical_code nullable, title, cohort, track, academic_year; UQ user/code/year when code exists |
| subject_aliases | subject_id, source_kind, normalized_alias, external_key, confidence, confirmed; UQ user/source/external_key |
| chapters | subject_id, title, ordinal, active |
| concepts | chapter_id, label, description, category, difficulty, importance, evidence_refs JSON, curriculum_revision, active |
| concept_edges | prerequisite_id, dependent_id, confidence, confirmed; UQ pair, reject cycles in confirmed graph |
| documents | user_id, subject_id nullable, chapter_id nullable, title, resource_type, active_version_id, source_status, classification_confidence |
| document_origins | document_id, connection_id nullable, external_id, source_url, remote_revision, remote_modified_at, folder_path, rights_status; UQ connection/external_id |
| document_versions | document_id, content_hash, MIME, size_bytes, blob_key, parser_version, status; UQ document/hash |
| chunks | version_id, ordinal, content, section_path, locator JSON, content_hash, index_revision; UQ version/ordinal/parser revision |
| document_links | from_document_id, to_document_id, relation (solution_of, covers, related), confidence, confirmed |
| events | user_id, connection_id nullable, external_id nullable, subject_id nullable, type, start_at/end_at or all_day_date, title, room, teacher, group, status, confirmation_required, recurrence_key |
| assessments | event_id nullable, subject_id, deadline_at, format, duration_min, difficulty, scope_status, scope_revision, status |
| assessment_concepts | assessment_id, concept_id, weight, source_refs, confirmed; UQ pair |
| tasks | user_id, parent_task_id nullable, subject_id nullable, external_key nullable, title, description, deadline_at, estimated_min, remaining_min, priority, status, locked, origin |
| task_resources | task_id, document_id or artifact_id, relation |
| plans | user_id, horizon_start/end, input_revision, status, score, warnings JSON; accepted_revision |
| study_sessions | plan_id, user_id, task_id/assessment_id nullable, start_at/end_at, estimated_min, actual_min, remaining_min, status, locked, activity_type, difficulty, objective, success_criteria, generation_spec JSON |
| session_concepts | session_id, concept_id, dimension, weight |
| session_resources | session_id, document_id or artifact_id, role |
| artifacts | user_id, subject_id/chapter_id nullable, type, title, active_version_id, status |
| artifact_versions | artifact_id, parent_version_id nullable, content JSON/blob_key, source_snapshot JSON, prompt_version, provider/model, cache_key, stale, edited_by_user, citations JSON |
| jobs | user_id, kind, payload JSON, idempotency_key, status, attempt_count, heartbeat, result_id, error_code; UQ user/kind/key |
| conversations / messages | conversation: user_id, subject_id nullable, title; message: conversation_id, role, content, citations, model, occurred_at |
| decks / flashcards | deck: user_id, subject_id, title; card: deck_id, concept_id, front, back, citations, state, revision |
| card_reviews | card_id, rating, reviewed_at, interval_days, due_at, algorithm_version, idempotency_key |
| quizzes / questions | quiz: user_id, subject_id, type, source_snapshot, rubric, version; question: quiz_id, type, prompt, options, private_answer, rubric, points, concept/dimension mapping |
| attempts / attempt_items | attempt: user_id, quiz_id/version, mode, started_at, expires_at, status, score; item: attempt_id, question_id, response, score, feedback, assisted, duration; UQ attempt/question |
| evidence | user_id, concept_id, dimension, activity_id, score nullable, difficulty, assisted, confidence, weight, occurred_at; UQ activity/item/concept/dimension |
| mastery | user_id, concept_id, dimension, score nullable, evidence_count, confidence, last_review_at, next_due_at, risk, algorithm_version; UQ user/concept/dimension |
| notebooks / notes | notebook: user_id, subject_id; note: notebook_id, chapter_id, concept_id, title, markdown, version |
| annotations / errors | annotation: user_id, note_id nullable, document_version_id, locator, excerpt, comment; error: user_id, concept_id, attempt_item_id nullable, category, reasoning, remediation, resolved_at |
| focus_sessions / work_intervals | focus: user_id, study_session_id/task_id nullable, mode, goal, status, reflection JSON; intervals: focus_id, started_at, ended_at, kind (work/break) |
| provider_usage | user_id, provider, model, job_id, tokens_in/out, estimated_cost, currency, status, occurred_at |
| notifications | user_id, event_type, target_id, channel, read_at, delivery_status, dedupe_key |
| share_packages | user_id, artifact_version_ids, license, attribution, exported_at; future community adds shared identities and moderation |
| outbox | event_id UQ, user_id, entity_id, type, payload, schema_version, delivered_at |

## Relationships and lifecycle
Subject 1:N chapters → concepts; document N:M origins/links and 1:N immutable versions → chunks. Assessment N:M concepts. Plan 1:N sessions; sessions N:M concepts/resources. Quiz version 1:N questions → attempt items → evidence → mastery projection. Notes cite exact document versions. Artifacts snapshot all source versions and prompt/model configuration. Edits create new versions, never replace a cached generated version silently.

Store blobs by content hash with atomic temp-file rename, but authorize references per user; identical bytes do not imply shared permissions. Soft-delete remote origins on removal; exclude unauthorized data from retrieval immediately. Preserve history and user-authored notes subject to rights and deletion requests. Backups include database, blobs and configuration metadata (not plaintext keys); index can be rebuilt. Use transactions for mutations and optimistic revision on plan/notes updates. Index common user/subject, due_at, status, external keys and active version columns. FTS rows contain chunk ID and text; vector IDs use same chunk ID and embedding revision.

## Migration ownership
Core integration owner creates base schema and approves all revision IDs; domain agents submit isolated schema patches. One migration head before merge. Tests: fresh install, upgrade from prior snapshot, foreign keys, restart persistence, cross-user isolation, deletion and reindex. See [API_CONTRACTS](API_CONTRACTS.md).

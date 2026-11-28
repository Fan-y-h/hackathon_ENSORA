"""Local single-user P1 API (source ingestion, sync, documents, academic records).

Moved from ``backend/app/main.py`` (branch feat/p1-data-engineering) when it was merged
with the planner/learning backend. Route code is unchanged; two options were added:

* ``prefix``: the routes' prefix (``/api/v1`` standalone, ``""`` when mounted under
  ``/api/v1/sources`` by the unified composition root ``app.main``);
* ``managed_runtime``: when ``True`` the parent application owns the ``Runtime``
  lifecycle and CORS, so this sub-application has no lifespan and no CORS middleware.
"""
from contextlib import asynccontextmanager
import hashlib
import secrets
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, insert, literal_column, select, tuple_, union_all, update
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.contracts.authentication import AuthenticationResult, ConnectionStatus
from backend.app.contracts.sources import Contract, Document, Key, ParsedContent, PersonalAvailability, Provenance, SourceKind, SourceRecord, SyncScope
from backend.app.contracts.users import Preferences, Profile
from backend.app.contracts.sync import SyncScheduleSettings
from backend.app.core.database import LOCAL_USER, academic_overrides, availability, chunks, connections, documents, new_id, records, runs, users, versions
from backend.app.core.runtime import Runtime
from backend.app.core.sync import ResumeUnavailable
from backend.app.core.settings import ProtectedImportPath, Settings


class ConsentInput(Contract):
    purpose: Literal["read", "automatic_sync"]
    granted: bool


class CalendarDetailsInput(Contract):
    enabled: bool


class ConnectionSetupInput(Contract):
    subject_id: Key | None = None


class ConnectionSetupResult(AuthenticationResult):
    job_id: str | None = None


class SourceSelection(Contract):
    source: SourceKind


class AcademicOverride(Contract):
    fields: dict[str, object]


class DocumentMatchInput(Contract):
    subject_id: Key
    chapter_id: Key | None = None


class ResumeSelection(Contract):
    source: SourceKind | None = None


def create_app(
    settings=None, components=None, *, prefix: str = "/api/v1",
    managed_runtime: bool = False, document_guard=None,
):
    @asynccontextmanager
    async def lifespan(application):
        with Runtime(settings or Settings.load(), components) as runtime:
            application.state.runtime = runtime
            runtime.start_schedule()
            yield

    app = FastAPI(title="Ensora+ P1", lifespan=None if managed_runtime else lifespan)

    class RuntimeCORS:
        def __init__(self, app):
            self.app = app
            self.middleware = None

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http" or not hasattr(scope["app"].state, "runtime"):
                return await self.app(scope, receive, send)
            if self.middleware is None:
                origin = str(scope["app"].state.runtime.settings.frontend_origin).rstrip("/")
                self.middleware = CORSMiddleware(
                    self.app, allow_origins=[origin],
                    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
                )
            await self.middleware(scope, receive, send)

    if not managed_runtime:
        app.add_middleware(RuntimeCORS)

    def runtime(request: Request):
        current = getattr(request.app.state, "runtime", None)
        if current is None:  # mounted while the source runtime could not start
            raise HTTPException(503, "Source synchronization is unavailable")
        return current

    bearer = HTTPBearer(auto_error=False)

    def authorize(request: Request,
                  credential: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        value = credential.credentials if credential else ""
        if not value.isascii() or not secrets.compare_digest(value, runtime(request).api_token):
            raise HTTPException(401, "Local session required")

    def error(status, message):
        return JSONResponse(status_code=status, content={"error": {
            "code": f"http_{status}", "message": message, "details": {}, "request_id": new_id(),
        }})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request, exc):
        return error(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc):
        return error(422, "Invalid request")

    @app.exception_handler(Exception)
    async def unexpected_error(_request, _exc):
        return error(500, "Internal application error")

    @app.get(f"{prefix}/health")
    def health(request: Request):
        with runtime(request).engine.connect() as db:
            db.execute(select(users.c.id).limit(1))
        return {"status": "ready", "database": "ready"}

    api = APIRouter(prefix=prefix, dependencies=[Depends(authorize)])
    document_dependencies = [Depends(document_guard)] if document_guard is not None else []

    def page(db, query, id_column, limit, cursor, order=None):
        if order is None:
            if cursor:
                query = query.where(id_column > cursor)
            sort = (id_column,)
        else:
            query = query.add_columns(order.label("_order"))
            if cursor:
                if "|" in cursor:
                    position, key = cursor.split("|", 1)
                    if getattr(order, "name", None) == "documents.rowid":
                        try:
                            position = int(position)
                        except ValueError:
                            raise HTTPException(400, "Invalid cursor") from None
                else:
                    position = db.scalar(select(order).where(id_column == cursor))
                    key = cursor
                if position is None:
                    raise HTTPException(400, "Invalid cursor")
                query = query.where(tuple_(order, id_column) > (position, key))
            sort = (order, id_column)
        found = [dict(row) for row in db.execute(query.order_by(*sort).limit(limit + 1)).mappings()]
        result = found[:limit]
        next_cursor = None
        if len(found) > limit:
            last = result[-1]
            next_cursor = last["id"] if order is None else f"{last['_order']}|{last['id']}"
        for row in result:
            row.pop("_order", None)
            report = row.get("report")
            if isinstance(report, dict):
                report.pop("_checkpoint_cursors", None)
        return {"items": result, "next_cursor": next_cursor}

    Limit = Annotated[int, Query(ge=1, le=200)]

    def account(request, column):
        with runtime(request).engine.connect() as db:
            return db.scalar(select(column).where(users.c.id == LOCAL_USER))

    def set_account(request, **values):
        with runtime(request).engine.begin() as db:
            db.execute(update(users).where(users.c.id == LOCAL_USER).values(**values))

    @api.get("/onboarding")
    def onboarding(request: Request):
        return {"completed": account(request, users.c.onboarded), "sources": list(SourceKind)}

    @api.post("/onboarding/complete")
    def onboarding_complete(request: Request):
        set_account(request, onboarded=True)
        return {"completed": True}

    @api.get("/settings")
    def settings_view(request: Request):
        state = runtime(request).settings
        return {"sync_on_startup": state.sync_on_startup, "sync_interval_minutes": state.sync_interval_minutes,
                "max_document_bytes": state.max_document_bytes, "google_project_id": state.google_project_id,
                "preferences": account(request, users.c.preferences)}

    @api.patch("/settings")
    def settings_update(request: Request, body: Preferences):
        current = Preferences.model_validate(account(request, users.c.preferences) or {})
        if "sync_schedule" not in body.model_fields_set:
            body = body.model_copy(update={"sync_schedule": current.sync_schedule})
        body = body.model_copy(update={"calendar_details": current.calendar_details})
        set_account(request, preferences=body.model_dump(mode="json"))
        return body

    @api.get("/calendar/details", response_model=CalendarDetailsInput)
    def calendar_details(request: Request):
        return CalendarDetailsInput(enabled=runtime(request).calendar_details_enabled())

    @api.put("/calendar/details", response_model=CalendarDetailsInput)
    def update_calendar_details(request: Request, body: CalendarDetailsInput):
        ctx = runtime(request)
        with ctx.auth.locks[SourceKind.GOOGLE_CALENDAR]:
            if body.enabled and not ctx.consent.allowed(SourceKind.GOOGLE_CALENDAR, "read"):
                raise HTTPException(403, "Calendar read consent is required")
            with ctx.engine.connect() as db:
                running = db.scalar(select(runs.c.id).where(
                    runs.c.user_id == LOCAL_USER,
                    runs.c.status.in_(("queued", "running")),
                    func.coalesce(runs.c.scope["source"].as_string(), SourceKind.GOOGLE_CALENDAR.value)
                        == SourceKind.GOOGLE_CALENDAR.value,
                ).limit(1))
            if running is not None:
                raise HTTPException(409, "Wait for the calendar sync to finish")
            ctx.update_calendar_details(body.enabled)
        return body

    @api.get("/sync/schedule")
    def sync_schedule_status(request: Request):
        return runtime(request).schedule_status().model_dump(mode="json")

    @api.patch("/sync/schedule")
    def sync_schedule_update(request: Request, body: SyncScheduleSettings):
        return runtime(request).update_schedule(body).model_dump(mode="json")

    @api.post("/sync/schedule/reset")
    def sync_schedule_reset(request: Request):
        return runtime(request).update_schedule(SyncScheduleSettings()).model_dump(mode="json")

    @api.get("/profile")
    def get_profile(request: Request):
        return account(request, users.c.profile)

    @api.patch("/profile")
    def set_profile(request: Request, body: Profile):
        set_account(request, profile=body.model_dump(mode="json"))
        return body

    @api.get("/connections")
    def list_connections(request: Request, limit: Limit = 50, cursor: str | None = None):
        ctx = runtime(request)
        with ctx.engine.connect() as db:
            data = page(db, select(connections.c.id, connections.c.source, connections.c.status,
                                   connections.c.last_success_at).where(connections.c.user_id == LOCAL_USER),
                        connections.c.id, limit, cursor)
        for item in data["items"]:
            source = SourceKind(item["source"])
            item["validation_required"] = ctx.auth.needs_validation(source)
            if item["validation_required"]:
                item["status"] = "NOT_CONFIGURED"
            item["consent"] = {purpose: ctx.consent.allowed(source, purpose)
                               for purpose in ("read", "automatic_sync")}
        return data

    @api.post("/connections")
    def choose_connection(request: Request, body: SourceSelection):
        return {"id": runtime(request).connection_ids[body.source], "source": body.source}

    def source_id(ctx, connection_id):
        for source, identifier in ctx.connection_ids.items():
            if identifier == connection_id:
                return source
        raise HTTPException(404, "Unknown connection")

    @api.put("/connections/{connection_id}/consent")
    def consent(request: Request, connection_id: str, body: ConsentInput):
        ctx = runtime(request)
        source = source_id(ctx, connection_id)
        with ctx.auth.locks[source]:
            if body.purpose == "automatic_sync" and body.granted:
                if ctx.auth.validate(source).status != ConnectionStatus.CONNECTED:
                    raise HTTPException(409, "Validate the source before enabling automatic sync")
            ctx.consent.record(source, body.purpose, body.granted)
            if body.purpose == "read" and not body.granted:
                ctx.consent.record(source, "automatic_sync", False)
        return body

    @api.post("/connections/{connection_id}/connect-and-sync", response_model=ConnectionSetupResult)
    def connect_and_sync(request: Request, connection_id: str, body: ConnectionSetupInput | None = None):
        """Save explicit read consent, use the official provider flow, then queue a read."""
        ctx = runtime(request)
        source = source_id(ctx, connection_id)
        if source not in (SourceKind.PAMPLEMOUSSE, SourceKind.MOODLE, SourceKind.ENSORA_DRIVE,
                          SourceKind.GOOGLE_CALENDAR):
            raise HTTPException(422, "This source uses a separate import flow")
        subject_id = body.subject_id if body else None
        if subject_id and source not in (SourceKind.MOODLE, SourceKind.ENSORA_DRIVE):
            raise HTTPException(422, "Choose a subject only for Moodle or Ensora Drive")
        with ctx.auth.locks[source]:
            was_granted = ctx.consent.allowed(source, "read")
            if not was_granted:
                ctx.consent.record(source, "read", True)
            previous = ctx.auth.status(source)
            # Reuse a valid stored connection without asking the student to sign in again.
            result = ctx.auth.validate(source)
            if result.status in (ConnectionStatus.NOT_CONFIGURED, ConnectionStatus.REAUTH_REQUIRED):
                action = ctx.auth.reauthorize if was_granted and previous != ConnectionStatus.NOT_CONFIGURED else ctx.auth.authorize
                result = action(source)
            job_id = None
            if result.status == ConnectionStatus.CONNECTED and source in ctx.sync.connectors:
                with ctx.engine.connect() as db:
                    job_id = db.scalar(select(runs.c.id).where(
                        runs.c.user_id == LOCAL_USER,
                        runs.c.status.in_(("queued", "running")),
                        runs.c.scope["source"].as_string() == source.value,
                        runs.c.scope["subject_id"].as_string() == subject_id,
                    ).order_by(runs.c.started_at.desc()).limit(1))
                if job_id is None:
                    job_id = ctx.sync.submit(SyncScope(source=source, subject_id=subject_id))
        return ConnectionSetupResult(**result.model_dump(), job_id=job_id)

    @api.post("/connections/{connection_id}/authorize")
    def authenticate(request: Request, connection_id: str):
        ctx = runtime(request)
        return ctx.auth.authorize(source_id(ctx, connection_id))

    @api.post("/connections/{connection_id}/reauthorize")
    def reauthenticate(request: Request, connection_id: str):
        ctx = runtime(request)
        return ctx.auth.reauthorize(source_id(ctx, connection_id))

    @api.post("/connections/{connection_id}/validate")
    def validate(request: Request, connection_id: str):
        ctx = runtime(request)
        return ctx.auth.validate(source_id(ctx, connection_id))

    @api.delete("/connections/{connection_id}", status_code=204)
    def disconnect(request: Request, connection_id: str):
        ctx = runtime(request)
        if not ctx.auth.disconnect(source_id(ctx, connection_id)):
            raise HTTPException(503, "Local access withdrawn. Retry to finish source cleanup.")

    @api.post("/sync", status_code=202)
    def sync(request: Request, scope: SyncScope,
             idempotency_key: Annotated[str | None, Header(max_length=200)] = None):
        try:
            if scope.source == SourceKind.LOCAL and not scope.folder_id:
                raise HTTPException(422, "Select a local import path")
            if scope.folder_id is not None and scope.source is None:
                raise HTTPException(422, "Select a source for folder synchronization")
            ctx = runtime(request)
            return {"id": ctx.sync.submit(scope, idempotency_key)}
        except ProtectedImportPath:
            raise HTTPException(403, "Selected path overlaps private application storage") from None
        except ValueError:
            raise HTTPException(409, "Idempotency key already used") from None

    @api.get("/sync/runs")
    def history(request: Request, limit: Limit = 50, cursor: str | None = None):
        with runtime(request).engine.connect() as db:
            return page(db, select(runs).where(runs.c.user_id == LOCAL_USER), runs.c.id, limit, cursor, runs.c.started_at)

    @api.get("/jobs/{job_id}")
    def job(request: Request, job_id: str):
        with runtime(request).engine.connect() as db:
            row = db.execute(select(runs).where(runs.c.id == job_id,
                                               runs.c.user_id == LOCAL_USER)).mappings().one_or_none()
            if row is None:
                raise HTTPException(404, "Unknown job")
            result = dict(row)
            report = result.get("report")
            if isinstance(report, dict):
                report = dict(report)
                report.pop("_checkpoint_cursors", None)
                result["report"] = report
            return result

    @api.post("/jobs/{job_id}/resume", status_code=202)
    def resume_sync(request: Request, job_id: str, body: ResumeSelection = ResumeSelection()):
        try:
            return {"id": runtime(request).sync.resume(job_id, body.source)}
        except LookupError:
            raise HTTPException(404, "Unknown job") from None
        except ResumeUnavailable as exc:
            raise HTTPException(409, str(exc)) from None

    @api.post("/jobs/{job_id}/cancel", status_code=202)
    def cancel(request: Request, job_id: str):
        job(request, job_id)
        runtime(request).sync.cancel(job_id)
        return {"id": job_id}

    @api.get("/pipeline/status")
    def pipeline_status(request: Request):
        ctx = runtime(request)
        with ctx.sync.active_lock:
            return {"active_jobs": len(ctx.sync.active), "registered_sources": list(ctx.sync.connectors)}

    def public_documents(ctx):
        # Local cache remains available after transient authentication failures.
        # Explicit consent withdrawal removes the source from API reads.
        return [ctx.connection_ids[source] for source in SourceKind if ctx.consent.allowed(source, "read")]

    def active_document():
        access = documents.c.metadata_json["access_status"].as_string()
        drive_connection_id = select(connections.c.id).where(
            connections.c.user_id == LOCAL_USER,
            connections.c.source == SourceKind.ENSORA_DRIVE,
        ).scalar_subquery()
        in_approved_drive_scope = ((documents.c.connection_id != drive_connection_id)
                                   | (func.ensora_drive_folder_allowed(
                                       documents.c.metadata_json["provenance"]["folder"].as_string()
                                   ) == 1))
        return ((func.coalesce(access, "active") == "active")
                & documents.c.active_version_id.is_not(None)
                & in_approved_drive_scope)

    @api.get("/documents", dependencies=document_dependencies)
    def list_documents(request: Request, limit: Limit = 50, cursor: str | None = None,
                       subject_id: str | None = None):
        ctx = runtime(request)
        query = select(documents).where(documents.c.connection_id.in_(public_documents(ctx)),
                                        active_document())
        if subject_id:
            query = query.where(documents.c.metadata_json["subject_id"].as_string() == subject_id)
        with ctx.engine.connect() as db:
            return page(db, query, documents.c.id, limit, cursor, literal_column("documents.rowid"))

    @api.post("/documents/import", status_code=201, dependencies=document_dependencies)
    def import_document(request: Request, file: UploadFile = File(...),
                        external_id: Annotated[str | None, Form(min_length=1, max_length=256)] = None):
        ctx = runtime(request)
        source = SourceKind.LOCAL
        with ctx.auth.locks[source]:
            if not ctx.consent.allowed(source, "read"):
                raise HTTPException(403, "Local import consent required")
            content = file.file.read(ctx.settings.max_document_bytes + 1)
            if len(content) > ctx.settings.max_document_bytes:
                raise HTTPException(413, "Document exceeds the local size limit")
            title = Path(file.filename or "document").name[:512]
            digest = hashlib.sha256(content).hexdigest()
            identity = "upload:" + hashlib.sha256((external_id or title).encode()).hexdigest()
            record = Document(title=title, mime_type=file.content_type or "application/octet-stream",
                              provenance=Provenance(external_id=identity, remote_revision=digest, source=source))
            try:
                result = ctx.pipeline.ingest(ctx.connection_ids[source], record, lambda _: content)
            except (UnicodeError, ValueError):
                raise HTTPException(422, "Document could not be processed") from None
        return {"result": result, "external_id": identity}

    @api.get("/documents/exam-correction-candidates", dependencies=document_dependencies)
    def exam_correction_candidates(request: Request):
        matcher = runtime(request).pipeline.matcher
        candidates = getattr(matcher, "exam_correction_candidates", None)
        return {"items": [entry.to_dict() for entry in candidates()] if candidates else []}

    @api.put("/documents/{document_id}/match", dependencies=document_dependencies)
    def confirm_document_match(request: Request, document_id: str, body: DocumentMatchInput):
        ctx = runtime(request)
        allowed = public_documents(ctx)
        if not allowed:
            raise HTTPException(403, "Read consent is required for document matching")
        with ctx.engine.begin() as db:
            document_row = db.execute(select(documents.c.connection_id, documents.c.active_version_id,
                                             documents.c.metadata_json).where(
                documents.c.id == document_id, documents.c.connection_id.in_(allowed),
                active_document(),
            )).mappings().one_or_none()
            if document_row is None:
                raise HTTPException(404, "Unknown active document")

            subjects = db.execute(select(records.c.id, records.c.external_id, records.c.connection_id,
                                         records.c.parent_id).where(
                records.c.kind == "subject", records.c.external_id == body.subject_id,
                records.c.connection_id.in_(allowed),
            )).mappings().all()
            if len(subjects) != 1:
                raise HTTPException(422, "Subject must resolve to exactly one read-consented catalog record")
            subject = subjects[0]
            parents = db.execute(select(records.c.external_id).where(
                records.c.id == subject["parent_id"], records.c.kind == "teaching_unit",
                records.c.connection_id == subject["connection_id"],
            )).all() if subject["parent_id"] else []
            teaching_unit_id = parents[0][0] if len(parents) == 1 else None

            metadata = document_row["metadata_json"] or {}
            override = dict(metadata.get("user_override") or {})
            changed_subject = metadata.get("subject_id") != body.subject_id
            effective_chapter_id = (body.chapter_id if "chapter_id" in body.model_fields_set else
                                    None if changed_subject else metadata.get("chapter_id"))
            if changed_subject:
                override.pop("teaching_unit_id", None)
                override.pop("chapter_id", None)
            override["subject_id"] = body.subject_id
            updated = {**metadata, "user_override": override, "subject_id": body.subject_id,
                       "match": {"subject_id": body.subject_id, "teaching_unit_id": teaching_unit_id,
                                 "chapter_id": effective_chapter_id,
                                 "confidence": 1.0, "confirmed": True}}
            if teaching_unit_id:
                override["teaching_unit_id"] = teaching_unit_id
                updated["teaching_unit_id"] = teaching_unit_id
            else:
                override.pop("teaching_unit_id", None)
                updated.pop("teaching_unit_id", None)
            if "chapter_id" in body.model_fields_set:
                override["chapter_id"] = body.chapter_id
            if effective_chapter_id is None:
                updated.pop("chapter_id", None)
            else:
                updated["chapter_id"] = effective_chapter_id
            db.execute(update(documents).where(documents.c.id == document_id).values(metadata_json=updated))
        return {"document_id": document_id, "subject_id": body.subject_id,
                "teaching_unit_id": teaching_unit_id, "chapter_id": effective_chapter_id,
                "confirmed": True}

    @api.get("/documents/{document_id}", dependencies=document_dependencies)
    def document(request: Request, document_id: str):
        ctx = runtime(request)
        with ctx.engine.connect() as db:
            item = db.execute(select(documents).where(documents.c.id == document_id,
                              documents.c.connection_id.in_(public_documents(ctx)), active_document())).mappings().one_or_none()
            if item is None:
                raise HTTPException(404, "Unknown document")
            return dict(item)

    @api.get("/documents/{document_id}/versions", dependencies=document_dependencies)
    def document_versions(request: Request, document_id: str, limit: Limit = 50, cursor: str | None = None):
        document(request, document_id)
        with runtime(request).engine.connect() as db:
            return page(db, select(versions).where(versions.c.document_id == document_id),
                        versions.c.id, limit, cursor)

    def document_version(request, document_id, version_id):
        document(request, document_id)
        with runtime(request).engine.connect() as db:
            row = db.execute(select(versions).where(versions.c.document_id == document_id,
                                                    versions.c.id == version_id)).mappings().one_or_none()
            if row is None:
                raise HTTPException(404, "Unknown document version")
            return dict(row)

    @api.get("/documents/{document_id}/versions/{version_id}/content", dependencies=document_dependencies)
    def content(request: Request, document_id: str, version_id: str):
        item = document_version(request, document_id, version_id)
        path = runtime(request).settings.local_data_dir / "blobs" / item["sha256"]
        if not path.is_file():
            raise HTTPException(503, "Stored document content is unavailable")
        title = Path(item["filename"] or document(request, document_id)["metadata_json"]["title"]).name
        title = "".join(character for character in title if character.isprintable()) or "document"
        safe_types = {"application/pdf", "text/plain", "text/csv", "image/png", "image/jpeg"}
        media_type = item["mime_type"] if item["mime_type"] in safe_types else "application/octet-stream"
        return FileResponse(path, media_type=media_type, filename=title,
                            headers={"X-Content-Type-Options": "nosniff"})

    @api.get("/documents/{document_id}/versions/{version_id}/chunks", dependencies=document_dependencies)
    def parsed_chunks(request: Request, document_id: str, version_id: str):
        item = document_version(request, document_id, version_id)
        with runtime(request).engine.connect() as db:
            result = db.execute(select(chunks.c.id, chunks.c.ordinal, chunks.c.payload)
                                .where(chunks.c.version_id == version_id).order_by(chunks.c.ordinal)).mappings()
            return {"version": item, "items": [dict(row) for row in result]}

    @api.get("/documents/{document_id}/versions/{version_id}/concept-candidates", dependencies=document_dependencies)
    def concept_candidates(request: Request, document_id: str, version_id: str):
        from backend.app.modules.ingestion.codex_bridge import extract_concept_candidates, extract_prerequisite_hints

        source = document(request, document_id)["metadata_json"]
        record = Document.model_validate({key: value for key, value in source.items()
                                          if key in Document.model_fields})
        result = parsed_chunks(request, document_id, version_id)
        parsed = ParsedContent(parser_version=result["version"]["parser_version"] or "none",
                               chunks=[row["payload"] for row in result["items"]])
        items = []
        by_ordinal = {row["ordinal"]: row["id"] for row in result["items"]}
        for concept in extract_concept_candidates(record, parsed):
            item = concept.model_dump(mode="json")
            for evidence in item["evidence"]:
                evidence["chunk_id"] = by_ordinal.get(evidence["chunk_ordinal"])
            items.append(item)
        hints = extract_prerequisite_hints(parsed)
        for hint in hints:
            hint["chunk_id"] = by_ordinal.get(hint["chunk_ordinal"])
        return {"version_id": version_id, "items": items, "prerequisite_hints": hints}

    amendment_fields = {
        "subject": {"title", "code", "ects", "aliases", "optional_activity", "record_status"},
        "teaching_unit": {"title", "ects", "record_status"},
        "assessment": {"title", "deadline_at", "instructions", "assessment_kind", "record_status"},
        "coefficient": {"weight", "level", "mode", "record_status"},
        "assessment_weight": {"weight", "mode", "record_status"},
        "grade": {"value", "minimum", "maximum", "record_status"},
    }

    @api.put("/academic/records/{record_id}/override")
    def set_academic_override(request: Request, record_id: str, body: AcademicOverride):
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        ctx = runtime(request)
        allowed = public_documents(ctx)
        with ctx.engine.begin() as db:
            source = db.execute(select(records.c.kind, records.c.payload).where(
                records.c.id == record_id, records.c.connection_id.in_(allowed),
            )).one_or_none()
            if source is None:
                raise HTTPException(404, "Unknown academic record")
            if source.kind not in amendment_fields or set(body.fields) - amendment_fields[source.kind]:
                raise HTTPException(422, "Unsupported correction fields")
            try:
                normalized = TypeAdapter(SourceRecord).validate_python(source.payload | body.fields).model_dump(mode="json")
            except ValidationError:
                raise HTTPException(422, "Invalid academic correction") from None
            fields = {key: normalized[key] for key in body.fields}
            entry = sqlite_insert(academic_overrides).values(record_id=record_id, fields=fields)
            db.execute(entry.on_conflict_do_update(index_elements=[academic_overrides.c.record_id],
                                                   set_={"fields": fields}))
        return {"id": record_id, "user_override": fields}

    def record_view(kind):
        def view(request: Request, limit: Limit = 50, cursor: str | None = None):
            ctx = runtime(request)
            with ctx.engine.connect() as db:
                order = None
                if kind == "event":
                    payload = records.c.payload
                    order = func.coalesce(func.strftime("%Y-%m-%dT%H:%M:%fZ", payload["starts_at"].as_string()),
                                          payload["all_day_start"].as_string(), "")
                elif kind == "grade":
                    order = func.coalesce(records.c.payload["published_at"].as_string(), "")
                query = select(records, academic_overrides.c.fields.label("user_override")).select_from(
                    records.outerjoin(academic_overrides)
                ).where(records.c.kind == kind, records.c.connection_id.in_(public_documents(ctx)))
                result = page(db, query, records.c.id, limit, cursor, order)
            for item in result["items"]:
                override = item["user_override"]
                if override:
                    item["effective_payload"] = item["payload"] | override
            return result
        return view

    for path, kind in (("subjects", "subject"), ("chapters", "chapter"), ("concepts", "concept"), ("events", "event"), ("teaching-units", "teaching_unit"),
                       ("assessments", "assessment"), ("grades", "grade"), ("coefficients", "coefficient"),
                       ("assessment-weights", "assessment_weight"), ("academic-years", "academic_year"),
                       ("semesters", "semester"), ("specializations", "specialization")):
        api.add_api_route(f"/{path}", record_view(kind), methods=["GET"], name=f"list_{kind}")

    @api.post("/academic/import", status_code=201)
    def academic_import(request: Request, items: list[SourceRecord], dry_run: bool = False):
        ctx = runtime(request)
        source = SourceKind.ACADEMIC_IMPORT
        if any(isinstance(item, Document) for item in items):
            raise HTTPException(422, "Use the document upload endpoint")
        with ctx.auth.locks[source]:
            if not ctx.consent.allowed(source, "read"):
                raise HTTPException(403, "Academic import consent required")
            result = ctx.pipeline.import_academics(ctx.connection_ids[source], items, dry_run)
        reconciliation = None
        if not dry_run and any(item["status"] != "error" for item in result):
            reconciliation = ctx.reconcile_academic_documents(source)
        status = 422 if not result or all(item["status"] == "error" for item in result) else (
            200 if dry_run or any(item["status"] == "error" for item in result) else 201
        )
        return JSONResponse(status_code=status, content={"results": result, "dry_run": dry_run,
                                                          "document_reconciliation": reconciliation})

    @api.get("/availability")
    def list_availability(request: Request, limit: Limit = 50, cursor: str | None = None):
        ctx = runtime(request)
        calendar_id = ctx.connection_ids[SourceKind.GOOGLE_CALENDAR]
        manual = availability.c.payload
        manual_query = select(
            availability.c.id, literal_column("'manual'").label("source"),
            func.ensora_interval_time(manual["starts_at"].as_string(), None, None).label("starts_at"),
            func.ensora_interval_time(manual["ends_at"].as_string(), None, None).label("ends_at"),
            manual["available"].as_boolean().label("available"),
        ).where(availability.c.user_id == LOCAL_USER)
        combined = manual_query
        if ctx.consent.allowed(SourceKind.GOOGLE_CALENDAR, "read"):
            data = records.c.payload
            tz = data["original_timezone"].as_string()
            calendar_query = select(
                records.c.id, literal_column("'google_calendar'"),
                func.ensora_interval_time(data["starts_at"].as_string(), data["all_day_start"].as_string(), tz),
                func.ensora_interval_time(data["ends_at"].as_string(), data["all_day_end"].as_string(), tz),
                literal_column("0"),
            ).where(records.c.connection_id == calendar_id, records.c.kind == "event",
                    data["status"].as_string() != "cancelled", func.coalesce(data["busy"].as_boolean(), True))
            combined = union_all(manual_query, calendar_query)
        items = combined.subquery()
        with ctx.engine.connect() as db:
            result = page(db, select(items), items.c.id, limit, cursor, items.c.starts_at)
        result["items"] = [{
            "id": row["id"], "source": row["source"],
            "payload": {"starts_at": row["starts_at"], "ends_at": row["ends_at"],
                        "available": row["available"]},
        } for row in result["items"]]
        return result

    @api.post("/availability", status_code=201)
    def add_availability(request: Request, interval: PersonalAvailability):
        key = new_id()
        with runtime(request).engine.begin() as db:
            db.execute(insert(availability).values(id=key, user_id=LOCAL_USER, payload=interval.model_dump(mode="json")))
        return {"id": key, "interval": interval}

    app.include_router(api)
    return app


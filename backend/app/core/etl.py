"""Transactional ingestion with immutable content-addressed blobs."""
from datetime import UTC
import hashlib
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from sqlalchemy import delete, func, insert, or_, select, update

from ..contracts.sources import CalendarEvent, ClassificationResult, Document, DocumentChunk, EntityMatch, FetchedDocument, ParsedContent, SourceKind
from .auth import now
from .database import chunks, documents, new_id, record_history, records, versions
from .drive_scope import drive_document_in_scope


def count(metrics, name, amount=1):
    if metrics is not None:
        metrics[name] = metrics.get(name, 0) + amount


def safe_parser_warnings(codes, *, fallback=None):
    """Keep parser diagnostics code-only so source text cannot escape via warnings."""
    safe = set()
    for code in codes or ():
        safe.add(code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code)
                 else "other")
    if fallback:
        safe.add(fallback)
    return sorted(safe)


class AcademicImportError(ValueError):
    """A normalized academic record conflicts with stored structure."""


class TextParser:
    def parse(self, document, content):
        if document.mime_type not in ("text/plain", "text/markdown", "text/csv"):
            return None
        lines = content.decode("utf-8-sig").splitlines()
        return ParsedContent(parser_version="text-1", chunks=[
            DocumentChunk(text="\n".join(lines[start:start + 40]),
                          locator={"kind": "lines", "line_start": start + 1,
                                   "line_end": min(start + 40, len(lines))})
            for start in range(0, len(lines), 40)
        ])


class Pipeline:
    def __init__(self, engine, blob_dir: Path, max_bytes, parser=None, classifier=None, matcher=None):
        self.engine = engine
        self.blob_dir = blob_dir
        self.max_bytes = max_bytes
        self.parser = parser or TextParser()
        self.classifier = classifier
        self.matcher = matcher

    def _store_blob(self, digest, content):
        destination = self.blob_dir / digest
        if destination.exists():
            return
        with NamedTemporaryFile(dir=self.blob_dir, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)

    def ingest(self, connection_id, record, fetch_blob=None, *, metrics=None, source=None):
        if isinstance(record, Document):
            return self._document(connection_id, record, fetch_blob, metrics)
        if source == SourceKind.GOOGLE_CALENDAR and isinstance(record, CalendarEvent):
            record = record.model_copy(update={
                "title": "Cancelled" if record.status == "cancelled" else "Busy" if record.busy else "Free",
                "room": None, "group": None, "teacher": None, "subject_id": None,
                "confirmation_reason": None, "recurrence_key": None,
                "provenance": record.provenance.model_copy(update={"source_url": None, "folder": None}),
            })
        return self._record(connection_id, record, metrics=metrics)

    def download_failed(self, connection_id, external_id):
        return self.download_error_code(connection_id, external_id) is not None

    def download_error_code(self, connection_id, external_id):
        with self.engine.connect() as db:
            metadata = db.scalar(select(documents.c.metadata_json).where(
                documents.c.connection_id == connection_id, documents.c.external_id == external_id,
            ))
        if metadata and metadata.get("ingestion_state") == "download_failed":
            return metadata.get("ingestion_error_code") or "download_failure"
        return None

    @staticmethod
    def _download_error_code(exc):
        """Map exception types to a small safe vocabulary; never persist provider messages."""
        name = type(exc).__name__
        if name in {"DownloadSizeLimitError", "DriveSizeLimitError"}:
            return "download_size_limit"
        if name == "DriveResourceNotFoundError":
            return "source_resource_missing"
        if name == "DriveUnexportableError":
            return "unsupported_export"
        if name == "DriveExportSizeLimitError":
            return "export_limit"
        if name in {"AuthenticationRequiredError", "ReauthenticationRequired"}:
            return "reauth_required"
        if name in {"PermissionDeniedError", "ServiceAccessDenied"}:
            return "source_access_denied"
        if name in {"RetryableSourceError", "TransientSourceError", "TimeoutError", "ConnectionError"}:
            return "transient_download_failure"
        return "download_failure"

    def _record_download_failure(self, connection_id, record, existing, error_code):
        metadata = {**(existing["metadata_json"] if existing else {}),
                    **record.model_dump(mode="json"),
                    "ingestion_state": "download_failed",
                    "ingestion_error_code": error_code}
        if existing:
            previous = existing["metadata_json"]
            override = previous.get("user_override", {})
            for field in ("subject_id", "chapter_id", "teaching_unit_id"):
                if field in override:
                    metadata[field] = override[field]
                elif previous.get("match", {}).get("confirmed"):
                    metadata[field] = previous.get(field)
            if previous.get("match", {}).get("confirmed"):
                metadata["match"] = previous["match"]
        with self.engine.begin() as db:
            if existing:
                db.execute(update(documents).where(documents.c.id == existing["id"]).values(
                    metadata_json=metadata,
                ))
            else:
                db.execute(insert(documents).values(
                    id=new_id(), connection_id=connection_id,
                    external_id=record.provenance.external_id, metadata_json=metadata,
                ))

    def _document(self, connection_id, record, fetch_blob, metrics):
        external_id = record.provenance.external_id
        revision = record.provenance.remote_revision
        identity = (documents.c.connection_id == connection_id, documents.c.external_id == external_id)
        with self.engine.connect() as db:
            existing = db.execute(select(documents).where(*identity)).mappings().one_or_none()
        incoming = record.model_dump(mode="json")
        source_title = record.title
        if record.access_status != "active":
            count(metrics, "documents_excluded")
            if existing:
                metadata = {**existing["metadata_json"], **incoming}
                with self.engine.begin() as db:
                    db.execute(update(documents).where(*identity).values(metadata_json=metadata))
            return "records" if existing else "unchanged"
        if (existing and revision and existing["remote_revision"] == revision and
                existing["active_version_id"] and existing["metadata_json"].get("access_status", "active") == "active"):
            return "unchanged"

        try:
            result = fetch_blob(record)
        except Exception as exc:
            count(metrics, "download_failures")
            error_code = self._download_error_code(exc)
            count(metrics, f"download_error_{error_code}")
            self._record_download_failure(connection_id, record, existing, error_code)
            raise
        if isinstance(result, FetchedDocument):
            record = record.model_copy(update={"title": result.filename or record.title,
                                               "mime_type": result.mime_type or record.mime_type})
            incoming = record.model_dump(mode="json") | {"source_title": source_title}
            content = result.content
        else:
            content = result
        if not isinstance(content, bytes) or len(content) > self.max_bytes:
            raise ValueError("Invalid document payload")
        count(metrics, "documents_downloaded")
        digest = hashlib.sha256(content).hexdigest()
        self._store_blob(digest, content)
        document_id = existing["id"] if existing else new_id()
        with self.engine.connect() as db:
            version = db.execute(select(versions).where(
                versions.c.document_id == document_id, versions.c.sha256 == digest,
            )).mappings().one_or_none()

        retry_parse = version is None or version["status"] == "failed"
        parsed = None
        status = version["status"] if version else "unsupported"
        metadata = {**(existing["metadata_json"] if existing else {}), **incoming}
        warning_codes = []
        if retry_parse:
            try:
                parsed = self.parser.parse(record, content)
            except Exception:
                parsed = None
                status = "failed"
                warning_codes = ["parser_exception"]
            if parsed is None and status != "failed":
                status = "unsupported"
                warning_codes = ["unsupported_format"]
            elif parsed is not None:
                status = parsed.status
                warning_codes = safe_parser_warnings(
                    parsed.warning_codes,
                    fallback="partial_reason_unspecified" if parsed.status == "partial" and not parsed.warning_codes else None,
                )
                try:
                    classification = (self.classifier.classify(record, parsed)
                                      if self.classifier else ClassificationResult())
                except Exception:
                    classification = ClassificationResult()
                    warning_codes = safe_parser_warnings(warning_codes, fallback="classification_failed")
                    status = "partial"
                try:
                    match = self.matcher.match(record, classification) if self.matcher else EntityMatch()
                except Exception:
                    match = EntityMatch()
                    warning_codes = safe_parser_warnings(warning_codes, fallback="entity_resolution_failed")
                    status = "partial"
                metadata.update(classification=classification.model_dump(mode="json"),
                                match=match.model_dump(mode="json"), parser_warning_codes=warning_codes)
                if match.confirmed:
                    metadata.update(subject_id=match.subject_id or record.subject_id,
                                    chapter_id=match.chapter_id or record.chapter_id,
                                    teaching_unit_id=match.teaching_unit_id or record.teaching_unit_id)
            else:
                metadata["parser_warning_codes"] = warning_codes
        if existing:
            previous = existing["metadata_json"]
            override = previous.get("user_override", {})
            for field in ("subject_id", "chapter_id", "teaching_unit_id"):
                if field in override:
                    metadata[field] = override[field]
                elif previous.get("match", {}).get("confirmed"):
                    metadata[field] = previous.get(field)
            if previous.get("match", {}).get("confirmed"):
                metadata["match"] = previous["match"]

        version_id = version["id"] if version else new_id()
        old_version_id = existing["active_version_id"] if existing else None
        with self.engine.begin() as db:
            if not existing:
                db.execute(insert(documents).values(id=document_id, connection_id=connection_id,
                                                    external_id=external_id, metadata_json=metadata))
            if retry_parse:
                version_fields = {"status": status, "parser_version": parsed.parser_version if parsed else None,
                                  "filename": record.title,
                                  "source_metadata": incoming | {"parser_warning_codes": warning_codes}}
                if version is None:
                    db.execute(insert(versions).values(
                        id=version_id, document_id=document_id, sha256=digest, mime_type=record.mime_type,
                        size_bytes=len(content), **version_fields,
                    ))
                else:
                    db.execute(update(versions).where(versions.c.id == version_id).values(**version_fields))
                if parsed and parsed.chunks:
                    db.execute(insert(chunks), [
                        {"id": new_id(), "version_id": version_id, "ordinal": ordinal,
                         "payload": chunk.model_dump(mode="json")}
                        for ordinal, chunk in enumerate(parsed.chunks)
                    ])
            if status != "failed" or old_version_id is None:
                prior_status = db.scalar(select(versions.c.status).where(versions.c.id == old_version_id))
                active_version_id = (old_version_id if status in ("failed", "unsupported")
                                     and prior_status in ("ready", "partial") else version_id)
                db.execute(update(documents).where(documents.c.id == document_id).values(
                    metadata_json={**metadata, "ingestion_state": "persisted",
                                   "ingestion_error_code": None},
                    remote_revision=revision, active_version_id=active_version_id,
                ))
        count(metrics, "documents_persisted")
        if retry_parse:
            count(metrics, {"ready": "documents_parsed", "partial": "documents_partially_parsed",
                            "unsupported": "documents_unsupported", "failed": "parsing_failures"}[status])
            if parsed:
                count(metrics, "chunks_generated", len(parsed.chunks))
        else:
            count(metrics, "existing_versions_reused")
        if status == "failed":
            raise ValueError("Document processing failed")
        return "documents" if retry_parse else "unchanged"

    def reconcile_documents(self, connection_ids, is_authorized=None):
        """Re-match stored active documents after a catalog arrives, without refetching content.

        Only high-confidence confirmed matches update document associations. Existing user
        overrides and confirmed mappings are authoritative; uncertain results stay unlinked.
        Each metadata update uses a short transaction and rechecks the current document row.
        """
        if self.matcher is None:
            return {"examined": 0, "matched": 0, "uncertain": 0, "skipped": 0, "errors": 0}
        allowed = tuple(dict.fromkeys(connection_ids or ()))
        if not allowed:
            return {"examined": 0, "matched": 0, "uncertain": 0, "skipped": 0, "errors": 0}

        with self.engine.connect() as db:
            drive_source = or_(
                documents.c.metadata_json["provenance"]["source"].as_string() == SourceKind.ENSORA_DRIVE.value,
                documents.c.external_id.like("ensora_drive:%"),
            )
            drive_scope = or_(~drive_source, func.ensora_drive_folder_allowed(
                documents.c.metadata_json["provenance"]["folder"].as_string()) == 1)
            candidates = db.execute(select(
                documents.c.id, documents.c.connection_id, documents.c.external_id,
                documents.c.active_version_id, documents.c.metadata_json,
            ).where(documents.c.connection_id.in_(allowed),
                    documents.c.active_version_id.is_not(None), drive_scope)).mappings().all()

        result = {"examined": 0, "matched": 0, "uncertain": 0, "skipped": 0, "errors": 0}
        prepared = []
        for row in candidates:
            connection_id = row["connection_id"]
            if is_authorized is not None and not is_authorized(connection_id):
                result["skipped"] += 1
                continue
            metadata = row["metadata_json"] or {}
            if (metadata.get("access_status", "active") != "active"
                    or not drive_document_in_scope(metadata, row["external_id"])):
                result["skipped"] += 1
                continue
            if (metadata.get("user_override", {}).get("subject_id")
                    or metadata.get("match", {}).get("confirmed")):
                result["skipped"] += 1
                continue
            try:
                record = Document.model_validate({
                    key: value for key, value in metadata.items() if key in Document.model_fields
                })
                classification = ClassificationResult.model_validate(metadata.get("classification") or {})
            except Exception:
                result["errors"] += 1
                continue

            prepared.append((row, record, classification))

        batch_match = getattr(self.matcher, "match_many", None)
        if callable(batch_match) and prepared:
            try:
                matches = batch_match([(record, classification) for _, record, classification in prepared])
                if len(matches) != len(prepared):
                    raise ValueError("Matcher returned an incomplete reconciliation batch")
            except Exception:
                result["errors"] += len(prepared)
                return result
        else:
            matches = []
            for _, record, classification in prepared:
                try:
                    matches.append(self.matcher.match(record, classification))
                except Exception:
                    matches.append(None)

        for (row, _, _), match in zip(prepared, matches):
            connection_id = row["connection_id"]
            if match is None:
                result["errors"] += 1
                continue
            result["examined"] += 1
            if not match.confirmed or not match.subject_id:
                result["uncertain"] += 1

            # Re-read under a short write transaction so a concurrent manual correction,
            # source update, or access revocation cannot be overwritten by a stale snapshot.
            with self.engine.begin() as db:
                current = db.execute(select(
                    documents.c.active_version_id, documents.c.metadata_json,
                ).where(documents.c.id == row["id"],
                        documents.c.connection_id == connection_id)).mappings().one_or_none()
                if (current is None or current["active_version_id"] != row["active_version_id"]
                        or (is_authorized is not None and not is_authorized(connection_id))):
                    result["skipped"] += 1
                    continue
                latest = current["metadata_json"] or {}
                override = latest.get("user_override", {})
                if (latest.get("access_status", "active") != "active"
                        or override.get("subject_id")
                        or latest.get("match", {}).get("confirmed")):
                    result["skipped"] += 1
                    continue
                updated = {**latest, "match": match.model_dump(mode="json")}
                if match.confirmed and match.subject_id:
                    if "subject_id" not in override:
                        updated["subject_id"] = match.subject_id
                    if match.chapter_id and "chapter_id" not in override:
                        updated["chapter_id"] = match.chapter_id
                    if match.teaching_unit_id and "teaching_unit_id" not in override:
                        updated["teaching_unit_id"] = match.teaching_unit_id
                if updated != latest:
                    db.execute(update(documents).where(documents.c.id == row["id"]).values(
                        metadata_json=updated,
                    ))
                    if match.confirmed and match.subject_id:
                        result["matched"] += 1
        return result

    def reprocess_documents(self, connection_ids, *, document_ids=None, statuses=None, limit=20,
                            is_authorized=None):
        """Reparse selected stored versions locally; this path never invokes a source connector."""
        if not 1 <= limit <= 100:
            raise ValueError("Reprocessing limit must be between 1 and 100")
        allowed = tuple(dict.fromkeys(connection_ids or ()))
        selected_ids = tuple(dict.fromkeys(document_ids or ()))
        if not allowed or (document_ids is not None and not selected_ids):
            return {"selected": 0, "reprocessed": 0, "ready": 0, "partial": 0,
                    "unsupported": 0, "errors": 0, "skipped": 0, "error_codes": {}}

        query = select(
            documents.c.id.label("document_id"), documents.c.connection_id, documents.c.external_id,
            documents.c.metadata_json, documents.c.active_version_id,
            versions.c.id.label("version_id"), versions.c.sha256, versions.c.mime_type,
            versions.c.status, versions.c.parser_version, versions.c.source_metadata,
            versions.c.filename,
        ).join(versions, versions.c.id == documents.c.active_version_id).where(
            documents.c.connection_id.in_(allowed),
            func.coalesce(documents.c.metadata_json["access_status"].as_string(), "active") == "active",
            or_(
                (documents.c.metadata_json["provenance"]["source"].as_string() != SourceKind.ENSORA_DRIVE.value)
                & ~documents.c.external_id.like("ensora_drive:%"),
                func.ensora_drive_folder_allowed(
                    documents.c.metadata_json["provenance"]["folder"].as_string()) == 1,
            ),
        )
        if selected_ids:
            query = query.where(documents.c.id.in_(selected_ids))
        if statuses:
            query = query.where(versions.c.status.in_(tuple(statuses)))
        with self.engine.connect() as db:
            candidates = db.execute(query.order_by(documents.c.id).limit(limit)).mappings().all()

        result = {"selected": len(candidates), "reprocessed": 0, "ready": 0, "partial": 0,
                  "unsupported": 0, "errors": 0, "skipped": 0, "error_codes": {}}

        def error(code):
            result["errors"] += 1
            result["error_codes"][code] = result["error_codes"].get(code, 0) + 1

        for row in candidates:
            connection_id = row["connection_id"]
            if is_authorized is not None and not is_authorized(connection_id):
                result["skipped"] += 1
                continue
            path = self.blob_dir / row["sha256"]
            try:
                if not path.is_file() or path.stat().st_size > self.max_bytes:
                    error("stored_blob_unavailable")
                    continue
                content = path.read_bytes()
            except OSError:
                error("stored_blob_unavailable")
                continue
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                error("stored_blob_integrity_failed")
                continue
            metadata = row["metadata_json"] or {}
            if not drive_document_in_scope(metadata, row["external_id"]):
                result["skipped"] += 1
                continue
            try:
                record = Document.model_validate({
                    key: value for key, value in metadata.items() if key in Document.model_fields
                })
                parsed = self.parser.parse(record, content)
            except Exception:
                error("parser_exception")
                continue
            if parsed is None:
                result["unsupported"] += 1
                # Keep the last successful parse and its chunks if this parser cannot
                # handle the format; the original bytes and version remain accessible.
                continue

            warning_codes = safe_parser_warnings(
                parsed.warning_codes,
                fallback="partial_reason_unspecified" if parsed.status == "partial" and not parsed.warning_codes else None,
            )
            status = parsed.status
            classification = ClassificationResult()
            match = EntityMatch()
            try:
                classification = (self.classifier.classify(record, parsed)
                                  if self.classifier else classification)
            except Exception:
                warning_codes = safe_parser_warnings(warning_codes, fallback="classification_failed")
                status = "partial"
            try:
                match = self.matcher.match(record, classification) if self.matcher else match
            except Exception:
                warning_codes = safe_parser_warnings(warning_codes, fallback="entity_resolution_failed")
                status = "partial"

            with self.engine.begin() as db:
                current = db.execute(select(
                    documents.c.active_version_id, documents.c.connection_id, documents.c.metadata_json,
                ).where(documents.c.id == row["document_id"])).mappings().one_or_none()
                if (current is None or current["active_version_id"] != row["version_id"]
                        or (is_authorized is not None and not is_authorized(connection_id))):
                    result["skipped"] += 1
                    continue
                latest = current["metadata_json"] or {}
                override = latest.get("user_override", {})
                previous_match = latest.get("match", {})
                updated = {**latest, "classification": classification.model_dump(mode="json"),
                           "parser_warning_codes": warning_codes}
                if override.get("subject_id"):
                    for field in ("subject_id", "chapter_id", "teaching_unit_id"):
                        if field in override:
                            updated[field] = override[field]
                elif previous_match.get("confirmed"):
                    updated["match"] = previous_match
                    for field in ("subject_id", "chapter_id", "teaching_unit_id"):
                        if field in latest:
                            updated[field] = latest[field]
                else:
                    updated["match"] = match.model_dump(mode="json")
                    if match.confirmed and match.subject_id:
                        updated["subject_id"] = match.subject_id
                        if match.chapter_id:
                            updated["chapter_id"] = match.chapter_id
                        if match.teaching_unit_id:
                            updated["teaching_unit_id"] = match.teaching_unit_id

                old_chunks = db.execute(select(chunks.c.id, chunks.c.ordinal).where(
                    chunks.c.version_id == row["version_id"],
                )).mappings().all()
                by_ordinal = {chunk["ordinal"]: chunk["id"] for chunk in old_chunks}
                new_ordinals = set()
                for ordinal, chunk in enumerate(parsed.chunks):
                    new_ordinals.add(ordinal)
                    payload = chunk.model_dump(mode="json")
                    if ordinal in by_ordinal:
                        db.execute(update(chunks).where(chunks.c.id == by_ordinal[ordinal]).values(payload=payload))
                    else:
                        db.execute(insert(chunks).values(
                            id=new_id(), version_id=row["version_id"], ordinal=ordinal, payload=payload,
                        ))
                stale_chunk_ids = [chunk["id"] for chunk in old_chunks if chunk["ordinal"] not in new_ordinals]
                if stale_chunk_ids:
                    db.execute(delete(chunks).where(chunks.c.id.in_(stale_chunk_ids)))
                db.execute(update(versions).where(versions.c.id == row["version_id"]).values(
                    status=status, parser_version=parsed.parser_version,
                    source_metadata={**(row["source_metadata"] or {}),
                                     "parser_warning_codes": warning_codes},
                ))
                db.execute(update(documents).where(documents.c.id == row["document_id"]).values(
                    metadata_json=updated,
                ))
            result["reprocessed"] += 1
            result[status] += 1
        return result

    def retire_missing_calendar_events(self, db, connection_id, snapshot, seen_ids):
        payload = records.c.payload
        query = select(records.c.external_id, payload).where(
            records.c.connection_id == connection_id, records.c.kind == "event",
            records.c.external_id.startswith(snapshot.external_id_prefix, autoescape=True),
            payload["status"].as_string() != "cancelled",
        )
        if snapshot.starts_at is not None:
            tz = payload["original_timezone"].as_string()
            start = func.ensora_interval_time(payload["starts_at"].as_string(), payload["all_day_start"].as_string(), tz)
            end = func.ensora_interval_time(payload["ends_at"].as_string(), payload["all_day_end"].as_string(), tz)
            query = query.where(start < snapshot.ends_at.astimezone(UTC).isoformat(timespec="microseconds"),
                                end > snapshot.starts_at.astimezone(UTC).isoformat(timespec="microseconds"))
        retired = 0
        for row in db.execute(query).mappings().all():
            if row["external_id"] in seen_ids:
                continue
            event = CalendarEvent.model_validate(row["payload"])
            self._record(connection_id, event.model_copy(update={"status": "cancelled", "busy": False}), db)
            retired += 1
        return retired

    def import_academics(self, connection_id, items, dry_run=False):
        results = []
        with self.engine.connect() as db:
            transaction = db.begin()
            try:
                for index, item in enumerate(items):
                    try:
                        status = self._record(connection_id, item, db)
                        results.append({"status": status})
                    except AcademicImportError as exc:
                        results.append({"index": index, "external_id": item.provenance.external_id,
                                        "status": "error", "code": str(exc)})
                if dry_run:
                    transaction.rollback()
                else:
                    transaction.commit()
            except BaseException:
                transaction.rollback()
                raise
        return results

    def _record(self, connection_id, record, db=None, metrics=None):
        if db is None:
            with self.engine.begin() as session:
                return self._record(connection_id, record, session, metrics)
        external_id = record.provenance.external_id
        parent_key = getattr(record, "parent_key", None)
        payload = record.model_dump(mode="json")
        previous = db.execute(select(records).where(
            records.c.connection_id == connection_id, records.c.external_id == external_id,
        )).mappings().one_or_none()
        if previous and previous["payload"] == payload:
            return "unchanged"
        item_id = previous["id"] if previous else new_id()
        parent_id = None
        if parent_key:
            parent_id = db.scalar(select(records.c.id).where(
                records.c.connection_id == connection_id, records.c.external_id == parent_key,
            ))
            if parent_id is None:
                raise AcademicImportError("missing_parent")
            ancestor = parent_id
            while ancestor is not None:
                if ancestor == item_id:
                    raise AcademicImportError("cycle")
                ancestor = db.scalar(select(records.c.parent_id).where(records.c.id == ancestor))
        values = {"parent_id": parent_id, "kind": record.kind, "payload": payload}
        if previous:
            if record.kind != previous["kind"]:
                raise AcademicImportError("kind_changed")
            db.execute(update(records).where(records.c.id == item_id).values(**values))
        else:
            db.execute(insert(records).values(id=item_id, connection_id=connection_id,
                                              external_id=external_id, **values))
        db.execute(insert(record_history).values(id=new_id(), record_id=item_id,
                                                  payload=payload, recorded_at=now()))
        if isinstance(record, CalendarEvent):
            if record.status == "cancelled":
                count(metrics, "intervals_cancelled")
            else:
                if record.busy:
                    count(metrics, "busy_intervals_written")
                if previous and (record.busy or previous["payload"].get("busy", True)):
                    count(metrics, "intervals_updated")
        elif record.kind == "assessment":
            count(metrics, "assessments_persisted")
        return "records"

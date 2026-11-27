"""Transactional ingestion with immutable content-addressed blobs."""
from datetime import UTC
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import func, insert, select, update

from ..contracts.sources import CalendarEvent, ClassificationResult, Document, DocumentChunk, EntityMatch, FetchedDocument, ParsedContent, SourceKind
from .auth import now
from .database import chunks, documents, new_id, record_history, records, versions


def count(metrics, name, amount=1):
    if metrics is not None:
        metrics[name] = metrics.get(name, 0) + amount


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
        except Exception:
            count(metrics, "download_failures")
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
        if retry_parse:
            try:
                parsed = self.parser.parse(record, content)
                status = parsed.status if parsed is not None else "unsupported"
                classification = self.classifier.classify(record, parsed) if self.classifier else ClassificationResult()
                match = self.matcher.match(record, classification) if self.matcher else EntityMatch()
                metadata.update(classification=classification.model_dump(mode="json"), match=match.model_dump(mode="json"),
                                parser_warning_codes=parsed.warning_codes if parsed else [])
                if match.confirmed:
                    metadata.update(subject_id=match.subject_id or record.subject_id,
                                    chapter_id=match.chapter_id or record.chapter_id,
                                    teaching_unit_id=match.teaching_unit_id or record.teaching_unit_id)
            except Exception:
                parsed = None
                status = "failed"
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
                                  "source_metadata": incoming | {"parser_warning_codes": parsed.warning_codes
                                                                 if parsed else []}}
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
            if status != "failed":
                prior_status = db.scalar(select(versions.c.status).where(versions.c.id == old_version_id))
                active_version_id = old_version_id if status == "unsupported" and prior_status in ("ready", "partial") else version_id
                db.execute(update(documents).where(documents.c.id == document_id).values(
                    metadata_json=metadata, remote_revision=revision, active_version_id=active_version_id,
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

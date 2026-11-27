"""End-to-end normalized ingestion pipeline helper for standalone runs & ETL handoff."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .classification.classifier import DocumentClassifier
from .connectors.base import SourceConnector
from .contracts import (
    ClassificationResult,
    EntityResolutionResult,
    ExamCorrectionMatch,
    FetchResult,
    ParsedDocument,
    SourceRecord,
)
from .parsers.registry import DocumentParserRegistry
from .resolution.entity_resolver import AcademicEntityResolver


@dataclass(frozen=True)
class ProcessedDocumentBundle:
    """Normalized ETL bundle combining SourceRecord, ParsedDocument, Classification, and Resolution."""

    record: SourceRecord
    fetch_result: FetchResult | None
    parsed: ParsedDocument | None
    classification: ClassificationResult
    resolution: EntityResolutionResult
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "parsed": self.parsed.to_dict() if self.parsed else None,
            "classification": self.classification.to_dict(),
            "resolution": self.resolution.to_dict(),
            "error": self.error,
        }


@dataclass(frozen=True)
class IngestionBatchResult:
    """Result of executing one paginated ingestion batch with exam-correction reconciliation."""

    documents: tuple[ProcessedDocumentBundle, ...]
    non_document_records: tuple[SourceRecord, ...]
    exam_correction_matches: tuple[ExamCorrectionMatch, ...]
    next_cursor: str | None
    complete: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


class IngestionPipelineAdapter:
    """Runs connector -> fetch -> parse -> classify -> resolve -> exam pairing cleanly."""

    def __init__(
        self,
        *,
        parser_registry: DocumentParserRegistry | None = None,
        classifier: DocumentClassifier | None = None,
        resolver: AcademicEntityResolver | None = None,
    ) -> None:
        self._parsers = parser_registry or DocumentParserRegistry()
        self._classifier = classifier or DocumentClassifier()
        self._resolver = resolver or AcademicEntityResolver()

    def process_connector_page(
        self,
        connector: SourceConnector,
        *,
        scope: dict[str, Any] | None = None,
        cursor: str | None = None,
        fetch_and_parse: bool = True,
    ) -> IngestionBatchResult:
        page = connector.list_changes(scope=scope, cursor=cursor)
        bundles: list[ProcessedDocumentBundle] = []
        non_docs: list[SourceRecord] = []
        errors: list[str] = list(page.errors)

        for rec in page.items:
            if rec.record_kind != "document":
                non_docs.append(rec)
                continue

            fetch_res: FetchResult | None = None
            parsed: ParsedDocument | None = None
            item_err: str | None = None

            if fetch_and_parse and connector.capabilities().supports_download:
                try:
                    fetch_res = connector.fetch_blob(rec)
                    parsed = self._parsers.parse_bytes(
                        fetch_res.content,
                        filename=fetch_res.filename,
                        mime_type=fetch_res.mime_type,
                        provenance=rec.provenance,
                    )
                except Exception as exc:
                    item_err = str(exc)
                    errors.append(f"{rec.external_id}: {exc}")

            classification = self._classifier.classify(
                title=rec.title,
                filename=fetch_res.filename if fetch_res else rec.title,
                folder_path=rec.folder_path,
                mime_type=fetch_res.mime_type if fetch_res else rec.mime_type,
                record=rec,
                parsed=parsed,
            )
            resolution = self._resolver.resolve_record(
                rec,
                body_excerpt=parsed.full_text[:1000] if parsed else None,
            )
            bundles.append(
                ProcessedDocumentBundle(
                    record=rec,
                    fetch_result=fetch_res,
                    parsed=parsed,
                    classification=classification,
                    resolution=resolution,
                    error=item_err,
                )
            )

        exam_matches = self._resolver.match_exams_and_corrections(
            [(b.record, b.classification.category, b.resolution) for b in bundles]
        )

        return IngestionBatchResult(
            documents=tuple(bundles),
            non_document_records=tuple(non_docs),
            exam_correction_matches=tuple(exam_matches),
            next_cursor=page.next_cursor,
            complete=page.complete,
            warnings=page.warnings,
            errors=tuple(errors),
        )

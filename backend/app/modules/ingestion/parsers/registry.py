"""Modular document parser registry routing documents by MIME type and extension.

Provides:
- Magic-byte MIME sniffing fallback for generic or mislabeled files.
- Bounded in-memory parser-version-aware LRU caching keyed by
  ``(sha256, parser_name, parser_version, effective_mime)`` so duplicate files and
  selective local reprocessing skip redundant parsing while invalidating cleanly
  whenever a parser version bumps.
- Bounded multi-process batch parsing (`parse_batch_parallel`) for CPU-bound
  multi-document workloads such as bulk PDF extraction.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import io
import json
import os
import threading
import zipfile
from pathlib import Path
from typing import Any, Sequence

from ..connectors.base import guess_mime_type, sha256_bytes
from ..contracts import ParsedDocument
from .base import DocumentParser
from .markup_parser import (
    HtmlDocumentParser,
    ImageOcrParser,
    MarkdownDocumentParser,
    PlainTextAndCodeParser,
)
from .notebook_parser import NotebookDocumentParser
from .office_parser import (
    DocxDocumentParser,
    LegacyOfficeDocumentParser,
    OpenDocumentParser,
    PptxDocumentParser,
    XlsxDocumentParser,
    ZipArchiveDocumentParser,
)
from .pdf_parser import PdfDocumentParser

DEFAULT_PARSE_CACHE_ENTRIES = 256
_WORKER_REGISTRY: DocumentParserRegistry | None = None


def _parse_in_worker(
    payload: tuple[bytes, str, str | None, dict[str, Any] | None, str | None],
) -> ParsedDocument:
    """Process-pool worker entrypoint reusing a per-process default parser registry."""
    global _WORKER_REGISTRY
    if _WORKER_REGISTRY is None:
        _WORKER_REGISTRY = DocumentParserRegistry(max_cache_entries=0)
    content, filename, mime_type, provenance, precomputed_sha256 = payload
    return _WORKER_REGISTRY.parse_bytes(
        content,
        filename=filename,
        mime_type=mime_type,
        provenance=provenance,
        precomputed_sha256=precomputed_sha256,
        use_cache=False,
    )


def sniff_mime_from_bytes(content: bytes) -> str | None:
    """Safely detect common document MIME types from magic bytes when MIME/extension is missing or generic."""
    if not content:
        return None
    head = content[:4096]
    stripped = head.lstrip()
    if stripped.startswith(b"%PDF-"):
        return "application/pdf"
    if stripped.startswith(b"{\\rtf"):
        return "application/rtf"
    if stripped[:256].lower().startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if head.startswith((b"\x89PNG\r\n\x1a\n",)):
        return "image/png"
    if head.startswith((b"\xff\xd8\xff",)):
        return "image/jpeg"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = set(zf.namelist())
                if "word/document.xml" in names:
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if "ppt/presentation.xml" in names:
                    return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                if "xl/workbook.xml" in names:
                    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if "content.xml" in names and "mimetype" in names:
                    odf_mime = zf.read("mimetype").decode("ascii", errors="ignore").strip()
                    if odf_mime.startswith("application/vnd.oasis.opendocument."):
                        return odf_mime
                if "content.xml" in names:
                    return "application/vnd.oasis.opendocument.text"
                return "application/zip"
        except Exception:
            return None
    # Detect Jupyter notebooks served as generic JSON/text/octet-stream
    if stripped.startswith(b"{") and b'"cells"' in content[:16384] and b'"nbformat"' in content[:65536]:
        try:
            nb = json.loads(content.decode("utf-8-sig"))
            if isinstance(nb, dict) and isinstance(nb.get("cells"), list):
                return "application/x-ipynb+json"
        except Exception:
            pass
    # Printable UTF-8 text fallback (no NUL bytes, >= 85% printable characters)
    if b"\x00" not in head:
        try:
            sample = head.decode("utf-8-sig")
            if sample.strip():
                printable = sum(1 for ch in sample if ch.isprintable() or ch in "\n\r\t")
                if (printable / max(1, len(sample))) >= 0.85:
                    return "text/plain"
        except UnicodeDecodeError:
            pass
    return None


class DocumentParserRegistry:
    """Extensible registry dispatching files to format-specific DocumentParsers.

    Maintains a bounded thread-safe in-memory LRU cache keyed by
    ``(content_sha256, parser_name, parser_version, effective_mime)`` so identical
    blobs (duplicate copies across folders or selective local reprocessing) avoid
    repeated subprocess/parsing work while automatically invalidating on parser
    version upgrades.
    """

    def __init__(
        self,
        parsers: list[DocumentParser] | None = None,
        *,
        max_cache_entries: int = DEFAULT_PARSE_CACHE_ENTRIES,
        enable_pdf_ocr: bool = False,
    ) -> None:
        self._is_default_registry = parsers is None and not enable_pdf_ocr
        if parsers is not None:
            self._parsers: list[DocumentParser] = list(parsers)
        else:
            self._parsers = [
                PdfDocumentParser(enable_ocr=enable_pdf_ocr),
                NotebookDocumentParser(),
                PptxDocumentParser(),
                DocxDocumentParser(),
                XlsxDocumentParser(),
                OpenDocumentParser(),
                LegacyOfficeDocumentParser(),
                MarkdownDocumentParser(),
                HtmlDocumentParser(),
                PlainTextAndCodeParser(),
                ImageOcrParser(),
            ]
            self._parsers.append(
                ZipArchiveDocumentParser(sub_parser_fn=self._parse_archive_member)
            )
        self._max_cache_entries = max(0, int(max_cache_entries))
        self._parse_cache: OrderedDict[tuple[str, str, str, str], ParsedDocument] = OrderedDict()
        self._cache_lock = threading.Lock()
        self.cache_hits: int = 0
        self.cache_misses: int = 0

    def clear_cache(self) -> None:
        """Evict all entries from the in-memory parser-version cache."""
        with self._cache_lock:
            self._parse_cache.clear()
            self.cache_hits = 0
            self.cache_misses = 0

    def cache_stats(self) -> dict[str, int]:
        """Return diagnostic hit/miss/size statistics for the in-memory parse cache."""
        with self._cache_lock:
            return {
                "entries": len(self._parse_cache),
                "max_entries": self._max_cache_entries,
                "hits": self.cache_hits,
                "misses": self.cache_misses,
            }

    def _parse_archive_member(self, content: bytes, *, filename: str) -> ParsedDocument | None:
        # Delegate archive members to non-ZIP parsers only (prevents nested zip recursion)
        effective_mime = guess_mime_type(filename)
        parser = self.resolve(filename=filename, mime_type=effective_mime)
        if parser is None or isinstance(parser, ZipArchiveDocumentParser):
            sniffed = sniff_mime_from_bytes(content)
            if sniffed and sniffed != "application/zip":
                parser = self.resolve(filename=filename, mime_type=sniffed)
                effective_mime = sniffed
        if parser is None or isinstance(parser, ZipArchiveDocumentParser):
            return None
        return parser.parse(content, filename=filename, mime_type=effective_mime)

    def register(self, parser: DocumentParser, *, prepend: bool = False) -> None:
        """Register a custom parser and mark the registry as non-default."""
        self._is_default_registry = False
        if prepend:
            self._parsers.insert(0, parser)
        else:
            self._parsers.append(parser)

    @property
    def registered_parsers(self) -> tuple[str, ...]:
        """Return the ordered names of all registered parsers."""
        return tuple(p.name for p in self._parsers)

    def resolve(self, *, filename: str = "", mime_type: str = "") -> DocumentParser | None:
        """Select the first registered parser that supports the given filename/MIME type."""
        effective_mime = mime_type or guess_mime_type(filename)
        for parser in self._parsers:
            if parser.can_parse(filename=filename, mime_type=effective_mime):
                return parser
        return None

    def _invoke_parser(
        self,
        parser: DocumentParser,
        content: bytes,
        *,
        filename: str,
        mime_type: str,
        provenance: dict[str, Any] | None,
        precomputed_sha256: str | None,
    ) -> ParsedDocument:
        if precomputed_sha256 is not None and isinstance(parser, PdfDocumentParser):
            return parser.parse(
                content,
                filename=filename,
                mime_type=mime_type,
                provenance=provenance,
                precomputed_sha256=precomputed_sha256,
            )
        return parser.parse(
            content,
            filename=filename,
            mime_type=mime_type,
            provenance=provenance,
        )

    def parse_bytes(
        self,
        content: bytes,
        *,
        filename: str,
        mime_type: str | None = None,
        provenance: dict[str, Any] | None = None,
        precomputed_sha256: str | None = None,
        use_cache: bool = True,
    ) -> ParsedDocument:
        """Parse raw document bytes with MIME sniffing, version-aware caching, and retry fallback.

        Args:
            content: Raw document bytes.
            filename: Original filename used for extension routing and fallback titles.
            mime_type: Optional declared MIME type.
            provenance: Optional source provenance dictionary attached to output.
            precomputed_sha256: Optional SHA-256 hex digest computed upstream.
            use_cache: When ``True`` and cache capacity > 0, reuse cached parses whose
                ``(sha256, parser_name, parser_version, effective_mime)`` matches.

        Returns:
            ``ParsedDocument`` preserving exact provenance and filename metadata.
        """
        effective_mime = mime_type or guess_mime_type(filename)
        parser = self.resolve(filename=filename, mime_type=effective_mime)
        if parser is None:
            sniffed = sniff_mime_from_bytes(content)
            if sniffed:
                parser = self.resolve(filename=filename, mime_type=sniffed)
                if parser is not None:
                    effective_mime = sniffed
        digest = precomputed_sha256 or (
            sha256_bytes(content) if (parser is None or (use_cache and self._max_cache_entries > 0)) else None
        )
        if parser is None:
            return ParsedDocument(
                parser_name="unsupported",
                parser_version="1.0.0",
                title=Path(filename).stem,
                mime_type=effective_mime,
                content_hash=digest or sha256_bytes(content),
                blocks=(),
                metadata={"filename": filename, "size_bytes": len(content)},
                provenance=dict(provenance or {}),
                extraction_quality=0.0,
                unsupported_elements=(f"unsupported_format:{effective_mime}",),
                warnings=(f"No registered parser supports '{filename}' ({effective_mime}).",),
                error=f"Unsupported format: {effective_mime}",
            )

        cache_key: tuple[str, str, str, str] | None = None
        if use_cache and self._max_cache_entries > 0 and digest is not None and len(content) >= 64:
            cache_key = (digest, parser.name, parser.version, effective_mime)
            with self._cache_lock:
                cached = self._parse_cache.get(cache_key)
                if cached is not None:
                    self._parse_cache.move_to_end(cache_key)
                    self.cache_hits += 1
                    cached_meta = dict(cached.metadata)
                    cached_meta["filename"] = filename
                    return replace(
                        cached,
                        metadata=cached_meta,
                        provenance=dict(provenance or {}),
                    )
                self.cache_misses += 1

        result = self._invoke_parser(
            parser,
            content,
            filename=filename,
            mime_type=effective_mime,
            provenance=provenance,
            precomputed_sha256=digest,
        )
        # If the MIME-matched parser errored on a mislabeled file (e.g. HTML/JSON/ZIP mismatch),
        # retry once with strongly-typed magic bytes ONLY (never coerce malformed PDF/Office into plain text).
        if result.error and not result.blocks:
            sniffed = sniff_mime_from_bytes(content)
            if sniffed and sniffed not in (effective_mime, "text/plain"):
                alt = self.resolve(filename="", mime_type=sniffed)
                if alt is not None and alt is not parser:
                    alt_res = self._invoke_parser(
                        alt,
                        content,
                        filename=filename,
                        mime_type=sniffed,
                        provenance=provenance,
                        precomputed_sha256=digest,
                    )
                    if alt_res.blocks:
                        result = alt_res

        if cache_key is not None and not result.error:
            with self._cache_lock:
                self._parse_cache[cache_key] = result
                self._parse_cache.move_to_end(cache_key)
                while len(self._parse_cache) > self._max_cache_entries:
                    self._parse_cache.popitem(last=False)
        return result

    def parse_batch_parallel(
        self,
        items: Sequence[tuple[bytes, str, str | None, dict[str, Any] | None]],
        *,
        max_workers: int | None = None,
    ) -> list[ParsedDocument]:
        """Parse a batch of documents in parallel across bounded worker processes.

        Uses ``ProcessPoolExecutor`` for CPU-bound batches (e.g. multi-PDF ingestion or
        selective local reprocessing) while preserving exact input order and falling
        back seamlessly to sequential execution for tiny batches (<= 2 items), custom
        parser registries, or constrained environments where process spawning fails.

        Args:
            items: Sequence of ``(content, filename, mime_type, provenance)`` tuples.
            max_workers: Optional worker ceiling (defaults to ``min(4, cpu_count)``).

        Returns:
            List of ``ParsedDocument`` objects aligned 1:1 with ``items``.
        """
        if not items:
            return []
        cpu_count = os.cpu_count() or 2
        worker_cap = max(1, min(int(max_workers or min(4, cpu_count)), cpu_count, len(items)))
        if worker_cap <= 1 or len(items) <= 2 or not self._is_default_registry:
            return [
                self.parse_bytes(content, filename=fname, mime_type=mime, provenance=prov)
                for content, fname, mime, prov in items
            ]

        # Resolve cache hits up front so duplicate payloads never cross IPC boundaries
        results: list[ParsedDocument | None] = [None] * len(items)
        pending_indices: list[int] = []
        pending_payloads: list[tuple[bytes, str, str | None, dict[str, Any] | None, str | None]] = []

        for idx, (content, fname, mime, prov) in enumerate(items):
            effective_mime = mime or guess_mime_type(fname)
            parser = self.resolve(filename=fname, mime_type=effective_mime)
            digest = sha256_bytes(content)
            if parser is not None and self._max_cache_entries > 0:
                ckey = (digest, parser.name, parser.version, effective_mime)
                with self._cache_lock:
                    cached = self._parse_cache.get(ckey)
                    if cached is not None:
                        self._parse_cache.move_to_end(ckey)
                        self.cache_hits += 1
                        cached_meta = dict(cached.metadata)
                        cached_meta["filename"] = fname
                        results[idx] = replace(
                            cached,
                            metadata=cached_meta,
                            provenance=dict(prov or {}),
                        )
                        continue
            pending_indices.append(idx)
            pending_payloads.append((content, fname, mime, prov, digest))

        if not pending_payloads:
            return [r for r in results if r is not None]

        if len(pending_payloads) <= 2:
            for idx, (content, fname, mime, prov, digest) in zip(pending_indices, pending_payloads):
                results[idx] = self.parse_bytes(
                    content,
                    filename=fname,
                    mime_type=mime,
                    provenance=prov,
                    precomputed_sha256=digest,
                )
            return [r for r in results if r is not None]

        try:
            with ProcessPoolExecutor(max_workers=min(worker_cap, len(pending_payloads))) as pool:
                parsed_list = list(pool.map(_parse_in_worker, pending_payloads))
        except Exception:
            parsed_list = [
                self.parse_bytes(
                    content,
                    filename=fname,
                    mime_type=mime,
                    provenance=prov,
                    precomputed_sha256=digest,
                )
                for content, fname, mime, prov, digest in pending_payloads
            ]

        for idx, parsed_doc in zip(pending_indices, parsed_list):
            results[idx] = parsed_doc
            if not parsed_doc.error and self._max_cache_entries > 0:
                ckey = (
                    parsed_doc.content_hash,
                    parsed_doc.parser_name,
                    parsed_doc.parser_version,
                    parsed_doc.mime_type,
                )
                with self._cache_lock:
                    self._parse_cache[ckey] = parsed_doc
                    self._parse_cache.move_to_end(ckey)
                    while len(self._parse_cache) > self._max_cache_entries:
                        self._parse_cache.popitem(last=False)

        return [r for r in results if r is not None]

    def parse_file(
        self,
        path: Path,
        *,
        mime_type: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> ParsedDocument:
        """Read a local file and parse it through ``parse_bytes``."""
        prov = dict(provenance or {})
        prov.setdefault("filename", path.name)
        return self.parse_bytes(
            path.read_bytes(),
            filename=path.name,
            mime_type=mime_type,
            provenance=prov,
        )

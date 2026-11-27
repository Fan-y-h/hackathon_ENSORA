"""Modular document parser registry routing documents by MIME type and extension."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

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
    OpenDocumentParser,
    PptxDocumentParser,
    XlsxDocumentParser,
    ZipArchiveDocumentParser,
)
from .pdf_parser import PdfDocumentParser


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
    """Extensible registry dispatching files to format-specific DocumentParsers."""

    def __init__(self, parsers: list[DocumentParser] | None = None) -> None:
        if parsers is not None:
            self._parsers: list[DocumentParser] = list(parsers)
        else:
            self._parsers = [
                PdfDocumentParser(),
                NotebookDocumentParser(),
                PptxDocumentParser(),
                DocxDocumentParser(),
                XlsxDocumentParser(),
                OpenDocumentParser(),
                MarkdownDocumentParser(),
                HtmlDocumentParser(),
                PlainTextAndCodeParser(),
                ImageOcrParser(),
            ]
            self._parsers.append(
                ZipArchiveDocumentParser(sub_parser_fn=self._parse_archive_member)
            )

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
        if prepend:
            self._parsers.insert(0, parser)
        else:
            self._parsers.append(parser)

    @property
    def registered_parsers(self) -> tuple[str, ...]:
        return tuple(p.name for p in self._parsers)

    def resolve(self, *, filename: str = "", mime_type: str = "") -> DocumentParser | None:
        effective_mime = mime_type or guess_mime_type(filename)
        for parser in self._parsers:
            if parser.can_parse(filename=filename, mime_type=effective_mime):
                return parser
        return None

    def parse_bytes(
        self,
        content: bytes,
        *,
        filename: str,
        mime_type: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> ParsedDocument:
        effective_mime = mime_type or guess_mime_type(filename)
        parser = self.resolve(filename=filename, mime_type=effective_mime)
        if parser is None:
            sniffed = sniff_mime_from_bytes(content)
            if sniffed:
                parser = self.resolve(filename=filename, mime_type=sniffed)
                if parser is not None:
                    effective_mime = sniffed
        if parser is None:
            return ParsedDocument(
                parser_name="unsupported",
                parser_version="1.0.0",
                title=Path(filename).stem,
                mime_type=effective_mime,
                content_hash=sha256_bytes(content),
                blocks=(),
                metadata={"filename": filename, "size_bytes": len(content)},
                provenance=dict(provenance or {}),
                extraction_quality=0.0,
                unsupported_elements=(f"unsupported_format:{effective_mime}",),
                warnings=(f"No registered parser supports '{filename}' ({effective_mime}).",),
                error=f"Unsupported format: {effective_mime}",
            )
        result = parser.parse(
            content,
            filename=filename,
            mime_type=effective_mime,
            provenance=provenance,
        )
        # If the MIME-matched parser errored on a mislabeled file (e.g. HTML/JSON/ZIP mismatch),
        # retry once with strongly-typed magic bytes ONLY (never coerce malformed PDF/Office into plain text).
        if result.error and not result.blocks:
            sniffed = sniff_mime_from_bytes(content)
            if sniffed and sniffed not in (effective_mime, "text/plain"):
                alt = self.resolve(filename="", mime_type=sniffed)
                if alt is not None and alt is not parser:
                    alt_res = alt.parse(
                        content,
                        filename=filename,
                        mime_type=sniffed,
                        provenance=provenance,
                    )
                    if alt_res.blocks:
                        return alt_res
        return result

    def parse_file(
        self,
        path: Path,
        *,
        mime_type: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> ParsedDocument:
        prov = dict(provenance or {})
        prov.setdefault("filename", path.name)
        return self.parse_bytes(
            path.read_bytes(),
            filename=path.name,
            mime_type=mime_type,
            provenance=prov,
        )

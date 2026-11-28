"""PDF document parser prioritizing page locators, headings, formulas, tables, and optional OCR.

Extraction strategy chain (best available automatically selected):
1. PyMuPDF (``fitz``) if explicitly installed in the user environment (note: AGPL-3.0, optional only).
2. ``pypdfium2`` (Apache-2.0 / BSD-3-Clause, MIT-compatible) if installed.
3. ``pypdf`` (BSD-3-Clause) if installed.
4. System ``pdftotext`` (Poppler CLI) via in-memory ``stdin`` pipe (zero temp files, zero ``pdfinfo``
   fork, automatic temporary-file fallback only if Poppler rejects stdin on a malformed xref table).
5. Pure-Python PDF text-stream fallback (supports uncompressed and FlateDecode content streams).

Optional OCR is never run systematically; missing text layers are explicitly flagged so
scanned-heavy PDFs surface actionable warnings while mixed-media slide decks retain ``ready`` status.
"""

from __future__ import annotations

from functools import lru_cache
import re
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any

from ..connectors.base import sha256_bytes
from ..contracts import Locator, ParsedBlock, ParsedDocument
from .base import DocumentParser, detect_language_hint, extract_math_expressions

_HEADING_RE = re.compile(
    r"^(?:(?:chapitre|chapter|section|partie|part)\s+[0-9IVX]+|[0-9]+(?:\.[0-9]+)*\.?\s+[A-ZÀ-ÖØ-Þ]).{2,100}$",
    re.IGNORECASE,
)
_FIGURE_RE = re.compile(r"^(?:figure|fig\.?)\s*[0-9]+(?:\.[0-9]+)?\s*[:\-–.]?\s*(.*)$", re.IGNORECASE)
_PDF_TITLE_RE = re.compile(rb"/Title\s*\(([^)]{1,200})\)")
_WS_TABLE_DETECT_RE = re.compile(r"\S\s{3,}\S")
_WS_TABLE_SPLIT_RE = re.compile(r"\s{3,}")
_ORIG_SHUTIL_WHICH = shutil.which


@lru_cache(maxsize=8)
def _cached_which(binary_name: str) -> str | None:
    """Resolve an external CLI binary once per process."""
    return _ORIG_SHUTIL_WHICH(binary_name)


def _find_cli_binary(binary_name: str) -> str | None:
    """Look up a CLI binary with process-lifetime caching while respecting test monkeypatches."""
    if shutil.which is _ORIG_SHUTIL_WHICH:
        return _cached_which(binary_name)
    return shutil.which(binary_name)


def _decode_pdf_literal(raw: bytes) -> str:
    """Unescape basic PDF literal string escapes inside parentheses."""
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == 92 and i + 1 < n:  # backslash
            nxt = raw[i + 1]
            mapping = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("("): 40, ord(")"): 41, ord("\\"): 92}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
        out.append(ch)
        i += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return out.decode("latin-1", errors="replace")


def _extract_title_from_pdf_bytes(content: bytes) -> str | None:
    """Extract an ASCII/UTF-8 ``/Title (...)`` metadata string without spawning ``pdfinfo``.

    Checks the document header and trailer windows first (where PDF Info dictionaries
    almost always reside) before scanning the full buffer for small PDFs.
    """
    windows = (
        (content,)
        if len(content) <= 65536
        else (content[:16384], content[-32768:])
    )
    for chunk in windows:
        match = _PDF_TITLE_RE.search(chunk)
        if match:
            title = _decode_pdf_literal(match.group(1)).strip()
            if title:
                return title
    return None


def _extract_pdf_text_pure_python(content: bytes) -> tuple[list[str], dict[str, Any]]:
    """Lightweight pure-Python PDF text fallback for standard PDFs and synthetic fixtures."""
    if not content.lstrip().startswith(b"%PDF-"):
        raise ValueError("Invalid PDF header: file does not start with %PDF-.")

    meta: dict[str, Any] = {}
    pdf_title = _extract_title_from_pdf_bytes(content)
    if pdf_title:
        meta["pdf_title"] = pdf_title

    pages_text: list[str] = []
    stream_re = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
    for m in stream_re.finditer(content):
        stream_data = m.group(1)
        # Attempt FlateDecode decompression when compressed
        decoded_stream = stream_data
        try:
            decoded_stream = zlib.decompress(stream_data)
        except Exception:
            pass

        if b"BT" not in decoded_stream and b"Tj" not in decoded_stream and b"TJ" not in decoded_stream:
            continue

        fragments: list[str] = []
        for tj in re.finditer(rb"\((?:\\.|[^\\)])*\)\s*Tj", decoded_stream):
            lit = tj.group(0)
            inner = lit[1 : lit.rfind(b")")]
            fragments.append(_decode_pdf_literal(inner))
        for arr in re.finditer(rb"\[(.*?)\]\s*TJ", decoded_stream, re.DOTALL):
            parts = []
            for lit_m in re.finditer(rb"\((?:\\.|[^\\)])*\)", arr.group(1)):
                parts.append(_decode_pdf_literal(lit_m.group(0)[1:-1]))
            if parts:
                fragments.append("".join(parts))

        text = "\n".join(f.strip() for f in fragments if f.strip()).strip()
        if text:
            pages_text.append(text)

    if not pages_text:
        # Count /Type /Page objects even if scanned (0 text)
        page_count = len(re.findall(rb"/Type\s*/Page\b", content))
        pages_text = [""] * max(1, page_count)
    return pages_text, meta


def _extract_pdf_via_pdftotext(content: bytes) -> tuple[list[str], dict[str, Any]] | None:
    """Extract per-page layout-preserving text via Poppler ``pdftotext`` over stdin.

    Pipes raw PDF bytes directly into ``pdftotext -layout - -`` to eliminate temporary
    directory creation, disk writes, and the second ``pdfinfo`` subprocess fork. Falls
    back once to a temporary file only if Poppler rejects non-seekable stdin on an
    unusual PDF structure.

    Args:
        content: Raw PDF bytes starting with ``%PDF-``.

    Returns:
        Tuple of ``(pages, metadata)`` on success, or ``None`` if ``pdftotext`` is
        unavailable or fails to decode the document.
    """
    pdftotext_bin = _find_cli_binary("pdftotext")
    if not pdftotext_bin:
        return None

    meta: dict[str, Any] = {}
    pdf_title = _extract_title_from_pdf_bytes(content)
    if pdf_title:
        meta["title"] = pdf_title

    try:
        txt_res = subprocess.run(
            [pdftotext_bin, "-layout", "-", "-"],
            input=content,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    # Fallback once to a seekable temporary file if Poppler rejects stdin for a malformed xref
    if txt_res.returncode != 0:
        try:
            with tempfile.TemporaryDirectory(prefix="ensora_pdf_") as tmpdir:
                pdf_path = Path(tmpdir) / "doc.pdf"
                pdf_path.write_bytes(content)
                txt_res = subprocess.run(
                    [pdftotext_bin, "-layout", str(pdf_path), "-"],
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
        except (OSError, subprocess.SubprocessError):
            return None
        if txt_res.returncode != 0:
            return None

    stdout_text = txt_res.stdout.decode("utf-8", errors="replace")
    raw_pages = stdout_text.split("\x0c")
    if raw_pages and raw_pages[-1] == "":
        raw_pages.pop()
    if not raw_pages:
        raw_pages = [""]
    meta["pages"] = str(len(raw_pages))
    return raw_pages, meta


def _detect_table_rows(lines: list[str]) -> list[tuple[str, ...]]:
    """Detect whitespace-aligned or pipe-delimited table rows inside a PDF page."""
    rows: list[tuple[str, ...]] = []
    for line in lines:
        if "|" in line and line.count("|") >= 2:
            cells = tuple(c.strip() for c in line.strip("|").split("|"))
            if any(cells) and not all(set(c) <= {"-", ":", "="} for c in cells if c):
                rows.append(cells)
        elif _WS_TABLE_DETECT_RE.search(line):
            cells = tuple(c.strip() for c in _WS_TABLE_SPLIT_RE.split(line.strip()) if c.strip())
            if len(cells) >= 2:
                rows.append(cells)
    return rows if len(rows) >= 2 else []


class PdfDocumentParser(DocumentParser):
    """PDF text, structure, formula, table, figure, and optional OCR parser.

    Preserves exact 1-based page locators, layout-aligned tables, LaTeX/Unicode
    math expressions, and deterministic scanned-page diagnostics.
    """

    name = "pdf_parser"
    version = "1.1.1"
    supported_mimes = frozenset({"application/pdf"})
    supported_extensions = frozenset({".pdf"})

    def __init__(self, *, enable_ocr: bool = False) -> None:
        self._enable_ocr = enable_ocr
        if enable_ocr:
            self.version = f"{self.version}+ocr1"

    def _ocr_pages(self, content: bytes, pages: list[str], candidates: list[int]) -> set[int]:
        """Render and transcribe a bounded set of empty pages using local system tools."""
        renderer = _find_cli_binary("pdftoppm")
        tesseract = _find_cli_binary("tesseract")
        if not renderer or not tesseract:
            return set()
        recovered: set[int] = set()
        with tempfile.TemporaryDirectory(prefix="ensora-pdf-ocr-") as directory:
            path = Path(directory) / "document.pdf"
            path.write_bytes(content)
            for page_number in candidates[:8]:
                page_arg = str(page_number)
                try:
                    image = subprocess.run(
                        [renderer, "-f", page_arg, "-l", page_arg, "-singlefile",
                         "-scale-to", "2400", "-png", str(path)],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=True,
                    ).stdout
                    output = subprocess.run(
                        [tesseract, "stdin", "stdout", "-l", "fra+eng"], input=image,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=True,
                    ).stdout.decode("utf-8", errors="replace").strip()
                except (OSError, subprocess.SubprocessError):
                    continue
                if output:
                    pages[page_number - 1] = output
                    recovered.add(page_number)
        return recovered

    def parse(
        self,
        content: bytes,
        *,
        filename: str,
        mime_type: str,
        provenance: dict[str, Any] | None = None,
        precomputed_sha256: str | None = None,
    ) -> ParsedDocument:
        """Parse raw PDF bytes into structured page blocks with provenance locators.

        Args:
            content: Raw PDF byte payload.
            filename: Original filename used for title fallback and metadata.
            mime_type: Declared or sniffed MIME type.
            provenance: Optional source provenance dictionary attached to output.
            precomputed_sha256: Optional SHA-256 hex digest computed upstream to
                avoid redundant hashing of large binaries.

        Returns:
            ``ParsedDocument`` containing page-located blocks, headings, math,
            tables, figures, extraction quality score, and structured warnings.
        """
        digest = precomputed_sha256 or sha256_bytes(content)
        prov = dict(provenance or {})
        warnings: list[str] = []
        unsupported: list[str] = []
        meta: dict[str, Any] = {"filename": filename}
        backend_used = "unknown"
        pages: list[str] | None = None

        signature = content[:1024].lstrip()
        if not signature.startswith(b"%PDF-"):
            is_html = signature.lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type or "application/pdf",
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=("pdf_html_payload" if is_html else "pdf_invalid_header",),
                error="Malformed PDF header",
            )

        # 1. PyMuPDF (fitz) if explicitly installed by user
        try:
            import fitz  # type: ignore

            doc = fitz.open(stream=content, filetype="pdf")
            try:
                pages = [page.get_text("text") for page in doc]
                pdf_meta = doc.metadata or {}
                if pdf_meta.get("title"):
                    meta["title"] = pdf_meta["title"]
                if pdf_meta.get("author"):
                    meta["author"] = pdf_meta["author"]
                backend_used = "pymupdf"
            finally:
                doc.close()
        except ImportError:
            pass
        except Exception as exc:
            warnings.append(f"PyMuPDF failed ({type(exc).__name__}); attempting fallback backend.")

        # 2. pypdfium2 (Apache-2.0 / BSD-3-Clause, MIT-compatible) if installed
        if pages is None:
            try:
                import pypdfium2 as pdfium  # type: ignore

                pdf_doc = pdfium.PdfDocument(content)
                try:
                    extracted_pages: list[str] = []
                    for idx in range(len(pdf_doc)):
                        page = pdf_doc[idx]
                        textpage = page.get_textpage()
                        try:
                            extracted_pages.append(textpage.get_text_bounded() or "")
                        finally:
                            textpage.close()
                            page.close()
                    pages = extracted_pages
                    pdf_title = _extract_title_from_pdf_bytes(content)
                    if pdf_title:
                        meta["title"] = pdf_title
                    backend_used = "pypdfium2"
                finally:
                    pdf_doc.close()
            except ImportError:
                pass
            except Exception as exc:
                warnings.append(f"pypdfium2 failed ({type(exc).__name__}); attempting fallback backend.")

        # 3. pypdf if available
        if pages is None:
            try:
                import io
                from pypdf import PdfReader  # type: ignore

                reader = PdfReader(io.BytesIO(content))
                pages = [(p.extract_text() or "") for p in reader.pages]
                if reader.metadata and getattr(reader.metadata, "title", None):
                    meta["title"] = str(reader.metadata.title)
                backend_used = "pypdf"
            except ImportError:
                pass
            except Exception as exc:
                warnings.append(f"pypdf failed ({type(exc).__name__}); attempting fallback backend.")

        # 4. Poppler pdftotext CLI over stdin (zero temp files, zero pdfinfo fork)
        if pages is None:
            poppler_res = _extract_pdf_via_pdftotext(content)
            if poppler_res is not None:
                pages, poppler_meta = poppler_res
                meta.update(poppler_meta)
                backend_used = "pdftotext"

        # 5. Pure-Python PDF stream fallback
        if pages is None:
            try:
                pages, py_meta = _extract_pdf_text_pure_python(content)
                meta.update(py_meta)
                backend_used = "pure_python_pdf"
            except Exception as exc:
                return ParsedDocument(
                    parser_name=self.name,
                    parser_version=self.version,
                    title=Path(filename).stem,
                    mime_type=mime_type or "application/pdf",
                    content_hash=digest,
                    blocks=(),
                    metadata=meta,
                    provenance=prov,
                    extraction_quality=0.0,
                    warnings=(f"Failed to parse PDF: {exc}",),
                    error=str(exc),
                )

        if not pages:
            warnings.append("pdf_no_pages")
            unsupported.append("pdf_no_pages")
        meta["pdf_backend"] = backend_used
        meta["page_count"] = len(pages)
        candidates = [number for number, page in enumerate(pages, start=1) if not page.strip()]
        scanned_heavy = bool(pages and candidates) and len(candidates) / len(pages) >= 0.30
        ocr_pages: set[int] = set()
        if self._enable_ocr and scanned_heavy and backend_used != "pure_python_pdf":
            try:
                ocr_pages = self._ocr_pages(content, pages, candidates)
            except OSError:
                warnings.append("pdf_ocr_storage_unavailable")
        if self._enable_ocr and scanned_heavy and len(ocr_pages) < len(candidates):
            warnings.append("pdf_ocr_incomplete")

        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        seen_headings: set[str] = set()
        all_math: list[str] = []
        seen_math: set[str] = set()
        tables: list[dict[str, Any]] = []
        figures: list[dict[str, Any]] = []
        current_heading_path: tuple[str, ...] = ()
        ordinal = 0
        empty_pages = 0

        for page_num, raw_text in enumerate(pages, start=1):
            text = raw_text.strip()
            if not text:
                empty_pages += 1
                continue

            lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
            page_math = extract_math_expressions(text)
            for expr in page_math:
                if expr not in seen_math:
                    seen_math.add(expr)
                    all_math.append(expr)

            # Detect headings and figures
            for ln in lines:
                stripped = ln.strip()
                if _HEADING_RE.match(stripped) and stripped not in seen_headings:
                    seen_headings.add(stripped)
                    headings.append(stripped)
                    current_heading_path = (stripped,)
                fig_m = _FIGURE_RE.match(stripped)
                if fig_m:
                    figures.append({"page": page_num, "caption": stripped})

            # Detect tables
            page_table = _detect_table_rows(lines)
            if page_table:
                tables.append({"page": page_num, "rows": [list(r) for r in page_table]})

            lang = detect_language_hint(text[:2500])
            blocks.append(
                ParsedBlock(
                    ordinal=ordinal,
                    kind="paragraph",
                    text=text,
                    locator=Locator(
                        kind="page",
                        page=page_num,
                        section=current_heading_path[-1] if current_heading_path else None,
                    ),
                    heading_path=current_heading_path,
                    language=lang,
                    table_rows=tuple(page_table),
                    math_expressions=page_math,
                    metadata={"ocr": True} if page_num in ocr_pages else {},
                )
            )
            ordinal += 1

        if scanned_heavy and empty_pages:
            unsupported.append("pdf_scanned_pages_unrecovered")
        empty_ratio = empty_pages / max(1, len(pages))
        if empty_pages > 0:
            # Distinguish genuinely scanned / image-heavy PDFs (0 text blocks or >=30% empty pages)
            # from normal text-rich slide decks/lectures with 1-2 blank or diagram-only pages.
            if scanned_heavy:
                if _find_cli_binary("tesseract") is None:
                    unsupported.append("scanned_pages_without_ocr_engine")
                    warnings.append(
                        f"{empty_pages}/{len(pages)} PDF page(s) contained no extractable text "
                        "(likely scanned); tesseract OCR is not installed."
                    )
                else:
                    warnings.append(
                        f"{empty_pages}/{len(pages)} PDF page(s) had no embedded text layer; "
                        "invoke OCR workflow for scanned pages."
                    )
            else:
                warnings.append(
                    f"pdf_mixed_media_empty_pages: {empty_pages}/{len(pages)} PDF page(s) "
                    "contained non-textual/blank slide content while the remaining pages extracted cleanly."
                )

        non_empty_ratio = (len(pages) - empty_pages) / max(1, len(pages))
        quality = round(min(1.0, max(0.0, non_empty_ratio)), 3)
        title = meta.get("title") or meta.get("pdf_title") or (headings[0] if headings else Path(filename).stem)

        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=str(title),
            mime_type=mime_type or "application/pdf",
            content_hash=digest,
            blocks=tuple(blocks),
            headings=tuple(headings),
            math_expressions=tuple(all_math),
            tables=tuple(tables),
            figures=tuple(figures),
            metadata=meta,
            provenance=prov,
            extraction_quality=quality,
            ocr_used=bool(ocr_pages),
            unsupported_elements=tuple(unsupported),
            warnings=tuple(warnings),
        )

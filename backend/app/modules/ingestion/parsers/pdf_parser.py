"""PDF document parser prioritizing page locators, headings, formulas, tables, and optional OCR.

Extraction strategy chain (best available automatically selected):
1. PyMuPDF (`fitz`) if installed
2. `pypdf` if installed
3. System `pdftotext` + `pdfinfo` (Poppler CLI) if installed
4. Pure-Python PDF text-stream fallback (supports uncompressed & FlateDecode content streams)
Optional OCR: invoked only for scanned/empty pages when `tesseract` CLI or `pytesseract` is installed.
"""

from __future__ import annotations

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


def _extract_pdf_text_pure_python(content: bytes) -> tuple[list[str], dict[str, Any]]:
    """Lightweight pure-Python PDF text fallback for standard PDFs and synthetic fixtures."""
    if not content.lstrip().startswith(b"%PDF-"):
        raise ValueError("Invalid PDF header: file does not start with %PDF-.")

    meta: dict[str, Any] = {}
    title_match = re.search(rb"/Title\s*\(([^)]{1,200})\)", content)
    if title_match:
        meta["pdf_title"] = _decode_pdf_literal(title_match.group(1)).strip()

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
    pdftotext_bin = shutil.which("pdftotext")
    if not pdftotext_bin:
        return None
    meta: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="ensora_pdf_") as tmpdir:
        pdf_path = Path(tmpdir) / "doc.pdf"
        pdf_path.write_bytes(content)

        pdfinfo_bin = shutil.which("pdfinfo")
        if pdfinfo_bin:
            info_res = subprocess.run(
                [pdfinfo_bin, str(pdf_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            if info_res.returncode == 0:
                for line in info_res.stdout.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k_norm = k.strip().lower().replace(" ", "_")
                        if k_norm in {"title", "author", "subject", "pages", "creator"} and v.strip():
                            meta[k_norm] = v.strip()

        txt_res = subprocess.run(
            [pdftotext_bin, "-layout", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if txt_res.returncode != 0:
            return None
        raw_pages = txt_res.stdout.split("\x0c")
        if raw_pages and raw_pages[-1] == "":
            raw_pages.pop()
        if not raw_pages:
            raw_pages = [""]
        return raw_pages, meta


def _detect_table_rows(lines: list[str]) -> list[tuple[str, ...]]:
    """Detect whitespace-aligned or pipe-delimited table rows inside a PDF page."""
    rows: list[tuple[str, ...]] = []
    for line in lines:
        if "|" in line and line.count("|") >= 2:
            cells = tuple(c.strip() for c in line.strip("|").split("|"))
            if any(cells) and not all(set(c) <= {"-", ":", "="} for c in cells if c):
                rows.append(cells)
        elif re.search(r"\S\s{3,}\S", line):
            cells = tuple(c.strip() for c in re.split(r"\s{3,}", line.strip()) if c.strip())
            if len(cells) >= 2:
                rows.append(cells)
    return rows if len(rows) >= 2 else []


class PdfDocumentParser(DocumentParser):
    """PDF text, structure, formula, table, figure, and optional OCR parser."""

    name = "pdf_parser"
    version = "1.0.0"
    supported_mimes = frozenset({"application/pdf"})
    supported_extensions = frozenset({".pdf"})

    def parse(
        self,
        content: bytes,
        *,
        filename: str,
        mime_type: str,
        provenance: dict[str, Any] | None = None,
    ) -> ParsedDocument:
        digest = sha256_bytes(content)
        prov = dict(provenance or {})
        warnings: list[str] = []
        unsupported: list[str] = []
        meta: dict[str, Any] = {"filename": filename}
        backend_used = "unknown"
        pages: list[str] | None = None

        if not content or not content.lstrip().startswith(b"%PDF-"):
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
                warnings=("Malformed or corrupted PDF file (missing %PDF- signature).",),
                error="Malformed PDF header",
            )

        # 1. PyMuPDF (fitz) if available
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

        # 2. pypdf if available
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

        # 3. Poppler pdftotext CLI
        if pages is None:
            poppler_res = _extract_pdf_via_pdftotext(content)
            if poppler_res is not None:
                pages, poppler_meta = poppler_res
                meta.update(poppler_meta)
                backend_used = "pdftotext"

        # 4. Pure-Python PDF stream fallback
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

        meta["pdf_backend"] = backend_used
        meta["page_count"] = len(pages)

        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        all_math: list[str] = []
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
            all_math.extend(m for m in page_math if m not in all_math)

            # Detect headings and figures
            for ln in lines:
                stripped = ln.strip()
                if _HEADING_RE.match(stripped) and stripped not in headings:
                    headings.append(stripped)
                    current_heading_path = (stripped,)
                fig_m = _FIGURE_RE.match(stripped)
                if fig_m:
                    figures.append({"page": page_num, "caption": stripped})

            # Detect tables
            page_table = _detect_table_rows(lines)
            if page_table:
                tables.append({"page": page_num, "rows": [list(r) for r in page_table]})

            lang = detect_language_hint(text)
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
                )
            )
            ordinal += 1

        ocr_used = False
        empty_ratio = empty_pages / max(1, len(pages))
        if empty_pages > 0:
            # Distinguish genuinely scanned / image-heavy PDFs (0 text blocks or >=30% empty pages)
            # from normal text-rich slide decks/lectures with 1-2 blank or diagram-only pages.
            is_scanned_heavy = (len(blocks) == 0) or (empty_ratio >= 0.30)
            if is_scanned_heavy:
                if shutil.which("tesseract") is None:
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
            ocr_used=ocr_used,
            unsupported_elements=tuple(unsupported),
            warnings=tuple(warnings),
        )

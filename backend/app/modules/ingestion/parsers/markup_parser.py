"""Markdown, sanitized HTML, plain-text, source code, and optional image OCR parsers."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..connectors.base import sha256_bytes
from ..contracts import Locator, ParsedBlock, ParsedDocument
from .base import (
    CODE_EXTENSIONS_TO_LANGUAGE,
    DocumentParser,
    detect_language_hint,
    extract_code_language,
    extract_math_expressions,
)

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_MD_FENCE_RE = re.compile(r"^```([A-Za-z0-9_+\-.]*)\s*$")
_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class MarkdownDocumentParser(DocumentParser):
    """Markdown parser preserving headings, fenced code indentation/language, tables, math, and images."""

    name = "markdown_parser"
    version = "1.0.0"
    supported_mimes = frozenset({"text/markdown", "text/x-markdown", "text/x-rmarkdown"})
    supported_extensions = frozenset({".md", ".markdown", ".rmd", ".qmd"})

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
        text = content.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
        lines = text.split("\n")

        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        all_math: list[str] = []
        tables: list[dict[str, Any]] = []
        figures: list[dict[str, Any]] = []
        heading_stack: list[tuple[int, str]] = []
        ordinal = 0

        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            h_match = _MD_HEADING_RE.match(line.strip())
            if h_match:
                level = len(h_match.group(1))
                htext = h_match.group(2).strip()
                headings.append(htext)
                heading_stack = [(lvl, t) for lvl, t in heading_stack if lvl < level]
                heading_stack.append((level, htext))
                hpath = tuple(t for _, t in heading_stack)
                blocks.append(
                    ParsedBlock(
                        ordinal=ordinal,
                        kind="heading",
                        text=htext,
                        locator=Locator(kind="line", line_start=i + 1, line_end=i + 1, section=htext),
                        heading_path=hpath,
                        language=detect_language_hint(htext),
                    )
                )
                ordinal += 1
                i += 1
                continue

            fence_match = _MD_FENCE_RE.match(line.strip())
            if fence_match:
                lang = fence_match.group(1).strip().lower() or None
                start_line = i + 1
                code_lines: list[str] = []
                i += 1
                while i < n and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                end_line = i + 1
                if i < n:
                    i += 1
                hpath = tuple(t for _, t in heading_stack)
                blocks.append(
                    ParsedBlock(
                        ordinal=ordinal,
                        kind="code",
                        text="\n".join(code_lines),
                        locator=Locator(
                            kind="line",
                            line_start=start_line,
                            line_end=end_line,
                            section=hpath[-1] if hpath else None,
                        ),
                        heading_path=hpath,
                        code_language=lang,
                    )
                )
                ordinal += 1
                continue

            # Markdown pipe table
            if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s\-:|]+\|[\s\-:|]*$", lines[i + 1]):
                start_line = i + 1
                raw_table_lines: list[str] = []
                while i < n and "|" in lines[i] and lines[i].strip():
                    raw_table_lines.append(lines[i])
                    i += 1
                rows: list[tuple[str, ...]] = []
                for idx_r, tl in enumerate(raw_table_lines):
                    if idx_r == 1 and re.match(r"^\s*\|?[\s\-:|]+\|[\s\-:|]*$", tl):
                        continue
                    cells = tuple(c.strip() for c in tl.strip().strip("|").split("|"))
                    rows.append(cells)
                if rows:
                    hpath = tuple(t for _, t in heading_stack)
                    tables.append({"line_start": start_line, "rows": [list(r) for r in rows]})
                    blocks.append(
                        ParsedBlock(
                            ordinal=ordinal,
                            kind="table",
                            text="\n".join(" | ".join(r) for r in rows),
                            locator=Locator(
                                kind="table",
                                line_start=start_line,
                                line_end=i,
                                section=hpath[-1] if hpath else None,
                            ),
                            heading_path=hpath,
                            table_rows=tuple(rows),
                        )
                    )
                    ordinal += 1
                continue

            if not line.strip():
                i += 1
                continue

            start_line = i + 1
            para_lines: list[str] = []
            while (
                i < n
                and lines[i].strip()
                and not _MD_HEADING_RE.match(lines[i].strip())
                and not _MD_FENCE_RE.match(lines[i].strip())
            ):
                for img_m in _MD_IMG_RE.finditer(lines[i]):
                    figures.append({"alt": img_m.group(1), "src": img_m.group(2), "line": i + 1})
                para_lines.append(lines[i])
                i += 1
            para_text = "\n".join(para_lines)
            math_exprs = extract_math_expressions(para_text)
            all_math.extend(m for m in math_exprs if m not in all_math)
            hpath = tuple(t for _, t in heading_stack)
            blocks.append(
                ParsedBlock(
                    ordinal=ordinal,
                    kind="paragraph",
                    text=para_text,
                    locator=Locator(
                        kind="line",
                        line_start=start_line,
                        line_end=i,
                        section=hpath[-1] if hpath else None,
                    ),
                    heading_path=hpath,
                    language=detect_language_hint(para_text),
                    math_expressions=math_exprs,
                )
            )
            ordinal += 1

        title = headings[0] if headings else Path(filename).stem
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=title,
            mime_type=mime_type or "text/markdown",
            content_hash=digest,
            blocks=tuple(blocks),
            headings=tuple(headings),
            math_expressions=tuple(all_math),
            tables=tuple(tables),
            figures=tuple(figures),
            metadata={"filename": filename, "line_count": len(lines)},
            provenance=prov,
            extraction_quality=1.0 if blocks else 0.0,
        )


class _SanitizingHTMLParser(HTMLParser):
    """Strips dangerous tags (script/style/iframe) and preserves headings, code, tables, figures."""

    SKIP_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_buf: list[str] = []
        self._heading_level: int | None = None
        self._heading_buf: list[str] = []
        self._in_pre = False
        self._code_lang: str | None = None
        self._code_buf: list[str] = []
        self._in_table = False
        self._current_table: list[tuple[str, ...]] = []
        self._current_row: list[str] = []
        self._in_cell = False
        self._cell_buf: list[str] = []
        self._para_buf: list[str] = []

        self.page_title: str | None = None
        self.events: list[tuple[str, Any]] = []
        self.figures: list[dict[str, Any]] = []

    def _flush_para(self) -> None:
        text = " ".join("".join(self._para_buf).split()).strip()
        if text:
            self.events.append(("paragraph", text))
        self._para_buf = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if low in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return

        if low == "title":
            self._in_title = True
            self._title_buf = []
        elif low in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_para()
            self._heading_level = int(low[1])
            self._heading_buf = []
        elif low == "pre":
            self._flush_para()
            self._in_pre = True
            self._code_buf = []
            self._code_lang = None
        elif low == "code" and self._in_pre:
            cls = attr_map.get("class", "")
            m = re.search(r"language-([a-zA-Z0-9_+\-]+)", cls)
            if m:
                self._code_lang = m.group(1).lower()
        elif low == "table":
            self._flush_para()
            self._in_table = True
            self._current_table = []
        elif low == "tr" and self._in_table:
            self._current_row = []
        elif low in ("td", "th") and self._in_table:
            self._in_cell = True
            self._cell_buf = []
        elif low == "img":
            self.figures.append({"alt": attr_map.get("alt", ""), "src": attr_map.get("src", "")})
        elif low in ("p", "div", "li", "section", "article", "br"):
            self._flush_para()

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self._title_buf.append(data)
        elif self._heading_level is not None:
            self._heading_buf.append(data)
        elif self._in_pre:
            self._code_buf.append(data)
        elif self._in_cell:
            self._cell_buf.append(data)
        else:
            self._para_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth > 0:
            return

        if low == "title" and self._in_title:
            self.page_title = " ".join("".join(self._title_buf).split()).strip() or None
            self._in_title = False
        elif low in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading_level is not None:
            text = " ".join("".join(self._heading_buf).split()).strip()
            if text:
                self.events.append(("heading", (self._heading_level, text)))
            self._heading_level = None
            self._heading_buf = []
        elif low == "pre" and self._in_pre:
            code_text = "".join(self._code_buf).strip("\n")
            if code_text.strip():
                self.events.append(("code", (self._code_lang, code_text)))
            self._in_pre = False
            self._code_buf = []
            self._code_lang = None
        elif low in ("td", "th") and self._in_cell:
            self._current_row.append(" ".join("".join(self._cell_buf).split()).strip())
            self._in_cell = False
            self._cell_buf = []
        elif low == "tr" and self._in_table:
            if any(self._current_row):
                self._current_table.append(tuple(self._current_row))
            self._current_row = []
        elif low == "table" and self._in_table:
            if self._current_table:
                self.events.append(("table", list(self._current_table)))
            self._in_table = False
            self._current_table = []
        elif low in ("p", "div", "li", "section", "article"):
            self._flush_para()


class HtmlDocumentParser(DocumentParser):
    """Sanitizing HTML parser treating all input as untrusted data."""

    name = "html_parser"
    version = "1.0.0"
    supported_mimes = frozenset({"text/html", "application/xhtml+xml"})
    supported_extensions = frozenset({".html", ".htm"})

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
        raw_html = content.decode("utf-8-sig", errors="replace")
        hp = _SanitizingHTMLParser()
        hp.feed(raw_html)
        hp._flush_para()

        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        all_math: list[str] = []
        tables: list[dict[str, Any]] = []
        heading_stack: list[tuple[int, str]] = []
        ordinal = 0

        for ev_kind, payload in hp.events:
            if ev_kind == "heading":
                level, text = payload
                headings.append(text)
                heading_stack = [(lvl, t) for lvl, t in heading_stack if lvl < level]
                heading_stack.append((level, text))
                hpath = tuple(t for _, t in heading_stack)
                blocks.append(
                    ParsedBlock(
                        ordinal=ordinal,
                        kind="heading",
                        text=text,
                        locator=Locator(kind="section", section=text),
                        heading_path=hpath,
                        language=detect_language_hint(text),
                    )
                )
                ordinal += 1
            elif ev_kind == "code":
                lang, code_text = payload
                hpath = tuple(t for _, t in heading_stack)
                blocks.append(
                    ParsedBlock(
                        ordinal=ordinal,
                        kind="code",
                        text=code_text,
                        locator=Locator(kind="section", section=hpath[-1] if hpath else None),
                        heading_path=hpath,
                        code_language=lang,
                    )
                )
                ordinal += 1
            elif ev_kind == "table":
                hpath = tuple(t for _, t in heading_stack)
                tables.append({"section": hpath[-1] if hpath else None, "rows": [list(r) for r in payload]})
                blocks.append(
                    ParsedBlock(
                        ordinal=ordinal,
                        kind="table",
                        text="\n".join(" | ".join(r) for r in payload),
                        locator=Locator(kind="table", section=hpath[-1] if hpath else None),
                        heading_path=hpath,
                        table_rows=tuple(payload),
                    )
                )
                ordinal += 1
            elif ev_kind == "paragraph":
                hpath = tuple(t for _, t in heading_stack)
                math_exprs = extract_math_expressions(payload)
                all_math.extend(m for m in math_exprs if m not in all_math)
                blocks.append(
                    ParsedBlock(
                        ordinal=ordinal,
                        kind="paragraph",
                        text=payload,
                        locator=Locator(kind="section", section=hpath[-1] if hpath else None),
                        heading_path=hpath,
                        language=detect_language_hint(payload),
                        math_expressions=math_exprs,
                    )
                )
                ordinal += 1

        title = hp.page_title or (headings[0] if headings else Path(filename).stem)
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=title,
            mime_type=mime_type or "text/html",
            content_hash=digest,
            blocks=tuple(blocks),
            headings=tuple(headings),
            math_expressions=tuple(all_math),
            tables=tuple(tables),
            figures=tuple(hp.figures),
            metadata={"filename": filename, "sanitized": True},
            provenance=prov,
            extraction_quality=1.0 if blocks else 0.0,
        )


class PlainTextAndCodeParser(DocumentParser):
    """Parser for plain text and source code preserving exact indentation, language, and line spans."""

    name = "text_code_parser"
    version = "1.1.0"
    supported_mimes = frozenset(
        {
            "text/plain",
            "text/x-python",
            "text/x-script.python",
            "text/x-rsrc",
            "text/x-r",
            "application/sql",
            "text/x-sql",
            "application/x-tex",
            "text/x-tex",
            "application/json",
            "application/x-yaml",
            "text/yaml",
            "text/csv",
            "text/tab-separated-values",
            "text/xml",
            "application/xml",
            "application/toml",
            "application/rtf",
            "text/rtf",
            "application/x-sh",
            "text/x-shellscript",
            "text/javascript",
            "application/javascript",
            "text/x-c",
            "text/x-java-source",
        }
    )
    supported_extensions = frozenset({".txt", ".rtf", *CODE_EXTENSIONS_TO_LANGUAGE.keys()})

    _CODE_ANCHOR_RE = re.compile(r"^\s*(?:def|class|function|async\s+def|CREATE\s+TABLE|\\section\{)\s*([A-Za-z0-9_.\-]+)")

    @staticmethod
    def _strip_rtf(raw: str) -> str:
        """Convert simple RTF documents into readable plain text without external dependencies."""
        # Remove header groups like fonttbl/colortbl/stylesheet
        cleaned = re.sub(r"\{\\(?:fonttbl|colortbl|stylesheet|info)[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "", raw, flags=re.IGNORECASE)
        cleaned = re.sub(r"\\par[d]?\b", "\n", cleaned)
        cleaned = re.sub(r"\\line\b", "\n", cleaned)
        cleaned = re.sub(r"\\tab\b", "\t", cleaned)
        cleaned = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", errors="replace"), cleaned)
        cleaned = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", cleaned)
        cleaned = cleaned.replace("{", "").replace("}", "")
        return "\n".join(ln.rstrip() for ln in cleaned.splitlines()).strip()

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
        text = content.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
        is_rtf = filename.lower().endswith(".rtf") or mime_type.lower() in ("application/rtf", "text/rtf") or text.lstrip().startswith("{\\rtf")
        if is_rtf:
            text = self._strip_rtf(text)
        lines = text.split("\n")
        code_lang = None if is_rtf else extract_code_language(filename)
        is_code = code_lang is not None and not filename.lower().endswith((".txt", ".log"))

        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        all_math = extract_math_expressions(text)

        if is_code:
            for ln in lines:
                am = self._CODE_ANCHOR_RE.match(ln)
                if am:
                    headings.append(am.group(0).strip())
            if text.strip():
                blocks.append(
                    ParsedBlock(
                        ordinal=0,
                        kind="code",
                        text=text,
                        locator=Locator(kind="line", line_start=1, line_end=len(lines)),
                        code_language=code_lang,
                        math_expressions=all_math,
                    )
                )
        else:
            # Split plain text into paragraph blocks preserving line spans
            ordinal = 0
            start = 1
            buf: list[str] = []
            for idx, ln in enumerate(lines, start=1):
                if not ln.strip():
                    if buf:
                        para = "\n".join(buf)
                        blocks.append(
                            ParsedBlock(
                                ordinal=ordinal,
                                kind="paragraph",
                                text=para,
                                locator=Locator(kind="line", line_start=start, line_end=idx - 1),
                                language=detect_language_hint(para),
                                math_expressions=extract_math_expressions(para),
                            )
                        )
                        ordinal += 1
                        buf = []
                    start = idx + 1
                else:
                    if not buf:
                        start = idx
                    buf.append(ln)
            if buf:
                para = "\n".join(buf)
                blocks.append(
                    ParsedBlock(
                        ordinal=ordinal,
                        kind="paragraph",
                        text=para,
                        locator=Locator(kind="line", line_start=start, line_end=len(lines)),
                        language=detect_language_hint(para),
                        math_expressions=extract_math_expressions(para),
                    )
                )

        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=Path(filename).stem,
            mime_type=mime_type or ("text/plain" if not is_code else "text/x-source"),
            content_hash=digest,
            blocks=tuple(blocks),
            headings=tuple(headings),
            math_expressions=all_math,
            metadata={"filename": filename, "code_language": code_lang, "line_count": len(lines)},
            provenance=prov,
            extraction_quality=1.0 if blocks else 0.0,
        )


class ImageOcrParser(DocumentParser):
    """Optional OCR parser for scanned images using tesseract CLI when installed."""

    name = "image_ocr_parser"
    version = "1.0.0"
    supported_mimes = frozenset({"image/png", "image/jpeg", "image/webp", "image/tiff"})
    supported_extensions = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})

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
        meta: dict[str, Any] = {"filename": filename, "size_bytes": len(content)}
        tesseract_bin = shutil.which("tesseract")

        if not tesseract_bin:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                figures=({"filename": filename, "mime_type": mime_type},),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                ocr_used=False,
                unsupported_elements=("ocr_engine_unavailable",),
                warnings=("Image preserved as figure, but tesseract OCR is not installed.",),
            )

        with tempfile.TemporaryDirectory(prefix="ensora_ocr_") as tmpdir:
            img_path = Path(tmpdir) / Path(filename).name
            img_path.write_bytes(content)
            res = subprocess.run(
                [tesseract_bin, str(img_path), "stdout", "-l", "fra+eng"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if res.returncode != 0:
                res = subprocess.run(
                    [tesseract_bin, str(img_path), "stdout"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
            text = res.stdout.strip() if res.returncode == 0 else ""

        blocks = (
            (
                ParsedBlock(
                    ordinal=0,
                    kind="paragraph",
                    text=text,
                    locator=Locator(kind="figure", page=1),
                    language=detect_language_hint(text),
                    math_expressions=extract_math_expressions(text),
                    metadata={"ocr": True},
                ),
            )
            if text
            else ()
        )
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=Path(filename).stem,
            mime_type=mime_type,
            content_hash=digest,
            blocks=blocks,
            figures=({"filename": filename, "mime_type": mime_type},),
            metadata=meta,
            provenance=prov,
            extraction_quality=0.75 if blocks else 0.0,
            ocr_used=True,
            warnings=() if blocks else ("OCR produced no text for image.",),
        )

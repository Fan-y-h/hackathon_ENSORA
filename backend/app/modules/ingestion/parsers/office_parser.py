"""Native PPTX and DOCX parsers preserving slide/heading locators, notes, tables, and figures.

Uses python-pptx / python-docx when installed, and includes a complete zero-dependency
pure-Python OOXML (zipfile + xml.etree.ElementTree) implementation so PowerPoint and
Word documents work out of the box in .venv without additional package installation.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from ..connectors.base import sha256_bytes
from ..contracts import Locator, ParsedBlock, ParsedDocument
from .base import DocumentParser, detect_language_hint, extract_math_expressions

_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PPT_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DC_NS = "http://purl.org/dc/elements/1.1/"

# Zip-bomb / archive expansion safety limits
MAX_OFFICE_ZIP_MEMBERS = 512
MAX_OFFICE_MEMBER_BYTES = 10 * 1024 * 1024  # 10 MiB per XML/media member
MAX_OFFICE_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MiB total expanded archive
MAX_OFFICE_COMPRESSION_RATIO = 100.0


class UnsafeArchiveError(ValueError):
    """Raised when an Office OOXML archive violates expansion safety bounds."""


def _validate_zip_bounds(
    zf: zipfile.ZipFile,
    *,
    max_members: int = MAX_OFFICE_ZIP_MEMBERS,
    max_member_bytes: int = MAX_OFFICE_MEMBER_BYTES,
    max_total_bytes: int = MAX_OFFICE_TOTAL_BYTES,
    max_ratio: float = MAX_OFFICE_COMPRESSION_RATIO,
) -> None:
    infos = zf.infolist()
    if len(infos) > max_members:
        raise UnsafeArchiveError(
            f"Archive exceeds maximum member limit ({len(infos)} > {max_members})."
        )
    total_uncompressed = 0
    for info in infos:
        if info.file_size > max_member_bytes:
            raise UnsafeArchiveError(
                f"Archive member '{info.filename}' exceeds uncompressed size limit "
                f"({info.file_size} > {max_member_bytes})."
            )
        if info.compress_size > 0 and (info.file_size / info.compress_size) > max_ratio:
            raise UnsafeArchiveError(
                f"Archive member '{info.filename}' exceeds safe compression ratio ({max_ratio:.0f}x)."
            )
        total_uncompressed += info.file_size
        if total_uncompressed > max_total_bytes:
            raise UnsafeArchiveError(
                f"Archive aggregate expanded size exceeds limit ({total_uncompressed} > {max_total_bytes})."
            )


def _read_bounded_member(
    zf: zipfile.ZipFile,
    name: str,
    *,
    remaining_budget: list[int],
    max_member_bytes: int = MAX_OFFICE_MEMBER_BYTES,
) -> bytes:
    """Stream-read a ZIP member with hard per-member and aggregate byte limits."""
    limit = min(max_member_bytes, remaining_budget[0])
    with zf.open(name, "r") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise UnsafeArchiveError(
            f"Archive member '{name}' exceeded runtime expansion budget ({limit} bytes)."
        )
    remaining_budget[0] -= len(data)
    return data


def _read_core_properties(zf: zipfile.ZipFile, remaining_budget: list[int]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if "docProps/core.xml" not in zf.namelist():
        return meta
    try:
        root = ET.fromstring(_read_bounded_member(zf, "docProps/core.xml", remaining_budget=remaining_budget))
        for field_name in ("title", "creator", "subject", "description"):
            elem = root.find(f"{{{_DC_NS}}}{field_name}")
            if elem is not None and elem.text and elem.text.strip():
                meta[field_name] = elem.text.strip()
    except UnsafeArchiveError:
        raise
    except Exception:
        pass
    return meta


def _slide_sort_key(name: str) -> int:
    m = re.search(r"slide([0-9]+)\.xml$", name)
    return int(m.group(1)) if m else 0


class PptxDocumentParser(DocumentParser):
    """PowerPoint (.pptx) parser extracting slides, titles, speaker notes, tables, and figures."""

    name = "pptx_parser"
    version = "1.0.0"
    supported_mimes = frozenset(
        {"application/vnd.openxmlformats-officedocument.presentationml.presentation"}
    )
    supported_extensions = frozenset({".pptx"})

    def __init__(
        self,
        *,
        max_members: int = MAX_OFFICE_ZIP_MEMBERS,
        max_member_bytes: int = MAX_OFFICE_MEMBER_BYTES,
        max_total_bytes: int = MAX_OFFICE_TOTAL_BYTES,
    ) -> None:
        self._max_members = max_members
        self._max_member_bytes = max_member_bytes
        self._max_total_bytes = max_total_bytes

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
        meta: dict[str, Any] = {"filename": filename}

        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
            _validate_zip_bounds(
                zf,
                max_members=self._max_members,
                max_member_bytes=self._max_member_bytes,
                max_total_bytes=self._max_total_bytes,
            )
        except Exception as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(f"Invalid or unsafe PPTX archive: {exc}",),
                error=str(exc),
            )

        remaining_budget = [self._max_total_bytes]
        try:
            with zf:
                meta.update(_read_core_properties(zf, remaining_budget))
                names = zf.namelist()
                slide_files = sorted(
                    (n for n in names if re.match(r"^ppt/slides/slide[0-9]+\.xml$", n)),
                    key=_slide_sort_key,
                )
                media_files = sorted(n for n in names if n.startswith("ppt/media/"))
                meta["slide_count"] = len(slide_files)
                meta["media_count"] = len(media_files)

                blocks: list[ParsedBlock] = []
                headings: list[str] = []
                all_math: list[str] = []
                tables: list[dict[str, Any]] = []
                figures: list[dict[str, Any]] = [{"media": m} for m in media_files]
                ordinal = 0

                for slide_num, slide_path in enumerate(slide_files, start=1):
                    root = ET.fromstring(
                        _read_bounded_member(
                            zf,
                            slide_path,
                            remaining_budget=remaining_budget,
                            max_member_bytes=self._max_member_bytes,
                        )
                    )
                    slide_title: str | None = None
                    body_lines: list[str] = []
                    slide_tables: list[tuple[str, ...]] = []

                    # Extract shapes & detect title placeholders
                    for sp in root.iter(f"{{{_PPT_NS}}}sp"):
                        ph = sp.find(f".//{{{_PPT_NS}}}ph")
                        ph_type = ph.attrib.get("type", "") if ph is not None else ""
                        paras: list[str] = []
                        for p in sp.iter(f"{{{_DRAWINGML_NS}}}p"):
                            runs = [t.text for t in p.iter(f"{{{_DRAWINGML_NS}}}t") if t.text]
                            line = "".join(runs).strip()
                            if line:
                                paras.append(line)
                        if not paras:
                            continue
                        if ph_type in ("title", "ctrTitle") and slide_title is None:
                            slide_title = paras[0]
                            headings.append(slide_title)
                            body_lines.extend(paras)
                        else:
                            body_lines.extend(paras)

                    # Extract tables
                    for tbl in root.iter(f"{{{_DRAWINGML_NS}}}tbl"):
                        t_rows: list[tuple[str, ...]] = []
                        for tr in tbl.findall(f"{{{_DRAWINGML_NS}}}tr"):
                            cells: list[str] = []
                            for tc in tr.findall(f"{{{_DRAWINGML_NS}}}tc"):
                                cell_text = " ".join(
                                    t.text.strip()
                                    for t in tc.iter(f"{{{_DRAWINGML_NS}}}t")
                                    if t.text and t.text.strip()
                                )
                                cells.append(cell_text)
                            if any(cells):
                                t_rows.append(tuple(cells))
                        if t_rows:
                            tables.append({"slide": slide_num, "rows": [list(r) for r in t_rows]})
                            slide_tables.extend(t_rows)

                    slide_text = "\n".join(body_lines).strip()
                    if slide_text or slide_tables:
                        math_exprs = extract_math_expressions(slide_text)
                        all_math.extend(m for m in math_exprs if m not in all_math)
                        blocks.append(
                            ParsedBlock(
                                ordinal=ordinal,
                                kind="paragraph",
                                text=slide_text,
                                locator=Locator(kind="slide", slide=slide_num, section=slide_title),
                                heading_path=(slide_title,) if slide_title else (),
                                language=detect_language_hint(slide_text),
                                table_rows=tuple(slide_tables),
                                math_expressions=math_exprs,
                            )
                        )
                        ordinal += 1

                    # Speaker notes if present
                    notes_path = f"ppt/notesSlides/notesSlide{slide_num}.xml"
                    if notes_path in names:
                        nroot = ET.fromstring(
                            _read_bounded_member(
                                zf,
                                notes_path,
                                remaining_budget=remaining_budget,
                                max_member_bytes=self._max_member_bytes,
                            )
                        )
                        note_lines = [
                            "".join(t.text for t in p.iter(f"{{{_DRAWINGML_NS}}}t") if t.text).strip()
                            for p in nroot.iter(f"{{{_DRAWINGML_NS}}}p")
                        ]
                        note_text = "\n".join(ln for ln in note_lines if ln and not ln.isdigit()).strip()
                        if note_text:
                            blocks.append(
                                ParsedBlock(
                                    ordinal=ordinal,
                                    kind="note",
                                    text=note_text,
                                    locator=Locator(kind="slide", slide=slide_num, section=slide_title),
                                    heading_path=(slide_title,) if slide_title else (),
                                    language=detect_language_hint(note_text),
                                    metadata={"speaker_notes": True},
                                )
                            )
                            ordinal += 1
        except UnsafeArchiveError as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(str(exc),),
                error=str(exc),
            )

        title = meta.get("title") or (headings[0] if headings else Path(filename).stem)
        quality = 1.0 if blocks else 0.0
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=str(title),
            mime_type=mime_type,
            content_hash=digest,
            blocks=tuple(blocks),
            headings=tuple(headings),
            math_expressions=tuple(all_math),
            tables=tuple(tables),
            figures=tuple(figures),
            metadata=meta,
            provenance=prov,
            extraction_quality=quality,
        )



class DocxDocumentParser(DocumentParser):
    """Word (.docx) parser preserving heading hierarchy, paragraphs, tables, and embedded media."""

    name = "docx_parser"
    version = "1.0.0"
    supported_mimes = frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    )
    supported_extensions = frozenset({".docx"})

    def __init__(
        self,
        *,
        max_members: int = MAX_OFFICE_ZIP_MEMBERS,
        max_member_bytes: int = MAX_OFFICE_MEMBER_BYTES,
        max_total_bytes: int = MAX_OFFICE_TOTAL_BYTES,
    ) -> None:
        self._max_members = max_members
        self._max_member_bytes = max_member_bytes
        self._max_total_bytes = max_total_bytes

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
        meta: dict[str, Any] = {"filename": filename}

        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
            _validate_zip_bounds(
                zf,
                max_members=self._max_members,
                max_member_bytes=self._max_member_bytes,
                max_total_bytes=self._max_total_bytes,
            )
        except Exception as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(f"Invalid or unsafe DOCX archive: {exc}",),
                error=str(exc),
            )

        remaining_budget = [self._max_total_bytes]
        try:
            with zf:
                meta.update(_read_core_properties(zf, remaining_budget))
                names = zf.namelist()
                if "word/document.xml" not in names:
                    return ParsedDocument(
                        parser_name=self.name,
                        parser_version=self.version,
                        title=Path(filename).stem,
                        mime_type=mime_type,
                        content_hash=digest,
                        blocks=(),
                        metadata=meta,
                        provenance=prov,
                        extraction_quality=0.0,
                        warnings=("DOCX archive missing word/document.xml.",),
                        error="Missing word/document.xml",
                    )

                media_files = sorted(n for n in names if n.startswith("word/media/"))
                root = ET.fromstring(
                    _read_bounded_member(
                        zf,
                        "word/document.xml",
                        remaining_budget=remaining_budget,
                        max_member_bytes=self._max_member_bytes,
                    )
                )
        except UnsafeArchiveError as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(str(exc),),
                error=str(exc),
            )

        body = root.find(f"{{{_WORD_NS}}}body")
        if body is None:
            body = root

        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        all_math: list[str] = []
        tables: list[dict[str, Any]] = []
        figures: list[dict[str, Any]] = [{"media": m} for m in media_files]
        heading_stack: list[tuple[int, str]] = []
        ordinal = 0

        for child in body:
            tag = child.tag
            if tag == f"{{{_WORD_NS}}}p":
                texts = [t.text for t in child.iter(f"{{{_WORD_NS}}}t") if t.text]
                text = "".join(texts).strip()
                if not text:
                    continue
                pstyle = child.find(f"{{{_WORD_NS}}}pPr/{{{_WORD_NS}}}pStyle")
                style_val = pstyle.attrib.get(f"{{{_WORD_NS}}}val", "") if pstyle is not None else ""
                h_match = re.match(r"^(?:Heading|Titre)([1-6])$", style_val, re.IGNORECASE)
                if style_val.lower() in ("title", "titre") or h_match:
                    level = int(h_match.group(1)) if h_match else 1
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
                else:
                    hpath = tuple(t for _, t in heading_stack)
                    math_exprs = extract_math_expressions(text)
                    all_math.extend(m for m in math_exprs if m not in all_math)
                    blocks.append(
                        ParsedBlock(
                            ordinal=ordinal,
                            kind="paragraph",
                            text=text,
                            locator=Locator(kind="section", section=hpath[-1] if hpath else None),
                            heading_path=hpath,
                            language=detect_language_hint(text),
                            math_expressions=math_exprs,
                        )
                    )
                    ordinal += 1
            elif tag == f"{{{_WORD_NS}}}tbl":
                t_rows: list[tuple[str, ...]] = []
                for tr in child.findall(f"{{{_WORD_NS}}}tr"):
                    cells = []
                    for tc in tr.findall(f"{{{_WORD_NS}}}tc"):
                        cell_text = " ".join(
                            "".join(t.text for t in p.iter(f"{{{_WORD_NS}}}t") if t.text).strip()
                            for p in tc.findall(f"{{{_WORD_NS}}}p")
                        ).strip()
                        cells.append(cell_text)
                    if any(cells):
                        t_rows.append(tuple(cells))
                if t_rows:
                    hpath = tuple(t for _, t in heading_stack)
                    tables.append({"section": hpath[-1] if hpath else None, "rows": [list(r) for r in t_rows]})
                    table_text = "\n".join(" | ".join(r) for r in t_rows)
                    blocks.append(
                        ParsedBlock(
                            ordinal=ordinal,
                            kind="table",
                            text=table_text,
                            locator=Locator(kind="table", section=hpath[-1] if hpath else None),
                            heading_path=hpath,
                            table_rows=tuple(t_rows),
                        )
                    )
                    ordinal += 1

        title = meta.get("title") or (headings[0] if headings else Path(filename).stem)
        quality = 1.0 if blocks else 0.0
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=str(title),
            mime_type=mime_type,
            content_hash=digest,
            blocks=tuple(blocks),
            headings=tuple(headings),
            math_expressions=tuple(all_math),
            tables=tuple(tables),
            figures=tuple(figures),
            metadata=meta,
            provenance=prov,
            extraction_quality=quality,
        )


_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_ODF_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_ODF_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"


class XlsxDocumentParser(DocumentParser):
    """Pure-Python Excel (.xlsx/.xlsm) parser with zip-bomb protection."""

    name = "xlsx_parser"
    version = "1.0.0"
    supported_mimes = frozenset(
        {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel.sheet.macroEnabled.12",
        }
    )
    supported_extensions = frozenset({".xlsx", ".xlsm"})

    def __init__(
        self,
        *,
        max_members: int = MAX_OFFICE_ZIP_MEMBERS,
        max_member_bytes: int = MAX_OFFICE_MEMBER_BYTES,
        max_total_bytes: int = MAX_OFFICE_TOTAL_BYTES,
    ) -> None:
        self._max_members = max_members
        self._max_member_bytes = max_member_bytes
        self._max_total_bytes = max_total_bytes

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
        meta: dict[str, Any] = {"filename": filename}
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
            _validate_zip_bounds(
                zf,
                max_members=self._max_members,
                max_member_bytes=self._max_member_bytes,
                max_total_bytes=self._max_total_bytes,
            )
        except Exception as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(f"Invalid or unsafe XLSX archive: {exc}",),
                error=str(exc),
            )

        remaining_budget = [self._max_total_bytes]
        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        tables: list[dict[str, Any]] = []
        ordinal = 0

        try:
            with zf:
                meta.update(_read_core_properties(zf, remaining_budget))
                names = zf.namelist()
                shared_strings: list[str] = []
                if "xl/sharedStrings.xml" in names:
                    ss_root = ET.fromstring(
                        _read_bounded_member(
                            zf,
                            "xl/sharedStrings.xml",
                            remaining_budget=remaining_budget,
                            max_member_bytes=self._max_member_bytes,
                        )
                    )
                    for si in ss_root.findall(f"{{{_SPREADSHEET_NS}}}si"):
                        parts = [t.text for t in si.iter(f"{{{_SPREADSHEET_NS}}}t") if t.text]
                        shared_strings.append("".join(parts))

                sheet_names: list[str] = []
                if "xl/workbook.xml" in names:
                    wb_root = ET.fromstring(
                        _read_bounded_member(
                            zf,
                            "xl/workbook.xml",
                            remaining_budget=remaining_budget,
                            max_member_bytes=self._max_member_bytes,
                        )
                    )
                    for sh in wb_root.iter(f"{{{_SPREADSHEET_NS}}}sheet"):
                        nm = sh.attrib.get("name")
                        if nm:
                            sheet_names.append(nm)

                sheet_files = sorted(
                    (n for n in names if re.match(r"^xl/worksheets/sheet[0-9]+\.xml$", n)),
                    key=lambda n: int(re.search(r"sheet([0-9]+)\.xml$", n).group(1)),  # type: ignore[union-attr]
                )
                meta["sheet_count"] = len(sheet_files)

                for idx_sh, sh_path in enumerate(sheet_files):
                    sh_title = sheet_names[idx_sh] if idx_sh < len(sheet_names) else f"Sheet {idx_sh + 1}"
                    sh_root = ET.fromstring(
                        _read_bounded_member(
                            zf,
                            sh_path,
                            remaining_budget=remaining_budget,
                            max_member_bytes=self._max_member_bytes,
                        )
                    )
                    rows: list[tuple[str, ...]] = []
                    for row_el in sh_root.iter(f"{{{_SPREADSHEET_NS}}}row"):
                        row_vals: list[str] = []
                        for c_el in row_el.findall(f"{{{_SPREADSHEET_NS}}}c"):
                            c_type = c_el.attrib.get("t", "")
                            val_text = ""
                            if c_type == "inlineStr":
                                val_text = "".join(t.text for t in c_el.iter(f"{{{_SPREADSHEET_NS}}}t") if t.text)
                            else:
                                v_el = c_el.find(f"{{{_SPREADSHEET_NS}}}v")
                                if v_el is not None and v_el.text is not None:
                                    if c_type == "s" and v_el.text.isdigit():
                                        ss_idx = int(v_el.text)
                                        val_text = shared_strings[ss_idx] if ss_idx < len(shared_strings) else v_el.text
                                    else:
                                        val_text = v_el.text
                            row_vals.append(val_text.strip())
                        if any(row_vals):
                            rows.append(tuple(row_vals))
                    if rows:
                        headings.append(sh_title)
                        tables.append({"sheet": sh_title, "rows": [list(r) for r in rows[:200]]})
                        text = "\n".join(" | ".join(r) for r in rows[:500])
                        blocks.append(
                            ParsedBlock(
                                ordinal=ordinal,
                                kind="table",
                                text=text,
                                locator=Locator(kind="table", section=sh_title),
                                heading_path=(sh_title,),
                                table_rows=tuple(rows[:200]),
                            )
                        )
                        ordinal += 1
        except UnsafeArchiveError as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(str(exc),),
                error=str(exc),
            )

        title = meta.get("title") or (headings[0] if headings else Path(filename).stem)
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=str(title),
            mime_type=mime_type,
            content_hash=digest,
            blocks=tuple(blocks),
            headings=tuple(headings),
            tables=tuple(tables),
            metadata=meta,
            provenance=prov,
            extraction_quality=1.0 if blocks else 0.0,
        )


class OpenDocumentParser(DocumentParser):
    """Pure-Python LibreOffice OpenDocument parser (.odt, .odp, .ods) with zip-bomb safety."""

    name = "opendocument_parser"
    version = "1.0.0"
    supported_mimes = frozenset(
        {
            "application/vnd.oasis.opendocument.text",
            "application/vnd.oasis.opendocument.presentation",
            "application/vnd.oasis.opendocument.spreadsheet",
        }
    )
    supported_extensions = frozenset({".odt", ".odp", ".ods"})

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
        meta: dict[str, Any] = {"filename": filename}
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
            _validate_zip_bounds(zf)
        except Exception as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(f"Invalid or unsafe OpenDocument archive: {exc}",),
                error=str(exc),
            )

        remaining_budget = [MAX_OFFICE_TOTAL_BYTES]
        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        all_math: list[str] = []
        ordinal = 0
        try:
            with zf:
                if "content.xml" not in zf.namelist():
                    return ParsedDocument(
                        parser_name=self.name,
                        parser_version=self.version,
                        title=Path(filename).stem,
                        mime_type=mime_type,
                        content_hash=digest,
                        blocks=(),
                        metadata=meta,
                        provenance=prov,
                        extraction_quality=0.0,
                        warnings=("OpenDocument missing content.xml.",),
                        error="Missing content.xml",
                    )
                root = ET.fromstring(
                    _read_bounded_member(zf, "content.xml", remaining_budget=remaining_budget)
                )
                current_heading: tuple[str, ...] = ()
                for elem in root.iter():
                    if elem.tag == f"{{{_ODF_TEXT_NS}}}h":
                        txt = " ".join("".join(elem.itertext()).split()).strip()
                        if txt:
                            headings.append(txt)
                            current_heading = (txt,)
                            blocks.append(
                                ParsedBlock(
                                    ordinal=ordinal,
                                    kind="heading",
                                    text=txt,
                                    locator=Locator(kind="section", section=txt),
                                    heading_path=current_heading,
                                    language=detect_language_hint(txt),
                                )
                            )
                            ordinal += 1
                    elif elem.tag == f"{{{_ODF_TEXT_NS}}}p":
                        txt = " ".join("".join(elem.itertext()).split()).strip()
                        if txt:
                            math_exprs = extract_math_expressions(txt)
                            all_math.extend(m for m in math_exprs if m not in all_math)
                            blocks.append(
                                ParsedBlock(
                                    ordinal=ordinal,
                                    kind="paragraph",
                                    text=txt,
                                    locator=Locator(kind="section", section=current_heading[-1] if current_heading else None),
                                    heading_path=current_heading,
                                    language=detect_language_hint(txt),
                                    math_expressions=math_exprs,
                                )
                            )
                            ordinal += 1
        except UnsafeArchiveError as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(str(exc),),
                error=str(exc),
            )

        title = headings[0] if headings else Path(filename).stem
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=str(title),
            mime_type=mime_type,
            content_hash=digest,
            blocks=tuple(blocks),
            headings=tuple(headings),
            math_expressions=tuple(all_math),
            metadata=meta,
            provenance=prov,
            extraction_quality=1.0 if blocks else 0.0,
        )


class ZipArchiveDocumentParser(DocumentParser):
    """Strictly inert, bounded ZIP archive parser extracting text/code/docs from educational archives.

    NEVER executes any archive content, refuses path traversal (`..` / absolute paths),
    enforces zip-bomb ratio and total-byte caps, and skips nested ZIP bombs.
    """

    name = "zip_archive_parser"
    version = "1.0.0"
    supported_mimes = frozenset({"application/zip", "application/x-zip-compressed"})
    supported_extensions = frozenset({".zip"})

    _NON_TEXT_BINARY_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".npy", ".npz", ".pt", ".pth", ".ckpt", ".onnx", ".h5", ".hdf5", ".pkl", ".pickle",
            ".parquet", ".feather", ".arrow", ".db", ".sqlite", ".sqlite3", ".bin", ".dat",
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".tif", ".svg",
            ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".avi", ".mov", ".mkv", ".webm",
            ".pyc", ".pyo", ".so", ".dll", ".exe", ".o", ".a", ".class", ".jar", ".woff", ".woff2", ".ttf",
            ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        }
    )

    def __init__(
        self,
        sub_parser_fn: Any = None,
        *,
        max_members_parsed: int = 30,
        max_total_bytes: int = 25 * 1024 * 1024,
        max_directory_entries: int = 5000,
    ) -> None:
        self._sub_parser_fn = sub_parser_fn
        self._max_members_parsed = max_members_parsed
        self._max_total_bytes = max_total_bytes
        self._max_directory_entries = max_directory_entries

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
        meta: dict[str, Any] = {"filename": filename}
        warnings: list[str] = []
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
            infos = zf.infolist()
            if len(infos) > self._max_directory_entries:
                zf.close()
                raise UnsafeArchiveError(
                    f"ZIP archive exceeds directory entry limit ({len(infos)} > {self._max_directory_entries})."
                )
        except Exception as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(f"Invalid or unsafe ZIP archive: {exc}",),
                error=str(exc),
            )

        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        all_math: list[str] = []
        remaining_budget = [self._max_total_bytes]
        parsed_members = 0
        skipped_binary = 0
        ext_counts: dict[str, int] = {}
        safe_member_names: list[str] = []
        ordinal = 0

        try:
            with zf:
                infos = sorted(infos, key=lambda i: i.filename)
                meta["archive_entry_count"] = len(infos)
                for info in infos:
                    if info.is_dir():
                        continue
                    member = info.filename
                    parts = Path(member).parts
                    if (
                        member.startswith(("/", "\\", "__MACOSX/"))
                        or "/__MACOSX/" in member
                        or "__pycache__" in parts
                        or ".git" in parts
                        or ".." in parts
                        or Path(member).name.startswith(("._", ".DS_Store"))
                    ):
                        continue
                    suffix = Path(member).suffix.lower()
                    ext_key = suffix or "<no_ext>"
                    ext_counts[ext_key] = ext_counts.get(ext_key, 0) + 1
                    if len(safe_member_names) < 40:
                        safe_member_names.append(member)
                    if suffix in self._NON_TEXT_BINARY_EXTENSIONS:
                        skipped_binary += 1
                        continue
                    # Per-member zip-bomb guard before inflating
                    if info.file_size > MAX_OFFICE_MEMBER_BYTES:
                        warnings.append("Skipped oversized ZIP member.")
                        continue
                    if info.compress_size > 0 and (info.file_size / info.compress_size) > MAX_OFFICE_COMPRESSION_RATIO:
                        warnings.append("Skipped high-compression-ratio ZIP member.")
                        continue
                    if parsed_members >= self._max_members_parsed or remaining_budget[0] <= 0:
                        warnings.append(f"ZIP archive truncated at {self._max_members_parsed} parsed members.")
                        break
                    try:
                        member_bytes = _read_bounded_member(
                            zf,
                            member,
                            remaining_budget=remaining_budget,
                            max_member_bytes=min(MAX_OFFICE_MEMBER_BYTES, remaining_budget[0]),
                        )
                    except UnsafeArchiveError:
                        warnings.append("Skipped oversized ZIP member.")
                        continue
                    if not member_bytes:
                        continue
                    sub_doc = (
                        self._sub_parser_fn(member_bytes, filename=Path(member).name)
                        if self._sub_parser_fn is not None
                        else None
                    )
                    if sub_doc is None or not sub_doc.blocks:
                        continue
                    parsed_members += 1
                    headings.append(member)
                    for b in sub_doc.blocks:
                        all_math.extend(m for m in b.math_expressions if m not in all_math)
                        blocks.append(
                            ParsedBlock(
                                ordinal=ordinal,
                                kind=b.kind,
                                text=b.text,
                                locator=Locator(
                                    kind=b.locator.kind,
                                    page=b.locator.page,
                                    slide=b.locator.slide,
                                    cell=b.locator.cell,
                                    line_start=b.locator.line_start,
                                    line_end=b.locator.line_end,
                                    section=f"{member} :: {b.locator.section}" if b.locator.section else member,
                                ),
                                heading_path=(member, *b.heading_path),
                                language=b.language,
                                code_language=b.code_language,
                                table_rows=b.table_rows,
                                math_expressions=b.math_expressions,
                                metadata={**b.metadata, "archive_member": member},
                            )
                        )
                        ordinal += 1
        except UnsafeArchiveError as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type,
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(str(exc),),
                error=str(exc),
            )

        meta["parsed_members"] = parsed_members
        meta["skipped_binary_members"] = skipped_binary
        meta["extensions_summary"] = ext_counts

        # If a valid educational ZIP contains only binary dataset/media assets (0 text members),
        # emit a clean non-empty manifest block instead of silently dropping the archive.
        if not blocks and safe_member_names:
            ext_summary = ", ".join(f"{k}: {v}" for k, v in sorted(ext_counts.items()))
            manifest_lines = [
                f"ZIP Archive Manifest: {Path(filename).name}",
                f"Total entries: {sum(ext_counts.values())} ({ext_summary})",
                "Sample members: " + ", ".join(safe_member_names[:20]),
            ]
            blocks.append(
                ParsedBlock(
                    ordinal=0,
                    kind="raw",
                    text="\n".join(manifest_lines),
                    locator=Locator(kind="section", section="archive_manifest"),
                    heading_path=("archive_manifest",),
                    metadata={"manifest_only": True, "extensions_summary": ext_counts},
                )
            )
            warnings.append("zip_contains_no_parseable_text_members")

        quality = 1.0 if parsed_members > 0 else (0.25 if blocks else 0.0)
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=Path(filename).stem,
            mime_type=mime_type,
            content_hash=digest,
            blocks=tuple(blocks),
            headings=tuple(headings),
            math_expressions=tuple(all_math),
            metadata=meta,
            provenance=prov,
            extraction_quality=quality,
            warnings=tuple(warnings),
        )

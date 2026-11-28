"""Inert Jupyter Notebook (.ipynb) parser.

Strictly NEVER executes notebook code cells or embedded scripts.
Preserves 1-based cell locators, heading hierarchy, exact code indentation,
programming language metadata, LaTeX math, markdown tables, inert text outputs,
and figure references.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..connectors.base import sha256_bytes
from ..contracts import Locator, ParsedBlock, ParsedDocument
from .base import DocumentParser, detect_language_hint, extract_math_expressions

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")


def _join_source(source: Any) -> str:
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    if isinstance(source, str):
        return source
    return ""


def _extract_inert_outputs(outputs: list[dict[str, Any]], cell_index: int) -> tuple[str, list[dict[str, Any]]]:
    """Extract inert plain-text/LaTeX outputs and figure metadata without executing anything."""
    text_parts: list[str] = []
    figures: list[dict[str, Any]] = []

    for out in outputs:
        if not isinstance(out, dict):
            continue
        otype = out.get("output_type", "")
        if otype == "stream":
            chunk = _join_source(out.get("text"))
            if chunk.strip():
                text_parts.append(chunk.rstrip())
        elif otype in ("execute_result", "display_data"):
            data = out.get("data") or {}
            if isinstance(data, dict):
                if "text/latex" in data:
                    text_parts.append(_join_source(data["text/latex"]).rstrip())
                elif "text/plain" in data:
                    text_parts.append(_join_source(data["text/plain"]).rstrip())
                for img_mime in ("image/png", "image/jpeg", "image/svg+xml"):
                    if img_mime in data:
                        figures.append({"cell": cell_index, "mime_type": img_mime, "source": "notebook_output"})
        elif otype == "error":
            ename = out.get("ename", "Error")
            evalue = out.get("evalue", "")
            text_parts.append(f"[Inert notebook error output] {ename}: {evalue}".strip())

    return "\n".join(p for p in text_parts if p), figures


class NotebookDocumentParser(DocumentParser):
    """Inert Jupyter Notebook (.ipynb) parser preserving cells, language, math, and indentation."""

    name = "notebook_parser"
    version = "1.0.0"
    supported_mimes = frozenset({"application/x-ipynb+json", "application/vnd.jupyter"})
    supported_extensions = frozenset({".ipynb"})

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
            nb = json.loads(content.decode("utf-8-sig"))
        except Exception as exc:
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type or "application/x-ipynb+json",
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=(f"Invalid Jupyter notebook JSON: {exc}",),
                error=str(exc),
            )

        if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
            return ParsedDocument(
                parser_name=self.name,
                parser_version=self.version,
                title=Path(filename).stem,
                mime_type=mime_type or "application/x-ipynb+json",
                content_hash=digest,
                blocks=(),
                metadata=meta,
                provenance=prov,
                extraction_quality=0.0,
                warnings=("Notebook JSON missing 'cells' list.",),
                error="Missing notebook cells",
            )

        nb_meta = nb.get("metadata") if isinstance(nb.get("metadata"), dict) else {}
        kernelspec = nb_meta.get("kernelspec") if isinstance(nb_meta.get("kernelspec"), dict) else {}
        lang_info = nb_meta.get("language_info") if isinstance(nb_meta.get("language_info"), dict) else {}
        code_lang = str(
            lang_info.get("name") or kernelspec.get("language") or "python"
        ).lower()
        meta.update(
            {
                "nbformat": nb.get("nbformat"),
                "kernel_name": kernelspec.get("name"),
                "code_language": code_lang,
                "cell_count": len(nb["cells"]),
            }
        )

        blocks: list[ParsedBlock] = []
        headings: list[str] = []
        all_math: list[str] = []
        figures: list[dict[str, Any]] = []
        heading_stack: list[tuple[int, str]] = []
        ordinal = 0

        for cell_idx, cell in enumerate(nb["cells"], start=1):
            if not isinstance(cell, dict):
                continue
            cell_type = str(cell.get("cell_type") or "raw")
            src = _join_source(cell.get("source"))
            if not src.strip() and not cell.get("outputs"):
                continue

            if cell_type == "markdown":
                for line in src.splitlines():
                    hm = _MD_HEADING_RE.match(line.strip())
                    if hm:
                        level = len(hm.group(1))
                        htext = hm.group(2).strip()
                        headings.append(htext)
                        heading_stack = [(lvl, t) for lvl, t in heading_stack if lvl < level]
                        heading_stack.append((level, htext))
                hpath = tuple(t for _, t in heading_stack)
                math_exprs = extract_math_expressions(src)
                all_math.extend(m for m in math_exprs if m not in all_math)
                blocks.append(
                    ParsedBlock(
                        ordinal=ordinal,
                        kind="paragraph",
                        text=src,
                        locator=Locator(
                            kind="cell",
                            cell=cell_idx,
                            section=hpath[-1] if hpath else None,
                        ),
                        heading_path=hpath,
                        language=detect_language_hint(src),
                        math_expressions=math_exprs,
                        metadata={"cell_type": "markdown"},
                    )
                )
                ordinal += 1
            elif cell_type == "code":
                hpath = tuple(t for _, t in heading_stack)
                # Preserve exact code indentation verbatim!
                if src.strip():
                    blocks.append(
                        ParsedBlock(
                            ordinal=ordinal,
                            kind="code",
                            text=src,
                            locator=Locator(
                                kind="cell",
                                cell=cell_idx,
                                section=hpath[-1] if hpath else None,
                            ),
                            heading_path=hpath,
                            code_language=code_lang,
                            metadata={"cell_type": "code", "inert": True},
                        )
                    )
                    ordinal += 1
                outputs = cell.get("outputs") if isinstance(cell.get("outputs"), list) else []
                out_text, out_figs = _extract_inert_outputs(outputs, cell_idx)
                figures.extend(out_figs)
                if out_text:
                    out_math = extract_math_expressions(out_text)
                    all_math.extend(m for m in out_math if m not in all_math)
                    blocks.append(
                        ParsedBlock(
                            ordinal=ordinal,
                            kind="output",
                            text=out_text,
                            locator=Locator(
                                kind="cell",
                                cell=cell_idx,
                                section=hpath[-1] if hpath else None,
                            ),
                            heading_path=hpath,
                            math_expressions=out_math,
                            metadata={"cell_type": "code_output", "inert": True},
                        )
                    )
                    ordinal += 1
            else:
                hpath = tuple(t for _, t in heading_stack)
                blocks.append(
                    ParsedBlock(
                        ordinal=ordinal,
                        kind="raw",
                        text=src,
                        locator=Locator(kind="cell", cell=cell_idx),
                        heading_path=hpath,
                        metadata={"cell_type": cell_type},
                    )
                )
                ordinal += 1

        title = headings[0] if headings else Path(filename).stem
        quality = 1.0 if blocks else 0.0
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            title=title,
            mime_type=mime_type or "application/x-ipynb+json",
            content_hash=digest,
            blocks=tuple(blocks),
            headings=tuple(headings),
            math_expressions=tuple(all_math),
            figures=tuple(figures),
            metadata=meta,
            provenance=prov,
            extraction_quality=quality,
        )

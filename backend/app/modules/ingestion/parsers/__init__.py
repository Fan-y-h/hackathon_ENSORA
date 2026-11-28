"""Modular document parsing subsystem for Ensora+ P1."""

from .base import DocumentParser, detect_language_hint, extract_code_language, extract_math_expressions
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
from .registry import DocumentParserRegistry, sniff_mime_from_bytes

__all__ = [
    "DocumentParser",
    "DocumentParserRegistry",
    "PdfDocumentParser",
    "NotebookDocumentParser",
    "PptxDocumentParser",
    "DocxDocumentParser",
    "XlsxDocumentParser",
    "OpenDocumentParser",
    "LegacyOfficeDocumentParser",
    "ZipArchiveDocumentParser",
    "MarkdownDocumentParser",
    "HtmlDocumentParser",
    "PlainTextAndCodeParser",
    "ImageOcrParser",
    "detect_language_hint",
    "extract_code_language",
    "extract_math_expressions",
    "sniff_mime_from_bytes",
]

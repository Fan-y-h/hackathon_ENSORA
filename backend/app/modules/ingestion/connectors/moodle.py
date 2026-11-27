"""Authorized read-only Moodle ENSAI connector.

Supports:
1. Primary authorized Playwright session acquisition via EnsaiAuthAdapter
   (since Moodle student preferences do not expose security keys and official
   Moodle Web Services availability remains unconfirmed).
2. Optional official Moodle Web Services REST API path if ever enabled and
   authorized with a verified token in OS keyring.
3. Sanitized HTML fixture parsing for deterministic testing without live access.

Strictly read-only: never submits assignments, never modifies Moodle content,
never follows unrelated external links automatically. Defaults to single-course
discovery safety when `single_course_first=True`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

import requests

from ..auth.ensai import (
    DEFAULT_ENSAI_USER_AGENT,
    SESSION_COOKIES_FILENAME,
    EnsaiAuthAdapter,
    sanitize_url_for_logs,
)
from ..config import SourcesRuntimeConfig
from ..contracts import (
    AccessStatus,
    AuthStatus,
    ChangePage,
    ConnectorCapabilities,
    FetchResult,
    SourceKind,
    SourceRecord,
    utc_now_iso,
)
from ..credentials import SecureCredentialManager, redact_sensitive_text
from .base import (
    AuthenticationRequiredError,
    PermissionDeniedError,
    RateLimiter,
    SourceConnector,
    TransientSourceError,
    guess_mime_type,
    is_same_origin,
    retry_with_backoff,
    sha256_bytes,
)

logger = logging.getLogger(__name__)

SUPPORTED_MOODLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".xlsx",
        ".xlsm",
        ".xls",
        ".odt",
        ".odp",
        ".ods",
        ".ipynb",
        ".py",
        ".r",
        ".rmd",
        ".qmd",
        ".sas",
        ".sql",
        ".java",
        ".c",
        ".cpp",
        ".js",
        ".ts",
        ".sh",
        ".tex",
        ".bib",
        ".md",
        ".txt",
        ".html",
        ".htm",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".rtf",
        ".zip",
    }
)


class DownloadSizeLimitError(ValueError):
    """Raised when a remote file exceeds the configured download byte limit."""

    def __init__(self, message: str, *, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(message)
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


_ACADEMIC_YEAR_RE = re.compile(r"\b(20\d{2})\s*[-/–]\s*(20\d{2})\b")
_FORBIDDEN_MOODLE_PATHS: tuple[str, ...] = (
    "/user/",
    "/message/",
    "/grade/report/grader",
    "/mod/assign/view.php?action=grading",
    "action=grading",
    "action=editsubmission",
)
_EXCLUDED_PLUGINFILE_SEGMENTS: tuple[str, ...] = (
    "/theme/",
    "/user/icon/",
    "/user/draft/",
    "/course/overviewfiles/",
    "/badges/",
    "/pix/",
    "/core_course/",
)


@dataclass(frozen=True)
class MoodleCourseSummary:
    """Discovered enrolled Moodle course metadata."""

    course_id: str
    title: str
    url: str
    shortname: str | None = None
    subject_hint: str | None = None
    category: str | None = None
    summary: str | None = None
    academic_year: str | None = None
    archived: bool = False


@dataclass(frozen=True)
class MoodleSectionTab:
    """Discovered section/tab link inside a Moodle course (e.g. OneTopic tab format)."""

    course_id: str
    section_num: str
    title: str
    url: str
    available: bool = True


@dataclass(frozen=True)
class MoodleResourceItem:
    """Normalized resource, assignment, announcement, or inline HTML section discovered in a course."""

    course_id: str
    course_title: str
    section_title: str
    module_id: str
    title: str
    resource_kind: str  # resource | assignment | announcement | folder | page | html_section | url
    url: str
    download_url: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    description: str | None = None
    deadline_at: str | None = None
    modified_at: str | None = None
    remote_version: str | None = None
    section_url: str | None = None
    inline_html: str | None = None
    section_id: str | None = None
    academic_year: str | None = None
    academic_category: str | None = None


def _extract_academic_year_hint(text: str | None) -> str | None:
    if not text:
        return None
    m = _ACADEMIC_YEAR_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _academic_year_from_timestamp(ts: Any) -> str | None:
    """Derive source-backed academic year (e.g. '2026-2027') from an official Moodle epoch timestamp."""
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    if dt.year < 2015 or dt.year > 2040:
        return None
    # Moodle courses for the upcoming year frequently open in July/August (month >= 7)
    start_year = dt.year if dt.month >= 7 else dt.year - 1
    return f"{start_year}-{start_year + 1}"


def _extract_course_title_from_html(html: str) -> str | None:
    """Extract course heading title from a Moodle course view page."""
    for pattern in (
        r'<div\b[^>]*class\s*=\s*["\'][^"\']*page-header-headings[^"\']*["\'][^>]*>.*?<h1[^>]*>(.*?)</h1>',
        r"<h1\b[^>]*>(.*?)</h1>",
    ):
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            plain = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split()).strip()
            if plain and len(plain) >= 2:
                return plain
    return None


def _extract_academic_year_from_course_html(html: str) -> str | None:
    """Extract an explicit academic year from Moodle breadcrumb/header markup when present."""
    for pattern in (
        r"<nav\b[^>]*breadcrumb[^>]*>(.*?)</nav>",
        r"<ol\b[^>]*breadcrumb[^>]*>(.*?)</ol>",
        r"<div\b[^>]*page-context-header[^>]*>(.*?)</div>",
        r"<title>(.*?)</title>",
    ):
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if m:
            plain = re.sub(r"<[^>]+>", " ", m.group(1))
            yr = _extract_academic_year_hint(plain)
            if yr:
                return yr
    return None


def parse_moodle_ajax_courses(payload: Any, base_url: str) -> list[MoodleCourseSummary]:
    """Parse Moodle `lib/ajax/service.php` enrolled-course JSON responses."""
    course_dicts: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict):
                if isinstance(entry.get("data"), dict) and isinstance(entry["data"].get("courses"), list):
                    course_dicts.extend(c for c in entry["data"]["courses"] if isinstance(c, dict))
                elif "id" in entry and ("fullname" in entry or "shortname" in entry):
                    course_dicts.append(entry)
    elif isinstance(payload, dict):
        if isinstance(payload.get("courses"), list):
            course_dicts.extend(c for c in payload["courses"] if isinstance(c, dict))
        elif isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("courses"), list):
            course_dicts.extend(c for c in payload["data"]["courses"] if isinstance(c, dict))

    courses: list[MoodleCourseSummary] = []
    seen: set[str] = set()
    for entry in course_dicts:
        cid = str(entry.get("id") or "").strip()
        if not cid or not cid.isdigit() or cid in seen:
            continue
        seen.add(cid)
        title = str(entry.get("fullname") or entry.get("shortname") or cid).strip()
        shortname = str(entry.get("shortname") or "").strip() or None
        cat = str(entry.get("coursecategory") or entry.get("category") or "").strip() or None
        viewurl = str(entry.get("viewurl") or "").strip()
        if viewurl and is_same_origin(urljoin(base_url, viewurl), base_url):
            parsed = urlparse(urljoin(base_url, viewurl))
            course_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?id={cid}"
        else:
            course_url = urljoin(base_url, f"course/view.php?id={cid}")
        yr = (
            _extract_academic_year_hint(f"{title} {shortname or ''} {cat or ''}")
            or _academic_year_from_timestamp(entry.get("enddate"))
            or _academic_year_from_timestamp(entry.get("startdate"))
        )
        archived = bool(entry.get("hidden", False)) or _is_archived_hint(f"{title} {cat or ''}")
        courses.append(
            MoodleCourseSummary(
                course_id=cid,
                title=title,
                url=course_url,
                shortname=shortname,
                subject_hint=title,
                category=cat,
                summary=str(entry.get("summary") or "").strip() or None,
                academic_year=yr,
                archived=archived,
            )
        )
    return courses


def _extract_moodle_pluginfile_url(html_text: str, page_url: str, moodle_base_url: str) -> str | None:
    """Resolve the real `/pluginfile.php/` URL from a Moodle resource wrapper HTML page.

    Handles all Moodle display modes:
    - `<div class="resourceworkaround"><a href=".../pluginfile.php/...">`
    - `<object id="resourceobject" data=".../pluginfile.php/...">`
    - `<iframe id="resourceobject" src=".../pluginfile.php/...">`
    - `<embed src=".../pluginfile.php/...">`
    - `<a href=".../pluginfile.php/.../mod_resource/content/...">`
    Strictly ignores theme images, user avatars, badges, and cross-origin URLs.
    """
    candidates: list[tuple[int, str]] = []
    # Highest priority: explicit resourceworkaround container or resourceobject tag
    for priority, pattern in (
        (
            0,
            r'class\s*=\s*["\'][^"\']*resourceworkaround[^"\']*["\'][^>]*>.*?(?:href|src|data)\s*=\s*["\']([^"\']*/pluginfile\.php/[^"\']+)["\']',
        ),
        (
            1,
            r'<(?:object|iframe|embed)\b[^>]*?(?:data|src)\s*=\s*["\']([^"\']*/pluginfile\.php/[^"\']+)["\']',
        ),
        (
            2,
            r'(?:href|src|data)\s*=\s*["\']([^"\']*/pluginfile\.php/[^"\']+)["\']',
        ),
    ):
        for raw_match in re.findall(pattern, html_text, re.IGNORECASE | re.DOTALL):
            cleaned = raw_match.replace("&amp;", "&").strip()
            if not cleaned:
                continue
            full = urljoin(page_url, cleaned)
            if not is_same_origin(full, moodle_base_url):
                continue
            low = full.lower()
            if any(seg in low for seg in _EXCLUDED_PLUGINFILE_SEGMENTS):
                continue
            if any(bad in low for bad in _FORBIDDEN_MOODLE_PATHS):
                continue
            score = priority
            if "/mod_resource/content/" in low or "/mod_folder/content/" in low or "/mod_assign/introattachment/" in low:
                score -= 10
            if "forcedownload=1" in low:
                score -= 5
            candidates.append((score, full))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


def _extract_moodle_main_html(html_text: str) -> str:
    """Extract the primary educational content region from a Moodle HTML page when available."""
    for pattern in (
        r'<section\b[^>]*id\s*=\s*["\']region-main["\'][^>]*>(.*?)</section>',
        r'<div\b[^>]*role\s*=\s*["\']main["\'][^>]*>(.*?)</div>\s*</section>',
        r'<div\b[^>]*class\s*=\s*["\'][^"\']*\b(?:generalbox|book_content|no-overflow)\b[^"\']*["\'][^>]*>(.*?)</div>',
    ):
        m = re.search(pattern, html_text, re.IGNORECASE | re.DOTALL)
        if m:
            snippet = m.group(1).strip()
            plain = " ".join(re.sub(r"<[^>]+>", " ", snippet).split())
            if len(plain) >= 40:
                return f"<html><body>{snippet}</body></html>"
    return html_text


def _with_redirect_param(url: str) -> str:
    """Add `redirect=1` to Moodle `/mod/resource/view.php` URLs so Moodle redirects directly to `/pluginfile.php/`."""
    parsed = urlparse(url)
    if "/mod/resource/view.php" not in parsed.path.lower():
        return url
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if "redirect" in qs:
        return url
    qs["redirect"] = ["1"]
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


def _is_archived_hint(text: str | None) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(tok in low for tok in ("archiv", "ancien", "past", "passé", "passe"))


class _MoodleCourseListHTMLParser(HTMLParser):
    """Conservative parser extracting standard Moodle course links (`/course/view.php?id=...`).

    Preserves course IDs, titles, categories, and academic-year/archive hints while
    ignoring recent-course-only blocks when full enrolled course cards are present.
    """

    _SR_PREFIXES: tuple[str, ...] = (
        "nom du cours",
        "course name",
        "intitulé du cours",
    )

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._current_href: str | None = None
        self._fallback_title: str | None = None
        self._text_buf: list[str] = []
        self._courses: dict[str, MoodleCourseSummary] = {}
        self._in_category = False
        self._cat_buf: list[str] = []
        self._last_category: str | None = None
        self._in_recent_block_depth = 0
        self._recent_course_ids: set[str] = set()
        self._non_recent_course_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low_tag = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        cls = attr_map.get("class", "").lower()
        data_block = attr_map.get("data-block", "").lower()

        if self._in_recent_block_depth > 0:
            self._in_recent_block_depth += 1
        elif "block_recentlyaccessedcourses" in cls or data_block == "recentlyaccessedcourses":
            self._in_recent_block_depth = 1

        if any(tok in cls for tok in ("categoryname", "coursecategory", "course-category")):
            self._in_category = True
            self._cat_buf = []

        if low_tag != "a":
            return
        href = attr_map.get("href")
        if not href:
            return
        full_url = urljoin(self._base_url, href)
        if not is_same_origin(full_url, self._base_url):
            return
        parsed = urlparse(full_url)
        if "/course/view.php" in parsed.path:
            qs = parse_qs(parsed.query)
            cid = (qs.get("id") or [None])[0]
            if cid and cid.isdigit():
                self._current_href = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?id={cid}"
                self._fallback_title = (attr_map.get("title") or attr_map.get("aria-label") or "").strip() or None
                self._text_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_category:
            self._cat_buf.append(data)
        if self._current_href is not None:
            self._text_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        low_tag = tag.lower()
        if self._in_category and low_tag in ("div", "span", "p", "a"):
            cat_text = " ".join("".join(self._cat_buf).split()).strip()
            if cat_text:
                self._last_category = cat_text
            self._in_category = False
            self._cat_buf = []

        if low_tag == "a" and self._current_href is not None:
            raw_title = " ".join("".join(self._text_buf).split()).strip()
            for prefix in self._SR_PREFIXES:
                if raw_title.lower().startswith(prefix):
                    raw_title = raw_title[len(prefix) :].lstrip(" :-–").strip()
            title = raw_title or (self._fallback_title or "")
            parsed = urlparse(self._current_href)
            cid = parse_qs(parsed.query)["id"][0]
            if self._in_recent_block_depth > 0:
                self._recent_course_ids.add(cid)
            else:
                self._non_recent_course_ids.add(cid)
            if title and cid not in self._courses:
                cat = self._last_category
                yr = _extract_academic_year_hint(f"{title} {cat or ''}")
                archived = _is_archived_hint(f"{title} {cat or ''}")
                self._courses[cid] = MoodleCourseSummary(
                    course_id=cid,
                    title=title,
                    url=self._current_href,
                    subject_hint=title,
                    category=cat,
                    academic_year=yr,
                    archived=archived,
                )
            self._current_href = None
            self._fallback_title = None
            self._text_buf = []

        if self._in_recent_block_depth > 0:
            self._in_recent_block_depth -= 1

    @property
    def courses(self) -> list[MoodleCourseSummary]:
        # Prefer full enrolled course listing over recently-accessed widget when both appear
        if self._non_recent_course_ids:
            return [c for cid, c in self._courses.items() if cid in self._non_recent_course_ids]
        return list(self._courses.values())


class _MoodleSectionTabsParser(HTMLParser):
    """Discover tab-based and sectioned course URLs (`/course/view.php?id=<cid>&section=<N>`)."""

    def __init__(self, base_url: str, course_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._course_id = str(course_id)
        self._in_anchor = False
        self._cur_url: str | None = None
        self._cur_sec: str | None = None
        self._cur_available = True
        self._cur_fallback_title: str = ""
        self._buf: list[str] = []
        self._tabs: dict[str, MoodleSectionTab] = {}
        self._disabled_parent_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        cls = attr_map.get("class", "").lower()
        is_disabled = (
            "disabled" in cls
            or "dimmed" in cls
            or "restricted" in cls
            or "unavailable" in cls
            or attr_map.get("aria-disabled", "").lower() == "true"
            or "disabled" in attr_map
        )
        if self._disabled_parent_depth > 0:
            self._disabled_parent_depth += 1
        elif is_disabled and low in ("li", "div", "span", "section"):
            self._disabled_parent_depth = 1

        if low != "a":
            return
        href = attr_map.get("href")
        if not href:
            return
        full_url = urljoin(self._base_url, href)
        if not is_same_origin(full_url, self._base_url):
            return
        parsed = urlparse(full_url)
        if "/course/view.php" not in parsed.path.lower():
            return
        qs = parse_qs(parsed.query)
        cid = (qs.get("id") or [None])[0]
        sec = (qs.get("section") or [None])[0]
        if cid != self._course_id or sec is None or not sec.isdigit():
            return
        canonical_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?id={cid}&section={sec}"
        self._in_anchor = True
        self._cur_url = canonical_url
        self._cur_sec = sec
        self._cur_available = not (is_disabled or self._disabled_parent_depth > 0)
        self._cur_fallback_title = (attr_map.get("title") or attr_map.get("aria-label") or "").strip()
        self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low == "a" and self._in_anchor and self._cur_sec is not None and self._cur_url is not None:
            label = " ".join("".join(self._buf).split()).strip() or self._cur_fallback_title or f"Section {self._cur_sec}"
            existing = self._tabs.get(self._cur_sec)
            if existing is None or (not existing.available and self._cur_available):
                self._tabs[self._cur_sec] = MoodleSectionTab(
                    course_id=self._course_id,
                    section_num=self._cur_sec,
                    title=label,
                    url=self._cur_url,
                    available=self._cur_available,
                )
            self._in_anchor = False
            self._cur_url = None
            self._cur_sec = None
            self._buf = []
        if self._disabled_parent_depth > 0:
            self._disabled_parent_depth -= 1

    @property
    def tabs(self) -> list[MoodleSectionTab]:
        return sorted(self._tabs.values(), key=lambda t: int(t.section_num))


def discover_course_section_tabs(
    html: str,
    base_url: str,
    course_id: str,
) -> list[MoodleSectionTab]:
    """Discover all section/tab URLs (`/course/view.php?id=<id>&section=<N>`) in a course page."""
    parser = _MoodleSectionTabsParser(base_url, course_id)
    parser.feed(html)
    return parser.tabs


class _MoodleCoursePageHTMLParser(HTMLParser):
    """Extracts Moodle sections, modules, files, assignments, announcements, and inline HTML instructions.

    Relies on standard Moodle URL patterns (`/mod/resource/`, `/mod/assign/`,
    `/mod/forum/`, `/mod/folder/`, `/mod/page/`, `/pluginfile.php/`) and semantic
    section/heading structure rather than brittle theme-specific CSS paths.
    """

    def __init__(
        self,
        base_url: str,
        course: MoodleCourseSummary,
        *,
        default_section: str = "General",
        section_url: str | None = None,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._course = course
        self._current_section: str = default_section
        self._section_url: str | None = section_url or course.url
        self._in_section_heading = False
        self._heading_buf: list[str] = []
        self._in_anchor = False
        self._anchor_href: str | None = None
        self._anchor_data_deadline: str | None = None
        self._anchor_data_modified: str | None = None
        self._anchor_text: list[str] = []
        self._items: list[MoodleResourceItem] = []
        self._seen_keys: set[str] = set()
        # Track meaningful embedded educational HTML instructions / code examples
        self._in_content_depth = 0
        self._content_chunks: list[str] = []
        self._inline_blocks: list[tuple[str, str]] = []  # (section_title, html_snippet)
        self._in_skip_tag = 0
        self.excluded_links_count: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low_tag = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        cls = attr_map.get("class", "").lower()

        if low_tag in ("script", "style", "noscript"):
            self._in_skip_tag += 1
            return

        if self._in_content_depth > 0:
            self._in_content_depth += 1
            if low_tag in ("p", "li", "pre", "code", "h3", "h4", "br", "tr", "div"):
                self._content_chunks.append(f"<{low_tag}>")
        elif low_tag in ("div", "section") and any(
            tok in cls for tok in ("contentwithoutlink", "course-description-item", "summary", "modtype_label", "activity-description")
        ):
            self._in_content_depth = 1
            self._content_chunks = []

        if low_tag in ("h2", "h3", "h4") and (
            "sectionname" in cls or "section-title" in cls or "section" in cls
        ):
            self._in_section_heading = True
            self._heading_buf = []
            return

        if low_tag == "a":
            href = attr_map.get("href")
            if not href:
                return
            full_url = urljoin(self._base_url, href)
            # Never follow unrelated external links or forbidden personal/admin URLs
            if not is_same_origin(full_url, self._base_url):
                if full_url.lower().startswith(("http://", "https://")):
                    self.excluded_links_count += 1
                return
            if any(bad in full_url.lower() for bad in _FORBIDDEN_MOODLE_PATHS):
                self.excluded_links_count += 1
                return
            if self._is_relevant_moodle_link(full_url):
                self._in_anchor = True
                self._anchor_href = full_url
                self._anchor_data_deadline = attr_map.get("data-deadline") or None
                self._anchor_data_modified = attr_map.get("data-modified") or None
                self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._in_skip_tag > 0:
            return
        if self._in_content_depth > 0:
            self._content_chunks.append(data)
        if self._in_section_heading:
            self._heading_buf.append(data)
        if self._in_anchor:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        low_tag = tag.lower()
        if low_tag in ("script", "style", "noscript") and self._in_skip_tag > 0:
            self._in_skip_tag -= 1
            return

        if self._in_content_depth > 0:
            if low_tag in ("p", "li", "pre", "code", "h3", "h4", "tr", "div"):
                self._content_chunks.append(f"</{low_tag}>")
            self._in_content_depth -= 1
            if self._in_content_depth == 0:
                snippet = "".join(self._content_chunks).strip()
                plain = re.sub(r"<[^>]+>", " ", snippet)
                plain = " ".join(plain.split()).strip()
                if len(plain) >= 40:
                    self._inline_blocks.append((self._current_section, snippet))
                self._content_chunks = []

        if self._in_section_heading and low_tag in ("h2", "h3", "h4"):
            heading = " ".join("".join(self._heading_buf).split()).strip()
            if heading:
                self._current_section = heading
            self._in_section_heading = False
            self._heading_buf = []
            return

        if self._in_anchor and low_tag == "a" and self._anchor_href is not None:
            title = " ".join("".join(self._anchor_text).split()).strip()
            url = self._anchor_href
            self._in_anchor = False
            self._anchor_href = None
            if not title:
                return

            kind, module_id, download_url, filename = self._classify_moodle_link(url, title)
            dedupe_key = f"{kind}:{module_id}:{url}"
            if dedupe_key in self._seen_keys:
                return
            self._seen_keys.add(dedupe_key)

            mime_type = (
                "text/html"
                if kind == "page"
                else (guess_mime_type(filename) if filename else None)
            )
            self._items.append(
                MoodleResourceItem(
                    course_id=self._course.course_id,
                    course_title=self._course.title,
                    section_title=self._current_section,
                    module_id=module_id,
                    title=title,
                    resource_kind=kind,
                    url=url,
                    download_url=download_url,
                    filename=filename,
                    mime_type=mime_type,
                    deadline_at=self._anchor_data_deadline,
                    modified_at=self._anchor_data_modified,
                    section_url=self._section_url,
                )
            )

    @staticmethod
    def _is_relevant_moodle_link(url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(
            token in path
            for token in (
                "/mod/resource/",
                "/mod/assign/",
                "/mod/forum/",
                "/mod/folder/",
                "/mod/page/",
                "/mod/book/",
                "/mod/url/",
                "/pluginfile.php/",
            )
        )

    @staticmethod
    def _classify_moodle_link(url: str, title: str) -> tuple[str, str, str | None, str | None]:
        parsed = urlparse(url)
        path = parsed.path
        qs = parse_qs(parsed.query)
        cmid = (qs.get("id") or [None])[0]
        filename: str | None = None
        download_url: str | None = None

        if "/pluginfile.php/" in path:
            kind = "resource"
            download_url = url
            filename = unquote(path.rsplit("/", 1)[-1]) or title
            module_id = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
        elif "/mod/assign/" in path:
            kind = "assignment"
            module_id = cmid or hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        elif "/mod/forum/" in path:
            kind = "announcement"
            module_id = cmid or hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        elif "/mod/folder/" in path:
            kind = "folder"
            module_id = cmid or hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        elif "/mod/page/" in path or "/mod/book/" in path:
            kind = "page"
            download_url = url
            module_id = cmid or hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
            safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", title).strip("_")[:60] or f"page_{module_id}"
            filename = f"{safe_slug}.html"
        elif "/mod/url/" in path:
            # Record pedagogical URL reference without auto-following arbitrary external sites
            kind = "url"
            download_url = None
            module_id = cmid or hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        else:
            kind = "resource"
            download_url = url
            module_id = cmid or hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
            for ext in SUPPORTED_MOODLE_EXTENSIONS:
                if title.lower().endswith(ext):
                    filename = title
                    break

        return kind, module_id, download_url, filename

    @property
    def items(self) -> list[MoodleResourceItem]:
        out = list(self._items)
        # Emit embedded HTML section instructions / code examples as parseable HTML educational pages
        grouped: dict[str, list[str]] = {}
        for sec, snippet in self._inline_blocks:
            grouped.setdefault(sec, []).append(snippet)
        for sec, snippets in grouped.items():
            combined_html = f"<html><body><h1>{sec}</h1>\n" + "\n".join(snippets) + "\n</body></html>"
            sec_hash = hashlib.sha1(f"{self._course.course_id}|{sec}|{self._section_url}".encode("utf-8")).hexdigest()[:12]
            safe_sec = re.sub(r"[^a-zA-Z0-9._-]+", "_", sec).strip("_")[:48] or "section"
            out.append(
                MoodleResourceItem(
                    course_id=self._course.course_id,
                    course_title=self._course.title,
                    section_title=sec,
                    module_id=f"htmlsec_{sec_hash}",
                    title=f"{sec} — Instructions",
                    resource_kind="html_section",
                    url=self._section_url or self._course.url,
                    download_url=self._section_url or self._course.url,
                    filename=f"{safe_sec}_instructions.html",
                    mime_type="text/html",
                    section_url=self._section_url,
                    inline_html=combined_html,
                )
            )
        return out


def parse_moodle_dashboard_html(html: str, base_url: str) -> list[MoodleCourseSummary]:
    """Parse enrolled Moodle courses from a sanitized dashboard/my-courses HTML page."""
    parser = _MoodleCourseListHTMLParser(base_url)
    parser.feed(html)
    return parser.courses


def parse_moodle_course_html(
    html: str,
    base_url: str,
    course: MoodleCourseSummary,
    *,
    default_section: str = "General",
    section_url: str | None = None,
) -> list[MoodleResourceItem]:
    """Parse course sections, resources, assignments, announcements, and inline HTML from HTML."""
    parser = _MoodleCoursePageHTMLParser(
        base_url,
        course,
        default_section=default_section,
        section_url=section_url,
    )
    parser.feed(html)
    return parser.items


_DEADLINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:due\s*date|date\s+de\s+remise|à\s+remettre|échéance)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T\s][0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:due\s*date|date\s+de\s+remise|échéance)\s*[:\-]?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}(?:\s+[0-9]{2}:[0-9]{2})?)",
        re.IGNORECASE,
    ),
)


def extract_assignment_deadline(text: str) -> str | None:
    """Extract an explicit ISO or DD/MM/YYYY assignment deadline from text when present."""
    for pattern in _DEADLINE_PATTERNS:
        m = pattern.search(text)
        if m:
            raw = m.group(1).strip()
            if "/" in raw:
                # Convert DD/MM/YYYY [HH:MM] -> YYYY-MM-DD[THH:MM:00]
                parts = raw.split()
                d, mo, y = parts[0].split("/")
                if len(parts) > 1:
                    return f"{y}-{mo}-{d}T{parts[1]}:00"
                return f"{y}-{mo}-{d}"
            return raw.replace(" ", "T")
    return None


class MoodleConnector(SourceConnector):
    """Read-only Moodle ENSAI connector supporting Playwright + optional WS REST + fixtures."""

    source_kind = SourceKind.MOODLE

    def __init__(
        self,
        config: SourcesRuntimeConfig,
        *,
        auth_adapter: EnsaiAuthAdapter | None = None,
        credential_manager: SecureCredentialManager | None = None,
        http_get: Callable[..., Any] | None = None,
        page_fetcher: Callable[[str], str] | None = None,
        blob_fetcher: Callable[[str], tuple[bytes, str, str]] | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._config = config
        self._credentials = credential_manager or SecureCredentialManager()
        self._auth = auth_adapter or EnsaiAuthAdapter(config, self._credentials)
        self._http_get = http_get or requests.get
        self._page_fetcher = page_fetcher
        self._blob_fetcher = blob_fetcher
        self._rate_limiter = rate_limiter or RateLimiter(config.rate_limit_min_interval_sec)
        self.last_stats: dict[str, Any] = {}

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            source=SourceKind.MOODLE,
            supports_documents=True,
            supports_events=True,  # assignment deadlines
            supports_assignments=True,
            supports_academic_records=False,
            supports_incremental=True,
            supports_full_scan=True,
            supports_download=True,
            read_only=True,
            fallback_formats=("html_fixture", "zip", "pdf", "local_folder"),
        )

    def authorize(self, config: dict[str, Any] | None = None) -> AuthStatus:
        interactive = bool((config or {}).get("interactive", False))
        if self._config.moodle_ws_enabled and self._credentials.get_moodle_ws_token():
            return self._validate_ws_token()
        return self._auth.ensure_authenticated(SourceKind.MOODLE, interactive=interactive)

    def _guarded_http_get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        stream: bool = False,
        max_redirects: int = 5,
    ) -> Any:
        """Unified same-origin pre-follow HTTP GET policy for all Moodle REST calls & downloads.

        Disables automatic redirects (`allow_redirects=False`) and validates every `Location`
        target against `moodle_base_url` BEFORE issuing any request to the redirected host.
        """
        current_url = url
        current_params = dict(params) if params is not None else None
        resp: Any = None
        for hop in range(max_redirects + 1):
            if not is_same_origin(current_url, self._config.moodle_base_url):
                raise PermissionDeniedError(
                    f"Blocked cross-origin Moodle request target: {sanitize_url_for_logs(current_url)}"
                )
            req_kwargs: dict[str, Any] = {
                "params": current_params,
                "timeout": self._config.request_timeout_sec,
                "allow_redirects": False,
            }
            if stream:
                req_kwargs["stream"] = True
            try:
                resp = self._http_get(current_url, **req_kwargs)
            except TypeError:
                if self._http_get is requests.get:
                    raise
                req_kwargs.pop("allow_redirects", None)
                resp = self._http_get(current_url, **req_kwargs)

            status = getattr(resp, "status_code", 200)
            headers = {k.lower(): v for k, v in (getattr(resp, "headers", None) or {}).items()}
            if status in (301, 302, 303, 307, 308):
                location = headers.get("location")
                if not location:
                    raise RuntimeError(f"Moodle redirect HTTP {status} missing Location header.")
                next_url = urljoin(current_url, str(location))
                if not is_same_origin(next_url, self._config.moodle_base_url):
                    raise PermissionDeniedError(
                        f"Blocked cross-origin redirect before follow: {sanitize_url_for_logs(next_url)}"
                    )
                if hop >= max_redirects:
                    raise RuntimeError("Exceeded maximum Moodle redirect hops.")
                current_url = next_url
                # Avoid duplicating query parameters if redirect target already embeds them
                if urlparse(next_url).query:
                    current_params = None
                continue
            break

        final_url = getattr(resp, "url", current_url) or current_url
        if not is_same_origin(final_url, self._config.moodle_base_url):
            raise PermissionDeniedError(
                f"Blocked cross-origin redirect during Moodle request: {sanitize_url_for_logs(final_url)}"
            )
        return resp

    def _validate_ws_token(self) -> AuthStatus:
        from ..contracts import AuthState

        token = self._credentials.get_moodle_ws_token()
        if not token:
            return AuthStatus(
                source=SourceKind.MOODLE,
                state=AuthState.UNCONFIGURED,
                authenticated=False,
                detail="Moodle Web Services REST token is not configured.",
                metadata={"mode": "webservice"},
            )
        endpoint = urljoin(self._config.moodle_base_url, "webservice/rest/server.php")
        try:
            resp = self._guarded_http_get(
                endpoint,
                params={
                    "wstoken": token,
                    "wsfunction": "core_webservice_get_site_info",
                    "moodlewsrestformat": "json",
                },
            )
            status_code = getattr(resp, "status_code", 200)
            if status_code == 401:
                return AuthStatus(
                    source=SourceKind.MOODLE,
                    state=AuthState.EXPIRED,
                    authenticated=False,
                    detail="Moodle Web Services token rejected (HTTP 401).",
                    metadata={"mode": "webservice"},
                )
            if status_code == 403:
                return AuthStatus(
                    source=SourceKind.MOODLE,
                    state=AuthState.REVOKED,
                    authenticated=False,
                    detail="Moodle Web Services access denied (HTTP 403).",
                    metadata={"mode": "webservice"},
                )
            data = resp.json()
            if isinstance(data, dict) and data.get("exception"):
                err_code = str(data.get("errorcode") or "")
                state = (
                    AuthState.EXPIRED
                    if err_code in ("invalidtoken", "accessexception")
                    else AuthState.ERROR
                )
                return AuthStatus(
                    source=SourceKind.MOODLE,
                    state=state,
                    authenticated=False,
                    detail=redact_sensitive_text(
                        f"Moodle Web Services unavailable: {data.get('message', err_code or 'unknown error')}"
                    ),
                    metadata={"mode": "webservice", "errorcode": err_code},
                )
            return AuthStatus(
                source=SourceKind.MOODLE,
                state=AuthState.AUTHENTICATED,
                authenticated=True,
                detail="Verified official Moodle Web Services REST token.",
                account_hint=data.get("username") if isinstance(data, dict) else None,
                metadata={
                    "userid": data.get("userid") if isinstance(data, dict) else None,
                    "mode": "webservice",
                },
            )
        except PermissionDeniedError:
            return AuthStatus(
                source=SourceKind.MOODLE,
                state=AuthState.ERROR,
                authenticated=False,
                detail="Blocked cross-origin redirect during Moodle Web Services validation.",
                metadata={"mode": "webservice", "errorcode": "cross_origin_redirect"},
            )
        except Exception as exc:
            return AuthStatus(
                source=SourceKind.MOODLE,
                state=AuthState.ERROR,
                authenticated=False,
                detail=redact_sensitive_text(f"Moodle Web Services validation failed: {type(exc).__name__}"),
                metadata={"mode": "webservice"},
            )

    @staticmethod
    def _authenticated_state():
        from ..contracts import AuthState

        return AuthState.AUTHENTICATED

    @staticmethod
    def _error_state():
        from ..contracts import AuthState

        return AuthState.ERROR

    def _fetch_html_page(
        self,
        url: str,
        *,
        interactive: bool = False,
        allow_auto_reauth: bool | None = None,
    ) -> str:
        if not is_same_origin(url, self._config.moodle_base_url):
            raise PermissionDeniedError(
                f"Refused to navigate outside Moodle origin: {sanitize_url_for_logs(url)}"
            )
        self._rate_limiter.wait()
        if self._page_fetcher is not None:
            return retry_with_backoff(
                lambda: self._page_fetcher(url),
                max_retries=self._config.max_retries,
            )
        auto_allowed = (
            self._config.ensai_allow_automated_credential_submit
            if allow_auto_reauth is None
            else allow_auto_reauth
        )
        can_reauth = interactive or auto_allowed
        html, _final_url = self._auth.fetch_protected_page(
            SourceKind.MOODLE,
            url,
            interactive=interactive,
            max_reauth_attempts=1 if can_reauth else 0,
        )
        return html

    def _use_rest_mode(self) -> bool:
        return bool(
            (self._config.moodle_ws_enabled or self._config.moodle_auth_mode == "rest")
            and self._credentials.get_moodle_ws_token()
        )

    def _ws_call(self, wsfunction: str, extra_params: dict[str, Any] | None = None) -> Any:
        token = self._credentials.get_moodle_ws_token()
        if not token:
            raise AuthenticationRequiredError("Moodle Web Services token missing.")
        endpoint = urljoin(self._config.moodle_base_url, "webservice/rest/server.php")
        params: dict[str, Any] = {
            "wstoken": token,
            "wsfunction": wsfunction,
            "moodlewsrestformat": "json",
        }
        if extra_params:
            params.update(extra_params)

        def _op():
            self._rate_limiter.wait()
            resp = self._guarded_http_get(endpoint, params=params)
            status = getattr(resp, "status_code", 200)
            if status == 401:
                raise AuthenticationRequiredError("Moodle Web Services returned HTTP 401.")
            if status == 403:
                raise PermissionDeniedError("Moodle Web Services returned HTTP 403.")
            if status in (429, 500, 502, 503, 504):
                raise TransientSourceError(f"Moodle REST transient HTTP {status}", status_code=status)
            data = resp.json()
            if isinstance(data, dict) and data.get("exception"):
                err_code = str(data.get("errorcode") or "")
                if err_code in ("invalidtoken", "accessexception"):
                    raise AuthenticationRequiredError(
                        redact_sensitive_text(str(data.get("message") or err_code))
                    )
                raise RuntimeError(redact_sensitive_text(str(data.get("message") or err_code)))
            return data

        return retry_with_backoff(_op, max_retries=self._config.max_retries)

    def _discover_courses_rest(self) -> list[MoodleCourseSummary]:
        site_info = self._ws_call("core_webservice_get_site_info")
        userid = site_info.get("userid")
        if userid is None:
            return []
        raw_courses = self._ws_call("core_enrol_get_users_courses", {"userid": userid}) or []
        courses: list[MoodleCourseSummary] = []
        for entry in raw_courses:
            cid = str(entry.get("id", "")).strip()
            if not cid:
                continue
            title = str(entry.get("fullname") or entry.get("shortname") or cid).strip()
            courses.append(
                MoodleCourseSummary(
                    course_id=cid,
                    title=title,
                    url=urljoin(self._config.moodle_base_url, f"course/view.php?id={cid}"),
                    shortname=entry.get("shortname"),
                    subject_hint=title,
                    summary=entry.get("summary"),
                )
            )
        return courses

    def _extract_course_items_rest(self, course: MoodleCourseSummary) -> list[MoodleResourceItem]:
        from datetime import datetime, timezone

        sections = self._ws_call("core_course_get_contents", {"courseid": course.course_id}) or []
        items: list[MoodleResourceItem] = []

        # Optional assignment deadlines via mod_assign_get_assignments
        assign_deadlines: dict[str, str] = {}
        try:
            assign_resp = self._ws_call(
                "mod_assign_get_assignments", {"courseids[0]": course.course_id}
            )
            for c_entry in (assign_resp or {}).get("courses") or []:
                for assign in c_entry.get("assignments") or []:
                    cmid = str(assign.get("cmid") or assign.get("id") or "")
                    duedate = assign.get("duedate")
                    if cmid and isinstance(duedate, (int, float)) and duedate > 0:
                        assign_deadlines[cmid] = (
                            datetime.fromtimestamp(duedate, tz=timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z")
                        )
        except Exception:
            pass

        for sec in sections:
            sec_title = str(sec.get("name") or "General").strip()
            for mod in sec.get("modules") or []:
                cmid = str(mod.get("id") or "")
                modname = str(mod.get("modname") or "resource").lower()
                title = str(mod.get("name") or cmid).strip()
                mod_url = str(
                    mod.get("url")
                    or urljoin(self._config.moodle_base_url, f"mod/{modname}/view.php?id={cmid}")
                )
                if not is_same_origin(mod_url, self._config.moodle_base_url):
                    continue

                contents = mod.get("contents") or []
                if contents:
                    for idx, content in enumerate(contents):
                        file_url = str(content.get("fileurl") or mod_url)
                        if not is_same_origin(file_url, self._config.moodle_base_url):
                            continue
                        fname = content.get("filename") or title
                        timemod = content.get("timemodified")
                        mod_iso = (
                            datetime.fromtimestamp(timemod, tz=timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z")
                            if isinstance(timemod, (int, float)) and timemod > 0
                            else None
                        )
                        items.append(
                            MoodleResourceItem(
                                course_id=course.course_id,
                                course_title=course.title,
                                section_title=sec_title,
                                module_id=f"{cmid}_{idx}" if idx > 0 else cmid,
                                title=str(fname),
                                resource_kind="resource",
                                url=mod_url,
                                download_url=file_url,
                                filename=str(fname),
                                mime_type=content.get("mimetype") or guess_mime_type(str(fname)),
                                description=mod.get("description"),
                                modified_at=mod_iso,
                                remote_version=str(timemod) if timemod else None,
                            )
                        )
                else:
                    rkind = (
                        "assignment"
                        if modname == "assign"
                        else ("announcement" if modname == "forum" else "resource")
                    )
                    items.append(
                        MoodleResourceItem(
                            course_id=course.course_id,
                            course_title=course.title,
                            section_title=sec_title,
                            module_id=cmid,
                            title=title,
                            resource_kind=rkind,
                            url=mod_url,
                            download_url=mod_url if rkind == "resource" else None,
                            description=mod.get("description"),
                            deadline_at=assign_deadlines.get(cmid),
                        )
                    )
        return items

    def _discover_courses_playwright(
        self,
        *,
        interactive: bool = False,
        allow_auto_reauth: bool | None = None,
        max_pages: int = 10,
    ) -> list[MoodleCourseSummary]:
        """Discover enrolled Moodle courses on `my/courses.php` handling JS rendering & pagination.

        Strictly read-only: waits for Moodle's dynamic course overview list, optionally steps
        through pagination controls inside `block_myoverview`, and falls back to `my/` or
        `user/profile.php` only if `my/courses.php` renders no course links.
        """
        my_courses_url = urljoin(self._config.moodle_base_url, "my/courses.php")
        if self._page_fetcher is not None or self._auth._context_factory is not None:
            # Deterministic/injected page fetcher or fake context path
            for rel_path in ("my/courses.php", "my/", "user/profile.php"):
                candidate_url = urljoin(self._config.moodle_base_url, rel_path)
                page_html = self._fetch_html_page(
                    candidate_url,
                    interactive=interactive,
                    allow_auto_reauth=allow_auto_reauth,
                )
                found = parse_moodle_dashboard_html(page_html, self._config.moodle_base_url)
                if found:
                    return found
            return []

        auto_allowed = (
            self._config.ensai_allow_automated_credential_submit
            if allow_auto_reauth is None
            else allow_auto_reauth
        )
        can_reauth = interactive or (auto_allowed and self._credentials.has_ensai_credentials())

        for attempt in range(2):
            profile_dir = self._auth.profile_dir_for(SourceKind.MOODLE)
            courses_by_id: dict[str, MoodleCourseSummary] = {}
            ajax_courses_by_id: dict[str, MoodleCourseSummary] = {}
            with self._auth.open_context(SourceKind.MOODLE, headless=True) as context:
                page = context.pages[0] if context.pages else context.new_page()

                def _on_ajax_response(resp: Any) -> None:
                    try:
                        r_url = str(getattr(resp, "url", "") or "")
                        if not is_same_origin(r_url, self._config.moodle_base_url):
                            return
                        if "lib/ajax/service.php" not in r_url.lower():
                            return
                        if getattr(resp, "status", 0) != 200:
                            return
                        payload = resp.json()
                        for c in parse_moodle_ajax_courses(payload, self._config.moodle_base_url):
                            ajax_courses_by_id[c.course_id] = c
                    except Exception:
                        return

                if hasattr(page, "on"):
                    try:
                        page.on("response", _on_ajax_response)
                    except Exception:
                        pass

                probe = self._auth._probe_on_page(
                    page,
                    SourceKind.MOODLE,
                    profile_dir=profile_dir,
                    allow_myensai_fallback=False,
                    target_url=my_courses_url,
                    context=context,
                    phase="moodle_discover_courses",
                )
                if not probe.authenticated:
                    if not probe.origin_verified:
                        raise PermissionDeniedError(probe.detail)
                    if attempt == 0 and can_reauth:
                        auth_status = self._auth.authenticate(
                            SourceKind.MOODLE,
                            interactive=interactive,
                            target_url=my_courses_url,
                        )
                        if auth_status.authenticated:
                            continue
                    raise AuthenticationRequiredError(probe.detail)

                self._auth._save_session_cookies(context, profile_dir)
                # Wait briefly for Moodle's JS course overview (`block_myoverview`) to populate
                if hasattr(page, "wait_for_selector"):
                    try:
                        page.wait_for_selector('a[href*="/course/view.php?id="]', timeout=8_000)
                    except Exception:
                        pass

                # Read-only core Moodle AJAX query for ALL enrolled courses (classification="all", limit=0)
                # using the page's own authenticated sesskey, bypassing UI pagination & grouping filters.
                if hasattr(page, "evaluate"):
                    try:
                        ajax_all = page.evaluate(
                            """async () => {
                                const sesskey = (window.M && window.M.cfg && window.M.cfg.sesskey) || null;
                                if (!sesskey) return null;
                                const url = `/lib/ajax/service.php?sesskey=${encodeURIComponent(sesskey)}&info=core_course_get_enrolled_courses_by_timeline_classification`;
                                const resp = await fetch(url, {
                                    method: 'POST',
                                    credentials: 'same-origin',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify([{
                                        index: 0,
                                        methodname: 'core_course_get_enrolled_courses_by_timeline_classification',
                                        args: {offset: 0, limit: 0, classification: 'all', sort: 'fullname', customfieldname: '', customfieldvalue: ''}
                                    }])
                                });
                                if (!resp.ok) return null;
                                return await resp.json();
                            }"""
                        )
                        if ajax_all:
                            for c in parse_moodle_ajax_courses(ajax_all, self._config.moodle_base_url):
                                ajax_courses_by_id[c.course_id] = c
                    except Exception:
                        pass

                # Read-only attempt to switch grouping filter to "All"/"Tout" so active courses aren't hidden
                if hasattr(page, "locator"):
                    for trigger_sel, option_sels in (
                        (
                            '.block_myoverview [id*="groupingdropdown"], .block_myoverview button[data-toggle="dropdown"]',
                            (
                                '.block_myoverview a[data-value="all"]',
                                '.block_myoverview a[data-value="allincludinghidden"]',
                                '.block_myoverview a:has-text("Tout")',
                                '.block_myoverview a:has-text("All")',
                            ),
                        ),
                    ):
                        try:
                            trig = page.locator(trigger_sel)
                            if trig.count() > 0 and trig.first.is_visible():
                                trig.first.click(timeout=2_000)
                                for opt_sel in option_sels:
                                    opt = page.locator(opt_sel)
                                    if opt.count() > 0 and opt.first.is_visible():
                                        opt.first.click(timeout=2_000)
                                        self._auth._settle_navigation(page, timeout_ms=4_000)
                                        break
                        except Exception:
                            pass

                # Paginate read-only through all pages of block_myoverview
                for _page_idx in range(max_pages):
                    html = page.content()
                    batch = parse_moodle_dashboard_html(html, self._config.moodle_base_url)
                    added = 0
                    for c in batch:
                        if c.course_id not in courses_by_id:
                            courses_by_id[c.course_id] = c
                            added += 1
                    next_clicked = False
                    if hasattr(page, "locator"):
                        for sel in (
                            '.block_myoverview [data-control="next"]:not(.disabled) a',
                            '.block_myoverview a[aria-label*="Suivant"]',
                            '.block_myoverview a[aria-label*="Next"]',
                        ):
                            try:
                                loc = page.locator(sel)
                                if loc.count() > 0 and loc.first.is_visible():
                                    loc.first.click(timeout=3_000)
                                    self._auth._settle_navigation(page, timeout_ms=4_000)
                                    next_clicked = True
                                    break
                            except Exception:
                                continue
                    if not next_clicked or added == 0:
                        break

                # Merge richer AJAX metadata (category, startdate -> academic_year, shortname)
                merged_courses: dict[str, MoodleCourseSummary] = dict(courses_by_id)
                for cid, ac in ajax_courses_by_id.items():
                    if cid not in merged_courses:
                        merged_courses[cid] = ac
                    else:
                        dom_c = merged_courses[cid]
                        merged_courses[cid] = MoodleCourseSummary(
                            course_id=cid,
                            title=dom_c.title or ac.title,
                            url=dom_c.url or ac.url,
                            shortname=ac.shortname or dom_c.shortname,
                            subject_hint=dom_c.subject_hint or ac.subject_hint,
                            category=ac.category or dom_c.category,
                            summary=ac.summary or dom_c.summary,
                            academic_year=dom_c.academic_year or ac.academic_year,
                            archived=dom_c.archived or ac.archived,
                        )

                if merged_courses:
                    return list(merged_courses.values())

                # Fallback to user/profile.php or my/ ONLY if my/courses.php yielded nothing
                for fallback_rel in ("user/profile.php", "my/"):
                    fb_url = urljoin(self._config.moodle_base_url, fallback_rel)
                    fb_probe = self._auth._probe_on_page(
                        page,
                        SourceKind.MOODLE,
                        profile_dir=profile_dir,
                        allow_myensai_fallback=False,
                        target_url=fb_url,
                        context=context,
                        phase="moodle_discover_fallback",
                    )
                    if fb_probe.authenticated:
                        found = parse_moodle_dashboard_html(
                            page.content(), self._config.moodle_base_url
                        )
                        if found:
                            return found
                return []
        return []

    def discover_courses(
        self,
        *,
        dashboard_html: str | None = None,
        course_ids: list[str] | None = None,
        max_courses: int | None = None,
        academic_year: str | None = None,
        include_archived: bool = False,
        interactive: bool = False,
        allow_auto_reauth: bool | None = None,
    ) -> list[MoodleCourseSummary]:
        """Discover enrolled courses via REST WS (when configured), fixture HTML, or Playwright session."""
        if dashboard_html is None and self._use_rest_mode():
            courses = self._discover_courses_rest()
        elif dashboard_html is None:
            courses = self._discover_courses_playwright(
                interactive=interactive,
                allow_auto_reauth=allow_auto_reauth,
            )
        else:
            courses = parse_moodle_dashboard_html(dashboard_html, self._config.moodle_base_url)

        if course_ids:
            allowed = {str(cid).strip() for cid in course_ids if str(cid).strip()}
            matched = [c for c in courses if c.course_id in allowed]
            matched_ids = {c.course_id for c in matched}
            # Never drop an explicitly requested authorized course ID (e.g. --course-id 505)
            # merely because my/courses.php pagination or grouping filter omitted it; resolve it directly.
            for cid in course_ids:
                scid = str(cid).strip()
                if scid and scid not in matched_ids and scid.isdigit():
                    matched.append(
                        MoodleCourseSummary(
                            course_id=scid,
                            title=f"Course {scid}",
                            url=urljoin(self._config.moodle_base_url, f"course/view.php?id={scid}"),
                            subject_hint=f"Course {scid}",
                        )
                    )
                    matched_ids.add(scid)
            courses = matched
        else:
            if not include_archived:
                courses = [c for c in courses if not c.archived]
            if academic_year:
                norm_yr = academic_year.replace("–", "-").replace("/", "-").strip()
                # Keep courses matching the requested year OR courses with no explicit year metadata
                courses = [
                    c
                    for c in courses
                    if c.academic_year is None or c.academic_year == norm_yr
                ]
        if max_courses is not None and max_courses >= 0:
            courses = courses[:max_courses]
        return courses

    def extract_course_items(
        self,
        course: MoodleCourseSummary,
        *,
        course_html: str | None = None,
        section_html_by_num: dict[str, str] | None = None,
        interactive: bool = False,
        allow_auto_reauth: bool | None = None,
        expand_folders: bool = True,
        max_folders: int = 20,
        max_sections: int = 50,
        stats: dict[str, Any] | None = None,
    ) -> list[MoodleResourceItem]:
        """Traverse one Moodle course via REST WS or HTML extraction (including tab-based sections & nested folders).

        - Discovers tab-based section links (`/course/view.php?id=<cid>&section=<N>`) and traverses
          every accessible section without duplicating or looping.
        - Respects unavailable/disabled sections (`available=False`) and skips them cleanly.
        - Expands nested `/mod/folder/` modules to surface `/pluginfile.php/` files.
        - Extracts embedded HTML educational instructions/code examples even when no file exists.
        """
        if course_html is None and self._use_rest_mode():
            items = self._extract_course_items_rest(course)
        else:
            html = (
                course_html
                if course_html is not None
                else self._fetch_html_page(
                    course.url,
                    interactive=interactive,
                    allow_auto_reauth=allow_auto_reauth,
                )
            )
            new_title = course.title
            if new_title == f"Course {course.course_id}":
                extracted_title = _extract_course_title_from_html(html)
                if extracted_title:
                    new_title = extracted_title
            new_year = course.academic_year or _extract_academic_year_from_course_html(html)
            if new_title != course.title or new_year != course.academic_year:
                course = MoodleCourseSummary(
                    course_id=course.course_id,
                    title=new_title,
                    url=course.url,
                    shortname=course.shortname,
                    subject_hint=new_title,
                    category=course.category,
                    summary=course.summary,
                    academic_year=new_year,
                    archived=course.archived,
                )
            items = list(
                parse_moodle_course_html(
                    html,
                    self._config.moodle_base_url,
                    course,
                    default_section="General",
                    section_url=course.url,
                )
            )
            seen_item_keys: set[str] = {
                f"{it.resource_kind}:{it.module_id}:{it.url}" for it in items
            }

            # Discover and traverse tab-based course sections (e.g. &section=1, &section=2, ...)
            tabs = discover_course_section_tabs(html, self._config.moodle_base_url, course.course_id)
            if stats is not None:
                stats["sections_discovered"] = stats.get("sections_discovered", 0) + max(1, len(tabs))
                stats["sections_traversed"] = stats.get("sections_traversed", 0) + 1  # initial course page

            visited_sections: set[str] = set()
            parsed_root_sec = (parse_qs(urlparse(course.url).query).get("section") or ["0"])[0]
            visited_sections.add(parsed_root_sec)

            sections_fetched = 0
            for tab in tabs:
                if not tab.available:
                    if stats is not None:
                        stats["unavailable_sections_skipped"] = (
                            stats.get("unavailable_sections_skipped", 0) + 1
                        )
                        stats["resources_excluded"] = stats.get("resources_excluded", 0) + 1
                    continue
                if tab.section_num in visited_sections:
                    continue
                if sections_fetched >= max_sections:
                    break
                visited_sections.add(tab.section_num)

                sec_html: str | None = None
                if section_html_by_num is not None:
                    sec_html = section_html_by_num.get(tab.section_num)
                elif course_html is None:
                    try:
                        sec_html = self._fetch_html_page(
                            tab.url,
                            interactive=interactive,
                            allow_auto_reauth=allow_auto_reauth,
                        )
                    except AuthenticationRequiredError:
                        raise
                    except Exception:
                        if stats is not None:
                            stats["failed_resources"] = stats.get("failed_resources", 0) + 1
                        continue

                if sec_html is None:
                    continue
                sections_fetched += 1
                if stats is not None:
                    stats["sections_traversed"] = stats.get("sections_traversed", 0) + 1

                sec_parser = _MoodleCoursePageHTMLParser(
                    self._config.moodle_base_url,
                    course,
                    default_section=tab.title,
                    section_url=tab.url,
                )
                sec_parser.feed(sec_html)
                if stats is not None:
                    stats["resources_excluded"] = (
                        stats.get("resources_excluded", 0) + sec_parser.excluded_links_count
                    )
                sec_items = sec_parser.items
                for sit in sec_items:
                    skey = f"{sit.resource_kind}:{sit.module_id}:{sit.url}"
                    if skey not in seen_item_keys:
                        seen_item_keys.add(skey)
                        items.append(
                            MoodleResourceItem(
                                course_id=sit.course_id,
                                course_title=sit.course_title,
                                section_title=sit.section_title,
                                module_id=sit.module_id,
                                title=sit.title,
                                resource_kind=sit.resource_kind,
                                url=sit.url,
                                download_url=sit.download_url,
                                filename=sit.filename,
                                mime_type=sit.mime_type,
                                description=sit.description,
                                deadline_at=sit.deadline_at,
                                modified_at=sit.modified_at,
                                remote_version=sit.remote_version,
                                section_url=sit.section_url,
                                inline_html=sit.inline_html,
                                section_id=f"section-{tab.section_num}",
                                academic_year=course.academic_year,
                                academic_category=course.category,
                            )
                        )

            if expand_folders and course_html is None:
                expanded: list[MoodleResourceItem] = []
                seen_urls = {it.url for it in items}
                folders_visited = 0
                assigns_visited = 0
                max_assigns = 25
                for it in list(items):
                    if it.resource_kind == "folder" and folders_visited < max_folders:
                        folders_visited += 1
                        if stats is not None:
                            stats["folders_traversed"] = stats.get("folders_traversed", 0) + 1
                        try:
                            folder_html = self._fetch_html_page(
                                it.url,
                                interactive=interactive,
                                allow_auto_reauth=allow_auto_reauth,
                            )
                            nested = parse_moodle_course_html(
                                folder_html,
                                self._config.moodle_base_url,
                                course,
                                default_section=f"{it.section_title}/{it.title}",
                                section_url=it.url,
                            )
                            for sub in nested:
                                if sub.resource_kind == "resource" and sub.url not in seen_urls:
                                    seen_urls.add(sub.url)
                                    expanded.append(
                                        MoodleResourceItem(
                                            course_id=sub.course_id,
                                            course_title=sub.course_title,
                                            section_title=f"{it.section_title}/{it.title}",
                                            module_id=sub.module_id,
                                            title=sub.title,
                                            resource_kind=sub.resource_kind,
                                            url=sub.url,
                                            download_url=sub.download_url,
                                            filename=sub.filename,
                                            mime_type=sub.mime_type,
                                            description=sub.description,
                                            deadline_at=sub.deadline_at,
                                            modified_at=sub.modified_at,
                                            remote_version=sub.remote_version,
                                            section_url=it.section_url,
                                            section_id=it.section_id,
                                            academic_year=course.academic_year,
                                            academic_category=course.category,
                                        )
                                    )
                        except AuthenticationRequiredError:
                            raise
                        except Exception:
                            if stats is not None:
                                stats["failed_resources"] = stats.get("failed_resources", 0) + 1
                    elif (
                        it.resource_kind == "assignment"
                        and assigns_visited < max_assigns
                        and "/mod/assign/view.php" in urlparse(it.url).path.lower()
                        and not any(bad in it.url.lower() for bad in _FORBIDDEN_MOODLE_PATHS)
                    ):
                        assigns_visited += 1
                        try:
                            assign_html = self._fetch_html_page(
                                it.url,
                                interactive=interactive,
                                allow_auto_reauth=allow_auto_reauth,
                            )
                            nested_assign = parse_moodle_course_html(
                                assign_html,
                                self._config.moodle_base_url,
                                course,
                                default_section=f"{it.section_title}/{it.title}",
                                section_url=it.url,
                            )
                            for sub in nested_assign:
                                if (
                                    sub.resource_kind == "resource"
                                    and "/pluginfile.php/" in urlparse(sub.url).path.lower()
                                    and sub.url not in seen_urls
                                    and not any(seg in sub.url.lower() for seg in _EXCLUDED_PLUGINFILE_SEGMENTS)
                                ):
                                    seen_urls.add(sub.url)
                                    expanded.append(
                                        MoodleResourceItem(
                                            course_id=sub.course_id,
                                            course_title=sub.course_title,
                                            section_title=f"{it.section_title}/{it.title}",
                                            module_id=sub.module_id,
                                            title=sub.title,
                                            resource_kind="resource",
                                            url=sub.url,
                                            download_url=sub.download_url or sub.url,
                                            filename=sub.filename,
                                            mime_type=sub.mime_type,
                                            description=sub.description,
                                            deadline_at=sub.deadline_at,
                                            modified_at=sub.modified_at,
                                            remote_version=sub.remote_version,
                                            section_url=it.section_url,
                                            section_id=it.section_id,
                                            academic_year=course.academic_year,
                                            academic_category=course.category,
                                        )
                                    )
                        except AuthenticationRequiredError as exc:
                            if "cas" in str(exc).lower() or "redirected" in str(exc).lower():
                                raise
                        except Exception:
                            pass
                items = [*items, *expanded]
        enriched: list[MoodleResourceItem] = []
        for item in items:
            deadline = item.deadline_at
            if item.resource_kind == "assignment" and not deadline:
                deadline = extract_assignment_deadline(f"{item.title} {item.description or ''}")
            enriched.append(
                MoodleResourceItem(
                    course_id=item.course_id,
                    course_title=item.course_title,
                    section_title=item.section_title,
                    module_id=item.module_id,
                    title=item.title,
                    resource_kind=item.resource_kind,
                    url=item.url,
                    download_url=item.download_url,
                    filename=item.filename,
                    mime_type=item.mime_type,
                    description=item.description,
                    deadline_at=deadline,
                    modified_at=item.modified_at,
                    remote_version=item.remote_version,
                    section_url=item.section_url,
                    inline_html=item.inline_html,
                    section_id=item.section_id,
                    academic_year=item.academic_year or course.academic_year,
                    academic_category=item.academic_category or course.category,
                )
            )
        return enriched

    def normalize(self, raw_item: dict[str, Any]) -> SourceRecord:
        """Convert a MoodleResourceItem dict or WS payload into a canonical SourceRecord."""
        course_id = str(raw_item["course_id"])
        module_id = str(raw_item["module_id"])
        kind = str(raw_item.get("resource_kind", "resource"))
        external_id = f"moodle:course:{course_id}:{kind}:{module_id}"
        record_kind = (
            "assignment"
            if kind == "assignment"
            else ("announcement" if kind == "announcement" else "document")
        )
        folder_path = f"Moodle/{raw_item.get('course_title', course_id)}/{raw_item.get('section_title', 'General')}"
        remote_version = raw_item.get("remote_version") or raw_item.get("modified_at")
        if not remote_version:
            fingerprint = f"{external_id}|{raw_item.get('title','')}|{raw_item.get('url','')}"
            remote_version = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]

        return SourceRecord(
            source_kind=SourceKind.MOODLE,
            external_id=external_id,
            title=str(raw_item["title"]),
            record_kind=record_kind,
            remote_version=str(remote_version),
            remote_modified_at=raw_item.get("modified_at"),
            mime_type=raw_item.get("mime_type"),
            folder_path=folder_path,
            course_id=course_id,
            course_title=raw_item.get("course_title"),
            section_title=raw_item.get("section_title"),
            source_url=raw_item.get("url"),
            access_status=AccessStatus.ACTIVE,
            download_url=raw_item.get("download_url"),
            payload={
                "resource_kind": kind,
                "filename": raw_item.get("filename"),
                "description": raw_item.get("description"),
                "deadline_at": raw_item.get("deadline_at"),
                "section_url": raw_item.get("section_url"),
                "section_id": raw_item.get("section_id"),
                "academic_year": raw_item.get("academic_year"),
                "academic_category": raw_item.get("academic_category"),
                "inline_html": raw_item.get("inline_html"),
            },
            provenance={
                "source": SourceKind.MOODLE.value,
                "course_id": course_id,
                "module_id": module_id,
                "section_url": raw_item.get("section_url"),
                "academic_year": raw_item.get("academic_year"),
                "retrieved_at": utc_now_iso(),
            },
        )

    def list_changes(
        self,
        scope: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> ChangePage:
        """Discover Moodle resources with pagination, tab traversal, selective sync, and rich stats.

        Supported scope keys:
        - course_ids: list[str] (selective sync)
        - single_course_first: bool (defaults to False unless requested)
        - academic_year: str | None (optional year filter e.g. '2026-2027')
        - include_archived: bool (default False)
        - page_size: int (number of courses per page, default 5)
        - known_versions: dict[external_id, remote_version] (incremental skip)
        - dashboard_html: str (fixture testing)
        - course_html_by_id: dict[course_id, str] (fixture testing)
        - section_html_by_course: dict[course_id, dict[section_num, str]] (fixture testing)
        """
        opts = scope or {}
        course_ids = opts.get("course_ids")
        single_course_first = bool(opts.get("single_course_first", False))
        academic_year: str | None = opts.get("academic_year")
        include_archived = bool(opts.get("include_archived", False))
        interactive = bool(opts.get("interactive", False))
        allow_auto_reauth: bool | None = opts.get("allow_auto_reauth")
        page_size = max(1, int(opts.get("page_size", 5)))
        known_versions: dict[str, str] = dict(opts.get("known_versions") or {})
        dashboard_html: str | None = opts.get("dashboard_html")
        course_html_by_id: dict[str, str] = dict(opts.get("course_html_by_id") or {})
        section_html_by_course: dict[str, dict[str, str]] = dict(
            opts.get("section_html_by_course") or {}
        )

        all_discovered = self.discover_courses(
            dashboard_html=dashboard_html,
            course_ids=course_ids,
            max_courses=None,
            academic_year=academic_year,
            include_archived=include_archived,
            interactive=interactive,
            allow_auto_reauth=allow_auto_reauth,
        )
        max_courses = 1 if (single_course_first and not course_ids) else opts.get("max_courses")
        courses = all_discovered[:max_courses] if (max_courses is not None and max_courses >= 0) else all_discovered

        offset = int(cursor) if (cursor and cursor.isdigit()) else 0
        page_courses = courses[offset : offset + page_size]
        next_offset = offset + len(page_courses)
        complete = next_offset >= len(courses)
        next_cursor = None if complete else str(next_offset)

        records: list[SourceRecord] = []
        warnings: list[str] = []
        errors: list[str] = []
        stats: dict[str, Any] = {
            "courses_discovered": len(all_discovered),
            "courses_traversed": 0,
            "sections_discovered": 0,
            "sections_traversed": 0,
            "folders_traversed": 0,
            "unavailable_sections_skipped": 0,
            "resources_excluded": 0,
            "resources_discovered": 0,
            "html_pages_extracted": 0,
            "formats_by_extension": {},
            "failed_resources": 0,
        }

        for course in page_courses:
            try:
                html_override = course_html_by_id.get(course.course_id)
                sec_map = section_html_by_course.get(course.course_id)
                items = self.extract_course_items(
                    course,
                    course_html=html_override,
                    section_html_by_num=sec_map,
                    interactive=interactive,
                    allow_auto_reauth=allow_auto_reauth,
                    stats=stats,
                )
                stats["courses_traversed"] += 1
                for item in items:
                    rec = self.normalize(
                        {
                            "course_id": item.course_id,
                            "course_title": item.course_title,
                            "section_title": item.section_title,
                            "module_id": item.module_id,
                            "title": item.title,
                            "resource_kind": item.resource_kind,
                            "url": item.url,
                            "download_url": item.download_url,
                            "filename": item.filename,
                            "mime_type": item.mime_type,
                            "description": item.description,
                            "deadline_at": item.deadline_at,
                            "modified_at": item.modified_at,
                            "remote_version": item.remote_version,
                            "section_url": item.section_url,
                            "section_id": item.section_id,
                            "academic_year": item.academic_year,
                            "academic_category": item.academic_category,
                            "inline_html": item.inline_html,
                        }
                    )
                    if (
                        rec.external_id in known_versions
                        and known_versions[rec.external_id] == rec.remote_version
                    ):
                        continue
                    records.append(rec)
                    stats["resources_discovered"] += 1
                    if item.resource_kind in ("html_section", "page"):
                        stats["html_pages_extracted"] += 1
                    ext = ""
                    if item.filename and "." in item.filename:
                        ext = "." + item.filename.rsplit(".", 1)[-1].lower()
                    elif item.resource_kind in ("html_section", "page"):
                        ext = ".html"
                    else:
                        ext = f"<{item.resource_kind}>"
                    fmt_counts = stats["formats_by_extension"]
                    fmt_counts[ext] = fmt_counts.get(ext, 0) + 1
            except AuthenticationRequiredError:
                raise
            except Exception as exc:
                stats["failed_resources"] += 1
                errors.append(
                    redact_sensitive_text(
                        f"Recoverable error extracting Moodle course {course.course_id}: {exc}"
                    )
                )

        if single_course_first and not course_ids and len(all_discovered) > 1:
            warnings.append(
                f"Single-course safety limit active ({len(all_discovered)} authorized courses discovered); "
                "pass course_ids or --all-courses to expand."
            )
        elif single_course_first and not course_ids:
            warnings.append(
                "Single-course safety limit active; pass course_ids or single_course_first=False to expand."
            )

        self.last_stats = stats
        return ChangePage(
            items=tuple(records),
            next_cursor=next_cursor,
            complete=complete,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    def _stream_download_rest(self, url: str, fallback_filename: str) -> tuple[bytes, str, str]:
        token = self._credentials.get_moodle_ws_token()
        if not token:
            raise AuthenticationRequiredError("Moodle Web Services token missing.")

        def _op() -> tuple[bytes, str, str]:
            req_params = None if "token=" in urlparse(url).query else {"token": token}
            resp = self._guarded_http_get(url, params=req_params, stream=True)
            status = getattr(resp, "status_code", 200)
            if status == 401:
                raise AuthenticationRequiredError("Moodle download returned HTTP 401.")
            if status == 403:
                raise PermissionDeniedError("Moodle download returned HTTP 403.")
            if status in (429, 500, 502, 503, 504):
                raise TransientSourceError(f"Moodle transient HTTP {status}", status_code=status)
            if status >= 400:
                raise RuntimeError(f"Moodle download failed with HTTP {status}")

            headers = {k.lower(): v for k, v in (getattr(resp, "headers", None) or {}).items()}
            cl_raw = headers.get("content-length")
            if cl_raw and str(cl_raw).isdigit() and int(cl_raw) > self._config.max_download_bytes:
                raise DownloadSizeLimitError(
                    f"Moodle file exceeds configured size limit ({int(cl_raw)} > {self._config.max_download_bytes}).",
                    size_bytes=int(cl_raw),
                    limit_bytes=self._config.max_download_bytes,
                )

            buf = bytearray()
            if hasattr(resp, "iter_content"):
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    if len(buf) + len(chunk) > self._config.max_download_bytes:
                        raise DownloadSizeLimitError(
                            f"Moodle streamed download exceeded limit ({self._config.max_download_bytes} bytes).",
                            size_bytes=len(buf) + len(chunk),
                            limit_bytes=self._config.max_download_bytes,
                        )
                    buf.extend(chunk)
            else:
                raw = getattr(resp, "content", b"")
                if len(raw) > self._config.max_download_bytes:
                    raise DownloadSizeLimitError(
                        f"Moodle file exceeds configured size limit ({len(raw)} > {self._config.max_download_bytes}).",
                        size_bytes=len(raw),
                        limit_bytes=self._config.max_download_bytes,
                    )
                buf.extend(raw)

            mime = (headers.get("content-type") or "").split(";")[0].strip()
            return bytes(buf), fallback_filename, mime or guess_mime_type(fallback_filename)

        return retry_with_backoff(_op, max_retries=self._config.max_retries)

    @staticmethod
    def _filename_from_content_disposition(header_val: str | None) -> str | None:
        if not header_val:
            return None
        m_star = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;\r\n]+)", header_val, re.IGNORECASE)
        if m_star:
            name = unquote(m_star.group(1).strip().strip('"'))
            return name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or None
        m_plain = re.search(r'filename\s*=\s*"?([^";\r\n]+)"?', header_val, re.IGNORECASE)
        if m_plain:
            name = m_plain.group(1).strip()
            return name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or None
        return None

    def _looks_like_login_html(self, html_text: str, final_url: str) -> bool:
        low_url = final_url.lower()
        if "/login/index.php" in low_url or is_same_origin(final_url, self._config.cas_login_url):
            return True
        low = html_text[:20_000].lower()
        if 'name="password"' in low and ('id="login"' in low or 'class="loginform"' in low or "/cas/login" in low):
            return True
        if "ssocas.ensai.fr/cas/login" in low and "logout.php" not in low:
            return True
        if (
            ("vous n'êtes pas connecté" in low or "you are not logged in" in low)
            and "logout.php" not in low
            and "sesskey" not in low
        ):
            return True
        return False

    def _finalize_downloaded_payload(
        self,
        *,
        content: bytes,
        headers: dict[str, str],
        final_url: str,
        fallback_filename: str,
        resource_kind: str = "resource",
    ) -> tuple[bytes, str, str]:
        mime = (headers.get("content-type") or "").split(";")[0].strip().lower()
        cd_name = self._filename_from_content_disposition(headers.get("content-disposition"))
        url_name = unquote(urlparse(final_url).path.rsplit("/", 1)[-1])
        filename = cd_name or (
            url_name
            if url_name and "." in url_name and not url_name.lower().endswith(".php")
            else fallback_filename
        )
        if filename.lower().endswith(".php"):
            safe_base = re.sub(r"[^a-zA-Z0-9._-]+", "_", fallback_filename).strip("_") or "moodle_resource"
            filename = f"{safe_base}.html" if mime == "text/html" else safe_base

        if mime == "text/html":
            html_text = content.decode("utf-8", errors="replace")
            if self._looks_like_login_html(html_text, final_url):
                raise AuthenticationRequiredError(
                    "Moodle session expired (HTML login page returned instead of resource)."
                )
            path_low = urlparse(final_url).path.lower()
            if resource_kind in ("page", "announcement", "folder") or any(
                seg in path_low for seg in ("/mod/page/", "/mod/book/", "/mod/forum/", "/mod/folder/")
            ):
                cleaned = _extract_moodle_main_html(html_text)
                content = cleaned.encode("utf-8")
                if not filename.lower().endswith((".html", ".htm")):
                    filename = f"{filename}.html"

        if not mime or mime in ("application/octet-stream", "binary/octet-stream", "application/force-download"):
            guessed = guess_mime_type(filename)
            if guessed and guessed != "application/octet-stream":
                mime = guessed
        if not mime:
            mime = guess_mime_type(filename)
        return content, filename, mime

    def _load_persisted_moodle_cookies(self) -> list[dict[str, Any]]:
        """Read allowlisted Moodle cookies from the private 0o600 session snapshot (no browser launch)."""
        import json
        import os

        profile_dir = self._auth.profile_dir_for(SourceKind.MOODLE)
        cookie_path = profile_dir / SESSION_COOKIES_FILENAME
        if not cookie_path.is_file():
            return []
        try:
            fd = os.open(str(cookie_path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return []
        raw_cookies = data.get("cookies", []) if isinstance(data, dict) else []
        moodle_host = urlparse(self._config.moodle_base_url).netloc.lower()
        valid: list[dict[str, Any]] = []
        for c in self._auth._filter_institutional_cookies(raw_cookies):
            dom = str(c.get("domain") or "").lstrip(".").lower()
            if dom and (moodle_host == dom or moodle_host.endswith(f".{dom}")):
                valid.append(c)
        return valid

    def _cookie_session_download_once(
        self,
        url: str,
        fallback_filename: str,
        *,
        resource_kind: str = "resource",
    ) -> tuple[bytes, str, str] | None:
        """Fast authenticated streaming download using persisted Moodle session cookies.

        Returns None if no persisted Moodle cookies are available so caller falls back to Playwright.
        Enforces strict same-origin pre-follow redirect validation and CAS redirect detection.
        """
        cookies = self._load_persisted_moodle_cookies()
        if not cookies:
            return None

        session = requests.Session()
        session.headers.update({"User-Agent": DEFAULT_ENSAI_USER_AGENT})
        for c in cookies:
            session.cookies.set(
                str(c["name"]),
                str(c["value"]),
                domain=str(c.get("domain") or "").lstrip(".") or None,
                path=str(c.get("path") or "/"),
            )

        max_redirects = 6

        def _get_stream(start_url: str) -> tuple[requests.Response, str]:
            current_url = start_url
            resp: requests.Response | None = None
            for hop in range(max_redirects + 1):
                if not is_same_origin(current_url, self._config.moodle_base_url):
                    if is_same_origin(current_url, self._config.cas_login_url):
                        raise AuthenticationRequiredError(
                            "Moodle session expired (redirected to official ENSAI CAS during download)."
                        )
                    raise PermissionDeniedError(
                        f"Blocked cross-origin Moodle download target: {sanitize_url_for_logs(current_url)}"
                    )
                resp = session.get(
                    current_url,
                    timeout=self._config.request_timeout_sec,
                    allow_redirects=False,
                    stream=True,
                )
                status = resp.status_code
                headers = {k.lower(): v for k, v in resp.headers.items()}
                if status in (301, 302, 303, 307, 308):
                    location = headers.get("location")
                    resp.close()
                    if not location:
                        raise RuntimeError(f"Moodle redirect HTTP {status} missing Location header.")
                    next_url = urljoin(current_url, str(location))
                    if is_same_origin(next_url, self._config.cas_login_url):
                        raise AuthenticationRequiredError(
                            "Moodle session expired (redirected to official ENSAI CAS during download)."
                        )
                    if not is_same_origin(next_url, self._config.moodle_base_url):
                        raise PermissionDeniedError(
                            f"Blocked cross-origin redirect before follow: {sanitize_url_for_logs(next_url)}"
                        )
                    if "/login/index.php" in urlparse(next_url).path.lower():
                        raise AuthenticationRequiredError(
                            "Moodle session expired (redirected to Moodle login page during download)."
                        )
                    if hop >= max_redirects:
                        raise RuntimeError("Exceeded maximum Moodle redirect hops.")
                    current_url = next_url
                    continue
                break
            assert resp is not None
            return resp, current_url

        def _read_bounded(resp: requests.Response) -> tuple[bytes, dict[str, str]]:
            try:
                status = resp.status_code
                if status == 401:
                    raise AuthenticationRequiredError("Moodle download returned HTTP 401.")
                if status == 403:
                    raise PermissionDeniedError("Moodle download returned HTTP 403.")
                if status in (429, 500, 502, 503, 504):
                    raise TransientSourceError(f"Moodle transient HTTP {status}", status_code=status)
                if status >= 400:
                    raise RuntimeError(f"Moodle download failed with HTTP {status}")
                headers = {k.lower(): v for k, v in resp.headers.items()}
                cl_raw = headers.get("content-length")
                if cl_raw and str(cl_raw).isdigit() and int(cl_raw) > self._config.max_download_bytes:
                    raise DownloadSizeLimitError(
                        f"Moodle file exceeds configured size limit ({int(cl_raw)} > {self._config.max_download_bytes}).",
                        size_bytes=int(cl_raw),
                        limit_bytes=self._config.max_download_bytes,
                    )
                buf = bytearray()
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    if len(buf) + len(chunk) > self._config.max_download_bytes:
                        raise DownloadSizeLimitError(
                            f"Moodle streamed download exceeded limit ({self._config.max_download_bytes} bytes).",
                            size_bytes=len(buf) + len(chunk),
                            limit_bytes=self._config.max_download_bytes,
                        )
                    buf.extend(chunk)
                return bytes(buf), headers
            finally:
                resp.close()

        try:
            target_url = _with_redirect_param(url) if resource_kind == "resource" else url
            resp, final_url = _get_stream(target_url)
            if resp.status_code >= 400 and target_url != url:
                resp.close()
                resp, final_url = _get_stream(url)
            content, headers = _read_bounded(resp)
            mime = (headers.get("content-type") or "").split(";")[0].strip().lower()
            if mime == "text/html":
                html_text = content.decode("utf-8", errors="replace")
                if self._looks_like_login_html(html_text, final_url):
                    raise AuthenticationRequiredError(
                        "Moodle session expired (HTML login page returned during download)."
                    )
                if "/mod/resource/" in urlparse(final_url).path.lower() or resource_kind == "resource":
                    plugin_url = _extract_moodle_pluginfile_url(
                        html_text, final_url, self._config.moodle_base_url
                    )
                    if plugin_url:
                        resp2, final_url2 = _get_stream(plugin_url)
                        content2, headers2 = _read_bounded(resp2)
                        content, headers, final_url = content2, headers2, final_url2
            return self._finalize_downloaded_payload(
                content=content,
                headers=headers,
                final_url=final_url,
                fallback_filename=fallback_filename,
                resource_kind=resource_kind,
            )
        finally:
            session.close()

    def _browser_download_once(
        self,
        url: str,
        fallback_filename: str,
        *,
        resource_kind: str = "resource",
    ) -> tuple[bytes, str, str]:
        """Perform one bounded Playwright APIRequestContext download with CAS redirect awareness."""
        max_redirects = 6
        with self._auth.open_context(SourceKind.MOODLE, headless=True) as context:
            api_req = getattr(context, "request", None)
            if api_req is None:
                raise RuntimeError("Playwright browser context lacks APIRequestContext.")

            def _get_following_same_origin(start_url: str) -> tuple[Any, str]:
                current_url = start_url
                resp: Any = None
                for hop in range(max_redirects + 1):
                    if not is_same_origin(current_url, self._config.moodle_base_url):
                        if is_same_origin(current_url, self._config.cas_login_url):
                            raise AuthenticationRequiredError(
                                "Moodle session expired (redirected to official ENSAI CAS during download)."
                            )
                        raise PermissionDeniedError(
                            f"Blocked cross-origin Moodle download target: {sanitize_url_for_logs(current_url)}"
                        )
                    try:
                        resp = api_req.get(
                            current_url,
                            timeout=self._config.ensai_timeout_ms,
                            max_redirects=0,
                        )
                    except TypeError:
                        resp = api_req.get(current_url, timeout=self._config.ensai_timeout_ms)
                    status = resp.status
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    if status in (301, 302, 303, 307, 308):
                        location = headers.get("location")
                        if not location:
                            raise RuntimeError(
                                f"Moodle redirect HTTP {status} missing Location header."
                            )
                        next_url = urljoin(current_url, str(location))
                        if is_same_origin(next_url, self._config.cas_login_url):
                            raise AuthenticationRequiredError(
                                "Moodle session expired (redirected to official ENSAI CAS during download)."
                            )
                        if not is_same_origin(next_url, self._config.moodle_base_url):
                            raise PermissionDeniedError(
                                f"Blocked cross-origin redirect before follow: {sanitize_url_for_logs(next_url)}"
                            )
                        if "/login/index.php" in urlparse(next_url).path.lower():
                            raise AuthenticationRequiredError(
                                "Moodle session expired (redirected to Moodle login page during download)."
                            )
                        if hop >= max_redirects:
                            raise RuntimeError("Exceeded maximum Moodle redirect hops.")
                        current_url = next_url
                        continue
                    break

                final_url = getattr(resp, "url", current_url) or current_url
                if is_same_origin(final_url, self._config.cas_login_url):
                    raise AuthenticationRequiredError(
                        "Moodle session expired (redirected to official ENSAI CAS during download)."
                    )
                if not is_same_origin(final_url, self._config.moodle_base_url):
                    raise PermissionDeniedError(
                        f"Blocked cross-origin redirect during Moodle download: {sanitize_url_for_logs(final_url)}"
                    )
                return resp, final_url

            target_url = (
                _with_redirect_param(url)
                if (resource_kind == "resource" and self._auth._context_factory is None)
                else url
            )
            resp, final_url = _get_following_same_origin(target_url)
            if resp.status >= 400 and target_url != url:
                resp, final_url = _get_following_same_origin(url)

            status = resp.status
            if status == 401:
                raise AuthenticationRequiredError("Moodle download returned HTTP 401.")
            if status == 403:
                raise PermissionDeniedError("Moodle download returned HTTP 403.")
            if status in (429, 500, 502, 503, 504):
                raise TransientSourceError(f"Moodle transient HTTP {status}", status_code=status)
            if status >= 400:
                raise RuntimeError(f"Moodle download failed with HTTP {status}")
            headers = {k.lower(): v for k, v in resp.headers.items()}
            cl_raw = headers.get("content-length")
            if cl_raw and str(cl_raw).isdigit() and int(cl_raw) > self._config.max_download_bytes:
                raise DownloadSizeLimitError(
                    f"Moodle file exceeds configured size limit ({int(cl_raw)} > {self._config.max_download_bytes}).",
                    size_bytes=int(cl_raw),
                    limit_bytes=self._config.max_download_bytes,
                )
            content = resp.body()
            mime = (headers.get("content-type") or "").split(";")[0].strip().lower()

            # Unwrap Moodle /mod/resource/view.php HTML wrapper pages pointing to /pluginfile.php/
            if mime == "text/html":
                html_text = content.decode("utf-8", errors="replace")
                if self._looks_like_login_html(html_text, final_url):
                    raise AuthenticationRequiredError(
                        "Moodle session expired (HTML login page returned during download)."
                    )
                if "/mod/resource/" in urlparse(final_url).path.lower() or resource_kind == "resource":
                    plugin_url = _extract_moodle_pluginfile_url(
                        html_text, final_url, self._config.moodle_base_url
                    )
                    if plugin_url:
                        resp, final_url = _get_following_same_origin(plugin_url)
                        if resp.status < 400:
                            headers = {k.lower(): v for k, v in resp.headers.items()}
                            cl_raw = headers.get("content-length")
                            if cl_raw and str(cl_raw).isdigit() and int(cl_raw) > self._config.max_download_bytes:
                                raise DownloadSizeLimitError(
                                    f"Moodle file exceeds configured size limit ({int(cl_raw)} > {self._config.max_download_bytes}).",
                                    size_bytes=int(cl_raw),
                                    limit_bytes=self._config.max_download_bytes,
                                )
                            content = resp.body()

            profile_dir = self._auth.profile_dir_for(SourceKind.MOODLE)
            self._auth._save_session_cookies(context, profile_dir)
            return self._finalize_downloaded_payload(
                content=content,
                headers=headers,
                final_url=final_url,
                fallback_filename=fallback_filename,
                resource_kind=resource_kind,
            )

    def fetch_blob(
        self,
        item: SourceRecord,
        *,
        interactive: bool = False,
        allow_auto_reauth: bool | None = None,
    ) -> FetchResult:
        """Download an authorized Moodle attachment or return embedded HTML educational content."""
        payload = item.payload or {}
        resource_kind = str(payload.get("resource_kind") or "resource")
        inline_html = payload.get("inline_html")
        if isinstance(inline_html, str) and inline_html.strip():
            content = inline_html.encode("utf-8")
            filename = payload.get("filename") or f"{item.external_id.replace(':', '_')}.html"
            return FetchResult(
                external_id=item.external_id,
                content=content,
                content_hash=sha256_bytes(content),
                mime_type="text/html",
                size_bytes=len(content),
                filename=str(filename),
                source_url=item.source_url,
            )

        # Pedagogical external URL pointers are never auto-followed; emit a lightweight HTML reference
        if resource_kind == "url" and not item.download_url:
            safe_title = re.sub(r"[<>]", "", item.title or item.external_id)
            ref_html = (
                f"<html><body><h1>{safe_title}</h1>"
                f"<p>Moodle pedagogical URL reference (external site not auto-fetched).</p>"
                f"</body></html>"
            ).encode("utf-8")
            safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", item.title or item.external_id).strip("_")[:60] or "url_ref"
            return FetchResult(
                external_id=item.external_id,
                content=ref_html,
                content_hash=sha256_bytes(ref_html),
                mime_type="text/html",
                size_bytes=len(ref_html),
                filename=f"{safe_slug}.html",
                source_url=item.source_url,
            )

        url = item.download_url or item.source_url
        if not url:
            raise ValueError(f"Moodle record {item.external_id} has no downloadable URL.")
        if not is_same_origin(url, self._config.moodle_base_url):
            raise PermissionDeniedError(
                f"Blocked cross-origin download attempt: {sanitize_url_for_logs(url)}"
            )
        if any(bad in url.lower() for bad in _FORBIDDEN_MOODLE_PATHS):
            raise PermissionDeniedError(
                f"Blocked forbidden personal/grading Moodle URL: {sanitize_url_for_logs(url)}"
            )

        fallback_filename = (
            payload.get("filename")
            or urlparse(url).path.rsplit("/", 1)[-1]
            or f"{item.external_id}.bin"
        )

        self._rate_limiter.wait()
        if self._blob_fetcher is not None:
            content, filename, mime = retry_with_backoff(
                lambda: self._blob_fetcher(url),
                max_retries=self._config.max_retries,
            )
        elif self._use_rest_mode():
            content, filename, mime = self._stream_download_rest(url, fallback_filename)
        else:
            auto_allowed = (
                self._config.ensai_allow_automated_credential_submit
                if allow_auto_reauth is None
                else allow_auto_reauth
            )

            def _download_with_browser_fallback() -> tuple[bytes, str, str]:
                # Use fast persisted-cookie streaming first in real browser mode; fall back to Playwright on miss/auth challenge
                if self._auth._context_factory is None:
                    try:
                        fast = self._cookie_session_download_once(
                            url, fallback_filename, resource_kind=resource_kind
                        )
                        if fast is not None:
                            return fast
                    except (DownloadSizeLimitError, PermissionDeniedError):
                        raise
                    except AuthenticationRequiredError:
                        # Retry once via Playwright persistent profile in case Chromium cookies are fresher
                        pass
                    except Exception:
                        pass
                return self._browser_download_once(
                    url, fallback_filename, resource_kind=resource_kind
                )

            try:
                content, filename, mime = _download_with_browser_fallback()
            except AuthenticationRequiredError:
                if not (interactive or auto_allowed):
                    raise
                auth_status = self._auth.authenticate(
                    SourceKind.MOODLE,
                    interactive=interactive,
                    target_url=url,
                )
                if not auth_status.authenticated:
                    raise AuthenticationRequiredError(auth_status.detail)
                content, filename, mime = _download_with_browser_fallback()

        if len(content) > self._config.max_download_bytes:
            raise DownloadSizeLimitError(
                f"Moodle file exceeds configured size limit ({len(content)} > {self._config.max_download_bytes}).",
                size_bytes=len(content),
                limit_bytes=self._config.max_download_bytes,
            )

        return FetchResult(
            external_id=item.external_id,
            content=content,
            content_hash=sha256_bytes(content),
            mime_type=mime or guess_mime_type(filename),
            size_bytes=len(content),
            filename=filename,
            source_url=url,
        )

    def disconnect(self) -> AuthStatus:
        return self._auth.disconnect(SourceKind.MOODLE)

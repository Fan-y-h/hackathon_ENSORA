"""Authorized read-only Pamplemousse ENSAI connector (timetable + academic data).

Investigation & acquisition order:
1. Official structured exports / endpoints (ICS, JSON, CSV) when available.
2. Authorized Playwright session extraction via EnsaiAuthAdapter when permitted.

Strict epistemic & academic integrity rules:
- Extract ONLY information actually present to the authenticated user.
- Distinguish missing coefficients/rules (`None` + ValueStatus.MISSING) from confirmed values.
- Never invent grades, coefficients, assessment weights, or official validation rules.
- Never infer a written exam from calendar color alone (e.g., red event = hint only;
  ambiguous events such as `Sout.ID` set `confirmation_required=True`).
- Never implement grade calculation or optimization algorithms here.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter

from ..auth.ensai import (
    DEFAULT_ENSAI_USER_AGENT,
    SESSION_COOKIES_FILENAME,
    EnsaiAuthAdapter,
    sanitize_url_for_logs,
)
from ..config import SourcesRuntimeConfig
from ..contracts import (
    AcademicRecord,
    AccessStatus,
    AuthStatus,
    CalendarEventRecord,
    CalendarEventType,
    ChangePage,
    ConnectorCapabilities,
    FetchResult,
    SourceKind,
    SourceRecord,
    ValueStatus,
    utc_now_iso,
)
from .base import (
    AuthenticationRequiredError,
    PermissionDeniedError,
    RateLimiter,
    SourceConnector,
    is_same_origin,
    retry_with_backoff,
)

PARIS_TZ = ZoneInfo("Europe/Paris")

_EXAM_KEYWORDS = ("partiel", "examen", "exam", "contrôle", "controle", "ds ", "épreuve", "epreuve")
_PRESENTATION_KEYWORDS = ("sout.", "soutenance", "oral", "présentation", "presentation", "défense")
_PROJECT_KEYWORDS = ("projet", "project", "statapp", "fil rouge")
_SPORT_KEYWORDS = ("sport", "eps", "bds", "gymnase", "natation")
_TP_KEYWORDS = ("tp ", "tp-", "travaux pratiques", "lab", "atelier")
_TD_KEYWORDS = ("td ", "td-", "travaux dirigés", "travaux diriges", "tutorial")
_CM_KEYWORDS = ("cm ", "cm-", "cours", "lecture", "amphi", "conférence", "conference", "séminaire", "seminaire", "anglais", "espagnol", "allemand", "langue")

# Strictly read-only Pamplemousse safety blocklist (never click/follow destructive or admin forms)
_FORBIDDEN_PAMPLEMOUSSE_KEYWORDS: tuple[str, ...] = (
    "logout",
    "deconnexion",
    "déconnexion",
    "disconnect",
    "evaluat",
    "évaluat",
    "sondage",
    "paiement",
    "payment",
    "profil",
    "password",
    "motdepasse",
    "supprim",
    "delete",
    "valider_eval",
)

# Observed ENSAI Pamplemousse (Leos) read-only page query paths (fallbacks if anchor discovery differs)
PAMPLEMOUSSE_EDT_PATH = "index.php?p=121"
PAMPLEMOUSSE_INSCRIPTIONS_PATH = "index.php?p=107"
PAMPLEMOUSSE_NOTES_PATH = "index.php?p=1052"
_ACADEMIC_YEAR_RE = re.compile(r"\b(20\d{2})\s*[-/–]\s*(20\d{2})\b")
_SUBJECT_CODE_RE = re.compile(
    r"\b("
    r"[1-3]A[A-Z]{2,5}\d{2,4}(?:[-_][A-Z0-9]+)*"
    r"|[1-3]A[-_][A-Z]{2,}(?:[-_][A-Z0-9]+)+"
    r"|[1-3]A[-_][A-Z]{2,}\d{2,}"
    r"|UE\d+[-_]?[A-Z0-9]*"
    r"|[A-Z]{2,5}\d{2,4}[A-Z]?"
    r")\b"
)


def _extract_subject_code(title: str, description: str | None = None) -> str | None:
    """Extract an ENSAI/Pamplemousse course code, preferring the title over description text."""
    m = _SUBJECT_CODE_RE.search(title or "")
    if m:
        return m.group(1)
    if description:
        # Exclude group/promo metadata lines so student group codes (e.g. 2A-G1) are not misread as subjects
        cleaned_lines = [
            line for line in description.splitlines()
            if not re.match(r"^\s*(?:groupe|group|promo|fili[eè]re)\s*[:\-]", line, re.IGNORECASE)
        ]
        m_desc = _SUBJECT_CODE_RE.search("\n".join(cleaned_lines))
        if m_desc:
            return m_desc.group(1)
    return None


def normalize_timestamp_utc(
    raw_value: str | datetime | None,
    *,
    default_tz: ZoneInfo = PARIS_TZ,
) -> tuple[str | None, str | None]:
    """Normalize diverse ENSAI/French/ISO timestamps to ISO 8601 UTC + original tz name."""
    if raw_value is None:
        return None, None
    if isinstance(raw_value, datetime):
        dt = raw_value
        tz_name = str(dt.tzinfo) if dt.tzinfo else str(default_tz)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), tz_name

    text = str(raw_value).strip()
    if not text:
        return None, None

    # ISO 8601 with trailing Z or explicit offset
    iso_candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso_candidate)
        tz_name = str(dt.tzinfo) if dt.tzinfo else str(default_tz)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), tz_name
    except ValueError:
        pass

    # French DD/MM/YYYY HH:MM[:SS]
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=default_tz)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), str(default_tz)
        except ValueError:
            continue

    return None, None


def classify_timetable_event(
    title: str,
    *,
    category_hint: str | None = None,
    color_hint: str | None = None,
    group: str | None = None,
) -> tuple[CalendarEventType, bool, str | None]:
    """Classify timetable event type and flag ambiguities per docs/DATA_ENGINEERING.md.

    Never assumes red calendar color = confirmed written exam.
    Flags `Sout.ID` / presentations or multi-slot ambiguous events for confirmation.
    """
    low_title = f" {title.lower()} "
    low_cat = f" {(category_hint or '').lower()} "
    combined = f"{low_title} {low_cat}"

    confirmation_required = False
    reason: str | None = None

    if any(k in combined for k in _PRESENTATION_KEYWORDS):
        event_type = CalendarEventType.PRESENTATION
        confirmation_required = True
        reason = "Presentation/defense slot detected (e.g. Sout.); confirm personal group/slot."
    elif any(k in combined for k in _EXAM_KEYWORDS):
        event_type = CalendarEventType.EXAM
        if not group:
            confirmation_required = True
            reason = "Assessment detected without explicit student group; confirm scope and slot."
    elif any(k in combined for k in _PROJECT_KEYWORDS):
        event_type = CalendarEventType.PROJECT
    elif any(k in combined for k in _SPORT_KEYWORDS):
        event_type = CalendarEventType.SPORT
    elif any(k in combined for k in _TP_KEYWORDS):
        event_type = CalendarEventType.TP
    elif any(k in combined for k in _TD_KEYWORDS):
        event_type = CalendarEventType.TD
    elif any(k in combined for k in _CM_KEYWORDS):
        event_type = CalendarEventType.CLASS
    else:
        event_type = CalendarEventType.OTHER
        # Do not flag ordinary scheduled Pamplemousse classes lacking a CM/TD prefix as ambiguous;
        # reserve confirmation_required for presentations, ungrouped exams, and red-color hints.

    # Red color hint is explicitly NOT proof of a written exam (S2 PPDP requirement)
    if color_hint and color_hint.strip().lower() in {"red", "#ff0000", "#e53935", "rouge", "danger"}:
        if event_type not in (CalendarEventType.EXAM, CalendarEventType.PRESENTATION):
            confirmation_required = True
            reason = (
                "Event highlighted in red without explicit exam label; "
                "color is a hint only — user confirmation required."
            )
        elif not confirmation_required:
            confirmation_required = True
            reason = "Highlighted assessment event; confirm official format and student slot."

    return event_type, confirmation_required, reason


def parse_decimal_strict(value: Any) -> Decimal | None:
    """Parse a Decimal strictly from string/int/float (French comma supported); returns None for missing."""
    if value is None:
        return None
    text = (
        str(value)
        .strip()
        .replace("\xa0", "")
        .replace("\u202f", "")
        .replace("\u2009", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    if not text or text.lower() in {
        "none",
        "null",
        "na",
        "n/a",
        "n.c.",
        "n/c",
        "-",
        "--",
        "—",
        "–",
        "...",
        "absent",
        "abs",
        "abi",
        "abj",
        "def",
        "déf",
        "disp",
        "exc",
        "nd",
        "nc",
        "nonévalué",
        "nonevalue",
        "nonnoté",
        "nonnote",
        "sansnote",
        "enattente",
        "verrouillé",
        "verrouille",
    }:
        return None
    # Strip optional trailing scale suffix like "16.5/20" -> only accept numeric numerator if clean
    if "/" in text:
        parts = text.split("/", 1)
        text = parts[0].strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_coefficient_cell(value: Any) -> tuple[Decimal | None, str | None]:
    """Parse a Pamplemousse coefficient cell strictly, distinguishing raw coefficients from percentages.

    - Supports French decimal commas ('2,5'), narrow no-break spaces, optional 'coef.'/'x' prefix.
    - Explicit percentages ('25%', '33,5 %') convert to Decimal fractions in [0, 1] with mode='fraction'.
    - Zero ('0', '0,0') is preserved as confirmed Decimal('0'), never conflated with None (missing).
    - Negative or non-numeric values return (None, None) without fabricating data.
    """
    if value is None:
        return None, None
    raw = str(value).strip().replace("\xa0", " ").replace("\u202f", " ").replace("\u2009", " ").strip()
    if not raw:
        return None, None
    if "%" in raw:
        num_part = raw.replace("%", "").strip()
        val = parse_decimal_strict(num_part)
        if val is None or val < Decimal("0") or val > Decimal("100"):
            return None, None
        return (val / Decimal("100")).normalize(), "fraction"
    cleaned = re.sub(r"^(?:coeff?\.?|x|×)\s*", "", raw, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*(?:pts?|points?)$", "", cleaned, flags=re.IGNORECASE).strip()
    val = parse_decimal_strict(cleaned)
    if val is None or val < Decimal("0"):
        return None, None
    return val, "raw"


def _parse_ics_duration(raw: str) -> timedelta | None:
    """Parse RFC 5545 DURATION (e.g. PT1H30M, PT90M, P1D) into a positive timedelta."""
    m = re.fullmatch(
        r"\+?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?",
        raw.strip().upper(),
    )
    if not m:
        return None
    weeks, days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    td = timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    return td if td > timedelta(0) else None


def _compute_duration_minutes(start_utc: str | None, end_utc: str | None) -> int | None:
    if not start_utc or not end_utc:
        return None
    try:
        s = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
        mins = int(round((e - s).total_seconds() / 60.0))
        return mins if mins >= 0 else None
    except ValueError:
        return None


def _unfold_ics_lines(ics_text: str) -> list[str]:
    """RFC 5545 line unfolding (CRLF/LF followed by single space or tab)."""
    raw_lines = ics_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded: list[str] = []
    for line in raw_lines:
        if (line.startswith(" ") or line.startswith("\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _parse_ics_datetime(raw_value: str, params: dict[str, str]) -> tuple[str | None, str | None, str | None]:
    """Return (iso_utc, all_day_date, tz_name) from an ICS DTSTART/DTEND value."""
    val = raw_value.strip()
    tzid = params.get("TZID")
    tz = PARIS_TZ
    if tzid:
        try:
            tz = ZoneInfo(tzid)
        except Exception:
            tz = PARIS_TZ

    if params.get("VALUE", "").upper() == "DATE" or (len(val) == 8 and val.isdigit()):
        d = date(int(val[0:4]), int(val[4:6]), int(val[6:8]))
        return None, d.isoformat(), tzid or str(tz)

    if val.endswith("Z") and len(val) >= 15:
        dt = datetime.strptime(val[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z"), None, "UTC"

    if "T" in val and len(val) >= 15:
        dt = datetime.strptime(val[:15], "%Y%m%dT%H%M%S").replace(tzinfo=tz)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), None, str(tz)

    iso_utc, tz_name = normalize_timestamp_utc(val, default_tz=tz)
    return iso_utc, None, tz_name


def _academic_year_from_event_time(
    start_utc: str | None,
    all_day_date: str | None = None,
    fallback_year: str | None = None,
) -> str | None:
    """Derive source-verified ENSAI academic year (Sept–Aug) from an event's actual date/timestamp."""
    raw = start_utc or all_day_date
    if raw:
        try:
            if len(raw) == 10:
                d = date.fromisoformat(raw)
                y, m = d.year, d.month
            else:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(PARIS_TZ)
                y, m = dt.year, dt.month
            start_y = y if m >= 9 else y - 1
            return f"{start_y}-{start_y + 1}"
        except ValueError:
            pass
    return fallback_year


def parse_ics_events(
    ics_text: str,
    *,
    source_kind: SourceKind = SourceKind.PAMPLEMOUSSE,
    academic_year: str | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> list[CalendarEventRecord]:
    """Pure-Python RFC 5545 ICS parser with recurrence expansion & cancellation handling."""
    lines = _unfold_ics_lines(ics_text)
    events: list[CalendarEventRecord] = []
    in_event = False
    props: dict[str, tuple[str, dict[str, str]]] = {}
    exdates: set[str] = set()

    def flush_event() -> None:
        if not props:
            return
        summary = _unescape_ics(props.get("SUMMARY", ("", {}))[0]).strip()
        uid = props.get("UID", ("", {}))[0].strip()
        if not summary and not uid:
            return
        location = _unescape_ics(props.get("LOCATION", ("", {}))[0]).strip() or None
        description = _unescape_ics(props.get("DESCRIPTION", ("", {}))[0]).strip() or None
        categories = _unescape_ics(props.get("CATEGORIES", ("", {}))[0]).strip() or None
        status_raw = props.get("STATUS", ("CONFIRMED", {}))[0].strip().lower()
        is_cancelled_label = "annul" in summary.lower() or "annul" in (categories or "").lower()
        status = (
            "cancelled"
            if (status_raw == "cancelled" or is_cancelled_label)
            else ("tentative" if status_raw == "tentative" else "confirmed")
        )
        rrule = props.get("RRULE", ("", {}))[0].strip() or None

        dtstart_val, dtstart_params = props.get("DTSTART", ("", {}))
        dtend_val, dtend_params = props.get("DTEND", ("", {}))
        duration_val = props.get("DURATION", ("", {}))[0].strip()
        start_utc, all_day_start, tz_name = (
            _parse_ics_datetime(dtstart_val, dtstart_params) if dtstart_val else (None, None, None)
        )
        end_utc, all_day_end, _ = (
            _parse_ics_datetime(dtend_val, dtend_params) if dtend_val else (None, None, None)
        )
        if start_utc and not end_utc and duration_val:
            dur_td = _parse_ics_duration(duration_val)
            if dur_td is not None:
                st_dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
                end_utc = (st_dt + dur_td).isoformat().replace("+00:00", "Z")

        teacher, group, subject_hint = _extract_pamplemousse_metadata(summary, description)
        course_code = _extract_subject_code(summary, description)
        if source_kind == SourceKind.PAMPLEMOUSSE:
            ev_type, confirm_req, confirm_reason = classify_timetable_event(
                summary,
                category_hint=categories,
                group=group,
            )
        else:
            ev_type = CalendarEventType.PERSONAL
            confirm_req = False
            confirm_reason = None

        base_uid = uid or hashlib.sha1(f"{summary}|{start_utc or all_day_start}".encode("utf-8")).hexdigest()[:16]

        instances: list[tuple[str | None, str | None, str | None, str | None, str]] = [
            (start_utc, end_utc, all_day_start, all_day_end, base_uid)
        ]

        if rrule and start_utc:
            instances = _expand_simple_rrule(
                base_uid=base_uid,
                start_utc=start_utc,
                end_utc=end_utc,
                rrule=rrule,
                exdates=exdates,
                range_end=range_end,
            )

        for inst_start, inst_end, inst_all_day, inst_all_day_end, inst_id in instances:
            if inst_start and range_start:
                st_dt = datetime.fromisoformat(inst_start.replace("Z", "+00:00"))
                if st_dt < range_start:
                    continue
            if inst_start and range_end:
                st_dt = datetime.fromisoformat(inst_start.replace("Z", "+00:00"))
                if st_dt > range_end:
                    continue

            ev_year = (
                _academic_year_from_event_time(inst_start, inst_all_day, fallback_year=academic_year)
                if source_kind == SourceKind.PAMPLEMOUSSE
                else None
            )
            ev_prov: dict[str, Any] = {
                "format": "ics",
                "uid": base_uid,
                "duration_minutes": _compute_duration_minutes(inst_start, inst_end),
                "retrieved_at": utc_now_iso(),
            }
            if ev_year:
                ev_prov["academic_year"] = ev_year

            events.append(
                CalendarEventRecord(
                    source_kind=source_kind,
                    external_id=f"{source_kind.value}:event:{inst_id}",
                    title=summary or "(Untitled event)",
                    event_type=ev_type,
                    start_at=inst_start,
                    end_at=inst_end,
                    all_day_date=inst_all_day,
                    all_day_end_date=inst_all_day_end,
                    original_timezone=tz_name,
                    status=status,
                    room=location,
                    teacher=teacher,
                    group=group,
                    subject_hint=subject_hint,
                    course_id=course_code,
                    recurrence_key=base_uid if rrule else None,
                    confirmation_required=confirm_req,
                    confirmation_reason=confirm_reason,
                    busy=status != "cancelled",
                    description=description,
                    provenance=ev_prov,
                )
            )

    for line in lines:
        if line == "BEGIN:VEVENT":
            in_event = True
            props = {}
            exdates = set()
            continue
        if line == "END:VEVENT":
            flush_event()
            in_event = False
            props = {}
            exdates = set()
            continue
        if not in_event or ":" not in line:
            continue
        key_part, value = line.split(":", 1)
        segments = key_part.split(";")
        prop_name = segments[0].upper()
        params: dict[str, str] = {}
        for seg in segments[1:]:
            if "=" in seg:
                pk, pv = seg.split("=", 1)
                params[pk.upper()] = pv
        if prop_name == "EXDATE":
            for raw_ex in value.split(","):
                ex_utc, ex_day, _ = _parse_ics_datetime(raw_ex, params)
                if ex_utc:
                    exdates.add(ex_utc)
                elif ex_day:
                    exdates.add(ex_day)
        else:
            props[prop_name] = (value, params)

    return events


def _unescape_ics(text: str) -> str:
    return (
        text.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _expand_simple_rrule(
    *,
    base_uid: str,
    start_utc: str,
    end_utc: str | None,
    rrule: str,
    exdates: set[str],
    range_end: datetime | None = None,
    max_occurrences: int = 120,
) -> list[tuple[str | None, str | None, str | None, str | None, str]]:
    """Expand common DAILY/WEEKLY/MONTHLY RFC 5545 RRULE recurrences safely."""
    parts: dict[str, str] = {}
    for token in rrule.split(";"):
        if "=" in token:
            k, v = token.split("=", 1)
            parts[k.upper()] = v.upper()

    freq = parts.get("FREQ")
    if freq not in {"DAILY", "WEEKLY", "MONTHLY"}:
        return [(start_utc, end_utc, None, None, base_uid)]

    interval = max(1, int(parts.get("INTERVAL", "1")))
    count = min(max_occurrences, int(parts["COUNT"])) if parts.get("COUNT", "").isdigit() else None
    until_dt: datetime | None = None
    if "UNTIL" in parts:
        u_iso, _, _ = _parse_ics_datetime(parts["UNTIL"], {})
        if u_iso:
            until_dt = datetime.fromisoformat(u_iso.replace("Z", "+00:00"))
    if until_dt is None and range_end is not None:
        until_dt = range_end

    start_dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    duration = (
        datetime.fromisoformat(end_utc.replace("Z", "+00:00")) - start_dt
        if end_utc
        else timedelta(hours=1)
    )

    results: list[tuple[str | None, str | None, str | None, str | None, str]] = []
    cur = start_dt
    produced = 0
    limit = count or max_occurrences

    while produced < limit:
        if until_dt is not None and cur > until_dt:
            break
        cur_iso = cur.isoformat().replace("+00:00", "Z")
        if cur_iso not in exdates:
            end_iso = (cur + duration).isoformat().replace("+00:00", "Z")
            inst_id = f"{base_uid}_{cur.strftime('%Y%m%dT%H%M%SZ')}"
            results.append((cur_iso, end_iso, None, None, inst_id))
        produced += 1

        if freq == "DAILY":
            cur = cur + timedelta(days=interval)
        elif freq == "WEEKLY":
            cur = cur + timedelta(weeks=interval)
        elif freq == "MONTHLY":
            month_idx = (cur.month - 1) + interval
            year = cur.year + month_idx // 12
            month = (month_idx % 12) + 1
            day = min(cur.day, 28)
            cur = cur.replace(year=year, month=month, day=day)

    return results or [(start_utc, end_utc, None, None, base_uid)]


def _extract_pamplemousse_metadata(
    summary: str,
    description: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Extract teacher, group, and subject hint from structured Pamplemousse text."""
    teacher: str | None = None
    group: str | None = None
    if description:
        m_teacher = re.search(
            r"(?:enseignant|professeur|prof|teacher|intervenant|responsable|resp|animateur|formateur)\s*[:\-]\s*(.+)",
            description,
            re.IGNORECASE,
        )
        if m_teacher:
            teacher = m_teacher.group(1).strip().splitlines()[0].strip()
        m_group = re.search(r"(?:groupe|group|promo|filière|filiere)\s*[:\-]\s*(.+)", description, re.IGNORECASE)
        if m_group:
            group = m_group.group(1).strip().splitlines()[0].strip()

    # Clean subject hint from leading type prefix like "CM - " or "TD "
    subject_hint = re.sub(
        r"^(?:cm|td|tp|cours|partiel|examen|sout\.?\s*id|soutenance)\s*[:\-]?\s*",
        "",
        summary,
        flags=re.IGNORECASE,
    ).strip() or summary
    return teacher, group, subject_hint


class _PamplemousseHTMLTableParser(HTMLParser):
    """Extracts rows from sanitized Pamplemousse timetable or academic HTML tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_row = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_row_attrs: dict[str, str] = {}
        self._cell_buf: list[str] = []
        self.rows: list[tuple[list[str], dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if low == "tr":
            self._in_row = True
            self._current_row = []
            self._current_row_attrs = attr_map
        elif low in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low in ("td", "th") and self._in_cell:
            self._current_row.append(" ".join("".join(self._cell_buf).split()).strip())
            self._in_cell = False
            self._cell_buf = []
        elif low == "tr" and self._in_row:
            if any(cell for cell in self._current_row):
                self.rows.append((self._current_row, self._current_row_attrs))
            self._in_row = False


def parse_pamplemousse_timetable_html(
    html: str,
    *,
    academic_year: str | None = None,
) -> list[CalendarEventRecord]:
    """Parse timetable entries from sanitized Pamplemousse HTML table or data-* elements."""
    parser = _PamplemousseHTMLTableParser()
    parser.feed(html)
    if len(parser.rows) < 2:
        return []

    headers = [h.lower() for h in parser.rows[0][0]]
    # Require at least one date/time column so academic grade tables (Matière/Note/Coef) are never misparsed as events
    has_time_col = any(
        any(tok in h for tok in ("date", "jour", "début", "debut", "start", "heure", "horaire"))
        for h in headers
    )
    if not has_time_col:
        return []
    events: list[CalendarEventRecord] = []

    def col(row: list[str], *candidates: str) -> str | None:
        for cand in candidates:
            for idx, h in enumerate(headers):
                if cand in h and idx < len(row):
                    val = row[idx].strip()
                    if val:
                        return val
        return None

    for idx, (row, attrs) in enumerate(parser.rows[1:], start=1):
        title = col(row, "matière", "matiere", "cours", "intitul", "title", "subject")
        if not title:
            continue
        date_str = col(row, "date", "jour")
        start_raw = col(row, "début", "debut", "start")
        end_raw = col(row, "fin", "end")
        if date_str and start_raw and len(start_raw) <= 8:
            start_raw = f"{date_str} {start_raw}"
        if date_str and end_raw and len(end_raw) <= 8:
            end_raw = f"{date_str} {end_raw}"

        start_utc, tz_name = normalize_timestamp_utc(start_raw)
        end_utc, _ = normalize_timestamp_utc(end_raw)
        room = col(row, "salle", "room")
        teacher = col(row, "enseignant", "prof", "teacher", "intervenant")
        group = col(row, "groupe", "group")
        cat = col(row, "type", "catégor", "categor")
        course_id = col(row, "code", "id")
        color_hint = attrs.get("data-color") or (
            "red" if "red" in attrs.get("class", "").lower() or "danger" in attrs.get("class", "").lower() else None
        )

        ev_type, confirm_req, confirm_reason = classify_timetable_event(
            title,
            category_hint=cat,
            color_hint=color_hint,
            group=group,
        )
        ext_seed = f"{course_id or ''}|{title}|{start_utc or idx}|{room or ''}"
        ext_id = f"pamplemousse:event:{hashlib.sha1(ext_seed.encode('utf-8')).hexdigest()[:16]}"

        ev_year = _academic_year_from_event_time(start_utc, None, fallback_year=academic_year)
        ev_prov: dict[str, Any] = {
            "format": "html",
            "row": idx,
            "duration_minutes": _compute_duration_minutes(start_utc, end_utc),
            "retrieved_at": utc_now_iso(),
        }
        if ev_year:
            ev_prov["academic_year"] = ev_year
        events.append(
            CalendarEventRecord(
                source_kind=SourceKind.PAMPLEMOUSSE,
                external_id=ext_id,
                title=title,
                event_type=ev_type,
                start_at=start_utc,
                end_at=end_utc,
                original_timezone=tz_name or "Europe/Paris",
                room=room,
                teacher=teacher,
                group=group,
                subject_hint=title,
                course_id=course_id,
                confirmation_required=confirm_req,
                confirmation_reason=confirm_reason,
                provenance=ev_prov,
            )
        )
    return events


class _PamplemousseNavParser(HTMLParser):
    """Discover read-only navigation links on Pamplemousse (`index.php`) by link text & href."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._in_a = False
        self._href: str | None = None
        self._buf: list[str] = []
        self.links: list[tuple[str, str]] = []  # (full_url, label)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        href = attr_map.get("href", "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            return
        full = urljoin(self._base_url, href.replace("&amp;", "&"))
        if not is_same_origin(full, self._base_url):
            return
        self._in_a = True
        self._href = full
        self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._in_a and self._href:
            label = " ".join("".join(self._buf).split()).strip()
            self.links.append((self._href, label))
            self._in_a = False
            self._href = None
            self._buf = []


def extract_pamplemousse_academic_year(html: str) -> str | None:
    """Extract the currently selected academic year (e.g. '2026-2027') from Pamplemousse HTML."""
    # 1. Selected <option> inside year selector
    m_sel = re.search(
        r"<option\b[^>]*\bselected\b[^>]*>([^<]*20\d{2}\s*[-/–]\s*20\d{2}[^<]*)</option>",
        html,
        re.IGNORECASE,
    )
    if m_sel:
        ym = _ACADEMIC_YEAR_RE.search(m_sel.group(1))
        if ym:
            return f"{ym.group(1)}-{ym.group(2)}"
    # 2. Fallback: first academic year token in header/nav snippet
    ym = _ACADEMIC_YEAR_RE.search(html[:8000])
    if ym:
        return f"{ym.group(1)}-{ym.group(2)}"
    return None


def discover_pamplemousse_nav_links(html: str, base_url: str) -> dict[str, str]:
    """Discover read-only Pamplemousse navigation URLs (EDT, iCalendar, Inscriptions, Notes)."""
    parser = _PamplemousseNavParser(base_url)
    parser.feed(html)
    nav: dict[str, str] = {}
    for full_url, label in parser.links:
        low_url = full_url.lower()
        low_lbl = label.lower()
        combined = f"{low_url} {low_lbl}"
        if any(bad in combined for bad in _FORBIDDEN_PAMPLEMOUSSE_KEYWORDS):
            continue
        if "icalendar" not in nav and (
            "icalendar" in low_lbl or "ical" in low_lbl or ".ics" in low_url or "ical" in low_url
        ):
            nav["icalendar"] = full_url
        elif "edt" not in nav and (
            low_lbl in ("edt", "emploi du temps", "planning")
            or "emploi du temps" in low_lbl
            or "p=121" in low_url
            or "planning" in low_url
        ):
            nav["edt"] = full_url
        elif "inscriptions" not in nav and (
            "inscription" in low_lbl or "p=107" in low_url
        ):
            nav["inscriptions"] = full_url
        elif "notes" not in nav and (
            low_lbl in ("notes", "mes notes", "bulletin", "bulletin détaillé", "bulletin detaille", "résultats", "resultats")
            or "note" in low_lbl
            or "bulletin" in low_lbl
            or "résultat" in low_lbl
            or "resultat" in low_lbl
            or "relevé" in low_lbl
            or "releve" in low_lbl
            or "p=1052" in low_url
        ):
            nav["notes"] = full_url
        elif "program" not in nav and (
            "programme" in low_lbl
            or "maquette" in low_lbl
            or "cursus" in low_lbl
            or "scolarit" in low_lbl
        ):
            nav["program"] = full_url
    return nav


def _is_allowed_pamplemousse_ical_url(candidate_url: str, base_url: str) -> bool:
    """Allow same-origin Pamplemousse URLs or official HTTPS *.leos.fr / *.ensai.fr iCalendar endpoints."""
    if is_same_origin(candidate_url, base_url):
        return True
    parsed = urlparse(candidate_url)
    host = parsed.netloc.lower()
    if parsed.scheme.lower() != "https" or not host:
        return False
    return host.endswith(".leos.fr") or host.endswith(".ensai.fr") or host == "ensai.fr"


def extract_ical_feed_urls(html: str, base_url: str) -> list[str]:
    """Extract all authorized Pamplemousse/Leos/ENSAI iCalendar (.ics / webcal) feed URLs from HTML."""
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()

    def _consider(raw_val: str, priority: int) -> None:
        cleaned = raw_val.replace("&amp;", "&").strip().strip("\"'<>")
        if not cleaned or cleaned.startswith("#") or cleaned.lower().startswith("javascript:"):
            return
        if cleaned.lower().startswith("webcal://"):
            cleaned = "https://" + cleaned[len("webcal://") :]
        full = urljoin(base_url, cleaned)
        if full in seen:
            return
        low = full.lower()
        if any(bad in low for bad in _FORBIDDEN_PAMPLEMOUSSE_KEYWORDS):
            return
        if not _is_allowed_pamplemousse_ical_url(full, base_url):
            return
        has_ical_marker = any(
            tok in low for tok in (".ics", "ical", "webcal", "export_edt", "exportedt", "agenda", "ics=")
        )
        if not has_ical_marker:
            return
        seen.add(full)
        score = priority
        if ".ics" in low:
            score -= 10
        if "token=" in low or "key=" in low or "hash=" in low or "uid=" in low:
            score -= 5
        candidates.append((score, full))

    for priority, pattern in (
        (0, r'((?:webcal|https)://[^\s"\'<>]+(?:\.ics|ical)[^\s"\'<>]*)'),
        (1, r'(?:href|value|data-url|data-href|action)\s*=\s*["\']([^"\']+)["\']'),
        (2, r'<(?:textarea|code|pre)\b[^>]*>\s*((?:webcal|https)://[^\s<]+)\s*</(?:textarea|code|pre)>'),
        (3, r"""onclick\s*=\s*["'][^"']*(?:location(?:\.href)?\s*=|window\.open\()\s*['"]([^'"]+)['"]"""),
    ):
        for m in re.findall(pattern, html, flags=re.IGNORECASE | re.DOTALL):
            _consider(m, priority)

    candidates.sort(key=lambda pair: pair[0])
    return [url for _, url in candidates]


def extract_ical_feed_url(html: str, base_url: str) -> str | None:
    """Extract the highest-priority authorized Pamplemousse/Leos/ENSAI iCalendar (.ics) feed URL."""
    urls = extract_ical_feed_urls(html, base_url)
    return urls[0] if urls else None


def extract_inline_fullcalendar_json(html: str) -> list[dict[str, Any]]:
    """Extract inline JS `events: [...]` JSON arrays embedded inside FullCalendar `<script>` blocks."""
    extracted: list[dict[str, Any]] = []
    for script_body in re.findall(r"<script\b[^>]*>(.*?)</script>", html, flags=re.IGNORECASE | re.DOTALL):
        if "events" not in script_body.lower():
            continue
        for m in re.finditer(r"events\s*:\s*(\[\s*\{.*?\}\s*\])", script_body, flags=re.DOTALL):
            blob = m.group(1)
            try:
                parsed = json.loads(blob)
            except Exception:
                # Best-effort trailing comma cleanup for JS object arrays
                cleaned = re.sub(r",\s*([\]}])", r"\1", blob)
                try:
                    parsed = json.loads(cleaned)
                except Exception:
                    continue
            if isinstance(parsed, list):
                for entry in parsed:
                    if isinstance(entry, dict) and any(
                        k in entry for k in ("start", "startStr", "debut", "date_debut", "title", "libelle")
                    ):
                        extracted.append(entry)
    return extracted


def parse_fullcalendar_json_events(
    events_data: Any,
    *,
    academic_year: str | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> list[CalendarEventRecord]:
    """Parse FullCalendar JSON event payloads captured from Pamplemousse EDT (`index.php?p=121`)."""
    raw_list: list[dict[str, Any]] = []
    if isinstance(events_data, list):
        raw_list = [e for e in events_data if isinstance(e, dict)]
    elif isinstance(events_data, dict):
        for key in ("events", "data", "items", "result", "cours", "seances"):
            val = events_data.get(key)
            if isinstance(val, list):
                raw_list = [e for e in val if isinstance(e, dict)]
                break

    events: list[CalendarEventRecord] = []
    for idx, entry in enumerate(raw_list, start=1):
        ext_props = entry.get("extendedProps") if isinstance(entry.get("extendedProps"), dict) else {}
        title = str(
            entry.get("title")
            or entry.get("summary")
            or entry.get("libelle")
            or entry.get("nom")
            or entry.get("cours")
            or ext_props.get("title")
            or ext_props.get("libelle")
            or ""
        ).strip()
        start_raw = (
            entry.get("start")
            or entry.get("startStr")
            or entry.get("start_at")
            or entry.get("debut")
            or entry.get("date_debut")
            or entry.get("start_date")
        )
        if not title or not start_raw:
            continue
        end_raw = (
            entry.get("end")
            or entry.get("endStr")
            or entry.get("end_at")
            or entry.get("fin")
            or entry.get("date_fin")
            or entry.get("end_date")
        )
        all_day = bool(entry.get("allDay", False))

        start_utc: str | None = None
        end_utc: str | None = None
        all_day_start: str | None = None
        all_day_end: str | None = None
        tz_name: str | None = "Europe/Paris"

        if all_day and isinstance(start_raw, str) and len(start_raw) == 10:
            all_day_start = start_raw
            all_day_end = str(end_raw)[:10] if end_raw else None
        else:
            start_utc, tz_name = normalize_timestamp_utc(start_raw)
            end_utc, _ = normalize_timestamp_utc(end_raw)

        if start_utc and range_start:
            st_dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
            if st_dt < range_start:
                continue
        if start_utc and range_end:
            st_dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
            if st_dt > range_end:
                continue

        desc_raw = entry.get("description") or ext_props.get("description") or ext_props.get("comment")
        desc = re.sub(r"<[^>]+>", "\n", str(desc_raw)).strip() if desc_raw else None
        teacher, group, subject_hint = _extract_pamplemousse_metadata(title, desc)
        teacher = (
            teacher
            or ext_props.get("teacher")
            or ext_props.get("enseignant")
            or ext_props.get("intervenant")
            or ext_props.get("professeur")
            or entry.get("teacher")
            or entry.get("enseignant")
            or entry.get("intervenant")
            or entry.get("professeur")
        )
        group = (
            group
            or ext_props.get("group")
            or ext_props.get("groupe")
            or entry.get("group")
            or entry.get("groupe")
        )
        room = (
            entry.get("location")
            or entry.get("room")
            or entry.get("salle")
            or entry.get("lieu")
            or ext_props.get("room")
            or ext_props.get("salle")
            or ext_props.get("location")
            or ext_props.get("lieu")
        )
        if not room and desc:
            m_room = re.search(r"(?:salle|amphi|room|lieu)\s*[:\-]\s*(.+)", desc, re.IGNORECASE)
            if m_room:
                room = m_room.group(1).strip().splitlines()[0].strip()

        course_code = (
            ext_props.get("code")
            or entry.get("code")
            or _extract_subject_code(title, desc)
        )
        color_hint = (
            entry.get("backgroundColor")
            or entry.get("color")
            or entry.get("borderColor")
            or ext_props.get("color")
        )
        cat_hint = ext_props.get("type") or ext_props.get("category") or entry.get("className")
        if isinstance(cat_hint, list):
            cat_hint = " ".join(str(x) for x in cat_hint)
        status_raw = str(entry.get("status") or ext_props.get("status") or "confirmed").lower()
        cancelled = "annul" in title.lower() or "cancel" in status_raw or "annul" in str(cat_hint or "").lower()
        status = "cancelled" if cancelled else ("tentative" if "tentat" in status_raw else "confirmed")

        ev_type, confirm_req, confirm_reason = classify_timetable_event(
            title,
            category_hint=str(cat_hint) if cat_hint else None,
            color_hint=str(color_hint) if color_hint else None,
            group=str(group) if group else None,
        )
        duration_min = _compute_duration_minutes(start_utc, end_utc)
        raw_id = entry.get("id")
        ext_seed = f"{raw_id or ''}|{title}|{start_utc or all_day_start or idx}|{room or ''}"
        ext_id = f"pamplemousse:edt:{hashlib.sha1(ext_seed.encode('utf-8')).hexdigest()[:16]}"

        events.append(
            CalendarEventRecord(
                source_kind=SourceKind.PAMPLEMOUSSE,
                external_id=ext_id,
                title=title,
                event_type=ev_type,
                start_at=start_utc,
                end_at=end_utc,
                all_day_date=all_day_start,
                all_day_end_date=all_day_end,
                original_timezone=tz_name or "Europe/Paris",
                status=status,
                room=str(room).strip() if room else None,
                teacher=str(teacher).strip() if teacher else None,
                group=str(group).strip() if group else None,
                subject_hint=subject_hint,
                course_id=str(course_code).strip() if course_code else None,
                confirmation_required=confirm_req,
                confirmation_reason=confirm_reason,
                busy=status != "cancelled",
                description=desc,
                provenance={
                    "format": "fullcalendar_json",
                    "academic_year": _academic_year_from_event_time(start_utc, all_day_start, fallback_year=academic_year),
                    "duration_minutes": duration_min,
                    "raw_event_id": str(raw_id) if raw_id is not None else None,
                    "retrieved_at": utc_now_iso(),
                },
            )
        )
    return events


_FRENCH_MONTHS: dict[str, int] = {
    "janvier": 1,
    "janv": 1,
    "février": 2,
    "fevrier": 2,
    "févr": 2,
    "fevr": 2,
    "mars": 3,
    "avril": 4,
    "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "juil": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "sept": 9,
    "octobre": 10,
    "oct": 10,
    "novembre": 11,
    "nov": 11,
    "décembre": 12,
    "decembre": 12,
    "déc": 12,
    "dec": 12,
}


def _parse_french_date_header(text: str) -> str | None:
    """Parse '01/12/2026', '2026-12-01', or 'mardi 1 décembre 2026' into YYYY-MM-DD."""
    m_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m_iso:
        return m_iso.group(1)
    m_fr = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if m_fr:
        return f"{m_fr.group(3)}-{int(m_fr.group(2)):02d}-{int(m_fr.group(1)):02d}"
    low = text.lower()
    m_named = re.search(r"\b(\d{1,2})(?:er)?\s+([a-zéûôà]+)\.?\s+(\d{4})\b", low)
    if m_named:
        month_num = _FRENCH_MONTHS.get(m_named.group(2))
        if month_num:
            return f"{m_named.group(3)}-{month_num:02d}-{int(m_named.group(1)):02d}"
    return None


def parse_fullcalendar_list_html(
    html: str,
    *,
    academic_year: str | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> list[CalendarEventRecord]:
    """Parse rendered FullCalendar List view HTML (`Liste semaine` / `Liste mois`, v3/v4/v5/v6)."""
    parser = _PamplemousseHTMLTableParser()
    parser.feed(html)
    events: list[CalendarEventRecord] = []
    current_date: str | None = None

    for idx, (row, attrs) in enumerate(parser.rows, start=1):
        cls = attrs.get("class", "").lower()
        if "fc-list-day" in cls or "fc-list-heading" in cls or attrs.get("data-date"):
            d_val = attrs.get("data-date", "").strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", d_val):
                current_date = d_val
            else:
                parsed_d = _parse_french_date_header(" ".join(row))
                if parsed_d:
                    current_date = parsed_d
            continue

        if ("fc-list-event" not in cls and "fc-list-item" not in cls) or not current_date:
            continue
        row_text = [c for c in row if c]
        if not row_text:
            continue
        time_cell = row_text[0]
        title = " ".join(row_text[1:]).strip() if len(row_text) > 1 else ""
        if not title:
            continue

        m_times = re.findall(r"(\d{1,2}[:h]\d{2})", time_cell.lower())
        start_utc: str | None = None
        end_utc: str | None = None
        all_day_start: str | None = None
        if len(m_times) >= 2:
            t0 = m_times[0].replace("h", ":")
            t1 = m_times[1].replace("h", ":")
            if len(t0) == 4:
                t0 = "0" + t0
            if len(t1) == 4:
                t1 = "0" + t1
            start_utc, _ = normalize_timestamp_utc(f"{current_date} {t0}")
            end_utc, _ = normalize_timestamp_utc(f"{current_date} {t1}")
        elif "journée" in time_cell.lower() or "all-day" in time_cell.lower():
            all_day_start = current_date
        else:
            continue

        if start_utc and range_start:
            st_dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
            if st_dt < range_start:
                continue
        if start_utc and range_end:
            st_dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
            if st_dt > range_end:
                continue

        teacher, group, subject_hint = _extract_pamplemousse_metadata(title, None)
        course_code = _extract_subject_code(title)
        is_cancelled = "annul" in title.lower()
        ev_status = "cancelled" if is_cancelled else "confirmed"
        ev_type, confirm_req, confirm_reason = classify_timetable_event(title, group=group)
        duration_min = _compute_duration_minutes(start_utc, end_utc)
        ext_seed = f"fclist|{title}|{start_utc or all_day_start}|{idx}"
        ext_id = f"pamplemousse:edt:{hashlib.sha1(ext_seed.encode('utf-8')).hexdigest()[:16]}"

        events.append(
            CalendarEventRecord(
                source_kind=SourceKind.PAMPLEMOUSSE,
                external_id=ext_id,
                title=title,
                event_type=ev_type,
                start_at=start_utc,
                end_at=end_utc,
                all_day_date=all_day_start,
                original_timezone="Europe/Paris",
                status=ev_status,
                teacher=teacher,
                group=group,
                subject_hint=subject_hint,
                course_id=course_code,
                confirmation_required=confirm_req,
                confirmation_reason=confirm_reason,
                busy=ev_status != "cancelled",
                provenance={
                    "format": "fullcalendar_list_html",
                    "academic_year": _academic_year_from_event_time(start_utc, all_day_start, fallback_year=academic_year),
                    "duration_minutes": duration_min,
                    "row": idx,
                    "retrieved_at": utc_now_iso(),
                },
            )
        )
    return events


class _RichHTMLTableParser(HTMLParser):
    """HTML table parser preserving per-row tags, CSS classes, inline styles, and cell formatting."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_table = False
        self._tables: list[list[dict[str, Any]]] = []
        self._current_table: list[dict[str, Any]] = []
        self._in_row = False
        self._row_attrs: dict[str, str] = {}
        self._cells: list[dict[str, Any]] = []
        self._in_cell = False
        self._cell_tag = "td"
        self._cell_attrs: dict[str, str] = {}
        self._cell_raw_buf: list[str] = []
        self._cell_bold = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if low == "table":
            self._in_table = True
            self._current_table = []
        elif low == "tr" and self._in_table:
            self._in_row = True
            self._row_attrs = attr_map
            self._cells = []
        elif low in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_tag = low
            self._cell_attrs = attr_map
            self._cell_raw_buf = []
            self._cell_bold = low == "th"
        elif low in ("strong", "b") and self._in_cell:
            self._cell_bold = True

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_raw_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low in ("td", "th") and self._in_cell:
            raw_text = "".join(self._cell_raw_buf)
            # Measure leading non-breaking space / whitespace indentation for hierarchical rows
            leading_nbsp = len(raw_text) - len(raw_text.lstrip("\xa0 "))
            clean_text = " ".join(raw_text.replace("\xa0", " ").split()).strip()
            style = self._cell_attrs.get("style", "")
            indent_px = 0
            m_pad = re.search(r"(?:padding|margin)-left\s*:\s*(\d+)\s*(px|em|rem)", style, re.IGNORECASE)
            if m_pad:
                val = int(m_pad.group(1))
                unit = m_pad.group(2).lower()
                indent_px = val * 16 if unit in ("em", "rem") else val
            self._cells.append(
                {
                    "tag": self._cell_tag,
                    "text": clean_text,
                    "bold": self._cell_bold,
                    "class": self._cell_attrs.get("class", ""),
                    "style": style,
                    "colspan": min(
                        64,
                        max(
                            1,
                            int(self._cell_attrs.get("colspan") or "1")
                            if str(self._cell_attrs.get("colspan") or "").isdigit()
                            else 1,
                        ),
                    ),
                    "rowspan": min(
                        64,
                        max(
                            1,
                            int(self._cell_attrs.get("rowspan") or "1")
                            if str(self._cell_attrs.get("rowspan") or "").isdigit()
                            else 1,
                        ),
                    ),
                    "indent_px": indent_px + leading_nbsp * 6,
                }
            )
            self._in_cell = False
            self._cell_raw_buf = []
        elif low == "tr" and self._in_row:
            if any(c["text"] for c in self._cells):
                self._current_table.append({"attrs": self._row_attrs, "cells": self._cells})
            self._in_row = False
        elif low == "table" and self._in_table:
            if self._current_table:
                self._tables.append(self._current_table)
            self._in_table = False

    @property
    def tables(self) -> list[list[dict[str, Any]]]:
        return self._tables


def _expand_html_table_grid(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand HTML ``rowspan`` and ``colspan`` attributes into a regular column-aligned grid.

    Ensures merged UE or semester cells never shift downstream ``Coef.`` / ``ECTS`` / ``Note``
    columns out of alignment.
    """
    if not raw_rows:
        return []
    pending_spans: dict[int, tuple[int, dict[str, Any]]] = {}
    expanded: list[dict[str, Any]] = []

    for row_obj in raw_rows:
        out_cells: list[dict[str, Any]] = []
        col_idx = 0

        def _flush_pending() -> None:
            nonlocal col_idx
            while col_idx in pending_spans:
                rem, span_cell = pending_spans[col_idx]
                out_cells.append({**span_cell, "inherited_from_rowspan": True})
                if rem <= 1:
                    del pending_spans[col_idx]
                else:
                    pending_spans[col_idx] = (rem - 1, span_cell)
                col_idx += 1

        for cell in row_obj["cells"]:
            _flush_pending()
            cspan = max(1, int(cell.get("colspan") or 1))
            rspan = max(1, int(cell.get("rowspan") or 1))
            base_cell = {**cell, "colspan": 1, "rowspan": 1}
            for offset in range(cspan):
                placed = base_cell if offset == 0 else {**base_cell, "colspan_continuation": True}
                out_cells.append(placed)
                if rspan > 1:
                    pending_spans[col_idx] = (rspan - 1, base_cell)
                col_idx += 1
        _flush_pending()
        expanded.append({"attrs": row_obj["attrs"], "cells": out_cells})

    return expanded


def _find_table_header_row(
    grid: list[dict[str, Any]],
    predicate: Callable[[list[str]], bool],
    *,
    max_scan: int = 4,
) -> tuple[int, list[str]] | None:
    """Locate the first header row matching ``predicate`` (skips full-width banner/title rows)."""
    limit = min(len(grid) - 1, max_scan)
    for idx in range(limit):
        row = grid[idx]
        # Skip single-label banner rows spanned across the whole table
        distinct_texts = [c["text"] for c in row["cells"] if c["text"] and not c.get("colspan_continuation")]
        if len(distinct_texts) < 2:
            continue
        headers = [c["text"].lower() for c in row["cells"]]
        if predicate(headers):
            return idx, headers
    return None


def _strip_accents(text: str) -> str:
    """Fold accented Latin characters to ASCII equivalents for deterministic French matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _slug_key(text: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")
    return norm[:48] or hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _normalize_semester_label(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    low = _strip_accents(s.lower())
    if low in ("1", "s1", "sem1", "semestre 1", "premier semestre", "1er semestre"):
        return "S1"
    if low in ("2", "s2", "sem2", "semestre 2", "second semestre", "deuxieme semestre", "2e semestre", "2eme semestre"):
        return "S2"
    m_sem = re.search(r"\b(?:semestre|sem\.?|s)\s*([1-6])\b", low)
    if m_sem:
        return f"S{m_sem.group(1)}"
    if "premier semestre" in low:
        return "S1"
    if "second semestre" in low or "deuxieme semestre" in low:
        return "S2"
    if low in ("annuel", "annee", "s1-s2", "s1/s2", "s1 et s2", "toute l'annee"):
        return "Annuel"
    return s


def parse_pamplemousse_inscriptions_html(
    html: str,
    *,
    academic_year: str | None = None,
) -> list[AcademicRecord]:
    """Parse Pamplemousse Inscriptions (`index.php?p=107`) enrollment catalog & coefficients.

    Expected columns: UE | Matière | Code | Coef. | ECTS | Sem.
    - Emits parent-first hierarchy: academic_year -> semester -> teaching_unit -> subject.
    - Handles 'sans UE' / blank UE without fabricating fake UEs.
    - Preserves zero coefficients (`0` -> Decimal('0'), CONFIRMED) distinctly from missing (`None`, MISSING).
    - Preserves ECTS separately from coefficients.
    - Uses stable context-scoped external IDs so repeated subject names under different codes/UEs are not merged.
    """
    detected_year = academic_year or extract_pamplemousse_academic_year(html)
    parser = _RichHTMLTableParser()
    parser.feed(html)

    records: list[AcademicRecord] = []
    emitted_ids: set[str] = set()
    year_id: str | None = None
    if detected_year:
        year_id = f"pamplemousse:year:{_slug_key(detected_year)}"
        records.append(
            AcademicRecord(
                source_kind=SourceKind.PAMPLEMOUSSE,
                external_id=year_id,
                record_type="academic_year",
                title=detected_year,
                academic_year=detected_year,
                observed_at=utc_now_iso(),
                provenance={"format": "inscriptions_html", "page": PAMPLEMOUSSE_INSCRIPTIONS_PATH},
            )
        )
        emitted_ids.add(year_id)

    for raw_table in parser.tables:
        table = _expand_html_table_grid(raw_table)
        if len(table) < 2:
            continue

        def _is_inscriptions_header(hdrs: list[str]) -> bool:
            norm_h = [_strip_accents(h) for h in hdrs]
            has_mat = any(
                any(tok in h for tok in ("matiere", "enseignement", "subject", "intitul", "libell", "module"))
                for h in norm_h
            )
            has_markers = any(
                any(tok in h for tok in ("ects", "coef", "ponder", "code", "sem", "ue"))
                for h in norm_h
            )
            has_grade = any("note" in h or "classement" in h for h in norm_h)
            return has_mat and has_markers and not has_grade

        found_hdr = _find_table_header_row(table, _is_inscriptions_header)
        if found_hdr is None:
            continue
        hdr_idx, headers = found_hdr
        norm_headers = [_strip_accents(h) for h in headers]

        def col_idx(
            *candidates: str,
            exclude: tuple[str, ...] = (),
            exact_first: tuple[str, ...] = (),
        ) -> int | None:
            for ex in exact_first:
                for i, h in enumerate(norm_headers):
                    if h.strip(" .:") == ex and not any(bad in h for bad in exclude):
                        return i
            for cand in candidates:
                for i, h in enumerate(norm_headers):
                    if cand in h and not any(bad in h for bad in exclude):
                        return i
            return None

        idx_ue = col_idx("ue", "unite", exclude=("code ue",), exact_first=("ue",))
        if idx_ue is None:
            idx_ue = col_idx("ue", "unite")
        idx_mat = col_idx("matiere", "enseignement", "subject", "intitul", "libell", "module")
        idx_code = col_idx(
            "code matiere",
            "code mat",
            "code",
            exclude=("code ue",),
            exact_first=("code", "code matiere"),
        )
        idx_coef = col_idx("coef", "poids", "weight", "ponder", exclude=("ects", "credit"))
        idx_ects = col_idx("ects", "credit", exclude=("coef", "poids", "weight", "ponder"))
        idx_sem = col_idx("sem")

        for row_num, row_obj in enumerate(table[hdr_idx + 1 :], start=1):
            cells = row_obj["cells"]

            def get_cell(i: int | None) -> str:
                if i is None or i >= len(cells):
                    return ""
                return cells[i]["text"].strip()

            subject_title = get_cell(idx_mat)
            if not subject_title:
                continue
            ue_raw = get_cell(idx_ue)
            code_raw = get_cell(idx_code) or None
            coef_raw = get_cell(idx_coef)
            ects_raw = get_cell(idx_ects)
            sem_raw = get_cell(idx_sem)
            sem_norm = _normalize_semester_label(sem_raw)

            coef_val, coef_mode = _parse_coefficient_cell(coef_raw)
            ects_val = parse_decimal_strict(ects_raw)
            is_sans_ue = (not ue_raw) or ("sans ue" in ue_raw.lower()) or ue_raw in ("-", "—", "–", "N/A")
            is_optional = (coef_val == Decimal("0")) or ("option" in subject_title.lower()) or ("sport" in subject_title.lower())

            # Emit Semester parent if present
            parent_id = year_id
            if sem_norm:
                sem_id = f"pamplemousse:sem:{_slug_key(detected_year or 'current')}:{_slug_key(sem_norm)}"
                if sem_id not in emitted_ids:
                    records.append(
                        AcademicRecord(
                            source_kind=SourceKind.PAMPLEMOUSSE,
                            external_id=sem_id,
                            record_type="semester",
                            title=sem_norm,
                            parent_external_id=year_id,
                            academic_year=detected_year,
                            semester=sem_norm,
                            weight_status=ValueStatus.UNKNOWN,
                            grade_status=ValueStatus.UNKNOWN,
                            observed_at=utc_now_iso(),
                            provenance={"format": "inscriptions_html", "raw_semester": sem_raw},
                        )
                    )
                    emitted_ids.add(sem_id)
                parent_id = sem_id

            # Emit UE parent only when NOT "sans UE" / blank
            if not is_sans_ue:
                ue_id = (
                    f"pamplemousse:ue:{_slug_key(detected_year or 'current')}:"
                    f"{_slug_key(sem_norm or 'any')}:{_slug_key(ue_raw)}"
                )
                if ue_id not in emitted_ids:
                    records.append(
                        AcademicRecord(
                            source_kind=SourceKind.PAMPLEMOUSSE,
                            external_id=ue_id,
                            record_type="teaching_unit",
                            title=ue_raw,
                            parent_external_id=parent_id,
                            academic_year=detected_year,
                            semester=sem_norm,
                            # Inscriptions table has no UE-level coefficient or grade column; never fabricate MISSING!
                            weight_status=ValueStatus.UNKNOWN,
                            grade_status=ValueStatus.UNKNOWN,
                            observed_at=utc_now_iso(),
                            provenance={"format": "inscriptions_html", "ue_label": ue_raw},
                        )
                    )
                    emitted_ids.add(ue_id)
                parent_id = ue_id

            # Context-scoped stable external ID preserving repeated subject names under different codes/UEs
            id_seed = f"{detected_year or ''}|{sem_norm or ''}|{'' if is_sans_ue else ue_raw}|{code_raw or subject_title}|{subject_title}"
            subj_hash = hashlib.sha1(id_seed.encode("utf-8")).hexdigest()[:12]
            subj_id = (
                f"pamplemousse:subject:{_slug_key(detected_year or 'current')}:"
                f"{_slug_key(code_raw) if code_raw else subj_hash}:{subj_hash[:6]}"
            )
            if subj_id in emitted_ids:
                subj_id = f"{subj_id}:r{row_num}"
            emitted_ids.add(subj_id)

            subj_weight_status = (
                ValueStatus.CONFIRMED
                if coef_val is not None
                else (ValueStatus.MISSING if idx_coef is not None else ValueStatus.UNKNOWN)
            )
            subj_ects_status = (
                ValueStatus.CONFIRMED
                if ects_val is not None
                else (ValueStatus.MISSING if idx_ects is not None else ValueStatus.UNKNOWN)
            )

            records.append(
                AcademicRecord(
                    source_kind=SourceKind.PAMPLEMOUSSE,
                    external_id=subj_id,
                    record_type="subject",
                    title=subject_title,
                    parent_external_id=parent_id,
                    academic_year=detected_year,
                    semester=sem_norm,
                    subject_code=code_raw,
                    weight=coef_val,
                    weight_mode=coef_mode,
                    weight_status=subj_weight_status,
                    grade_value=None,
                    # Inscriptions (p=107) has no grade column; never fabricate MISSING grade rows from catalog!
                    grade_status=ValueStatus.UNKNOWN,
                    observed_at=utc_now_iso(),
                    rule_verification_status=ValueStatus.UNKNOWN,
                    provenance={
                        "format": "inscriptions_html",
                        "page": PAMPLEMOUSSE_INSCRIPTIONS_PATH,
                        "row": row_num,
                        "ue_label": None if is_sans_ue else ue_raw,
                        "sans_ue": is_sans_ue,
                        "ects": str(ects_val) if ects_val is not None else None,
                        "ects_status": subj_ects_status.value,
                        "optional_activity": is_optional,
                        "zero_coefficient": coef_val == Decimal("0"),
                        "retrieved_at": utc_now_iso(),
                    },
                )
            )
    if not any(r.record_type == "subject" for r in records):
        return []
    return records


def parse_pamplemousse_notes_html(
    html: str,
    *,
    academic_year: str | None = None,
) -> list[AcademicRecord]:
    """Parse Pamplemousse Detailed Grades (`BULLETIN DÉTAILLÉ`, `index.php?p=1052`).

    Expected columns: Enseignement | Note finale | Coefficient (Classement is ignored by policy).
    - Parses hierarchical rows (Overall -> Semester -> UE -> Subject -> Assessment -> Sub-assessment)
      using HTML structure, indentation, bold/heading tags, and semantic row markers.
    - Separates official aggregate grades from individual assessment grades.
    - Supports French comma decimals; NEVER converts missing/locked grades to zero.
    """
    detected_year = academic_year or extract_pamplemousse_academic_year(html)
    parser = _RichHTMLTableParser()
    parser.feed(html)

    records: list[AcademicRecord] = []
    emitted_ids: set[str] = set()
    year_slug = _slug_key(detected_year or "current")
    year_id: str | None = None
    if detected_year:
        year_id = f"pamplemousse:year:{year_slug}"
        records.append(
            AcademicRecord(
                source_kind=SourceKind.PAMPLEMOUSSE,
                external_id=year_id,
                record_type="academic_year",
                title=detected_year,
                academic_year=detected_year,
                observed_at=utc_now_iso(),
                provenance={"format": "notes_html", "page": PAMPLEMOUSSE_NOTES_PATH},
            )
        )
        emitted_ids.add(year_id)

    is_bulletin_page = "bulletin" in html.lower() or "relev" in _strip_accents(html.lower())
    for raw_table in parser.tables:
        table = _expand_html_table_grid(raw_table)
        if len(table) < 2:
            continue

        def _is_notes_header(hdrs: list[str]) -> bool:
            norm_h = [_strip_accents(h) for h in hdrs]
            has_ens = any(
                tok in h for h in norm_h for tok in ("enseignement", "matiere", "epreuve", "intitul", "libell")
            )
            has_note = any("note" in h or "moyenne" in h or "grade" in h for h in norm_h)
            if not (has_ens and has_note):
                return False
            return (
                is_bulletin_page
                or any("enseignement" in h or "note finale" in h or "classement" in h for h in norm_h)
                or any(
                    any(
                        tok in (r["attrs"].get("class", "").lower())
                        for tok in ("semestre", "ue", "epreuve", "overall", "matiere")
                    )
                    or any(int(c.get("indent_px", 0)) > 0 for c in r["cells"][:1])
                    for r in table[1:]
                )
            )

        found_hdr = _find_table_header_row(table, _is_notes_header)
        if found_hdr is None:
            continue
        hdr_idx, headers = found_hdr
        norm_headers = [_strip_accents(h) for h in headers]

        def col_idx(*candidates: str, exclude: tuple[str, ...] = ()) -> int | None:
            for cand in candidates:
                for i, h in enumerate(norm_headers):
                    if cand in h and not any(bad in h for bad in exclude):
                        return i
            return None

        idx_label = col_idx("enseignement", "matiere", "epreuve", "intitul", "libell")
        idx_note = col_idx("note finale", "note", "moyenne", "grade")
        idx_coef = col_idx("coefficient", "coef", "poids", "weight", "ponder", exclude=("ects", "credit"))
        # Deliberately NEVER read idx_classement (class ranking excluded by policy)

        current_sem_id: str | None = None
        current_sem_label: str | None = None
        current_ue_id: str | None = None
        current_subject_id: str | None = None
        current_subject_indent: int | None = None
        current_assessment_id: str | None = None
        current_assessment_indent: int | None = None

        for row_num, row_obj in enumerate(table[hdr_idx + 1 :], start=1):
            cells = row_obj["cells"]
            row_cls = row_obj["attrs"].get("class", "").lower()
            if idx_label is None or idx_label >= len(cells):
                continue
            label_cell = cells[idx_label]
            label = label_cell["text"].strip()
            if not label:
                continue
            low_label = label.lower()
            ascii_label = _strip_accents(low_label)
            cell_cls = label_cell.get("class", "").lower()
            combined_cls = f"{row_cls} {cell_cls}"
            indent_px = int(label_cell.get("indent_px", 0))
            is_bold = bool(label_cell.get("bold", False))

            note_raw = cells[idx_note]["text"].strip() if (idx_note is not None and idx_note < len(cells)) else ""
            coef_raw = cells[idx_coef]["text"].strip() if (idx_coef is not None and idx_coef < len(cells)) else ""
            grade_val = parse_decimal_strict(note_raw)
            coef_val, coef_mode = _parse_coefficient_cell(coef_raw)
            row_weight_status = (
                ValueStatus.CONFIRMED
                if coef_val is not None
                else (ValueStatus.MISSING if idx_coef is not None else ValueStatus.UNKNOWN)
            )
            # Detect scale if explicitly shown like "15,5 / 20"
            scale_val: Decimal | None = None
            m_scale = re.search(r"/\s*(\d+(?:[.,]\d+)?)", note_raw)
            if m_scale:
                scale_val = parse_decimal_strict(m_scale.group(1))
            elif grade_val is not None:
                scale_val = Decimal("20")

            # Classify row hierarchy level conservatively
            is_overall = any(
                tok in ascii_label for tok in ("moyenne generale", "moyenne annuelle", "resultat annuel")
            ) or "overall" in combined_cls
            is_semester = (
                "semestre" in combined_cls
                or "semester" in combined_cls
                or re.match(r"^(?:premier|second|deuxieme|1er|2e|2eme)\s+semestre\b", ascii_label) is not None
                or re.match(r"^semestre\s*\d+\b", ascii_label) is not None
                or re.match(r"^s[1-6]\b", ascii_label) is not None
            )
            is_ue = (
                not is_semester
                and not is_overall
                and (
                    re.search(r"\b(?:[1-3]a[-_\s]*)?ue\s*\d*[a-z]*\b", ascii_label) is not None
                    and (
                        re.search(r"\b(?:[1-3]a[-_\s]*)?ue\s*\d+\b", ascii_label) is not None
                        or re.search(r"\b[1-3]a[-_\s]+ue\b", ascii_label) is not None
                        or "ue" in combined_cls.split()
                        or "teaching_unit" in combined_cls
                        or "unite" in combined_cls
                    )
                )
            )

            if is_overall:
                agg_id = f"pamplemousse:notes:overall:{year_slug}"
                records.append(
                    AcademicRecord(
                        source_kind=SourceKind.PAMPLEMOUSSE,
                        external_id=agg_id,
                        record_type="aggregate_grade",
                        title=label,
                        parent_external_id=year_id,
                        academic_year=detected_year,
                        weight=coef_val,
                        weight_mode=coef_mode,
                        weight_status=row_weight_status,
                        grade_value=grade_val,
                        grade_scale_max=scale_val,
                        grade_kind="official_aggregate",
                        grade_status=ValueStatus.CONFIRMED if grade_val is not None else ValueStatus.MISSING,
                        observed_at=utc_now_iso(),
                        rule_verification_status=ValueStatus.UNKNOWN,
                        provenance={
                            "format": "notes_html",
                            "page": PAMPLEMOUSSE_NOTES_PATH,
                            "aggregate_scope": "overall",
                            "is_official_aggregate": True,
                            "row": row_num,
                        },
                    )
                )
                continue

            if is_semester:
                sem_norm = _normalize_semester_label(label) or label
                sem_id = f"pamplemousse:sem:{year_slug}:{_slug_key(sem_norm)}"
                current_sem_id = sem_id
                current_sem_label = sem_norm
                current_ue_id = None
                current_subject_id = None
                current_subject_indent = None
                current_assessment_id = None
                current_assessment_indent = None
                if sem_id not in emitted_ids:
                    records.append(
                        AcademicRecord(
                            source_kind=SourceKind.PAMPLEMOUSSE,
                            external_id=sem_id,
                            record_type="semester",
                            title=sem_norm,
                            parent_external_id=year_id,
                            academic_year=detected_year,
                            semester=sem_norm,
                            weight_status=ValueStatus.UNKNOWN,
                            grade_status=ValueStatus.UNKNOWN,
                            observed_at=utc_now_iso(),
                            provenance={"format": "notes_html", "raw_label": label, "row": row_num},
                        )
                    )
                    emitted_ids.add(sem_id)
                if grade_val is not None or coef_val is not None:
                    records.append(
                        AcademicRecord(
                            source_kind=SourceKind.PAMPLEMOUSSE,
                            external_id=f"{sem_id}:official_aggregate",
                            record_type="aggregate_grade",
                            title=f"{label} — Moyenne officielle",
                            parent_external_id=sem_id,
                            academic_year=detected_year,
                            semester=sem_norm,
                            weight=coef_val,
                            weight_mode=coef_mode,
                            weight_status=row_weight_status,
                            grade_value=grade_val,
                            grade_scale_max=scale_val,
                            grade_kind="official_aggregate",
                            grade_status=ValueStatus.CONFIRMED if grade_val is not None else ValueStatus.MISSING,
                            observed_at=utc_now_iso(),
                            rule_verification_status=ValueStatus.UNKNOWN,
                            provenance={
                                "format": "notes_html",
                                "aggregate_scope": "semester",
                                "is_official_aggregate": True,
                                "row": row_num,
                            },
                        )
                    )
                continue

            if is_ue:
                ue_id = f"pamplemousse:ue:{year_slug}:{_slug_key(current_sem_label or 'any')}:{_slug_key(label)}"
                current_ue_id = ue_id
                current_subject_id = None
                current_subject_indent = None
                current_assessment_id = None
                current_assessment_indent = None
                if ue_id not in emitted_ids:
                    records.append(
                        AcademicRecord(
                            source_kind=SourceKind.PAMPLEMOUSSE,
                            external_id=ue_id,
                            record_type="teaching_unit",
                            title=label,
                            parent_external_id=current_sem_id or year_id,
                            academic_year=detected_year,
                            semester=current_sem_label,
                            weight=coef_val,
                            weight_mode=coef_mode,
                            weight_status=row_weight_status,
                            grade_status=ValueStatus.UNKNOWN,
                            observed_at=utc_now_iso(),
                            provenance={"format": "notes_html", "row": row_num},
                        )
                    )
                    emitted_ids.add(ue_id)
                if grade_val is not None:
                    records.append(
                        AcademicRecord(
                            source_kind=SourceKind.PAMPLEMOUSSE,
                            external_id=f"{ue_id}:official_aggregate",
                            record_type="aggregate_grade",
                            title=f"{label} — Moyenne UE",
                            parent_external_id=ue_id,
                            academic_year=detected_year,
                            semester=current_sem_label,
                            # Parent teaching_unit already carries this row's coefficient; never emit a duplicate missing coef.
                            weight=None,
                            weight_mode=None,
                            weight_status=ValueStatus.UNKNOWN,
                            grade_value=grade_val,
                            grade_scale_max=scale_val,
                            grade_kind="official_aggregate",
                            grade_status=ValueStatus.CONFIRMED,
                            observed_at=utc_now_iso(),
                            rule_verification_status=ValueStatus.UNKNOWN,
                            provenance={
                                "format": "notes_html",
                                "aggregate_scope": "teaching_unit",
                                "is_official_aggregate": True,
                                "row": row_num,
                            },
                        )
                    )
                continue

            # Distinguish Subject vs Assessment vs Sub-assessment using indentation, class, bold, and leading dash hints
            is_explicit_assessment = any(
                tok in combined_cls for tok in ("epreuve", "épreuve", "assessment", "eval", "sub", "child", "level-3", "level-4")
            )
            is_explicit_subject = any(
                tok in combined_cls for tok in ("matiere", "matière", "subject", "module", "level-2")
            )
            has_dash_prefix = bool(re.match(r"^\s*[-–•]\s+\S", label))

            is_child_of_subject = (
                current_subject_id is not None
                and (
                    is_explicit_assessment
                    or (
                        not is_explicit_subject
                        and (
                            (current_subject_indent is not None and indent_px > current_subject_indent)
                            or has_dash_prefix
                            or (not is_bold and current_subject_indent is not None and indent_px >= current_subject_indent and re.match(r"^(?:tp|td|ds|dm|cm|exam|partiel|projet|oral|contrôle|controle|test|qcm|final|midterm|devoir|soutenance)\b", low_label))
                        )
                    )
                )
            )

            if not is_child_of_subject:
                # Subject row in detailed bulletin
                code_m = _SUBJECT_CODE_RE.search(label)
                subj_code = code_m.group(1) if code_m else None
                parent_for_subj = current_ue_id or current_sem_id or year_id
                subj_seed = f"{year_slug}|{current_sem_label or ''}|{current_ue_id or ''}|{label}"
                subj_hash = hashlib.sha1(subj_seed.encode("utf-8")).hexdigest()[:12]
                subj_id = f"pamplemousse:notes:subject:{year_slug}:{subj_hash}"
                current_subject_id = subj_id
                current_subject_indent = indent_px
                current_assessment_id = None
                current_assessment_indent = None

                records.append(
                    AcademicRecord(
                        source_kind=SourceKind.PAMPLEMOUSSE,
                        external_id=subj_id,
                        record_type="subject",
                        title=label,
                        parent_external_id=parent_for_subj,
                        academic_year=detected_year,
                        semester=current_sem_label,
                        subject_code=subj_code,
                        weight=coef_val,
                        weight_mode=coef_mode,
                        weight_status=row_weight_status,
                        grade_value=None,  # Keep official subject average separate from assessments!
                        grade_status=ValueStatus.UNKNOWN,
                        observed_at=utc_now_iso(),
                        rule_verification_status=ValueStatus.UNKNOWN,
                        provenance={
                            "format": "notes_html",
                            "page": PAMPLEMOUSSE_NOTES_PATH,
                            "row": row_num,
                            "from_notes": True,
                        },
                    )
                )
                if grade_val is not None:
                    records.append(
                        AcademicRecord(
                            source_kind=SourceKind.PAMPLEMOUSSE,
                            external_id=f"{subj_id}:official_aggregate",
                            record_type="aggregate_grade",
                            title=f"{label} — Note finale",
                            parent_external_id=subj_id,
                            academic_year=detected_year,
                            semester=current_sem_label,
                            # Parent subject already carries this row's coefficient; never emit a duplicate missing coef.
                            weight=None,
                            weight_mode=None,
                            weight_status=ValueStatus.UNKNOWN,
                            grade_value=grade_val,
                            grade_scale_max=scale_val,
                            grade_kind="official_aggregate",
                            grade_status=ValueStatus.CONFIRMED,
                            observed_at=utc_now_iso(),
                            rule_verification_status=ValueStatus.UNKNOWN,
                            provenance={
                                "format": "notes_html",
                                "aggregate_scope": "subject",
                                "is_official_aggregate": True,
                                "row": row_num,
                            },
                        )
                    )
            else:
                # Assessment or Sub-assessment row under current_subject_id
                is_sub_assessment = (
                    current_assessment_id is not None
                    and current_assessment_indent is not None
                    and indent_px > current_assessment_indent
                )
                parent_for_assess = current_assessment_id if is_sub_assessment else current_subject_id
                assess_seed = f"{parent_for_assess}|{label}|{row_num}"
                assess_hash = hashlib.sha1(assess_seed.encode("utf-8")).hexdigest()[:12]
                assess_id = f"pamplemousse:notes:assessment:{year_slug}:{assess_hash}"
                if not is_sub_assessment:
                    current_assessment_id = assess_id
                    current_assessment_indent = indent_px

                records.append(
                    AcademicRecord(
                        source_kind=SourceKind.PAMPLEMOUSSE,
                        external_id=assess_id,
                        record_type="assessment",
                        title=label,
                        parent_external_id=parent_for_assess,
                        academic_year=detected_year,
                        semester=current_sem_label,
                        weight=coef_val,
                        weight_mode=coef_mode,
                        weight_status=row_weight_status,
                        grade_value=grade_val,
                        grade_scale_max=scale_val,
                        grade_kind="assessment" if grade_val is not None else None,
                        grade_status=ValueStatus.CONFIRMED if grade_val is not None else ValueStatus.MISSING,
                        observed_at=utc_now_iso(),
                        rule_verification_status=ValueStatus.UNKNOWN,
                        provenance={
                            "format": "notes_html",
                            "page": PAMPLEMOUSSE_NOTES_PATH,
                            "is_sub_assessment": is_sub_assessment,
                            "row": row_num,
                        },
                    )
                )

    if not any(r.record_type in ("subject", "assessment", "aggregate_grade") for r in records):
        return []
    return records


def reconcile_pamplemousse_academics(
    inscriptions_records: list[AcademicRecord],
    notes_records: list[AcademicRecord],
) -> list[AcademicRecord]:
    """Reconcile Pamplemousse Inscriptions (catalog/coefficients) with Notes (detailed grades).

    - Inscriptions are authoritative for the enrolled subject catalog, codes, coefficients, and ECTS.
    - Enriches matching Inscriptions UEs/subjects with official coefficients from Notes whenever
      Inscriptions lacked a coefficient (e.g. UE-level coefficients present only on BULLETIN DÉTAILLÉ,
      or subjects whose coefficient cell was blank in Inscriptions).
    - Links Notes UEs/subjects & their child assessments/aggregates to the matching Inscriptions
      node ONLY when the match is unambiguous (exact ID, exact code, or unique accent-insensitive
      normalized title within the same semester).
    - Preserves ambiguity when multiple enrolled subjects share a name instead of silently merging.
    - Keeps raw coefficients untouched and emits parents strictly before children.
    """
    if not inscriptions_records:
        return list(notes_records)
    if not notes_records:
        return list(inscriptions_records)

    def _norm_title(t: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", _strip_accents(t).lower()).strip()

    def _merge_weight(base: AcademicRecord, notes_rec: AcademicRecord) -> AcademicRecord:
        """Enrich ``base`` with coefficient evidence from ``notes_rec`` without inventing values."""
        prov = dict(base.provenance or {})
        new_weight = base.weight
        new_mode = base.weight_mode
        new_status = base.weight_status

        if base.weight is None and notes_rec.weight is not None:
            new_weight = notes_rec.weight
            new_mode = notes_rec.weight_mode
            new_status = notes_rec.weight_status
            prov["weight_source"] = "notes_html"
        elif base.weight is not None and notes_rec.weight is not None and base.weight != notes_rec.weight:
            prov["notes_weight"] = str(notes_rec.weight)
            prov["weight_discrepancy"] = True
        elif base.weight is None and base.weight_status == ValueStatus.UNKNOWN and notes_rec.weight_status == ValueStatus.MISSING:
            new_status = ValueStatus.MISSING

        if new_weight == base.weight and new_mode == base.weight_mode and new_status == base.weight_status and prov == (base.provenance or {}):
            return base
        return AcademicRecord(
            source_kind=base.source_kind,
            external_id=base.external_id,
            record_type=base.record_type,
            title=base.title,
            parent_external_id=base.parent_external_id,
            academic_year=base.academic_year,
            cohort=base.cohort,
            semester=base.semester,
            specialization=base.specialization,
            subject_code=base.subject_code,
            weight=new_weight,
            weight_mode=new_mode,
            weight_status=new_status,
            grade_value=base.grade_value,
            grade_scale_max=base.grade_scale_max,
            grade_kind=base.grade_kind,
            grade_status=base.grade_status,
            observed_at=base.observed_at,
            rule_payload=base.rule_payload,
            rule_verification_status=base.rule_verification_status,
            provenance=prov,
        )

    merged: list[AcademicRecord] = list(inscriptions_records)
    index_by_id: dict[str, int] = {r.external_id: i for i, r in enumerate(merged)}

    # 1. Index Inscriptions UEs for exact/accent-insensitive reconciliation
    ue_by_sem_title: dict[tuple[str, str], list[AcademicRecord]] = {}
    ue_by_title: dict[str, list[AcademicRecord]] = {}
    for r in merged:
        if r.record_type == "teaching_unit":
            nt = _norm_title(r.title)
            ue_by_sem_title.setdefault((r.semester or "", nt), []).append(r)
            ue_by_title.setdefault(nt, []).append(r)

    # 2. Index Inscriptions Subjects for exact-code or accent-insensitive title reconciliation
    insc_subjects = [r for r in merged if r.record_type == "subject"]
    by_code: dict[str, list[AcademicRecord]] = {}
    by_sem_title: dict[tuple[str, str], list[AcademicRecord]] = {}
    by_title: dict[str, list[AcademicRecord]] = {}
    for s in insc_subjects:
        if s.subject_code:
            by_code.setdefault(s.subject_code.lower(), []).append(s)
        nt = _norm_title(s.title)
        by_sem_title.setdefault((s.semester or "", nt), []).append(s)
        by_title.setdefault(nt, []).append(s)

    id_remap: dict[str, str] = {}
    ambiguous_notes_subjects: set[str] = set()
    for nr in notes_records:
        if nr.record_type == "teaching_unit":
            if nr.external_id in index_by_id:
                idx = index_by_id[nr.external_id]
                merged[idx] = _merge_weight(merged[idx], nr)
                continue
            nt = _norm_title(nr.title)
            ue_matches = ue_by_sem_title.get((nr.semester or "", nt)) or ue_by_title.get(nt) or []
            if len(ue_matches) == 1:
                target_id = ue_matches[0].external_id
                id_remap[nr.external_id] = target_id
                idx = index_by_id[target_id]
                merged[idx] = _merge_weight(merged[idx], nr)
            continue
        if nr.record_type != "subject":
            continue
        matches: list[AcademicRecord] = []
        if nr.subject_code and nr.subject_code.lower() in by_code:
            matches = by_code[nr.subject_code.lower()]
        else:
            nt = _norm_title(nr.title)
            matches = by_sem_title.get((nr.semester or "", nt)) or by_title.get(nt) or []
        if len(matches) == 1:
            target_id = matches[0].external_id
            id_remap[nr.external_id] = target_id
            idx = index_by_id[target_id]
            merged[idx] = _merge_weight(merged[idx], nr)
        elif len(matches) > 1:
            ambiguous_notes_subjects.add(nr.external_id)

    seen_ids: set[str] = {r.external_id for r in merged}

    for nr in notes_records:
        # Skip duplicate year/semester/ue already emitted by inscriptions (weights already merged above)
        if nr.external_id in seen_ids:
            continue
        # If this notes UE or subject cleanly matched an inscriptions record, skip duplicate node
        if nr.record_type in ("subject", "teaching_unit") and nr.external_id in id_remap:
            continue
        new_parent = id_remap.get(nr.parent_external_id or "", nr.parent_external_id)
        prov = dict(nr.provenance or {})
        if nr.external_id in ambiguous_notes_subjects:
            prov["reconciliation_status"] = "ambiguous"
        elif nr.parent_external_id in id_remap:
            prov["reconciled_subject_id"] = new_parent

        updated = AcademicRecord(
            source_kind=nr.source_kind,
            external_id=nr.external_id,
            record_type=nr.record_type,
            title=nr.title,
            parent_external_id=new_parent,
            academic_year=nr.academic_year,
            cohort=nr.cohort,
            semester=nr.semester,
            specialization=nr.specialization,
            subject_code=nr.subject_code,
            weight=nr.weight,
            weight_mode=nr.weight_mode,
            weight_status=nr.weight_status,
            grade_value=nr.grade_value,
            grade_scale_max=nr.grade_scale_max,
            grade_kind=nr.grade_kind,
            grade_status=nr.grade_status,
            observed_at=nr.observed_at,
            rule_payload=nr.rule_payload,
            rule_verification_status=nr.rule_verification_status,
            provenance=prov,
        )
        merged.append(updated)
        seen_ids.add(updated.external_id)

    return merged


def parse_pamplemousse_academic_html(html: str) -> list[AcademicRecord]:
    """Extract published grades, coefficients, UEs, and subjects from sanitized HTML.

    Automatically dispatches to Inscriptions (`UE|Matière|Code|Coef|ECTS|Sem`) or
    Detailed Notes (`BULLETIN DÉTAILLÉ`) parsers when their structures are present,
    falling back to generic academic table parsing for simple synthetic tables.
    """
    insc = parse_pamplemousse_inscriptions_html(html)
    if any(r.record_type == "subject" for r in insc):
        return insc
    notes = parse_pamplemousse_notes_html(html)
    if any(r.record_type in ("subject", "assessment", "aggregate_grade") for r in notes):
        return notes

    parser = _PamplemousseHTMLTableParser()
    parser.feed(html)
    if len(parser.rows) < 2:
        return []

    headers = [h.lower() for h in parser.rows[0][0]]
    records: list[AcademicRecord] = []

    def col(row: list[str], *candidates: str) -> str | None:
        for cand in candidates:
            for idx, h in enumerate(headers):
                if cand in h and idx < len(row):
                    val = row[idx].strip()
                    if val:
                        return val
        return None

    for idx, (row, _) in enumerate(parser.rows[1:], start=1):
        subject = col(row, "matière", "matiere", "subject", "module", "épreuve", "epreuve")
        if not subject:
            continue
        ue = col(row, "ue", "unité", "unite")
        code = col(row, "code")
        sem = col(row, "semestre", "semester")
        year = col(row, "année", "annee", "year")
        spec = col(row, "filière", "filiere", "spécial", "special")
        coef = parse_decimal_strict(col(row, "coef", "poids", "weight"))
        grade = parse_decimal_strict(col(row, "note", "grade", "mark"))
        scale = parse_decimal_strict(col(row, "barème", "bareme", "scale"))

        ext_seed = f"{year or ''}|{sem or ''}|{ue or ''}|{code or subject}"
        ext_id = f"pamplemousse:academic:{hashlib.sha1(ext_seed.encode('utf-8')).hexdigest()[:16]}"

        records.append(
            AcademicRecord(
                source_kind=SourceKind.PAMPLEMOUSSE,
                external_id=ext_id,
                record_type="grade" if grade is not None else "subject",
                title=subject,
                parent_external_id=f"pamplemousse:ue:{ue}" if ue else None,
                academic_year=year,
                semester=sem,
                specialization=spec,
                subject_code=code,
                weight=coef,
                weight_mode="coefficient" if coef is not None else None,
                weight_status=ValueStatus.CONFIRMED if coef is not None else ValueStatus.MISSING,
                grade_value=grade,
                grade_scale_max=scale if scale is not None else (Decimal("20") if grade is not None else None),
                grade_kind="actual" if grade is not None else None,
                grade_status=ValueStatus.CONFIRMED if grade is not None else ValueStatus.MISSING,
                observed_at=utc_now_iso(),
                rule_verification_status=ValueStatus.UNKNOWN,
                provenance={"format": "html", "row": idx, "retrieved_at": utc_now_iso()},
            )
        )
    return records


PAMPLEMOUSSE_SYNC_SCOPES: frozenset[str] = frozenset({"timetable", "grades", "catalog"})
_PAMPLEMOUSSE_SCOPE_ALIASES: dict[str, str] = {
    "timetable": "timetable",
    "edt": "timetable",
    "schedule": "timetable",
    "ical": "timetable",
    "grades": "grades",
    "notes": "grades",
    "marks": "grades",
    "catalog": "catalog",
    "academic_catalog": "catalog",
    "inscriptions": "catalog",
    "coefficients": "catalog",
}


def resolve_pamplemousse_scopes(raw: Any) -> set[str]:
    """Normalize optional Pamplemousse sync slice selection into a subset of {timetable, grades, catalog}."""
    if raw is None:
        return set(PAMPLEMOUSSE_SYNC_SCOPES)
    if isinstance(raw, str):
        tokens = [t.strip().lower() for t in re.split(r"[,;\s]+", raw) if t.strip()]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        tokens = [str(t).strip().lower() for t in raw if str(t).strip()]
    else:
        return set(PAMPLEMOUSSE_SYNC_SCOPES)
    if not tokens or "all" in tokens:
        return set(PAMPLEMOUSSE_SYNC_SCOPES)
    resolved: set[str] = set()
    for tok in tokens:
        mapped = _PAMPLEMOUSSE_SCOPE_ALIASES.get(tok)
        if mapped is None:
            raise ValueError(
                f"Unsupported Pamplemousse sync scope '{tok}'; expected one of timetable, grades, catalog, all."
            )
        resolved.add(mapped)
    return resolved


_REAL_REQUESTS_SESSION = requests.Session


class PamplemousseConnector(SourceConnector):
    """Authorized read-only Pamplemousse connector with ICS/CSV/JSON fallbacks.

    Reuses a thread-local pooled ``requests.Session`` (``HTTPAdapter`` with TCP/TLS
    keep-alive) hydrated from the private ``session_cookies.json`` snapshot,
    automatically invalidating whenever cookies change on disk or ``requests.Session``
    is replaced in unit tests.
    """

    source_kind = SourceKind.PAMPLEMOUSSE

    def __init__(
        self,
        config: SourcesRuntimeConfig,
        *,
        auth_adapter: EnsaiAuthAdapter | None = None,
        page_fetcher: Callable[[str], str] | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._config = config
        self._auth = auth_adapter or EnsaiAuthAdapter(config)
        self._page_fetcher = page_fetcher
        self._rate_limiter = rate_limiter or RateLimiter(config.rate_limit_min_interval_sec)
        self._http_local = threading.local()
        self._cookie_cache_lock = threading.Lock()
        self._cookie_snapshot_cache: tuple[tuple[int, int], list[dict[str, Any]]] | None = None
        self.last_stats: dict[str, Any] = {}

    def close(self) -> None:
        """Close any cached thread-local pooled HTTP session cleanly."""
        cached = getattr(self._http_local, "cookie_session", None)
        if cached is not None:
            try:
                cached[0].close()
            except Exception:
                pass
            self._http_local.cookie_session = None

    def _load_persisted_pamplemousse_cookies_with_sig(self) -> tuple[list[dict[str, Any]], tuple[int, int] | None]:
        """Read allowlisted Pamplemousse cookies, cached by file ``(mtime_ns, size)``."""
        profile_dir = self._auth.profile_dir_for(SourceKind.PAMPLEMOUSSE)
        cookie_path = profile_dir / SESSION_COOKIES_FILENAME
        if not cookie_path.is_file() or cookie_path.is_symlink():
            with self._cookie_cache_lock:
                self._cookie_snapshot_cache = None
            return [], None
        try:
            st = cookie_path.stat()
            sig = (int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            return [], None
        with self._cookie_cache_lock:
            cached = self._cookie_snapshot_cache
            if cached is not None and cached[0] == sig:
                return cached[1], sig
        try:
            fd = os.open(str(cookie_path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return [], None
        raw_cookies = data.get("cookies", []) if isinstance(data, dict) else []
        pamp_host = urlparse(self._config.pamplemousse_base_url).netloc.lower()
        valid: list[dict[str, Any]] = []
        for c in self._auth._filter_institutional_cookies(raw_cookies):
            dom = str(c.get("domain") or "").lstrip(".").lower()
            if dom and (pamp_host == dom or pamp_host.endswith(f".{dom}")):
                valid.append(c)
        with self._cookie_cache_lock:
            self._cookie_snapshot_cache = (sig, valid)
        return valid, sig

    def _get_pooled_cookie_session(self) -> tuple[Any, bool] | None:
        """Return a thread-local pooled ``requests.Session`` for Pamplemousse HTTP calls."""
        cookies, cookie_sig = self._load_persisted_pamplemousse_cookies_with_sig()
        if not cookies:
            return None
        session_cls = requests.Session
        is_real_session = session_cls is _REAL_REQUESTS_SESSION
        cached = getattr(self._http_local, "cookie_session", None)
        if (
            is_real_session
            and cached is not None
            and cached[1] is session_cls
            and cached[2] == cookie_sig
        ):
            return cached[0], True
        if cached is not None:
            try:
                cached[0].close()
            except Exception:
                pass
            self._http_local.cookie_session = None

        session = session_cls()
        if is_real_session and hasattr(session, "mount"):
            adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
        session.headers.update({"User-Agent": DEFAULT_ENSAI_USER_AGENT})
        for c in cookies:
            session.cookies.set(
                str(c["name"]),
                str(c["value"]),
                domain=str(c.get("domain") or "").lstrip(".") or None,
                path=str(c.get("path") or "/"),
            )
        if is_real_session:
            self._http_local.cookie_session = (session, session_cls, cookie_sig)
            return session, True
        return session, False

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            source=SourceKind.PAMPLEMOUSSE,
            supports_documents=False,
            supports_events=True,
            supports_assignments=False,
            supports_academic_records=True,
            supports_incremental=True,
            supports_full_scan=True,
            supports_download=False,
            read_only=True,
            fallback_formats=("ics", "csv", "json"),
        )

    def authorize(self, config: dict[str, Any] | None = None) -> AuthStatus:
        interactive = bool((config or {}).get("interactive", False))
        return self._auth.ensure_authenticated(SourceKind.PAMPLEMOUSSE, interactive=interactive)

    def _cookie_session_fetch_html(self, url: str) -> str | None:
        """Fast authenticated HTML fetch using a pooled Pamplemousse cookie session.

        Returns ``None`` when persisted cookies are absent or expired/redirected to CAS
        so callers fall back seamlessly to Playwright protected-page access.
        """
        pooled = self._get_pooled_cookie_session()
        if pooled is None:
            return None
        session, is_pooled = pooled

        current_url = url
        try:
            for hop in range(6):
                if not is_same_origin(current_url, self._config.pamplemousse_base_url):
                    if is_same_origin(current_url, self._config.cas_login_url):
                        return None
                    raise PermissionDeniedError(
                        f"Refused to navigate outside Pamplemousse origin: {sanitize_url_for_logs(current_url)}"
                    )
                resp = session.get(
                    current_url,
                    timeout=self._config.request_timeout_sec,
                    allow_redirects=False,
                )
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location") or resp.headers.get("location")
                    if not loc:
                        return None
                    next_url = urljoin(current_url, str(loc))
                    if is_same_origin(next_url, self._config.cas_login_url):
                        return None
                    if not is_same_origin(next_url, self._config.pamplemousse_base_url):
                        raise PermissionDeniedError(
                            f"Blocked cross-origin redirect before follow: {sanitize_url_for_logs(next_url)}"
                        )
                    if hop >= 5:
                        return None
                    current_url = next_url
                    continue
                if resp.status_code != 200:
                    return None
                html_text = resp.text or ""
                probe = self._auth.classify_page_state(
                    SourceKind.PAMPLEMOUSSE,
                    requested_url=url,
                    current_url=current_url,
                    html_content=html_text,
                    has_service_cookies=True,
                )
                if not probe.authenticated:
                    return None
                return html_text
            return None
        except PermissionDeniedError:
            raise
        except Exception:
            return None
        finally:
            if not is_pooled:
                session.close()

    def _fetch_html(
        self,
        url: str,
        *,
        interactive: bool = False,
        allow_auto_reauth: bool | None = None,
    ) -> str:
        if not is_same_origin(url, self._config.pamplemousse_base_url):
            raise PermissionDeniedError(
                f"Refused to navigate outside Pamplemousse origin: {sanitize_url_for_logs(url)}"
            )
        self._rate_limiter.wait()
        if self._page_fetcher is not None:
            return retry_with_backoff(
                lambda: self._page_fetcher(url),
                max_retries=self._config.max_retries,
            )
        if self._auth._context_factory is None:
            fast_html = self._cookie_session_fetch_html(url)
            if fast_html is not None:
                return fast_html
        auto_allowed = (
            self._config.ensai_allow_automated_credential_submit
            if allow_auto_reauth is None
            else allow_auto_reauth
        )
        can_reauth = interactive or auto_allowed
        html, _final_url = self._auth.fetch_protected_page(
            SourceKind.PAMPLEMOUSSE,
            url,
            interactive=interactive,
            max_reauth_attempts=1 if can_reauth else 0,
        )
        return html

    def _discover_pamplemousse_subpages(self, html: str, base_url: str, *, max_links: int = 6) -> list[str]:
        """Discover same-origin read-only timetable/inscriptions/notes/ICS links from portal HTML."""
        nav = discover_pamplemousse_nav_links(html, base_url)
        discovered: list[str] = []
        seen: set[str] = {base_url}
        for key in ("icalendar", "edt", "inscriptions", "notes"):
            u = nav.get(key)
            if u and u not in seen:
                seen.add(u)
                discovered.append(u)
        # Legacy keyword href fallback
        keywords = ("emploi", "planning", "edt", "agenda", "calendrier", "note", "releve", "scolarit", ".ics")
        hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
        for raw in hrefs:
            if len(discovered) >= max_links:
                break
            cleaned = raw.replace("&amp;", "&").strip()
            if not cleaned or cleaned.startswith("#") or cleaned.lower().startswith("javascript:"):
                continue
            full = urljoin(base_url, cleaned)
            if full in seen or not is_same_origin(full, self._config.pamplemousse_base_url):
                continue
            low = full.lower()
            if any(k in low for k in _FORBIDDEN_PAMPLEMOUSSE_KEYWORDS):
                continue
            if any(k in low for k in keywords):
                seen.add(full)
                discovered.append(full)
        return discovered[:max_links]

    def _load_persisted_pamplemousse_cookies(self) -> list[dict[str, Any]]:
        """Read allowlisted Pamplemousse cookies from the private 0o600 session snapshot."""
        cookies, _ = self._load_persisted_pamplemousse_cookies_with_sig()
        return cookies

    def _http_get_text_with_cookies(self, url: str) -> tuple[str, str] | None:
        """Fetch raw text (ICS or HTML) directly via a pooled authenticated ``requests.Session``."""
        if not _is_allowed_pamplemousse_ical_url(url, self._config.pamplemousse_base_url):
            raise PermissionDeniedError(
                f"Blocked unauthorized Pamplemousse iCalendar host: {sanitize_url_for_logs(url)}"
            )
        pooled = self._get_pooled_cookie_session()
        if pooled is None:
            return None
        session, is_pooled = pooled
        try:
            current_url = url
            resp: requests.Response | None = None
            for _hop in range(6):
                if not _is_allowed_pamplemousse_ical_url(current_url, self._config.pamplemousse_base_url):
                    if is_same_origin(current_url, self._config.cas_login_url):
                        raise AuthenticationRequiredError(
                            "Pamplemousse session expired (redirected to ENSAI CAS during iCalendar fetch)."
                        )
                    raise PermissionDeniedError(
                        f"Blocked cross-origin redirect during iCalendar fetch: {sanitize_url_for_logs(current_url)}"
                    )
                resp = session.get(
                    current_url,
                    timeout=self._config.request_timeout_sec,
                    allow_redirects=False,
                )
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        break
                    next_url = urljoin(current_url, loc)
                    if is_same_origin(next_url, self._config.cas_login_url):
                        raise AuthenticationRequiredError(
                            "Pamplemousse session expired (redirected to ENSAI CAS during iCalendar fetch)."
                        )
                    current_url = next_url
                    continue
                break
            if resp is None or resp.status_code >= 400:
                return None
            raw_bytes = resp.content
            try:
                text = raw_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw_bytes.decode("latin-1", errors="replace")
            return text, current_url
        finally:
            if not is_pooled:
                session.close()

    def _fetch_ical_payload(
        self,
        url: str,
        *,
        interactive: bool = False,
        allow_auto_reauth: bool | None = None,
    ) -> tuple[str | None, list[str]]:
        """Fetch an authorized Pamplemousse iCalendar (.ics) feed safely without Playwright page.goto download crashes.

        Supports:
        1. Direct `.ics` response (`BEGIN:VCALENDAR`) via HTTP/APIRequestContext (never fails on Content-Disposition: attachment).
        2. Intermediate HTML iCalendar page containing a `webcal://` or `.ics` subscription URL or download link.
        3. Injected `page_fetcher` / fake context for deterministic unit tests.
        """
        candidates_tried: list[str] = []

        # Deterministic unit-test / injected page_fetcher path
        if self._page_fetcher is not None or self._auth._context_factory is not None:
            candidates_tried.append(sanitize_url_for_logs(url))
            body = self._fetch_html(
                url,
                interactive=interactive,
                allow_auto_reauth=allow_auto_reauth,
            )
            if "BEGIN:VCALENDAR" in body:
                return body, candidates_tried
            for nested in extract_ical_feed_urls(body, url):
                if nested == url:
                    continue
                candidates_tried.append(sanitize_url_for_logs(nested))
                nested_body = self._fetch_html(
                    nested,
                    interactive=interactive,
                    allow_auto_reauth=allow_auto_reauth,
                )
                if "BEGIN:VCALENDAR" in nested_body:
                    return nested_body, candidates_tried
            return None, candidates_tried

        # 1. Fast direct authenticated cookie-backed HTTP GET (handles .ics attachments cleanly)
        try:
            candidates_tried.append(sanitize_url_for_logs(url))
            direct = self._http_get_text_with_cookies(url)
            if direct is not None:
                body, final_url = direct
                if "BEGIN:VCALENDAR" in body:
                    return body, candidates_tried
                for nested in extract_ical_feed_urls(body, final_url):
                    if nested == url:
                        continue
                    candidates_tried.append(sanitize_url_for_logs(nested))
                    sub = self._http_get_text_with_cookies(nested)
                    if sub is not None and "BEGIN:VCALENDAR" in sub[0]:
                        return sub[0], candidates_tried
        except AuthenticationRequiredError:
            pass
        except PermissionDeniedError:
            raise
        except Exception:
            pass

        # 2. Playwright APIRequestContext + optional download capture on the shared persistent profile
        profile_dir = self._auth.profile_dir_for(SourceKind.PAMPLEMOUSSE)
        with self._auth.open_context(SourceKind.PAMPLEMOUSSE, headless=True) as context:
            api_req = getattr(context, "request", None)
            if api_req is not None:
                def _api_fetch(target_u: str) -> tuple[str, str] | None:
                    if not _is_allowed_pamplemousse_ical_url(target_u, self._config.pamplemousse_base_url):
                        return None
                    resp = api_req.get(target_u, timeout=self._config.ensai_timeout_ms)
                    final_u = getattr(resp, "url", target_u) or target_u
                    if is_same_origin(final_u, self._config.cas_login_url):
                        raise AuthenticationRequiredError(
                            "Pamplemousse session expired (redirected to ENSAI CAS during iCalendar fetch)."
                        )
                    if getattr(resp, "status", 500) >= 400:
                        return None
                    raw = resp.body()
                    try:
                        txt = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        txt = raw.decode("latin-1", errors="replace")
                    return txt, final_u

                res = _api_fetch(url)
                if res is not None:
                    body, final_u = res
                    self._auth._save_session_cookies(context, profile_dir)
                    if "BEGIN:VCALENDAR" in body:
                        return body, candidates_tried
                    for nested in extract_ical_feed_urls(body, final_u):
                        if nested == url:
                            continue
                        candidates_tried.append(sanitize_url_for_logs(nested))
                        sub_res = _api_fetch(nested)
                        if sub_res is not None and "BEGIN:VCALENDAR" in sub_res[0]:
                            return sub_res[0], candidates_tried
        return None, candidates_tried

    def _fetch_edt_playwright(
        self,
        edt_url: str,
        *,
        academic_year: str | None = None,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
        interactive: bool = False,
        allow_auto_reauth: bool | None = None,
        edt_diag: dict[str, Any] | None = None,
    ) -> tuple[list[CalendarEventRecord], str]:
        """Fetch Pamplemousse EDT (`index.php?p=121`) capturing passive FullCalendar JSON, inline scripts & DOM views.

        Strictly read-only:
        - Passively intercepts same-origin XHR/fetch JSON or ICS event feeds regardless of Content-Type.
        - Extracts inline `<script>` FullCalendar event arrays when embedded server-side.
        - Switches read-only to 'Liste mois' / 'Liste semaine' and steps across bounded months
          (1 month back + 3 months ahead) to capture full semester timetables even when the current week is empty.
        - Never clicks evaluation, logout, payment, or administrative controls.
        """
        if self._page_fetcher is not None or self._auth._context_factory is not None:
            html = self._fetch_html(
                edt_url,
                interactive=interactive,
                allow_auto_reauth=allow_auto_reauth,
            )
            evs: list[CalendarEventRecord] = []
            inline_json = extract_inline_fullcalendar_json(html)
            if inline_json:
                evs = parse_fullcalendar_json_events(
                    inline_json,
                    academic_year=academic_year,
                    range_start=range_start,
                    range_end=range_end,
                )
            if not evs:
                evs = parse_fullcalendar_list_html(
                    html,
                    academic_year=academic_year,
                    range_start=range_start,
                    range_end=range_end,
                )
            if not evs:
                evs = parse_pamplemousse_timetable_html(html)
            return evs, html

        captured_json: list[Any] = []
        captured_ics: list[str] = []
        captured_list_htmls: list[str] = []
        captured_dom_events: list[dict[str, Any]] = []
        captured_html = ""
        months_scanned = 0
        profile_dir = self._auth.profile_dir_for(SourceKind.PAMPLEMOUSSE)
        with self._auth.open_context(SourceKind.PAMPLEMOUSSE, headless=True) as context:
            page = context.pages[0] if context.pages else context.new_page()

            def _on_response(resp: Any) -> None:
                try:
                    r_url = str(getattr(resp, "url", "") or "")
                    if not is_same_origin(r_url, self._config.pamplemousse_base_url):
                        return
                    if getattr(resp, "status", 0) != 200:
                        return
                    ct = (resp.headers.get("content-type") or "").lower()
                    req = getattr(resp, "request", None)
                    res_type = str(getattr(req, "resource_type", "") or "").lower()
                    is_candidate_type = (
                        res_type in ("xhr", "fetch")
                        or "json" in ct
                        or "calendar" in ct
                        or "javascript" in ct
                        or "text/plain" in ct
                    )
                    if not is_candidate_type:
                        return
                    raw_text = resp.text()
                    stripped = raw_text.lstrip()
                    if not stripped:
                        return
                    if "BEGIN:VCALENDAR" in stripped[:400]:
                        captured_ics.append(raw_text)
                        return
                    if stripped[0] not in "[{":
                        return
                    payload = json.loads(stripped)
                    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                        if any(
                            k in payload[0]
                            for k in ("start", "startStr", "debut", "date_debut", "title", "libelle")
                        ):
                            captured_json.append(payload)
                    elif isinstance(payload, dict):
                        for k in ("events", "data", "items", "result", "cours", "seances"):
                            val = payload.get(k)
                            if (
                                isinstance(val, list)
                                and val
                                and isinstance(val[0], dict)
                                and any(fk in val[0] for fk in ("start", "startStr", "debut", "date_debut", "title"))
                            ):
                                captured_json.append(val)
                                break
                except Exception:
                    return

            if hasattr(page, "on"):
                try:
                    page.on("response", _on_response)
                except Exception:
                    pass

            probe = self._auth._probe_on_page(
                page,
                SourceKind.PAMPLEMOUSSE,
                profile_dir=profile_dir,
                allow_myensai_fallback=False,
                target_url=edt_url,
                context=context,
                phase="pamplemousse_edt",
            )
            if not probe.authenticated:
                if not probe.origin_verified:
                    raise PermissionDeniedError(probe.detail)
                raise AuthenticationRequiredError(probe.detail)

            self._auth._save_session_cookies(context, profile_dir)
            if hasattr(page, "wait_for_selector"):
                try:
                    page.wait_for_selector(
                        ".fc-event, .fc-list-event, .fc-list-item, .fc-toolbar, table",
                        timeout=8_000,
                    )
                except Exception:
                    pass
            captured_html = self._auth._collect_page_html(
                page,
                self._auth.service_url_for(SourceKind.PAMPLEMOUSSE).split("://", 1)[-1].split("/", 1)[0].lower(),
            )
            captured_list_htmls.append(captured_html)

            def _extract_dom_snapshot() -> None:
                if not hasattr(page, "evaluate"):
                    return
                try:
                    dom_rows = page.evaluate(
                        """() => {
                            const out = [];
                            // FullCalendar v5/v6 and v3/v4 list views
                            let curDate = null;
                            document.querySelectorAll('tr.fc-list-day, tr.fc-list-heading, tr.fc-list-event, tr.fc-list-item').forEach(tr => {
                                const cls = (tr.className || '').toLowerCase();
                                if (cls.includes('fc-list-day') || cls.includes('fc-list-heading')) {
                                    curDate = tr.getAttribute('data-date') || tr.innerText.trim();
                                } else if (curDate) {
                                    const timeEl = tr.querySelector('.fc-list-event-time, .fc-list-item-time');
                                    const titleEl = tr.querySelector('.fc-list-event-title, .fc-list-item-title');
                                    const timeTxt = timeEl ? timeEl.innerText.trim() : '';
                                    const titleTxt = titleEl ? titleEl.innerText.trim() : tr.innerText.trim();
                                    if (titleTxt) out.push({kind: 'list', date: curDate, time: timeTxt, title: titleTxt});
                                }
                            });
                            return out;
                        }"""
                    )
                    if isinstance(dom_rows, list) and dom_rows:
                        captured_dom_events.extend(dom_rows)
                except Exception:
                    pass

            # Switch read-only to 'Liste mois' / 'Liste semaine' and step across bounded months
            if hasattr(page, "locator"):
                for btn_sel in (
                    'button:has-text("Liste mois")',
                    ".fc-listMonth-button",
                    'button:has-text("Liste semaine")',
                    ".fc-listWeek-button",
                    'button:has-text("Mois")',
                    ".fc-dayGridMonth-button",
                ):
                    try:
                        loc = page.locator(btn_sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.click(timeout=3_000)
                            self._auth._settle_navigation(page, timeout_ms=5_000)
                            captured_list_htmls.append(page.content())
                            _extract_dom_snapshot()
                            months_scanned += 1
                            break
                    except Exception:
                        continue

                # Bounded multi-month scan (1 step back + up to 3 steps ahead) to cover full semester timetable
                prev_sel = ".fc-prev-button, button[aria-label*='prev' i], button[aria-label*='préc' i]"
                next_sel = ".fc-next-button, button[aria-label*='next' i], button[aria-label*='suiv' i]"
                try:
                    prev_loc = page.locator(prev_sel)
                    next_loc = page.locator(next_sel)
                    if prev_loc.count() > 0 and prev_loc.first.is_visible() and next_loc.count() > 0 and next_loc.first.is_visible():
                        prev_loc.first.click(timeout=3_000)
                        self._auth._settle_navigation(page, timeout_ms=4_000)
                        captured_list_htmls.append(page.content())
                        _extract_dom_snapshot()
                        months_scanned += 1
                        # Return to starting month then step forward 3 periods
                        for _step in range(4):
                            next_loc.first.click(timeout=3_000)
                            self._auth._settle_navigation(page, timeout_ms=4_000)
                            if _step >= 1:
                                captured_list_htmls.append(page.content())
                                _extract_dom_snapshot()
                                months_scanned += 1
                except Exception:
                    pass

        events: list[CalendarEventRecord] = []
        seen_ids: set[str] = set()
        strategy = "none"

        for ics_blob in captured_ics:
            for ev in self.import_ics_text(ics_blob, range_start=range_start, range_end=range_end):
                if ev.external_id not in seen_ids:
                    seen_ids.add(ev.external_id)
                    events.append(ev)
                    strategy = "ics_xhr"

        for batch in captured_json:
            for ev in parse_fullcalendar_json_events(
                batch,
                academic_year=academic_year,
                range_start=range_start,
                range_end=range_end,
            ):
                if ev.external_id not in seen_ids:
                    seen_ids.add(ev.external_id)
                    events.append(ev)
                    strategy = "fullcalendar_json"

        if not events and captured_html:
            inline_entries = extract_inline_fullcalendar_json(captured_html)
            for ev in parse_fullcalendar_json_events(
                inline_entries,
                academic_year=academic_year,
                range_start=range_start,
                range_end=range_end,
            ):
                if ev.external_id not in seen_ids:
                    seen_ids.add(ev.external_id)
                    events.append(ev)
                    strategy = "fullcalendar_inline_json"

        if not events:
            for list_html in captured_list_htmls:
                for ev in parse_fullcalendar_list_html(
                    list_html,
                    academic_year=academic_year,
                    range_start=range_start,
                    range_end=range_end,
                ):
                    if ev.external_id not in seen_ids:
                        seen_ids.add(ev.external_id)
                        events.append(ev)
                        strategy = "fullcalendar_list_dom"

        if not events and captured_dom_events:
            for idx, row in enumerate(captured_dom_events, start=1):
                day_iso = _parse_french_date_header(str(row.get("date") or ""))
                title = str(row.get("title") or "").strip()
                time_txt = str(row.get("time") or "").lower()
                if not day_iso or not title:
                    continue
                m_times = re.findall(r"(\d{1,2}[:h]\d{2})", time_txt)
                if len(m_times) < 2:
                    continue
                t0 = m_times[0].replace("h", ":")
                t1 = m_times[1].replace("h", ":")
                if len(t0) == 4:
                    t0 = "0" + t0
                if len(t1) == 4:
                    t1 = "0" + t1
                st_utc, _ = normalize_timestamp_utc(f"{day_iso} {t0}")
                en_utc, _ = normalize_timestamp_utc(f"{day_iso} {t1}")
                if not st_utc or not en_utc:
                    continue
                teacher, group, subject_hint = _extract_pamplemousse_metadata(title, None)
                ev_type, confirm_req, confirm_reason = classify_timetable_event(title, group=group)
                ext_id = f"pamplemousse:edt:{hashlib.sha1(f'dom|{title}|{st_utc}|{en_utc}'.encode('utf-8')).hexdigest()[:16]}"
                if ext_id in seen_ids:
                    continue
                seen_ids.add(ext_id)
                events.append(
                    CalendarEventRecord(
                        source_kind=SourceKind.PAMPLEMOUSSE,
                        external_id=ext_id,
                        title=title,
                        event_type=ev_type,
                        start_at=st_utc,
                        end_at=en_utc,
                        original_timezone="Europe/Paris",
                        teacher=teacher,
                        group=group,
                        subject_hint=subject_hint,
                        confirmation_required=confirm_req,
                        confirmation_reason=confirm_reason,
                        provenance={
                            "format": "fullcalendar_dom_eval",
                            "academic_year": academic_year,
                            "duration_minutes": _compute_duration_minutes(st_utc, en_utc),
                            "row": idx,
                            "retrieved_at": utc_now_iso(),
                        },
                    )
                )
                strategy = "fullcalendar_dom_eval"

        if not events and captured_html:
            for ev in parse_pamplemousse_timetable_html(captured_html):
                if ev.external_id not in seen_ids:
                    seen_ids.add(ev.external_id)
                    events.append(ev)
                    strategy = "html_table"

        if edt_diag is not None:
            edt_diag["edt_xhr_captured"] = len(captured_json) + len(captured_ics)
            edt_diag["edt_months_scanned"] = months_scanned
            if events:
                edt_diag["timetable_strategy"] = strategy
        return events, captured_html

    def import_ics_text(
        self,
        ics_text: str,
        *,
        academic_year: str | None = None,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
    ) -> list[CalendarEventRecord]:
        """Parse Pamplemousse timetable from an authorized ICS export."""
        return parse_ics_events(
            ics_text,
            source_kind=SourceKind.PAMPLEMOUSSE,
            academic_year=academic_year,
            range_start=range_start,
            range_end=range_end,
        )

    def import_csv_text(self, csv_text: str) -> tuple[list[CalendarEventRecord], list[AcademicRecord]]:
        """Import timetable or academic records from CSV fallback (handles ',' and ';')."""
        sample = csv_text[:2048]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")), delimiter=delimiter)
        events: list[CalendarEventRecord] = []
        academics: list[AcademicRecord] = []

        for idx, row in enumerate(reader, start=1):
            norm = {k.strip().lower(): (v.strip() if isinstance(v, str) else "") for k, v in row.items() if k}
            if any(k in norm for k in ("start", "start_at", "debut", "début", "date_debut")):
                title = (
                    norm.get("title")
                    or norm.get("subject")
                    or norm.get("matiere")
                    or norm.get("matière")
                    or norm.get("cours")
                    or f"Event {idx}"
                )
                start_raw = norm.get("start_at") or norm.get("start") or norm.get("debut") or norm.get("début")
                end_raw = norm.get("end_at") or norm.get("end") or norm.get("fin")
                start_utc, tz_name = normalize_timestamp_utc(start_raw)
                end_utc, _ = normalize_timestamp_utc(end_raw)
                ev_type, confirm_req, confirm_reason = classify_timetable_event(
                    title,
                    category_hint=norm.get("type") or norm.get("category"),
                    color_hint=norm.get("color"),
                    group=norm.get("group") or norm.get("groupe"),
                )
                ext_id = (
                    norm.get("external_id")
                    or norm.get("id")
                    or f"pamplemousse:csv_event:{idx}:{hashlib.sha1(f'{title}|{start_utc}'.encode()).hexdigest()[:10]}"
                )
                events.append(
                    CalendarEventRecord(
                        source_kind=SourceKind.PAMPLEMOUSSE,
                        external_id=ext_id,
                        title=title,
                        event_type=ev_type,
                        start_at=start_utc,
                        end_at=end_utc,
                        original_timezone=tz_name or "Europe/Paris",
                        room=norm.get("room") or norm.get("salle") or None,
                        teacher=norm.get("teacher") or norm.get("enseignant") or None,
                        group=norm.get("group") or norm.get("groupe") or None,
                        subject_hint=norm.get("subject") or title,
                        course_id=norm.get("course_id") or norm.get("code") or None,
                        confirmation_required=confirm_req,
                        confirmation_reason=confirm_reason,
                        provenance={"format": "csv", "row": idx, "retrieved_at": utc_now_iso()},
                    )
                )
            else:
                title = (
                    norm.get("title")
                    or norm.get("subject")
                    or norm.get("matiere")
                    or norm.get("matière")
                    or norm.get("assessment")
                )
                if not title:
                    continue
                coef = parse_decimal_strict(norm.get("coefficient") or norm.get("weight") or norm.get("coef"))
                grade = parse_decimal_strict(norm.get("grade") or norm.get("note") or norm.get("mark"))
                scale = parse_decimal_strict(norm.get("scale_max") or norm.get("bareme") or norm.get("barème"))
                w_mode = norm.get("weight_mode") or ("coefficient" if coef is not None else None)
                ext_id = (
                    norm.get("external_id")
                    or norm.get("id")
                    or f"pamplemousse:csv_academic:{idx}:{hashlib.sha1(title.encode()).hexdigest()[:10]}"
                )
                academics.append(
                    AcademicRecord(
                        source_kind=SourceKind.PAMPLEMOUSSE,
                        external_id=ext_id,
                        record_type=norm.get("record_type") or ("grade" if grade is not None else "subject"),
                        title=title,
                        parent_external_id=norm.get("parent_id") or norm.get("ue") or None,
                        academic_year=norm.get("academic_year") or norm.get("year") or None,
                        cohort=norm.get("cohort") or None,
                        semester=norm.get("semester") or norm.get("semestre") or None,
                        specialization=norm.get("specialization") or norm.get("filiere") or None,
                        subject_code=norm.get("subject_code") or norm.get("code") or None,
                        weight=coef,
                        weight_mode=w_mode,
                        weight_status=ValueStatus.CONFIRMED if coef is not None else ValueStatus.MISSING,
                        grade_value=grade,
                        grade_scale_max=scale if scale is not None else (Decimal("20") if grade is not None else None),
                        grade_kind="actual" if grade is not None else None,
                        grade_status=ValueStatus.CONFIRMED if grade is not None else ValueStatus.MISSING,
                        observed_at=norm.get("observed_at") or utc_now_iso(),
                        rule_verification_status=ValueStatus.UNKNOWN,
                        provenance={"format": "csv", "row": idx, "retrieved_at": utc_now_iso()},
                    )
                )

        return events, academics

    def import_json_data(
        self,
        payload: dict[str, Any] | list[dict[str, Any]] | str,
    ) -> tuple[list[CalendarEventRecord], list[AcademicRecord]]:
        """Import structured Pamplemousse timetable events and/or academic records from JSON."""
        data = json.loads(payload) if isinstance(payload, str) else payload
        raw_events: list[dict[str, Any]] = []
        raw_academics: list[dict[str, Any]] = []

        if isinstance(data, list):
            for entry in data:
                if "start_at" in entry or "start" in entry:
                    raw_events.append(entry)
                else:
                    raw_academics.append(entry)
        elif isinstance(data, dict):
            raw_events = list(data.get("events") or data.get("timetable") or [])
            raw_academics = list(data.get("academic_records") or data.get("grades") or data.get("subjects") or [])

        events: list[CalendarEventRecord] = []
        for idx, entry in enumerate(raw_events, start=1):
            title = str(entry.get("title") or entry.get("subject") or f"Event {idx}")
            start_utc, tz_name = normalize_timestamp_utc(entry.get("start_at") or entry.get("start"))
            end_utc, _ = normalize_timestamp_utc(entry.get("end_at") or entry.get("end"))
            ev_type, confirm_req, confirm_reason = classify_timetable_event(
                title,
                category_hint=entry.get("type") or entry.get("category"),
                color_hint=entry.get("color"),
                group=entry.get("group"),
            )
            ext_id = str(
                entry.get("external_id")
                or entry.get("id")
                or f"pamplemousse:json_event:{idx}:{hashlib.sha1(f'{title}|{start_utc}'.encode()).hexdigest()[:10]}"
            )
            events.append(
                CalendarEventRecord(
                    source_kind=SourceKind.PAMPLEMOUSSE,
                    external_id=ext_id,
                    title=title,
                    event_type=ev_type,
                    start_at=start_utc,
                    end_at=end_utc,
                    original_timezone=entry.get("timezone") or tz_name or "Europe/Paris",
                    room=entry.get("room"),
                    teacher=entry.get("teacher"),
                    group=entry.get("group"),
                    subject_hint=entry.get("subject") or title,
                    course_id=entry.get("course_id"),
                    confirmation_required=confirm_req,
                    confirmation_reason=confirm_reason,
                    provenance={"format": "json", "index": idx, "retrieved_at": utc_now_iso()},
                )
            )

        academics: list[AcademicRecord] = []
        for idx, entry in enumerate(raw_academics, start=1):
            title = str(entry.get("title") or entry.get("subject") or entry.get("name") or f"Record {idx}")
            coef = parse_decimal_strict(entry.get("weight") if "weight" in entry else entry.get("coefficient"))
            grade = parse_decimal_strict(entry.get("grade_value") if "grade_value" in entry else entry.get("grade"))
            scale = parse_decimal_strict(entry.get("grade_scale_max") or entry.get("scale_max"))
            rules = entry.get("rules") if isinstance(entry.get("rules"), dict) else None
            rule_verified = (
                ValueStatus.CONFIRMED
                if rules and entry.get("rule_verified") is True
                else ValueStatus.UNKNOWN
            )
            ext_id = str(
                entry.get("external_id")
                or entry.get("id")
                or f"pamplemousse:json_academic:{idx}:{hashlib.sha1(title.encode()).hexdigest()[:10]}"
            )
            academics.append(
                AcademicRecord(
                    source_kind=SourceKind.PAMPLEMOUSSE,
                    external_id=ext_id,
                    record_type=str(entry.get("record_type") or ("grade" if grade is not None else "subject")),
                    title=title,
                    parent_external_id=entry.get("parent_external_id") or entry.get("ue"),
                    academic_year=entry.get("academic_year"),
                    cohort=entry.get("cohort"),
                    semester=str(entry["semester"]) if entry.get("semester") is not None else None,
                    specialization=entry.get("specialization"),
                    subject_code=entry.get("subject_code") or entry.get("code"),
                    weight=coef,
                    weight_mode=entry.get("weight_mode") or ("coefficient" if coef is not None else None),
                    weight_status=ValueStatus.CONFIRMED if coef is not None else ValueStatus.MISSING,
                    grade_value=grade,
                    grade_scale_max=scale if scale is not None else (Decimal("20") if grade is not None else None),
                    grade_kind="actual" if grade is not None else None,
                    grade_status=ValueStatus.CONFIRMED if grade is not None else ValueStatus.MISSING,
                    observed_at=entry.get("observed_at") or utc_now_iso(),
                    rule_payload=rules,
                    rule_verification_status=rule_verified,
                    provenance={"format": "json", "index": idx, "retrieved_at": utc_now_iso()},
                )
            )

        return events, academics

    @staticmethod
    def _stable_version_payload(raw_item: dict[str, Any]) -> dict[str, Any]:
        """Strip volatile observation timestamps so identical upstream facts keep identical remote_version."""
        cleaned: dict[str, Any] = {}
        for k, v in raw_item.items():
            if k in ("observed_at", "retrieved_at", "imported_at"):
                continue
            if k == "provenance" and isinstance(v, dict):
                cleaned[k] = {
                    pk: pv
                    for pk, pv in v.items()
                    if pk not in ("observed_at", "retrieved_at", "imported_at")
                }
            else:
                cleaned[k] = v
        return cleaned

    def normalize(self, raw_item: dict[str, Any]) -> SourceRecord:
        """Wrap either a CalendarEventRecord dict or AcademicRecord dict into SourceRecord."""
        kind = raw_item.get("record_kind", "event")
        ext_id = str(raw_item["external_id"])
        title = str(raw_item["title"])
        version = hashlib.sha256(
            json.dumps(self._stable_version_payload(raw_item), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        return SourceRecord(
            source_kind=SourceKind.PAMPLEMOUSSE,
            external_id=ext_id,
            title=title,
            record_kind=kind,
            remote_version=version,
            remote_modified_at=raw_item.get("observed_at") or raw_item.get("start_at"),
            course_id=raw_item.get("course_id") or raw_item.get("subject_code"),
            course_title=raw_item.get("subject_hint") or title,
            source_url=self._config.pamplemousse_base_url,
            access_status=AccessStatus.ACTIVE,
            payload=raw_item,
            provenance=raw_item.get("provenance") or {"source": SourceKind.PAMPLEMOUSSE.value},
        )

    def list_changes(
        self,
        scope: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> ChangePage:
        """Acquire Pamplemousse timetable (EDT/iCalendar), Inscriptions catalog, and Detailed Notes.

        Supports independent sync slices via ``sections`` (``"timetable"``, ``"grades"``,
        ``"catalog"``, or ``"all"``) so frequent timetable refreshes never touch catalog or
        grade endpoints. Separates portal-confirmed ``source_confirmed_academic_year`` from
        caller-requested ``academic_year``, preserves explicit missing/ambiguous grades and
        coefficients as ``None`` (never fabricating ``0``), and skips unchanged records via
        deterministic wall-clock-free ``known_versions`` hashes (skipping academic facts only
        as a consistent whole so hierarchy and sibling-weight derivation remain intact).

        Args:
            scope: Optional scope dictionary supporting:
                - ``sections`` / ``pamplemousse_scope``: Slice selector (``timetable``, ``grades``,
                  ``catalog``, or ``all``).
                - ``academic_year``: Requested year label (e.g. ``"2026-2027"``).
                - ``known_versions``: ``dict[external_id, remote_version]`` for incremental skip.
                - ``range_start`` / ``range_end`` / ``days_back`` / ``days_ahead``: Timetable bounds.
                - ``ics_text`` / ``csv_text`` / ``json_data`` / ``*_html``: Offline fixture inputs.
                - ``max_items`` / ``page_size``: Bounded pagination controls.
            cursor: Optional numeric page offset cursor.

        Returns:
            ``ChangePage`` containing normalized timetable events and/or academic records.
        """
        opts = scope or {}
        raw_sections = (
            opts.get("sections")
            if "sections" in opts
            else (
                opts.get("pamplemousse_scope")
                or opts.get("sync_slice")
                or opts.get("slice")
                or opts.get("dataset")
            )
        )
        active_scopes = resolve_pamplemousse_scopes(raw_sections)
        want_timetable = "timetable" in active_scopes
        want_grades = "grades" in active_scopes
        want_catalog = "catalog" in active_scopes

        events: list[CalendarEventRecord] = []
        inscriptions_academics: list[AcademicRecord] = []
        notes_academics: list[AcademicRecord] = []
        other_academics: list[AcademicRecord] = []
        warnings: list[str] = []
        errors: list[str] = []
        pages_visited: list[str] = []
        ics_used = False
        timetable_diag: dict[str, Any] = {
            "timetable_strategy": "none",
            "ical_candidates_tried": 0,
            "edt_xhr_captured": 0,
            "edt_months_scanned": 0,
        }
        requested_year: str | None = opts.get("academic_year")
        source_confirmed_year: str | None = None

        def _capture_source_year(html_blob: str | None) -> None:
            nonlocal source_confirmed_year
            if source_confirmed_year is None and html_blob:
                found_yr = extract_pamplemousse_academic_year(html_blob)
                if found_yr:
                    source_confirmed_year = found_yr

        for html_key in ("inscriptions_html", "notes_html", "timetable_html", "academic_html"):
            if html_key in opts and isinstance(opts[html_key], str):
                _capture_source_year(opts[html_key])

        detected_year: str | None = source_confirmed_year or requested_year

        range_start: datetime | None = opts.get("range_start")
        range_end: datetime | None = opts.get("range_end")
        if range_start is None and opts.get("days_back") is not None:
            range_start = datetime.now(timezone.utc) - timedelta(days=int(opts["days_back"]))
        if range_end is None and opts.get("days_ahead") is not None:
            range_end = datetime.now(timezone.utc) + timedelta(days=int(opts["days_ahead"]))

        offline_keys = (
            "ics_text",
            "csv_text",
            "json_data",
            "timetable_html",
            "academic_html",
            "inscriptions_html",
            "notes_html",
            "fullcalendar_json",
            "fullcalendar_html",
        )
        if "ics_text" in opts and want_timetable:
            ics_used = True
            events.extend(
                self.import_ics_text(
                    opts["ics_text"],
                    academic_year=detected_year,
                    range_start=range_start,
                    range_end=range_end,
                )
            )
        if "csv_text" in opts:
            ev, ac = self.import_csv_text(opts["csv_text"])
            if want_timetable:
                events.extend(ev)
            if want_grades or want_catalog:
                other_academics.extend(ac)
        if "json_data" in opts:
            ev, ac = self.import_json_data(opts["json_data"])
            if want_timetable:
                events.extend(ev)
            if want_grades or want_catalog:
                other_academics.extend(ac)
        if "timetable_html" in opts and want_timetable:
            events.extend(
                parse_pamplemousse_timetable_html(
                    opts["timetable_html"], academic_year=detected_year
                )
            )
        if "fullcalendar_json" in opts and want_timetable:
            events.extend(
                parse_fullcalendar_json_events(
                    opts["fullcalendar_json"],
                    academic_year=detected_year,
                    range_start=range_start,
                    range_end=range_end,
                )
            )
        if "fullcalendar_html" in opts and want_timetable:
            events.extend(
                parse_fullcalendar_list_html(
                    opts["fullcalendar_html"],
                    academic_year=detected_year,
                    range_start=range_start,
                    range_end=range_end,
                )
            )
        if "inscriptions_html" in opts and want_catalog:
            inscriptions_academics.extend(
                parse_pamplemousse_inscriptions_html(
                    opts["inscriptions_html"], academic_year=detected_year
                )
            )
        if "notes_html" in opts and want_grades:
            notes_academics.extend(
                parse_pamplemousse_notes_html(opts["notes_html"], academic_year=detected_year)
            )
        if "academic_html" in opts and (want_grades or want_catalog):
            other_academics.extend(parse_pamplemousse_academic_html(opts["academic_html"]))

        # Live authenticated browser traversal across Mon Espace -> iCalendar/EDT + Inscriptions + Notes
        if not any(k in opts for k in offline_keys):
            interactive = bool(opts.get("interactive", False))
            allow_auto_reauth: bool | None = opts.get("allow_auto_reauth")
            start_url = opts.get("url") or self._config.pamplemousse_base_url
            home_html = self._fetch_html(
                start_url,
                interactive=interactive,
                allow_auto_reauth=allow_auto_reauth,
            )
            pages_visited.append("index.php")
            _capture_source_year(home_html)
            if source_confirmed_year:
                detected_year = source_confirmed_year

            # Check if start_url itself already holds timetable or academic data (fixture/direct URL)
            if want_timetable:
                events.extend(parse_pamplemousse_timetable_html(home_html, academic_year=detected_year))
            if want_catalog:
                inscriptions_academics.extend(
                    parse_pamplemousse_inscriptions_html(home_html, academic_year=detected_year)
                )
            if want_grades:
                notes_academics.extend(
                    parse_pamplemousse_notes_html(home_html, academic_year=detected_year)
                )

            nav = discover_pamplemousse_nav_links(home_html, start_url)
            discovered_explicit = self._discover_pamplemousse_subpages(home_html, start_url)
            looks_like_leos = (
                "index.php?p=" in home_html.lower()
                or bool(nav.get("inscriptions") or nav.get("notes") or nav.get("icalendar"))
            )

            # 1. Official iCalendar export (timetable slice only)
            ical_url = (
                (nav.get("icalendar") or extract_ical_feed_url(home_html, start_url))
                if want_timetable
                else None
            )
            if want_timetable and ical_url:
                try:
                    ical_payload, tried = self._fetch_ical_payload(
                        ical_url,
                        interactive=interactive,
                        allow_auto_reauth=allow_auto_reauth,
                    )
                    timetable_diag["ical_candidates_tried"] = len(tried)
                    pages_visited.append("icalendar")
                    if ical_payload and "BEGIN:VCALENDAR" in ical_payload:
                        ics_used = True
                        parsed_ics = self.import_ics_text(
                            ical_payload,
                            academic_year=detected_year,
                            range_start=range_start,
                            range_end=range_end,
                        )
                        events.extend(parsed_ics)
                        if parsed_ics:
                            timetable_diag["timetable_strategy"] = "ics"
                except AuthenticationRequiredError:
                    raise
                except Exception as exc:
                    warnings.append(f"pamplemousse_ical_unavailable:{type(exc).__name__}")

            # 2. Timetable EDT (`index.php?p=121`) whenever timetable requested and iCalendar yielded no events
            edt_url = (
                nav.get("edt")
                or (
                    urljoin(start_url, PAMPLEMOUSSE_EDT_PATH)
                    if not events and (looks_like_leos or not discovered_explicit)
                    else None
                )
            ) if want_timetable else None
            if want_timetable and edt_url and not events:
                try:
                    edt_events, edt_html = self._fetch_edt_playwright(
                        edt_url,
                        academic_year=detected_year,
                        range_start=range_start,
                        range_end=range_end,
                        interactive=interactive,
                        allow_auto_reauth=allow_auto_reauth,
                        edt_diag=timetable_diag,
                    )
                    pages_visited.append("edt")
                    if "BEGIN:VCALENDAR" in edt_html:
                        ics_used = True
                        events.extend(
                            self.import_ics_text(
                                edt_html,
                                academic_year=detected_year,
                                range_start=range_start,
                                range_end=range_end,
                            )
                        )
                        timetable_diag["timetable_strategy"] = "ics_from_edt"
                    else:
                        # Also check if EDT page itself exposes an iCalendar link we missed on index.php
                        if not edt_events:
                            edt_ical = extract_ical_feed_url(edt_html, edt_url)
                            if edt_ical and edt_ical != ical_url:
                                ical_payload2, tried2 = self._fetch_ical_payload(
                                    edt_ical,
                                    interactive=interactive,
                                    allow_auto_reauth=allow_auto_reauth,
                                )
                                timetable_diag["ical_candidates_tried"] += len(tried2)
                                if ical_payload2 and "BEGIN:VCALENDAR" in ical_payload2:
                                    ics_used = True
                                    edt_events = self.import_ics_text(
                                        ical_payload2,
                                        academic_year=detected_year,
                                        range_start=range_start,
                                        range_end=range_end,
                                    )
                                    if edt_events:
                                        timetable_diag["timetable_strategy"] = "ics_from_edt_link"
                        events.extend(edt_events)
                except AuthenticationRequiredError:
                    raise
                except Exception as exc:
                    warnings.append(f"pamplemousse_edt_extraction_failed:{type(exc).__name__}")

            # 3. Academic catalog & coefficients — Inscriptions (`index.php?p=107`, catalog slice only)
            has_leos_menu_paths = "index.php?p=" in home_html.lower()
            insc_url = (
                nav.get("inscriptions")
                or (
                    urljoin(start_url, PAMPLEMOUSSE_INSCRIPTIONS_PATH)
                    if not inscriptions_academics and (has_leos_menu_paths or not discovered_explicit)
                    else None
                )
            ) if want_catalog else None
            if want_catalog and insc_url and not inscriptions_academics:
                try:
                    insc_html = self._fetch_html(
                        insc_url,
                        interactive=interactive,
                        allow_auto_reauth=allow_auto_reauth,
                    )
                    pages_visited.append("inscriptions")
                    _capture_source_year(insc_html)
                    if source_confirmed_year:
                        detected_year = source_confirmed_year
                    inscriptions_academics.extend(
                        parse_pamplemousse_inscriptions_html(
                            insc_html, academic_year=detected_year
                        )
                    )
                except AuthenticationRequiredError:
                    raise
                except Exception as exc:
                    warnings.append(f"pamplemousse_inscriptions_failed:{type(exc).__name__}")

            # 3b. Optional pedagogical program / curriculum page when catalog still empty or lacks coefficients
            prog_url = nav.get("program") if want_catalog else None
            if (
                want_catalog
                and prog_url
                and prog_url != insc_url
                and (
                    not inscriptions_academics
                    or not any(r.record_type == "subject" and r.weight is not None for r in inscriptions_academics)
                )
            ):
                try:
                    prog_html = self._fetch_html(
                        prog_url,
                        interactive=interactive,
                        allow_auto_reauth=allow_auto_reauth,
                    )
                    pages_visited.append("program")
                    _capture_source_year(prog_html)
                    if source_confirmed_year:
                        detected_year = source_confirmed_year
                    prog_recs = parse_pamplemousse_inscriptions_html(
                        prog_html, academic_year=detected_year
                    )
                    if prog_recs:
                        if not inscriptions_academics:
                            inscriptions_academics.extend(prog_recs)
                        else:
                            inscriptions_academics = reconcile_pamplemousse_academics(
                                inscriptions_academics, prog_recs
                            )
                except AuthenticationRequiredError:
                    raise
                except Exception as exc:
                    warnings.append(f"pamplemousse_program_failed:{type(exc).__name__}")

            # 4. Detailed grades — Notes (`index.php?p=1052`, grades slice only)
            notes_url = (
                nav.get("notes")
                or (
                    urljoin(start_url, PAMPLEMOUSSE_NOTES_PATH)
                    if not notes_academics and (has_leos_menu_paths or not discovered_explicit)
                    else None
                )
            ) if want_grades else None
            if want_grades and notes_url and not notes_academics:
                try:
                    notes_html = self._fetch_html(
                        notes_url,
                        interactive=interactive,
                        allow_auto_reauth=allow_auto_reauth,
                    )
                    pages_visited.append("notes")
                    _capture_source_year(notes_html)
                    if source_confirmed_year:
                        detected_year = source_confirmed_year
                    parsed_notes = parse_pamplemousse_notes_html(
                        notes_html, academic_year=detected_year
                    )
                    if parsed_notes:
                        notes_academics.extend(parsed_notes)
                    else:
                        other_academics.extend(parse_pamplemousse_academic_html(notes_html))
                except AuthenticationRequiredError:
                    raise
                except Exception as exc:
                    warnings.append(f"pamplemousse_notes_failed:{type(exc).__name__}")

            # 5. Additional explicit subpages only when running full (all-slices) Pamplemousse discovery
            visited_urls = {start_url, ical_url, edt_url, insc_url, prog_url, notes_url}
            if active_scopes == PAMPLEMOUSSE_SYNC_SCOPES:
                for sub_url in discovered_explicit:
                    if sub_url in visited_urls:
                        continue
                    visited_urls.add(sub_url)
                    try:
                        sub_body = self._fetch_html(
                            sub_url,
                            interactive=interactive,
                            allow_auto_reauth=allow_auto_reauth,
                        )
                    except AuthenticationRequiredError:
                        raise
                    except Exception:
                        continue
                    if "BEGIN:VCALENDAR" in sub_body:
                        ics_used = True
                        events.extend(
                            self.import_ics_text(sub_body, range_start=range_start, range_end=range_end)
                        )
                    else:
                        events.extend(parse_pamplemousse_timetable_html(sub_body))
                        other_academics.extend(parse_pamplemousse_academic_html(sub_body))

            if not events and not inscriptions_academics and not notes_academics and not other_academics:
                warnings.append(
                    "pamplemousse_no_records_extracted: Authenticated pages loaded without recognized "
                    "EDT events, Inscriptions rows, or Notes rows; verify page structure or use ICS/CSV fallback."
                )

        # Deduplicate events by external_id
        dedup_events: list[CalendarEventRecord] = []
        seen_ev_ids: set[str] = set()
        for ev in events:
            if ev.external_id not in seen_ev_ids:
                seen_ev_ids.add(ev.external_id)
                dedup_events.append(ev)

        reconciled_academics = reconcile_pamplemousse_academics(
            inscriptions_academics, notes_academics
        )
        seen_ac_ids = {r.external_id for r in reconciled_academics}
        for oa in other_academics:
            if oa.external_id not in seen_ac_ids:
                seen_ac_ids.add(oa.external_id)
                reconciled_academics.append(oa)

        # Compute rich distinct aggregate counts for Pamplemousse diagnostics
        enrolled_subjects = [r for r in reconciled_academics if r.record_type == "subject"]
        teaching_units = [r for r in reconciled_academics if r.record_type == "teaching_unit"]
        semesters = [r for r in reconciled_academics if r.record_type == "semester"]
        assessments = [r for r in reconciled_academics if r.record_type == "assessment"]
        aggregates = [r for r in reconciled_academics if r.record_type == "aggregate_grade"]
        subject_coefs = [r for r in enrolled_subjects if r.weight is not None]
        ects_vals = [r for r in enrolled_subjects if (r.provenance or {}).get("ects") is not None]
        available_grades = [r for r in reconciled_academics if r.grade_value is not None]
        ambiguous_events_count = sum(1 for ev in dedup_events if ev.confirmation_required)
        missing_coefficients_count = sum(1 for r in enrolled_subjects if r.weight_status == ValueStatus.MISSING)
        ambiguous_reconciliations_count = sum(
            1
            for r in reconciled_academics
            if (r.provenance or {}).get("reconciliation_status") == "ambiguous"
        )
        missing_grades_count = sum(1 for r in assessments if r.grade_status == ValueStatus.MISSING)
        missing_or_ambiguous = (
            ambiguous_events_count
            + missing_coefficients_count
            + ambiguous_reconciliations_count
            + missing_grades_count
        )
        self.last_stats = {
            "active_scopes": sorted(active_scopes),
            "academic_year": detected_year,
            "source_confirmed_academic_year": source_confirmed_year,
            "requested_academic_year": requested_year,
            "academic_year_confirmed": source_confirmed_year is not None,
            "pages_visited": pages_visited,
            "ics_used": ics_used,
            "timetable_strategy": timetable_diag["timetable_strategy"],
            "ical_candidates_tried": timetable_diag["ical_candidates_tried"],
            "edt_xhr_captured": timetable_diag["edt_xhr_captured"],
            "edt_months_scanned": timetable_diag["edt_months_scanned"],
            "timetable_events": len(dedup_events),
            "events_with_room": sum(1 for ev in dedup_events if ev.room),
            "events_with_teacher": sum(1 for ev in dedup_events if ev.teacher),
            "events_with_deadline": sum(
                1 for ev in dedup_events if ev.event_type.value in ("exam", "deadline", "presentation")
            ),
            "enrolled_subjects": len(enrolled_subjects),
            "teaching_units": len(teaching_units),
            "semester_associations": len(semesters),
            "subject_coefficients": len(subject_coefs),
            "ects_values": len(ects_vals),
            "available_grades": len(available_grades),
            "assessment_records": len(assessments),
            "aggregate_grades": len(aggregates),
            "ambiguous_events_count": ambiguous_events_count,
            "missing_coefficients_count": missing_coefficients_count,
            "missing_grades_count": missing_grades_count,
            "ambiguous_reconciliations_count": ambiguous_reconciliations_count,
            "missing_or_ambiguous_count": missing_or_ambiguous,
        }

        max_items = opts.get("max_items")
        known_versions: dict[str, str] = dict(opts.get("known_versions") or {})
        all_records: list[SourceRecord] = []
        unchanged_skipped = 0
        for ev in dedup_events:
            d = ev.to_dict()
            d["record_kind"] = "event"
            rec = self.normalize(d)
            if (
                rec.external_id in known_versions
                and rec.remote_version is not None
                and known_versions[rec.external_id] == rec.remote_version
            ):
                unchanged_skipped += 1
                continue
            all_records.append(rec)
        normalized_academics: list[SourceRecord] = []
        for ac in reconciled_academics:
            d = ac.to_dict()
            d["record_kind"] = "academic"
            normalized_academics.append(self.normalize(d))
        # Skip academic records as a consistent whole when zero academic facts changed so
        # parent-before-child hierarchy and sibling-weight derivation always stay intact on updates.
        if normalized_academics and known_versions and all(
            r.external_id in known_versions
            and r.remote_version is not None
            and known_versions[r.external_id] == r.remote_version
            for r in normalized_academics
        ):
            unchanged_skipped += len(normalized_academics)
        else:
            all_records.extend(normalized_academics)
        self.last_stats["unchanged_skipped"] = unchanged_skipped

        if max_items is not None:
            all_records = all_records[: int(max_items)]

        page_size = max(1, int(opts.get("page_size", 100)))
        offset = int(cursor) if (cursor and cursor.isdigit()) else 0
        sliced = all_records[offset : offset + page_size]
        next_offset = offset + len(sliced)
        complete = next_offset >= len(all_records)

        return ChangePage(
            items=tuple(sliced),
            next_cursor=None if complete else str(next_offset),
            complete=complete,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    def fetch_blob(self, item: SourceRecord) -> FetchResult:
        raise NotImplementedError(
            "PamplemousseConnector emits structured timetable/academic records rather than binary blobs."
        )

    def disconnect(self) -> AuthStatus:
        return self._auth.disconnect(SourceKind.PAMPLEMOUSSE)

"""Integration bridge registering Gemini's ingestion adapters into Codex's Runtime.

Entry point for Codex's `Settings.adapter_factory`:
    ADAPTER_FACTORY=backend.app.modules.ingestion.codex_bridge:build_components

Implements Codex's exact shared contracts from:
- backend.app.contracts.authentication
- backend.app.contracts.sources
- backend.app.core.runtime.Components
"""

from __future__ import annotations

from contextlib import closing
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import SecretStr

from ...contracts.authentication import (
    AuthenticationResult,
    BrowserSessionManager,
    ConnectionStatus,
    CredentialStore,
    ProfileBusy,
    ReauthenticationRequired,
    ServiceAccessDenied,
    SourceAuthenticationAdapter,
)
from ...contracts.sources import (
    AcademicYear,
    Assessment,
    AssessmentWeight,
    CalendarEvent,
    CalendarSnapshot,
    ChangePage as CodexChangePage,
    ClassificationResult as CodexClassificationResult,
    Concept,
    ConceptEvidence,
    Document,
    DocumentChunk,
    EntityMatch,
    FetchedDocument,
    ParsedContent,
    Provenance,
    PublishedGrade,
    RetryableSourceError,
    Semester,
    SourceKind as CodexSourceKind,
    SourceRecord as CodexSourceRecord,
    Specialization,
    Subject,
    SubjectCoefficient,
    SyncScope,
    TeachingUnit,
)
from ...core.runtime import Components
from ...core.drive_scope import ENSORA_DRIVE_ALLOWED_TOP_LEVEL_FOLDERS, folder_scope
from ...core.settings import Settings
from .auth.ensai import BrowserProfileLockError, EnsaiAuthAdapter
from .auth.google_oauth import GoogleOAuthAdapter
from .classification.classifier import DocumentClassifier
from .config import SourcesRuntimeConfig
from .connectors.base import (
    AuthenticationRequiredError,
    PermissionDeniedError,
    SourceConnector as InternalSourceConnector,
    TransientSourceError,
)
from .connectors.ensora_drive import EnsoraDriveConnector
from .connectors.google_calendar import GoogleCalendarConnector
from .connectors.local_import import LocalFileConnector, StructuredAcademicImporter
from .connectors.moodle import MoodleConnector
from .connectors.pamplemousse import PamplemousseConnector
from .contracts import (
    AccessStatus,
    AuthState,
    AuthStatus,
    CredentialStoreProtocol,
    DocumentCategory,
    ExamCorrectionMatch,
    Locator,
    ParsedBlock,
    ParsedDocument,
    SourceKind as InternalSourceKind,
    SourceRecord as InternalSourceRecord,
)
from .credentials import SecureCredentialManager, redact_sensitive_text
from .parsers.registry import DocumentParserRegistry
from .resolution.entity_resolver import AcademicEntityResolver, CanonicalSubject

logger = logging.getLogger(__name__)

MAX_PAGE_CACHE_ENTRIES = 500
DRIVE_STREAMING_PAGE_SIZE = 25


class RecordConversionError(ValueError):
    """Raised when a single source record is incomplete/invalid and cannot be safely mapped."""


class _CodexCredentialStoreAdapter(CredentialStoreProtocol):
    """Wraps Codex's CredentialStore (SecretStr-based) for Gemini's SecureCredentialManager."""

    def __init__(self, store: CredentialStore) -> None:
        self._store = store

    def get_secret(self, namespace: str, key: str) -> str | None:
        if self._store is None:
            return None
        try:
            val = self._store.get(f"{namespace}:{key}")
        except Exception:
            return None
        return val.get_secret_value() if val is not None else None

    def set_secret(self, namespace: str, key: str, value: str) -> None:
        if self._store is None:
            raise RuntimeError("Credential store unavailable.")
        self._store.set(f"{namespace}:{key}", SecretStr(value))

    def delete_secret(self, namespace: str, key: str) -> None:
        if self._store is None:
            return
        try:
            self._store.delete(f"{namespace}:{key}")
        except Exception:
            pass


def runtime_config_from_codex_settings(settings: Settings) -> SourcesRuntimeConfig:
    """Build Gemini's SourcesRuntimeConfig directly from Codex's validated Settings.

    Preserves explicit paths verbatim and propagates all centralized ENSAI/Google
    settings directly from Settings without reopening sources.env.
    """
    cfg = SourcesRuntimeConfig(
        config_dir=settings.config_dir,
        ensai_home_url=str(settings.ensai_home_url),
        myensai_url=str(settings.ensai_portal_url),
        cas_login_url=str(settings.ensai_cas_url),
        moodle_base_url=str(settings.moodle_base_url),
        pamplemousse_base_url=str(settings.pamplemousse_base_url),
        moodle_protected_url=(
            str(settings.moodle_protected_url) if settings.moodle_protected_url is not None else None
        ),
        pamplemousse_protected_url=(
            str(settings.pamplemousse_protected_url)
            if settings.pamplemousse_protected_url is not None
            else None
        ),
        moodle_ws_enabled=(settings.moodle_auth_mode == "rest"),
        moodle_auth_mode=settings.moodle_auth_mode,
        pamplemousse_auth_mode=settings.pamplemousse_auth_mode,
        ensai_headless=settings.browser_headless,
        ensai_allow_automated_credential_submit=(
            settings.ensai_auth_mode == "credentials" or bool(settings.ensai_auto_login)
        ),
        ensai_use_shared_session=bool(settings.ensai_shared_session),
        ensai_timeout_ms=int(settings.ensai_timeout_ms),
        ensai_username_hint=(
            settings.ensai_username.get_secret_value()
            if settings.ensai_username is not None
            else None
        ),
        google_cloud_project_id=settings.google_project_id,
        ensora_drive_root_folder_id=settings.ensora_drive_folder_id,
        google_calendar_id=settings.google_calendar_id,
        rate_limit_min_interval_sec=float(settings.request_delay_seconds),
        max_download_bytes=int(settings.max_document_bytes),
    )
    cfg.ensai_profile_dir = settings.ensai_profile_dir
    cfg.google_credentials_path = settings.google_credentials_path
    cfg.google_token_path = settings.google_token_path
    return cfg


def _to_codex_auth_result(
    status: AuthStatus,
    *,
    institutional: bool = False,
    rest_mode: bool = False,
) -> AuthenticationResult:
    meta = status.metadata or {}
    if status.authenticated and status.state == AuthState.AUTHENTICATED:
        if institutional:
            if rest_mode:
                return AuthenticationResult(
                    status=ConnectionStatus.CONNECTED,
                    session_state="unknown",
                    service_access="verified",
                )
            # Only report CONNECTED when a protected service-specific probe positively verified access
            if meta.get("origin_verified") is True and not meta.get("cas_redirect_detected", False):
                return AuthenticationResult(
                    status=ConnectionStatus.CONNECTED,
                    session_state="active",
                    service_access="verified",
                )
            return AuthenticationResult(
                status=ConnectionStatus.REAUTH_REQUIRED,
                reason="service_verification_required",
                session_state="active",
                service_access="unknown",
            )
        return AuthenticationResult(
            status=ConnectionStatus.CONNECTED,
            session_state="active",
            service_access="verified",
        )
    if status.state == AuthState.UNCONFIGURED:
        return AuthenticationResult(
            status=ConnectionStatus.NOT_CONFIGURED,
            reason="configuration_missing",
            session_state="unknown",
            service_access="unknown",
        )
    if status.state == AuthState.REVOKED:
        if institutional and rest_mode:
            return AuthenticationResult(
                status=ConnectionStatus.ACCESS_DENIED,
                reason="service_access_denied",
                session_state="unknown",
                service_access="denied",
            )
        raise ReauthenticationRequired("cas_login_required" if institutional else "credential_expired")
    if status.state == AuthState.EXPIRED:
        raise ReauthenticationRequired("cas_login_required" if institutional else "credential_expired")
    if status.state == AuthState.NEEDS_LOGIN:
        if institutional:
            return AuthenticationResult(
                status=ConnectionStatus.REAUTH_REQUIRED,
                reason="cas_login_required",
                session_state="reauth_required",
                service_access="unknown",
            )
        return AuthenticationResult(
            status=ConnectionStatus.REAUTH_REQUIRED,
            reason="consent_required",
            session_state="reauth_required",
            service_access="unknown",
        )
    if meta.get("errorcode") == "service_access_denied":
        return AuthenticationResult(
            status=ConnectionStatus.ACCESS_DENIED,
            reason="service_access_denied",
            session_state="unknown" if rest_mode else "active",
            service_access="denied",
        )
    if meta.get("errorcode") == "transient_failure":
        return AuthenticationResult(
            status=ConnectionStatus.UNAVAILABLE,
            reason="transient_failure",
            session_state="unknown",
            service_access="unknown",
        )
    return AuthenticationResult(
        status=ConnectionStatus.ERROR,
        reason="adapter_error",
        session_state="unknown",
        service_access="unknown",
    )


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        iso = value[:-1] + "+00:00" if value.endswith("Z") else value
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _map_access_status(status: AccessStatus) -> str:
    if status == AccessStatus.TOMBSTONED:
        return "tombstoned"
    if status in (AccessStatus.REVOKED, AccessStatus.BLOCKED):
        return "revoked"
    return "active"


def _infer_source_kind_from_external_id(external_id: str) -> InternalSourceKind:
    lower = external_id.lower()
    if lower.startswith("moodle:"):
        return InternalSourceKind.MOODLE
    if lower.startswith("pamplemousse:"):
        return InternalSourceKind.PAMPLEMOUSSE
    if lower.startswith("ensora_drive:"):
        return InternalSourceKind.ENSORA_DRIVE
    if lower.startswith("google_calendar:"):
        return InternalSourceKind.GOOGLE_CALENDAR
    if lower.startswith("academic:"):
        return InternalSourceKind.ACADEMIC_IMPORT
    return InternalSourceKind.LOCAL_FILE


def _to_codex_source_kind(kind: InternalSourceKind) -> CodexSourceKind:
    if kind == InternalSourceKind.LOCAL_FILE:
        return CodexSourceKind.LOCAL
    return CodexSourceKind(kind.value)


def _from_codex_source_kind(kind: CodexSourceKind | None) -> InternalSourceKind | None:
    if kind is None:
        return None
    if kind == CodexSourceKind.LOCAL:
        return InternalSourceKind.LOCAL_FILE
    try:
        return InternalSourceKind(kind.value)
    except ValueError:
        return None


def _to_codex_records(
    rec: InternalSourceRecord,
    *,
    emitted_parents: set[str] | None = None,
) -> list[CodexSourceRecord]:
    """Convert one internal SourceRecord into an ordered list of Codex typed facts.

    Guarantees:
    - Parent records (e.g. Moodle course Subject) are emitted strictly before children.
    - ID-only cancelled calendar events become valid CalendarEvent tombstones.
    - Incomplete timed events NEVER fabricate a 1-hour duration (raise RecordConversionError).
    - Academic subjects/assessments preserve independent coefficients, weights, and grades.
    - Missing grades NEVER fall through to fabricated Subject records.
    """
    out: list[CodexSourceRecord] = []
    seen_parents = emitted_parents if emitted_parents is not None else set()
    codex_src = _to_codex_source_kind(rec.source_kind)

    rec_payload = rec.payload if isinstance(rec.payload, dict) else {}
    rec_prov = rec.provenance if isinstance(rec.provenance, dict) else {}
    academic_year_hint = (
        rec_payload.get("academic_year")
        or rec_prov.get("academic_year")
        or (rec_payload.get("provenance") or {}).get("academic_year")
        if isinstance(rec_payload.get("provenance"), dict)
        else (rec_payload.get("academic_year") or rec_prov.get("academic_year"))
    )
    prov = Provenance(
        external_id=rec.external_id,
        remote_revision=rec.remote_version,
        modified_at=_parse_iso_dt(rec.remote_modified_at),
        source=codex_src,
        source_url=rec.source_url,
        folder=rec.folder_path,
        academic_year=str(academic_year_hint).strip() if academic_year_hint else None,
    )

    # Emit Moodle parent Subject record before course documents or assignments
    moodle_subject_key: str | None = None
    if rec.source_kind == InternalSourceKind.MOODLE and rec.course_id:
        moodle_subject_key = f"moodle:course:{rec.course_id}"
        if moodle_subject_key not in seen_parents:
            seen_parents.add(moodle_subject_key)
            out.append(
                Subject(
                    provenance=Provenance(
                        external_id=moodle_subject_key,
                        source=codex_src,
                        source_url=rec.source_url,
                        folder=f"Moodle/{rec.course_title or rec.course_id}",
                        academic_year=str(academic_year_hint).strip() if academic_year_hint else None,
                    ),
                    title=rec.course_title or f"Course {rec.course_id}",
                    code=rec.course_id,
                    source_code=rec.course_id,
                )
            )

    if rec.record_kind in ("document", "announcement"):
        title = rec.title or rec.external_id
        # Align exported Google Workspace filename extension with exported MIME type
        export_ext = rec_payload.get("export_extension")
        if export_ext and not title.lower().endswith(str(export_ext).lower()):
            title = f"{title}{export_ext}"
        raw_res_kind = str(rec_payload.get("resource_kind") or "")
        if rec.record_kind == "announcement" or raw_res_kind == "announcement":
            doc_res_kind = "announcement"
        elif raw_res_kind in ("html_section", "page"):
            doc_res_kind = "educational_html"
        else:
            doc_res_kind = "document"
        sec_path = [seg.strip() for seg in (rec.section_title or "").split("/") if seg.strip()]
        out.append(
            Document(
                provenance=prov,
                title=title,
                mime_type=rec.mime_type or "application/octet-stream",
                subject_id=moodle_subject_key or rec.course_id,
                chapter_id=rec.section_title,
                course_id=rec.course_id,
                course_title=rec.course_title,
                academic_category=rec_payload.get("academic_category"),
                section_id=rec_payload.get("section_id"),
                section_path=sec_path,
                resource_kind=doc_res_kind,
                access_status=_map_access_status(rec.access_status),
            )
        )
        return out

    if rec.record_kind == "assignment":
        out.append(
            Assessment(
                provenance=prov,
                title=rec.title or rec.external_id,
                parent_key=moodle_subject_key or rec.course_id,
                deadline_at=_parse_iso_dt(rec_payload.get("deadline_at")),
                format="assignment",
                instructions=rec_payload.get("description"),
                assessment_kind="assignment",
            )
        )
        return out

    if rec.record_kind == "event":
        payload = rec.payload or {}
        status = str(payload.get("status") or "confirmed").lower()
        if status not in ("confirmed", "tentative", "cancelled"):
            status = "confirmed"

        common_kwargs: dict[str, Any] = {
            "provenance": prov,
            "title": rec.title or ("Cancelled event" if status == "cancelled" else "Event"),
            "subject_id": payload.get("course_id"),
            "status": status,
            "room": payload.get("room"),
            "group": payload.get("group"),
            "event_type": str(payload.get("event_type") or "other"),
            "teacher": payload.get("teacher"),
            "confirmation_required": bool(payload.get("confirmation_required", False)),
            "confirmation_reason": payload.get("confirmation_reason"),
            "recurrence_key": payload.get("recurrence_key"),
            "original_timezone": payload.get("original_timezone"),
            "busy": bool(payload.get("busy", status != "cancelled")),
        }

        all_day = payload.get("all_day_date")
        starts = _parse_iso_dt(payload.get("start_at"))
        ends = _parse_iso_dt(payload.get("end_at"))

        # Explicit ID-only cancelled tombstone (Codex CalendarEvent supports all-None cancelled)
        if status == "cancelled" and all_day is None and starts is None and ends is None:
            out.append(CalendarEvent(**common_kwargs))
            return out

        if all_day:
            d_start = date.fromisoformat(str(all_day))
            d_end_raw = payload.get("all_day_end_date")
            if d_end_raw:
                d_end = date.fromisoformat(str(d_end_raw))
                if d_end <= d_start:
                    d_end = d_start + timedelta(days=1)
            else:
                d_end = d_start + timedelta(days=1)
            out.append(
                CalendarEvent(
                    all_day_start=d_start,
                    all_day_end=d_end,
                    **common_kwargs,
                )
            )
            return out

        # Never fabricate a duration when start or end is missing/invalid
        if starts is None or ends is None or ends <= starts:
            raise RecordConversionError(
                f"Incomplete or non-positive timed interval for event '{rec.external_id}'; "
                "refusing to fabricate duration."
            )

        dur_min = int(round((ends - starts).total_seconds() / 60.0))
        out.append(
            CalendarEvent(
                starts_at=starts,
                ends_at=ends,
                duration_minutes=dur_min if dur_min >= 0 else None,
                **common_kwargs,
            )
        )
        return out

    if rec.record_kind == "academic":
        p = rec.payload or {}
        prov_meta = p.get("provenance") if isinstance(p.get("provenance"), dict) else {}
        rtype = str(p.get("record_type") or "subject")
        parent_key = p.get("parent_external_id")
        title = str(p.get("title") or rec.title)
        raw_weight = p.get("weight")
        raw_grade = p.get("grade_value")
        raw_ects = prov_meta.get("ects")
        weight_dec = Decimal(str(raw_weight)) if raw_weight is not None else None
        grade_dec = Decimal(str(raw_grade)) if raw_grade is not None else None
        ects_dec = Decimal(str(raw_ects)) if raw_ects is not None else None
        scale_dec = Decimal(str(p.get("grade_scale_max") or "20"))
        rec_status = "ambiguous" if prov_meta.get("reconciliation_status") == "ambiguous" else "known"
        src_code = p.get("subject_code")

        if rtype == "academic_year":
            out.append(AcademicYear(provenance=prov, title=title, parent_key=parent_key))
            return out
        if rtype == "specialization":
            out.append(Specialization(provenance=prov, title=title, parent_key=parent_key))
            return out
        if rtype == "semester":
            out.append(Semester(provenance=prov, title=title, parent_key=parent_key))
            return out
        if rtype in ("teaching_unit", "unit"):
            out.append(
                TeachingUnit(
                    provenance=prov,
                    title=title,
                    parent_key=parent_key,
                    ects=ects_dec,
                    record_status=rec_status,
                )
            )
            weight_mode = str(p.get("weight_mode") or "").lower()
            coef_mode = weight_mode if weight_mode in ("raw", "relative", "fraction") else "raw"
            weight_status = str(p.get("weight_status") or prov_meta.get("weight_status") or "")
            if weight_dec is not None or weight_status in ("missing", "ambiguous"):
                out.append(
                    SubjectCoefficient(
                        provenance=Provenance(
                            external_id=f"{rec.external_id}:coef",
                            remote_revision=rec.remote_version,
                            modified_at=prov.modified_at,
                            source=codex_src,
                            source_url=rec.source_url,
                            folder=rec.folder_path,
                            academic_year=prov.academic_year,
                        ),
                        title=f"{title} coefficient",
                        parent_key=rec.external_id,
                        weight=weight_dec,
                        level="teaching_unit",
                        mode=coef_mode,
                        record_status=weight_status if weight_status in ("missing", "ambiguous") else rec_status,
                    )
                )
            return out
        if rtype == "subject":
            out.append(
                Subject(
                    provenance=prov,
                    title=title,
                    parent_key=parent_key,
                    code=src_code,
                    source_code=src_code,
                    ects=ects_dec,
                    optional_activity=(
                        bool(prov_meta["optional_activity"])
                        if "optional_activity" in prov_meta
                        else None
                    ),
                    record_status=rec_status,
                )
            )
            weight_mode = str(p.get("weight_mode") or "").lower()
            coef_mode = weight_mode if weight_mode in ("raw", "relative", "fraction") else "raw"
            weight_status = str(p.get("weight_status") or prov_meta.get("weight_status") or "")
            if weight_dec is not None or weight_status in ("missing", "ambiguous"):
                out.append(
                    SubjectCoefficient(
                        provenance=Provenance(
                            external_id=f"{rec.external_id}:coef",
                            remote_revision=rec.remote_version,
                            modified_at=prov.modified_at,
                            source=codex_src,
                            source_url=rec.source_url,
                            folder=rec.folder_path,
                            academic_year=prov.academic_year,
                        ),
                        title=f"{title} coefficient",
                        parent_key=rec.external_id,
                        weight=weight_dec,
                        level="subject",
                        mode=coef_mode,
                        record_status=weight_status if weight_status in ("missing", "ambiguous") else rec_status,
                    )
                )
            grade_status = str(p.get("grade_status") or prov_meta.get("grade_status") or "")
            if grade_dec is not None or grade_status in ("missing", "ambiguous"):
                out.append(
                    PublishedGrade(
                        provenance=Provenance(
                            external_id=f"{rec.external_id}:grade",
                            remote_revision=rec.remote_version,
                            modified_at=prov.modified_at,
                            source=codex_src,
                            source_url=rec.source_url,
                            folder=rec.folder_path,
                            academic_year=prov.academic_year,
                        ),
                        title=f"{title} grade",
                        parent_key=rec.external_id,
                        value=grade_dec,
                        maximum=scale_dec,
                        grade_kind="official_aggregate",
                        record_status=grade_status if grade_status in ("missing", "ambiguous") else rec_status,
                        published_at=_parse_iso_dt(p.get("observed_at")),
                    )
                )
            return out
        if rtype in ("assessment", "assessment_group"):
            assess_format = p.get("format") or ("assessment_group" if rtype == "assessment_group" else None)
            out.append(
                Assessment(
                    provenance=prov,
                    title=title,
                    parent_key=parent_key,
                    deadline_at=_parse_iso_dt(p.get("deadline_at")),
                    format=str(assess_format) if assess_format else None,
                    record_status=rec_status,
                )
            )
            weight_mode = str(p.get("weight_mode") or "").lower()
            weight_status = str(p.get("weight_status") or prov_meta.get("weight_status") or "")
            if weight_dec is not None or weight_status in ("missing", "ambiguous"):
                if weight_dec is not None and weight_mode in ("relative", "fraction"):
                    out.append(
                        AssessmentWeight(
                            provenance=Provenance(
                                external_id=f"{rec.external_id}:weight",
                                remote_revision=rec.remote_version,
                                modified_at=prov.modified_at,
                                source=codex_src,
                                source_url=rec.source_url,
                                folder=rec.folder_path,
                                academic_year=prov.academic_year,
                            ),
                            title=f"{title} weight",
                            parent_key=rec.external_id,
                            weight=weight_dec,
                            mode=weight_mode,
                        )
                    )
                else:
                    # Bare institutional "Coefficient" values remain raw, scoped
                    # coefficients; only explicitly relative/fraction values normalize.
                    out.append(
                        SubjectCoefficient(
                            provenance=Provenance(
                                external_id=f"{rec.external_id}:coef",
                                remote_revision=rec.remote_version,
                                modified_at=prov.modified_at,
                                source=codex_src,
                                source_url=rec.source_url,
                                folder=rec.folder_path,
                                academic_year=prov.academic_year,
                            ),
                            title=f"{title} coefficient",
                            parent_key=rec.external_id,
                            weight=weight_dec,
                            level="assessment",
                            mode="raw",
                            record_status=weight_status if weight_status in ("missing", "ambiguous") else rec_status,
                        )
                    )
            grade_status = str(p.get("grade_status") or prov_meta.get("grade_status") or "")
            if grade_dec is not None or grade_status in ("missing", "ambiguous"):
                out.append(
                    PublishedGrade(
                        provenance=Provenance(
                            external_id=f"{rec.external_id}:grade",
                            remote_revision=rec.remote_version,
                            modified_at=prov.modified_at,
                            source=codex_src,
                            source_url=rec.source_url,
                            folder=rec.folder_path,
                            academic_year=prov.academic_year,
                        ),
                        title=f"{title} grade",
                        parent_key=rec.external_id,
                        value=grade_dec,
                        maximum=scale_dec,
                        grade_kind="assessment",
                        record_status=grade_status if grade_status in ("missing", "ambiguous") else rec_status,
                        published_at=_parse_iso_dt(p.get("observed_at")),
                    )
                )
            return out
        if rtype in ("grade", "aggregate_grade"):
            # Separate official aggregates from individual assessment grades; never fabricate Subject on missing grade!
            g_kind = (
                "official_aggregate"
                if (rtype == "aggregate_grade" or p.get("grade_kind") == "official_aggregate" or prov_meta.get("is_official_aggregate"))
                else "assessment"
            )
            grade_status = str(p.get("grade_status") or prov_meta.get("grade_status") or "")
            if grade_dec is not None or grade_status in ("missing", "ambiguous"):
                out.append(
                    PublishedGrade(
                        provenance=prov,
                        title=title,
                        parent_key=parent_key,
                        value=grade_dec,
                        maximum=scale_dec,
                        grade_kind=g_kind,
                        record_status=grade_status if grade_status in ("missing", "ambiguous") else rec_status,
                        published_at=_parse_iso_dt(p.get("observed_at")),
                    )
                )
            if weight_dec is not None or str(p.get("weight_status") or prov_meta.get("weight_status") or "") in ("missing", "ambiguous"):
                aggregate_level = {
                    "overall": "year",
                    "year": "year",
                    "semester": "semester",
                    "teaching_unit": "teaching_unit",
                    "subject": "subject",
                    "assessment": "assessment",
                }.get(str(prov_meta.get("aggregate_scope") or p.get("coefficient_level") or ""), "unspecified")
                weight_status = str(p.get("weight_status") or prov_meta.get("weight_status") or "")
                out.append(
                    SubjectCoefficient(
                        provenance=Provenance(
                            external_id=f"{rec.external_id}:coef",
                            remote_revision=rec.remote_version,
                            modified_at=prov.modified_at,
                            source=codex_src,
                            source_url=rec.source_url,
                            folder=rec.folder_path,
                            academic_year=prov.academic_year,
                        ),
                        title=f"{title} coefficient",
                        parent_key=parent_key,
                        weight=weight_dec,
                        level=aggregate_level,
                        mode="raw",
                        record_status=weight_status if weight_status in ("missing", "ambiguous") else rec_status,
                    )
                )
            return out
        if rtype == "coefficient":
            weight_status = str(p.get("weight_status") or prov_meta.get("weight_status") or "")
            if weight_dec is not None or weight_status in ("missing", "ambiguous"):
                out.append(
                    SubjectCoefficient(
                        provenance=prov,
                        title=title,
                        parent_key=parent_key,
                        weight=weight_dec,
                        mode="raw",
                        record_status=weight_status if weight_status in ("missing", "ambiguous") else rec_status,
                    )
                )
            return out
        return out

    return out


class CodexEnsaiAuthBridge(SourceAuthenticationAdapter):
    """Bridges EnsaiAuthAdapter to Codex's SourceAuthenticationAdapter & BrowserSessionManager.

    EnsaiAuthAdapter receives Codex's `sessions` directly so a single shared `.ensora.lock`
    lease is held for the exact Chromium lifetime (Chromium is always closed before release).
    """

    def __init__(
        self,
        adapter: EnsaiAuthAdapter,
        source: InternalSourceKind,
        sessions: BrowserSessionManager,
        cred_manager: SecureCredentialManager,
    ) -> None:
        self._adapter = adapter
        self._source = source
        self._sessions = sessions
        self._creds = cred_manager
        self.last_action_code: str | None = None

    def authorize(self, inputs: Mapping[str, SecretStr]) -> AuthenticationResult:
        self.last_action_code = None
        username = inputs.get("username")
        password = inputs.get("password")
        allow_auto: bool | None = None
        if username and password:
            try:
                self._creds.store_ensai_credentials(
                    username.get_secret_value(), password.get_secret_value()
                )
                allow_auto = True
            except Exception:
                # Keyring write failed: disable automated submit so we never reuse stale credentials
                allow_auto = False
                self.last_action_code = "keyring_unavailable_interactive_login_required"
                logger.warning(
                    "Secure credential store unavailable for %s; falling back to interactive browser login.",
                    self._source.value,
                )
        try:
            status = self._adapter.authenticate(
                self._source,
                interactive=True,
                allow_automated_submit=allow_auto,
            )
        except BrowserProfileLockError as exc:
            raise ProfileBusy(str(exc)) from exc
        except ProfileBusy:
            raise
        if (
            not status.authenticated
            and status.state == AuthState.ERROR
            and "locked by another process" in status.detail.lower()
        ):
            raise ProfileBusy(status.detail)
        return _to_codex_auth_result(status, institutional=True)

    def validate(self) -> AuthenticationResult:
        try:
            status = self._adapter.check_source_connection(
                self._source,
                headless=True,
                allow_auto_reauth=True,
            )
        except BrowserProfileLockError as exc:
            raise ProfileBusy(str(exc)) from exc
        except ProfileBusy:
            raise
        if (
            not status.authenticated
            and status.state == AuthState.ERROR
            and "locked by another process" in status.detail.lower()
        ):
            raise ProfileBusy(status.detail)
        return _to_codex_auth_result(status, institutional=True)

    def reauthorize(self, inputs: Mapping[str, SecretStr]) -> AuthenticationResult:
        return self.authorize(inputs)

    def disconnect(self) -> None:
        try:
            # Preserve shared ENSAI browser profile while another source may retain consent (GEMINI-REV-018)
            self._adapter.disconnect(self._source, clear_shared_profile=False)
        except BrowserProfileLockError as exc:
            raise ProfileBusy(str(exc)) from exc


class CodexMoodleRestAuthBridge(SourceAuthenticationAdapter):
    """Consent-gated Moodle Web Services REST authentication adapter (never touches Playwright/CAS)."""

    def __init__(
        self,
        connector: MoodleConnector,
        cred_manager: SecureCredentialManager,
    ) -> None:
        self._connector = connector
        self._creds = cred_manager

    def authorize(self, inputs: Mapping[str, SecretStr]) -> AuthenticationResult:
        token = inputs.get("token") or inputs.get("wstoken")
        if token is not None and token.get_secret_value().strip():
            self._creds.store_moodle_ws_token(token.get_secret_value().strip())
        return self.validate()

    def reauthorize(self, inputs: Mapping[str, SecretStr]) -> AuthenticationResult:
        return self.authorize(inputs)

    def validate(self) -> AuthenticationResult:
        status = self._connector._validate_ws_token()
        return _to_codex_auth_result(status, institutional=True, rest_mode=True)

    def disconnect(self) -> None:
        self._creds.clear_moodle_ws_token()


class CodexGoogleAuthBridge(SourceAuthenticationAdapter):
    """Bridges GoogleOAuthAdapter + source-specific read-only remote probe to Codex."""

    def __init__(
        self,
        oauth: GoogleOAuthAdapter,
        *,
        remote_probe: Callable[[], Any] | None = None,
    ) -> None:
        self._oauth = oauth
        self._remote_probe = remote_probe

    def _run_remote_probe(self) -> AuthenticationResult:
        if self._remote_probe is None:
            return AuthenticationResult(status=ConnectionStatus.CONNECTED)
        try:
            self._remote_probe()
            return AuthenticationResult(status=ConnectionStatus.CONNECTED)
        except AuthenticationRequiredError as exc:
            raise ReauthenticationRequired(str(exc)) from exc
        except PermissionDeniedError:
            return AuthenticationResult(
                status=ConnectionStatus.ACCESS_DENIED,
                reason="consent_required",
            )
        except TransientSourceError:
            return AuthenticationResult(
                status=ConnectionStatus.UNAVAILABLE,
                reason="transient_failure",
            )
        except Exception as exc:
            logger.warning("Google remote validation failed: %s", type(exc).__name__)
            return AuthenticationResult(
                status=ConnectionStatus.ERROR,
                reason="adapter_error",
            )

    def authorize(self, inputs: Mapping[str, SecretStr]) -> AuthenticationResult:
        status = self._oauth.authenticate(interactive=True)
        local_res = _to_codex_auth_result(status)
        if local_res.status != ConnectionStatus.CONNECTED:
            return local_res
        return self._run_remote_probe()

    def reauthorize(self, inputs: Mapping[str, SecretStr]) -> AuthenticationResult:
        status = self._oauth.reauthorize()
        local_res = _to_codex_auth_result(status)
        if local_res.status != ConnectionStatus.CONNECTED:
            return local_res
        return self._run_remote_probe()

    def validate(self) -> AuthenticationResult:
        status = self._oauth.validate_connection()
        local_res = _to_codex_auth_result(status)
        if local_res.status != ConnectionStatus.CONNECTED:
            return local_res
        return self._run_remote_probe()

    def disconnect(self) -> None:
        self._oauth.disconnect(revoke_remote=True)


def _load_active_drive_documents(db_path: Path) -> dict[str, dict[str, str | None]] | None:
    """Read-only SQLite lookup of currently active Ensora Drive documents for root reconciliation.

    Returns `{external_id: {"title": title, "folder": folder_path}}` (including `{}` when zero
    Drive docs are currently active), or `None` if no database/table baseline is available.
    Preserving `folder` ensures reconciled tombstones for previously imported Global documents
    retain their original folder provenance when excluded non-destructively.
    """
    if not db_path.is_file():
        return None
    try:
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as conn:
            has_docs = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
            ).fetchone()
            if not has_docs:
                return None
            rows = conn.execute(
                """
                SELECT
                    d.external_id,
                    json_extract(d.metadata_json, '$.title'),
                    json_extract(d.metadata_json, '$.provenance.folder')
                FROM documents d
                JOIN connections c ON c.id = d.connection_id
                WHERE c.source = 'ensora_drive'
                  AND COALESCE(json_extract(d.metadata_json, '$.access_status'), 'active') = 'active'
                """
            ).fetchall()
            return {
                str(ext_id): {
                    "title": str(title or ext_id),
                    "folder": str(folder) if folder is not None else None,
                }
                for ext_id, title, folder in rows
                if folder_scope(folder) == "allowed"
            }
    except Exception:
        return None


def _load_known_document_revisions(db_path: Path, source_value: str) -> dict[str, str]:
    """Read-only SQLite lookup of `{external_id: remote_revision}` for active versioned documents.

    Enables incremental scheduled runs (e.g. weekly Moodle or on-change local sync) to skip
    unchanged documents at listing time without redundant downloads or reparsing.
    """
    if not db_path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as conn:
            has_docs = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
            ).fetchone()
            if not has_docs:
                return {}
            rows = conn.execute(
                """
                SELECT d.external_id, d.remote_revision
                FROM documents d
                JOIN connections c ON c.id = d.connection_id
                WHERE c.source = ?
                  AND d.active_version_id IS NOT NULL
                  AND d.remote_revision IS NOT NULL
                  AND COALESCE(json_extract(d.metadata_json, '$.access_status'), 'active') = 'active'
                """,
                (source_value,),
            ).fetchall()
            return {str(ext_id): str(rev) for ext_id, rev in rows if ext_id and rev}
    except Exception:
        return {}


def _load_known_record_revisions(db_path: Path, source_value: str) -> dict[str, str]:
    """Load persisted source-record revisions without opening the database for writes.

    Structured records store their source revision inside the normalized JSON
    provenance payload (unlike documents, which have a dedicated column).
    Returning these revisions lets incremental connectors omit unchanged facts.
    """
    if not db_path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as conn:
            has_recs = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'source_records'"
            ).fetchone()
            if not has_recs:
                return {}
            rows = conn.execute(
                """
                SELECT r.external_id,
                       json_extract(r.payload, '$.provenance.remote_revision')
                FROM source_records r
                JOIN connections c ON c.id = r.connection_id
                WHERE c.source = ?
                  AND json_extract(r.payload, '$.provenance.remote_revision') IS NOT NULL
                """,
                (source_value,),
            ).fetchall()
            return {str(ext_id): str(rev) for ext_id, rev in rows if ext_id and rev}
    except Exception:
        return {}


class CodexConnectorBridge:
    """Bridges any Gemini SourceConnector to Codex's SourceConnector protocol."""

    def __init__(
        self,
        connector: InternalSourceConnector,
        *,
        calendar_availability_only: bool = True,
        known_external_ids_loader: Callable[[], set[str] | Mapping[str, str] | None] | None = None,
        known_versions_loader: Callable[[], Mapping[str, str] | None] | None = None,
        drive_allowed_cohorts: tuple[str, ...] = ENSORA_DRIVE_ALLOWED_TOP_LEVEL_FOLDERS,
        page_size: int = MAX_PAGE_CACHE_ENTRIES,
    ) -> None:
        self._connector = connector
        self._calendar_availability_only = calendar_availability_only
        self._known_external_ids_loader = known_external_ids_loader
        self._known_versions_loader = known_versions_loader
        self._drive_allowed_cohorts = tuple(drive_allowed_cohorts)
        self._page_size = max(1, min(int(page_size), MAX_PAGE_CACHE_ENTRIES))
        # Strictly page-scoped bounded cache (replaced every list_changes call)
        self._page_cache: dict[str, InternalSourceRecord] = {}
        self.last_page_warnings: tuple[str, ...] = ()
        self.last_page_errors: tuple[str, ...] = ()
        self.last_checkpoint_blocked: bool = False

    def list_changes(self, scope: SyncScope, cursor: str | None) -> CodexChangePage:
        """Translate Codex's ``SyncScope`` into connector scope and convert returned records.

        Enforces approved Drive cohort scope defense-in-depth, excludes unscoped local-folder
        runs, injects read-only known revisions/active IDs for non-full incremental runs,
        propagates bounded allowlisted observation counters and safe warning/error codes,
        and withholds terminal ``resume_cursor`` whenever errors occur or scope is selective.

        Args:
            scope: Codex ``SyncScope`` describing source, filters, full/retry/discovery flags.
            cursor: Optional opaque continuation cursor or durable sync watermark.

        Returns:
            ``CodexChangePage`` ready for ``SyncEngine`` persistence and checkpoint tracking.

        Raises:
            PermissionDeniedError: If Drive scope requests an unauthorized folder ID.
            ReauthenticationRequired: On expired institutional CAS or Google OAuth credentials.
            ServiceAccessDenied: On institutional HTTP 403/access denial.
            ProfileBusy: When the shared ENSAI Chromium profile lock is already held.
            RetryableSourceError: On transient upstream failures or empty error-only pages.
        """
        # Local folder synchronization must be explicitly scoped; exclude from unscoped runs.
        if self._connector.source_kind == InternalSourceKind.LOCAL_FILE and not scope.folder_id:
            self._page_cache = {}
            self.last_page_warnings = ()
            self.last_page_errors = ()
            self.last_checkpoint_blocked = False
            return CodexChangePage(items=[], next_cursor=None, complete=True)

        scope_dict: dict[str, Any] = {
            "page_size": self._page_size,
        }
        if self._connector.source_kind == InternalSourceKind.ENSORA_DRIVE:
            root_id = getattr(self._connector, "root_folder_id", None)
            if scope.folder_id and (not root_id or scope.folder_id != root_id):
                raise PermissionDeniedError("Drive synchronization is restricted to the approved cohort folders.")
            # This filter is applied by connector discovery for initial, incremental,
            # full and failed-resource retry runs alike.
            scope_dict["cohorts"] = list(self._drive_allowed_cohorts)
        if scope.subject_id:
            scope_dict["course_ids"] = [scope.subject_id]
        if scope.folder_id:
            scope_dict["folder_id"] = scope.folder_id
            scope_dict["root_path"] = scope.folder_id
        if scope.full:
            scope_dict["full"] = True
        if getattr(scope, "discovery_only", False):
            scope_dict["discovery_only"] = True
        if getattr(scope, "academic_year", None):
            scope_dict["academic_year"] = scope.academic_year
        if getattr(scope, "window_start", None) is not None:
            ws = scope.window_start
            scope_dict["range_start"] = ws
            scope_dict["time_min"] = ws.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if getattr(scope, "window_end", None) is not None:
            we = scope.window_end
            scope_dict["range_end"] = we
            scope_dict["time_max"] = we.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if self._connector.source_kind == InternalSourceKind.GOOGLE_CALENDAR:
            scope_dict["availability_only"] = self._calendar_availability_only
        if self._connector.source_kind in (InternalSourceKind.MOODLE, InternalSourceKind.PAMPLEMOUSSE):
            # Runtime connector listing must never prompt, open a headed window, or auto-submit credentials
            scope_dict["interactive"] = False
            scope_dict["allow_auto_reauth"] = False
        if self._connector.source_kind == InternalSourceKind.PAMPLEMOUSSE:
            pamp_slice = (
                getattr(scope, "pamplemousse_scope", None)
                or getattr(scope, "sync_slice", None)
                or getattr(scope, "dataset", None)
                or getattr(scope, "sections", None)
            )
            if pamp_slice:
                scope_dict["sections"] = pamp_slice
        if (
            self._known_versions_loader is not None
            and not scope.full
            and not getattr(scope, "retry_failed", False)
            and self._connector.source_kind in (
                InternalSourceKind.MOODLE,
                InternalSourceKind.LOCAL_FILE,
                InternalSourceKind.PAMPLEMOUSSE,
            )
        ):
            known_revs = self._known_versions_loader()
            if known_revs:
                scope_dict["known_versions"] = dict(known_revs)
                scope_dict["known_hashes"] = dict(known_revs)
        if (
            self._connector.source_kind == InternalSourceKind.ENSORA_DRIVE
            and not scope.subject_id
            and not scope.folder_id
            and not (cursor and cursor.startswith("drvpage:"))
            and self._known_external_ids_loader is not None
        ):
            known_active = self._known_external_ids_loader()
            if known_active is not None:
                scope_dict["known_external_ids"] = known_active

        is_institutional = self._connector.source_kind in (
            InternalSourceKind.MOODLE,
            InternalSourceKind.PAMPLEMOUSSE,
        )
        try:
            page = self._connector.list_changes(scope=scope_dict, cursor=cursor)
        except AuthenticationRequiredError as exc:
            raise ReauthenticationRequired(
                "cas_login_required" if is_institutional else "credential_expired"
            ) from exc
        except PermissionDeniedError as exc:
            if is_institutional:
                raise ServiceAccessDenied("service_access_denied") from exc
            raise
        except BrowserProfileLockError as exc:
            raise ProfileBusy(str(exc)) from exc
        except ProfileBusy:
            raise
        except TransientSourceError as exc:
            raise RetryableSourceError(str(exc)) from exc
        except Exception as exc:
            if type(exc).__name__ == "DriveSizeLimitError":
                from .connectors.moodle import DownloadSizeLimitError

                raise DownloadSizeLimitError(
                    "Drive file exceeds configured size limit.",
                    size_bytes=getattr(exc, "size_bytes", 0),
                    limit_bytes=getattr(exc, "limit_bytes", 0),
                ) from exc
            raise

        # Replace cache per page and bound size; page_size <= MAX_PAGE_CACHE_ENTRIES ensures
        # every fetched item retains its full metadata.
        new_cache: dict[str, InternalSourceRecord] = {}
        codex_items: list[CodexSourceRecord] = []
        emitted_parents: set[str] = set()
        conversion_error_codes: list[str] = []

        for item in page.items:
            if (
                self._connector.source_kind == InternalSourceKind.ENSORA_DRIVE
                and item.access_status == AccessStatus.ACTIVE
                and folder_scope(item.folder_path) != "allowed"
            ):
                # Defense in depth for stale durable cursors or connector inventory:
                # never cache, normalize, download or expose active Global/unverified items,
                # while still forwarding tombstones so moved/removed docs retire cleanly.
                continue
            if len(new_cache) < MAX_PAGE_CACHE_ENTRIES:
                new_cache[item.external_id] = item
            try:
                converted_list = _to_codex_records(item, emitted_parents=emitted_parents)
                codex_items.extend(converted_list)
            except RecordConversionError:
                # Never log item.external_id or raw exception/field content (ROUND2-NEW-004)
                conversion_error_codes.append("incomplete_event_interval")
                logger.warning("Isolated record conversion failure (code=incomplete_event_interval)")
            except ValueError:
                conversion_error_codes.append("invalid_record_payload")
                logger.warning("Isolated record conversion failure (code=invalid_record_payload)")
            except Exception:
                conversion_error_codes.append("record_conversion_error")
                logger.warning("Isolated record conversion failure (code=record_conversion_error)")

        # Pamplemousse reports raw coefficients for assessments. Preserve each raw
        # coefficient above, and only add relative weights when a complete, healthy
        # listing contains every known assessment sibling with an explicit coefficient.
        # This normalizes within each parent assessment group rather than globally.
        if (
            self._connector.source_kind == InternalSourceKind.PAMPLEMOUSSE
            and page.complete
            and cursor is None
            and not page.warnings
            and not page.errors
            and not conversion_error_codes
        ):
            assessment_facts: dict[str, dict[str, Any]] = {}
            for item in page.items:
                payload = item.payload if isinstance(item.payload, dict) else {}
                if item.record_kind != "academic" or payload.get("record_type") != "assessment":
                    continue
                parent = payload.get("parent_external_id")
                if not parent:
                    continue
                mode = str(payload.get("weight_mode") or "").lower()
                raw = payload.get("weight")
                status = str(payload.get("weight_status") or "")
                try:
                    coefficient = Decimal(str(raw)) if raw is not None else None
                except Exception:
                    coefficient = None
                assessment_facts[item.external_id] = {
                    "parent": str(parent),
                    "title": str(payload.get("title") or item.title or item.external_id),
                    "source_url": item.source_url,
                    "revision": item.remote_version,
                    "modified_at": _parse_iso_dt(item.remote_modified_at),
                    "academic_year": (
                        str(payload.get("academic_year") or (item.provenance or {}).get("academic_year")).strip()
                        if (payload.get("academic_year") or (item.provenance or {}).get("academic_year"))
                        else None
                    ),
                    "mode": mode,
                    "coefficient": coefficient,
                    "status": status,
                    "payload": payload,
                }

            groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
            for external_id, fact in assessment_facts.items():
                groups.setdefault(fact["parent"], []).append((external_id, fact))
            existing_weight_ids = {
                record.provenance.external_id
                for record in codex_items
                if isinstance(record, AssessmentWeight)
            }
            for siblings in groups.values():
                # Do not normalize a partial/ambiguous sibling set or mix explicit
                # relative values with raw institutional coefficients.
                if len(siblings) < 2 or any(
                    fact["mode"] != "raw"
                    or fact["coefficient"] is None
                    or fact["status"] in ("missing", "ambiguous", "unknown")
                    for _, fact in siblings
                ):
                    continue
                total = sum((fact["coefficient"] for _, fact in siblings), Decimal(0))
                if total <= 0:
                    continue
                for external_id, fact in siblings:
                    derived_id = f"{external_id}:normalized_sibling_weight"
                    if derived_id in existing_weight_ids:
                        continue
                    codex_items.append(
                        AssessmentWeight(
                            provenance=Provenance(
                                external_id=derived_id,
                                remote_revision=fact["revision"],
                                modified_at=fact["modified_at"],
                                source=CodexSourceKind.PAMPLEMOUSSE,
                                source_url=fact["source_url"],
                                academic_year=fact.get("academic_year"),
                            ),
                            title=f"{fact['title']} relative weight",
                            parent_key=external_id,
                            weight=fact["coefficient"] / total,
                            mode="fraction",
                            weight_basis="normalized_sibling_coefficients",
                        )
                    )

        self._page_cache = new_cache
        self.last_page_warnings = tuple(
            "source_warning" for _ in page.warnings
        )
        safe_listing_codes = tuple("source_listing_error" for _ in page.errors)
        all_error_codes = (*safe_listing_codes, *conversion_error_codes)
        self.last_page_errors = all_error_codes
        has_errors = len(all_error_codes) > 0
        self.last_checkpoint_blocked = has_errors

        warning_codes: list[str] = []
        if page.warnings:
            warning_codes.append("source_warning")
        error_codes: list[str] = []
        if page.errors:
            error_codes.append("source_listing_error")
        for code in conversion_error_codes:
            if code not in error_codes:
                error_codes.append(code)

        # If upstream reported partial/folder listing errors and yielded no healthy items,
        # signal RetryableSourceError so SyncEngine never marks an incomplete run "succeeded".
        if page.errors and not codex_items:
            raise RetryableSourceError(
                f"Source {self._connector.source_kind.value} reported listing errors: {len(page.errors)}"
            )

        # Attach CalendarSnapshot marker for BOTH unscoped Google Calendar full/410 runs
        # AND explicitly window-bounded Google Calendar runs (required by SyncEngine window_verified).
        # Window-scoped runs still remain `selective=True` so they NEVER overwrite the durable global syncToken!
        cal_snapshot: CalendarSnapshot | None = None
        window_scoped = (
            getattr(scope, "window_start", None) is not None
            and getattr(scope, "window_end", None) is not None
            and not scope.subject_id
            and not scope.folder_id
            and not getattr(scope, "academic_year", None)
        )
        selective = bool(
            scope.subject_id
            or scope.folder_id
            or getattr(scope, "pamplemousse_scope", None)
            or getattr(scope, "window_start", None) is not None
            or getattr(scope, "academic_year", None)
        )
        if self._connector.source_kind == InternalSourceKind.GOOGLE_CALENDAR and (not selective or window_scoped):
            snap_info = getattr(self._connector, "last_snapshot_info", None)
            if snap_info is not None:
                prefix, tmin_raw, tmax_raw = snap_info
                s_dt = _parse_iso_dt(tmin_raw)
                e_dt = _parse_iso_dt(tmax_raw)
                if s_dt is not None and e_dt is not None and s_dt < e_dt:
                    cal_snapshot = CalendarSnapshot(
                        external_id_prefix=prefix,
                        starts_at=s_dt,
                        ends_at=e_dt,
                    )
                else:
                    cal_snapshot = CalendarSnapshot(external_id_prefix=prefix)

        # Propagate strictly allowlisted non-negative integer page observation counters (zero private data)
        observations: dict[str, int] = {}
        raw_stats = getattr(self._connector, "last_stats", None)
        if isinstance(raw_stats, dict):
            run_totals = {
                "courses_discovered", "pages_visited", "timetable_events", "enrolled_subjects",
                "teaching_units", "semester_associations", "subject_coefficients", "ects_values",
                "available_grades", "assessment_records", "aggregate_grades", "missing_or_ambiguous_count",
            }
            for obs_key in (
                "courses_discovered", "courses_traversed", "sections_discovered", "sections_traversed",
                "folders_traversed", "resources_excluded", "resources_discovered", "pages_visited",
                "timetable_events", "enrolled_subjects", "teaching_units", "semester_associations",
                "subject_coefficients", "ects_values", "available_grades", "assessment_records",
                "aggregate_grades", "missing_or_ambiguous_count",
            ):
                val = raw_stats.get(obs_key)
                # Moodle's discovered-course count, Pamplemousse's academic totals, and Drive's
                # root-scan totals are listing-wide values repeated across continuation pages; count once.
                if obs_key in run_totals and cursor not in (None, "0"):
                    continue
                if (
                    self._connector.source_kind == InternalSourceKind.ENSORA_DRIVE
                    and obs_key in ("folders_traversed", "resources_discovered")
                ):
                    delta_key = f"page_{obs_key}"
                    if delta_key in raw_stats:
                        val = raw_stats.get(delta_key)
                    elif not page.complete:
                        continue
                if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
                    observations[obs_key] = val
            if (
                self._connector.source_kind == InternalSourceKind.MOODLE
                and raw_stats.get("discovery_incomplete") is True
                and "discovery_coverage_unknown" not in warning_codes
            ):
                warning_codes.append("discovery_coverage_unknown")

        if not page.complete:
            return CodexChangePage(
                items=codex_items,
                next_cursor=page.next_cursor,
                complete=False,
                calendar_snapshot=cal_snapshot,
                observations=observations,
                warning_codes=warning_codes,
                error_codes=error_codes,
            )

        # On a terminal page, set resume_cursor ONLY when zero errors occurred and scope is global.
        durable_cursor = None if (has_errors or selective) else (page.sync_token or page.next_cursor)
        return CodexChangePage(
            items=codex_items,
            next_cursor=durable_cursor,
            resume_cursor=durable_cursor,
            complete=True,
            calendar_snapshot=cal_snapshot,
            observations=observations,
            warning_codes=warning_codes,
            error_codes=error_codes,
        )

    def fetch_blob(self, record: Document) -> FetchedDocument:
        """Fetch raw document bytes for ``record`` using page-cached connector metadata when available.

        Translates internal authentication, permission, browser-lock, transient, and
        Drive size-limit exceptions into Codex's shared exception hierarchy and returns
        a basename-sanitized ``FetchedDocument``.
        """
        cached = self._page_cache.get(record.provenance.external_id)
        internal_rec = cached or InternalSourceRecord(
            source_kind=self._connector.source_kind,
            external_id=record.provenance.external_id,
            title=record.title,
            mime_type=record.mime_type,
            folder_path=record.provenance.folder,
            source_url=record.provenance.source_url,
            download_url=record.provenance.source_url,
        )
        is_institutional = self._connector.source_kind in (
            InternalSourceKind.MOODLE,
            InternalSourceKind.PAMPLEMOUSSE,
        )
        try:
            if self._connector.source_kind == InternalSourceKind.MOODLE:
                try:
                    res = self._connector.fetch_blob(
                        internal_rec, interactive=False, allow_auto_reauth=False
                    )
                except TypeError:
                    res = self._connector.fetch_blob(internal_rec)
            else:
                res = self._connector.fetch_blob(internal_rec)
            basename = Path(res.filename).name if res.filename else None
            return FetchedDocument(
                content=res.content,
                mime_type=res.mime_type or record.mime_type,
                filename=basename or None,
            )
        except AuthenticationRequiredError as exc:
            raise ReauthenticationRequired(
                "cas_login_required" if is_institutional else "credential_expired"
            ) from exc
        except PermissionDeniedError as exc:
            if is_institutional:
                raise ServiceAccessDenied("service_access_denied") from exc
            raise
        except BrowserProfileLockError as exc:
            raise ProfileBusy(str(exc)) from exc
        except ProfileBusy:
            raise
        except TransientSourceError as exc:
            raise RetryableSourceError(str(exc)) from exc
        except Exception as exc:
            if type(exc).__name__ == "DriveSizeLimitError":
                from .connectors.moodle import DownloadSizeLimitError

                raise DownloadSizeLimitError(
                    "Drive file exceeds configured size limit.",
                    size_bytes=getattr(exc, "size_bytes", 0),
                    limit_bytes=getattr(exc, "limit_bytes", 0),
                ) from exc
            raise


MAX_RAG_CHUNK_CHARS = 1800
RAG_CHUNK_OVERLAP_CHARS = 180


def _split_rag_text(
    text: str,
    *,
    max_chars: int = MAX_RAG_CHUNK_CHARS,
    overlap_chars: int = RAG_CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Deterministically split oversized text blocks into RAG-ready chunks preserving boundaries."""
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    step_min = max(1, max_chars - overlap_chars)
    while start < n:
        end = min(n, start + max_chars)
        if end < n:
            # Prefer paragraph break, then newline, then sentence boundary, then space
            cut = text.rfind("\n\n", start + step_min // 2, end)
            if cut == -1:
                cut = text.rfind("\n", start + step_min // 2, end)
            if cut == -1:
                cut = text.rfind(". ", start + step_min // 2, end)
                if cut != -1:
                    cut += 1
            if cut == -1:
                cut = text.rfind(" ", start + step_min // 2, end)
            if cut > start:
                end = cut
        segment = text[start:end].strip()
        if segment:
            chunks.append(segment)
        if end >= n:
            break
        next_start = max(start + 1, end - overlap_chars)
        # Avoid starting in the middle of a word when backing up for overlap
        if next_start > start and next_start < n and text[next_start - 1].isalnum() and text[next_start].isalnum():
            ws = text.find(" ", next_start, end)
            if ws != -1:
                next_start = ws + 1
        start = next_start
    return chunks or [text[:max_chars]]


class CodexParserBridge:
    """Bridges DocumentParserRegistry to Codex's DocumentParser protocol.

    Preserves block kinds, code languages, language hints, math/table/figure counts,
    bounded RAG-ready chunk sizes, partial extraction status, and parser warnings.
    """

    def __init__(
        self,
        registry: DocumentParserRegistry | None = None,
        *,
        max_chunk_chars: int = MAX_RAG_CHUNK_CHARS,
        chunk_overlap_chars: int = RAG_CHUNK_OVERLAP_CHARS,
    ) -> None:
        self._registry = registry or DocumentParserRegistry()
        self._max_chunk_chars = max(256, int(max_chunk_chars))
        self._chunk_overlap_chars = max(0, min(int(chunk_overlap_chars), self._max_chunk_chars // 3))

    def parse(self, document: Document, content: bytes) -> ParsedContent | None:
        """Parse raw document bytes into Codex RAG chunks with bounded size and provenance.

        Computes a single SHA-256 digest up front so downstream parsers and the
        version-aware registry cache avoid hashing large binaries repeatedly.
        """
        from .connectors.base import sha256_bytes

        content_sha = sha256_bytes(content)
        parsed = self._registry.parse_bytes(
            content,
            filename=document.title,
            mime_type=document.mime_type,
            provenance=document.provenance.model_dump(mode="json", exclude_none=True),
            precomputed_sha256=content_sha,
        )
        if parsed.error and not parsed.blocks:
            return None
        chunks: list[DocumentChunk] = []
        warning_codes: list[str] = []

        def _add_warn(code: str) -> None:
            if code not in warning_codes:
                warning_codes.append(code)

        if "scanned_pages_without_ocr_engine" in parsed.unsupported_elements:
            _add_warn("scanned_pages_without_ocr")
        if "pdf_scanned_pages_unrecovered" in parsed.unsupported_elements:
            _add_warn("pdf_scanned_pages_unrecovered")
        if "ocr_engine_unavailable" in parsed.unsupported_elements:
            _add_warn("ocr_unavailable")
        for w in parsed.warnings:
            low_w = str(w).lower()
            if "pdf_mixed_media_empty_pages" in low_w:
                _add_warn("mixed_media_pages_present")
            elif "pdf_ocr_incomplete" in low_w:
                _add_warn("pdf_ocr_incomplete")
            elif "legacy_binary_office" in low_w:
                _add_warn("legacy_binary_office")
            elif "zip_contains_no_parseable_text_members" in low_w:
                _add_warn("non_textual_archive")
            elif "zip archive truncated" in low_w or "skipped oversized zip" in low_w or "high-compression-ratio" in low_w:
                _add_warn("archive_limit_truncated")
        if parsed.parser_name == "notebook_parser" and parsed.figures:
            _add_warn("notebook_visual_outputs")
        if parsed.warnings and not warning_codes:
            _add_warn("parse_warning")

        figure_count = len(parsed.figures)
        for idx, block in enumerate(parsed.blocks):
            base_loc: dict[str, str | int] = {
                "kind": block.locator.kind,
                "block_kind": block.kind,
            }
            for k in ("page", "slide", "cell", "line_start", "line_end", "section"):
                v = getattr(block.locator, k)
                if v is not None:
                    base_loc[k] = v
            if block.code_language:
                base_loc["code_language"] = block.code_language

            # Keep structured tables intact; split only oversized prose/code/raw blocks for RAG context windows
            segments = (
                [block.text]
                if block.table_rows or len(block.text) <= self._max_chunk_chars
                else _split_rag_text(
                    block.text,
                    max_chars=self._max_chunk_chars,
                    overlap_chars=self._chunk_overlap_chars,
                )
            )
            seg_total = len(segments)
            for seg_idx, seg_text in enumerate(segments):
                loc_dict = dict(base_loc)
                if seg_total > 1:
                    loc_dict["sub_chunk"] = seg_idx + 1
                    loc_dict["sub_chunks_total"] = seg_total

                structured: dict[str, object] = {}
                if block.metadata.get("ocr") is True:
                    structured["ocr"] = True
                if block.language:
                    structured["language"] = block.language
                if block.code_language:
                    structured["code_language"] = block.code_language
                if block.table_rows and seg_idx == 0:
                    structured["table_rows"] = [list(r) for r in block.table_rows]
                if block.math_expressions and seg_idx == 0:
                    structured["math_expressions"] = list(block.math_expressions)
                if idx == 0 and seg_idx == 0:
                    structured["quality_pct"] = int(round(parsed.extraction_quality * 100))
                    if figure_count:
                        structured["figure_count"] = figure_count

                chunks.append(
                    DocumentChunk(
                        text=seg_text,
                        section=list(block.heading_path),
                        locator=loc_dict,
                        block_kind=block.kind,
                        structured=structured,
                    )
                )

        is_partial = bool(
            parsed.unsupported_elements
            or (0.0 < parsed.extraction_quality < 0.70)
        )
        return ParsedContent(
            parser_version=f"{parsed.parser_name}-{parsed.parser_version}",
            status="partial" if is_partial else "ready",
            chunks=chunks,
            warning_codes=warning_codes,
        )


def extract_concept_candidates(
    document: Document,
    parsed: ParsedContent | None,
) -> list[Concept]:
    """Extract deterministic, provenance-linked candidate Concept records from parsed evidence (R07 / REV-020).

    Emits reviewable Concept candidates for explicit definitions, theorems, propositions,
    algorithms, and math formulas without invoking external LLMs.
    """
    import hashlib
    import re

    if parsed is None or not parsed.chunks:
        return []

    marker_re = re.compile(
        r"^\s*(?:définition|definition|théorème|theoreme|theorem|proposition|lemme|lemma|corollaire|algorithme|algorithm|propriété|propriete)\s*[0-9.]*\s*[:\-–]?\s*(.{3,120})",
        re.IGNORECASE | re.MULTILINE,
    )
    concepts: list[Concept] = []
    seen_titles: set[str] = set()
    parent_key = document.subject_id or document.provenance.external_id

    for idx, chunk in enumerate(parsed.chunks):
        chunk_evidence = [ConceptEvidence(chunk_ordinal=idx, locator=dict(chunk.locator))]
        for match in marker_re.finditer(chunk.text):
            label = " ".join(match.group(0).split()).strip()[:160]
            norm = label.lower()
            if not label or norm in seen_titles:
                continue
            seen_titles.add(norm)
            cid = hashlib.sha1(f"{document.provenance.external_id}:{idx}:{norm}".encode("utf-8")).hexdigest()[:12]
            concepts.append(
                Concept(
                    provenance=Provenance(
                        external_id=f"{document.provenance.external_id}:concept:{cid}",
                        remote_revision=document.provenance.remote_revision,
                        source=document.provenance.source,
                        source_url=document.provenance.source_url,
                        folder=document.provenance.folder,
                        academic_year=document.provenance.academic_year,
                    ),
                    title=label,
                    parent_key=parent_key,
                    evidence=chunk_evidence,
                )
            )
        for expr in chunk.structured.get("math_expressions") or []:
            expr_str = str(expr).strip()
            if len(expr_str) < 3 or len(expr_str) > 120:
                continue
            label = f"Formula: {expr_str}"
            norm = label.lower()
            if norm in seen_titles:
                continue
            seen_titles.add(norm)
            cid = hashlib.sha1(f"{document.provenance.external_id}:math:{norm}".encode("utf-8")).hexdigest()[:12]
            concepts.append(
                Concept(
                    provenance=Provenance(
                        external_id=f"{document.provenance.external_id}:concept:{cid}",
                        remote_revision=document.provenance.remote_revision,
                        source=document.provenance.source,
                        source_url=document.provenance.source_url,
                        folder=document.provenance.folder,
                        academic_year=document.provenance.academic_year,
                    ),
                    title=label,
                    parent_key=parent_key,
                    evidence=chunk_evidence,
                )
            )
    return concepts


def extract_prerequisite_hints(parsed: ParsedContent | None) -> list[dict[str, object]]:
    """Extract deterministic candidate prerequisite cues with chunk-ordinal/locator evidence for P2/P3."""
    import re

    if parsed is None or not parsed.chunks:
        return []
    prereq_re = re.compile(
        r"^\s*(?:pr[eé][\-\s]?requis|prerequisites?|requires|n[eé]cessite|notions?\s+requises?|chapitres?\s+requis|avant\s+de\s+commencer)\s*[:\-–]\s*(.{3,160})",
        re.IGNORECASE | re.MULTILINE,
    )
    hints: list[dict[str, object]] = []
    seen: set[str] = set()
    for idx, chunk in enumerate(parsed.chunks):
        for match in prereq_re.finditer(chunk.text):
            text = " ".join(match.group(1).split()).strip()[:160]
            norm = text.lower()
            if not text or norm in seen:
                continue
            seen.add(norm)
            hints.append(
                {
                    "text": text,
                    "chunk_ordinal": idx,
                    "locator": dict(chunk.locator),
                }
            )
    return hints


class CodexClassifierBridge:
    """Bridges DocumentClassifier to Codex's DocumentClassifier protocol using parsed text."""

    def __init__(self, classifier: DocumentClassifier | None = None) -> None:
        self._classifier = classifier or DocumentClassifier()

    def classify(
        self,
        document: Document,
        parsed: ParsedContent | None,
    ) -> CodexClassificationResult:
        parsed_doc: ParsedDocument | None = None
        if parsed is not None and parsed.chunks:
            blocks = tuple(
                ParsedBlock(
                    ordinal=idx,
                    kind=str(ch.locator.get("block_kind", "paragraph")),
                    text=ch.text,
                    locator=Locator(kind=str(ch.locator.get("kind", "section"))),
                    heading_path=tuple(ch.section),
                )
                for idx, ch in enumerate(parsed.chunks[:20])
            )
            parsed_doc = ParsedDocument(
                parser_name="codex_chunks",
                parser_version=parsed.parser_version,
                title=document.title,
                mime_type=document.mime_type,
                content_hash="",
                blocks=blocks,
            )

        resolved_source = (
            _from_codex_source_kind(document.provenance.source)
            or _infer_source_kind_from_external_id(document.provenance.external_id)
        )
        section_hint = document.chapter_id or (
            " / ".join(document.section_path) if document.section_path else None
        )
        synth_record = InternalSourceRecord(
            source_kind=resolved_source,
            external_id=document.provenance.external_id,
            title=document.title,
            record_kind="announcement" if document.resource_kind == "announcement" else "document",
            mime_type=document.mime_type,
            folder_path=document.provenance.folder,
            course_id=document.course_id or document.subject_id,
            course_title=document.course_title,
            section_title=section_hint,
            payload={"resource_kind": document.resource_kind, "filename": document.title},
        )
        res = self._classifier.classify(
            title=document.title,
            filename=document.title,
            folder_path=document.provenance.folder,
            mime_type=document.mime_type,
            record=synth_record,
            parsed=parsed_doc,
        )
        return CodexClassificationResult(
            resource_type=res.category.value,
            confidence=res.confidence,
        )


class CodexEntityMatcherBridge:
    """Catalog-backed bridge adapting AcademicEntityResolver to Codex's EntityMatcher."""

    def __init__(
        self,
        resolver: AcademicEntityResolver | None = None,
        *,
        catalog_loader: Callable[[], list[CanonicalSubject]] | None = None,
    ) -> None:
        self._resolver = resolver or AcademicEntityResolver()
        self._catalog_loader = catalog_loader

    @property
    def resolver(self) -> AcademicEntityResolver:
        return self._resolver

    def sync_catalog_subjects(self, subjects: list[CanonicalSubject]) -> int:
        """Register persisted catalog subjects into the underlying resolver."""
        for subj in subjects:
            self._resolver.register_subject(subj)
        return len(subjects)

    def load_from_sqlite_engine(
        self,
        engine: Any,
        *,
        consent_manager: Any | None = None,
        connection_ids: Mapping[CodexSourceKind, str] | None = None,
    ) -> int:
        """Hydrate canonical subjects and confirmed overrides from Codex SQLite tables.

        When `consent_manager` and `connection_ids` are provided, strictly filters BOTH
        subjects and cross-record user overrides to currently read-consented sources.
        """
        import re as _re_mod
        from sqlalchemy import func, select
        from ...core.database import documents as db_documents, records as db_records

        allowed_conn_ids: list[str] | None = None
        if consent_manager is not None and connection_ids is not None:
            allowed_conn_ids = [
                cid
                for src, cid in connection_ids.items()
                if consent_manager.allowed(src, "read")
            ]
            if not allowed_conn_ids:
                return 0

        added = 0
        with engine.connect() as db:
            # Hydrate semester/UE hierarchy metadata so subjects retain semester & cohort hints
            ancestors: dict[str, dict[str, Any]] = {}
            anc_query = select(db_records.c.external_id, db_records.c.kind, db_records.c.payload).where(
                db_records.c.kind.in_(("semester", "teaching_unit"))
            )
            if allowed_conn_ids is not None:
                anc_query = anc_query.where(db_records.c.connection_id.in_(allowed_conn_ids))
            for arow in db.execute(anc_query).mappings():
                ancestors[str(arow["external_id"])] = {
                    "kind": str(arow["kind"]),
                    "payload": arow["payload"] or {},
                }

            subj_query = select(db_records.c.external_id, db_records.c.payload).where(
                db_records.c.kind == "subject"
            )
            if allowed_conn_ids is not None:
                subj_query = subj_query.where(db_records.c.connection_id.in_(allowed_conn_ids))
            subj_rows = db.execute(subj_query).mappings()
            for row in subj_rows:
                payload = row["payload"] or {}
                title = payload.get("title") or row["external_id"]
                code = payload.get("code") or payload.get("source_code")
                raw_aliases = payload.get("aliases") or []
                aliases = tuple(str(a) for a in raw_aliases if a)
                if payload.get("source_code") and payload.get("source_code") != code:
                    aliases = (*aliases, str(payload["source_code"]))
                parent_ue = payload.get("parent_key")
                # Resolve explicit semester/cohort from parent UE or Semester without inventing values
                subj_sem: str | None = None
                subj_cohort: str | None = None
                cur_key = str(parent_ue) if parent_ue else None
                for _ in range(3):
                    if not cur_key or cur_key not in ancestors:
                        break
                    anc = ancestors[cur_key]
                    anc_p = anc["payload"]
                    anc_title = str(anc_p.get("title") or "")
                    if anc["kind"] == "semester" and not subj_sem:
                        m_s = _re_mod.search(r"\bS([1-6])\b", anc_title, _re_mod.IGNORECASE)
                        subj_sem = f"S{m_s.group(1)}" if m_s else anc_title or None
                    cur_key = anc_p.get("parent_key")
                if not subj_sem:
                    sem_probe = f"{row['external_id']} {parent_ue or ''} {title}"
                    m_s = _re_mod.search(r"(?:^|[:_\-\s/])s([1-6])(?:[:_\-\s/]|$)", sem_probe, _re_mod.IGNORECASE)
                    if m_s:
                        subj_sem = f"S{m_s.group(1)}"
                m_c = _re_mod.search(r"(?:^|[:_\-\s/])([1-3]a)(?:[:_\-\s/]|$)", f"{title} {code or ''}", _re_mod.IGNORECASE)
                if m_c:
                    subj_cohort = m_c.group(1).upper()
                self._resolver.register_subject(
                    CanonicalSubject(
                        subject_id=row["external_id"],
                        title=str(title),
                        code=str(code) if code else None,
                        aliases=aliases,
                        cohort=subj_cohort,
                        semester=subj_sem,
                        teaching_unit=str(parent_ue) if parent_ue else None,
                    )
                )
                added += 1

            doc_query = select(db_documents.c.external_id, db_documents.c.metadata_json).where(
                func.json_extract(db_documents.c.metadata_json, "$.user_override.subject_id").is_not(None)
            )
            if allowed_conn_ids is not None:
                doc_query = doc_query.where(db_documents.c.connection_id.in_(allowed_conn_ids))
            doc_rows = db.execute(doc_query).mappings()
            for row in doc_rows:
                meta = row["metadata_json"] or {}
                override = meta.get("user_override") or {}
                if override.get("subject_id"):
                    self._resolver.manual_store.set_subject_override(
                        row["external_id"],
                        str(override["subject_id"]),
                        chapter=override.get("chapter_id"),
                    )
        return added

    def find_exam_correction_pairs_from_engine(
        self,
        engine: Any,
        *,
        consent_manager: Any | None = None,
        connection_ids: Mapping[CodexSourceKind, str] | None = None,
    ) -> list[ExamCorrectionMatch]:
        """Discover reviewable exam-to-correction links from consent-scoped persisted documents."""
        from sqlalchemy import select
        from ...core.database import documents as db_documents

        allowed_conn_ids: list[str] | None = None
        if consent_manager is not None and connection_ids is not None:
            allowed_conn_ids = [
                cid
                for src, cid in connection_ids.items()
                if consent_manager.allowed(src, "read")
            ]
            if not allowed_conn_ids:
                return []

        docs_with_types: list[tuple[Document, str]] = []
        with engine.connect() as db:
            query = select(db_documents.c.metadata_json)
            if allowed_conn_ids is not None:
                query = query.where(db_documents.c.connection_id.in_(allowed_conn_ids))
            for row in db.execute(query).mappings():
                meta = row["metadata_json"] or {}
                if meta.get("access_status", "active") != "active":
                    continue
                if meta.get("ingestion_state") == "download_failed":
                    continue
                clf = meta.get("classification") or {}
                res_type = str(clf.get("resource_type") or "other")
                if res_type not in ("examination", "examination_correction"):
                    continue
                try:
                    doc = Document.model_validate(
                        {k: v for k, v in meta.items() if k in Document.model_fields}
                    )
                except Exception:
                    continue
                docs_with_types.append((doc, res_type))
        return self.link_exam_corrections(docs_with_types)

    def match(
        self,
        document: Document,
        classification: CodexClassificationResult,
    ) -> EntityMatch:
        """Resolve ``document`` against the hydrated canonical curriculum without forcing ties.

        Passes document title, folder path, course ID/title, and chapter/section path to
        ``AcademicEntityResolver.resolve_record()``. Ambiguous matches (within the ``0.06``
        tie margin) or scores below ``0.90`` return ``confirmed=False`` for human review,
        while confirmed user overrides always return ``confirmed=True``.
        """
        if self._catalog_loader is not None:
            try:
                self.sync_catalog_subjects(self._catalog_loader())
            except Exception:
                pass

        resolved_source = (
            _from_codex_source_kind(document.provenance.source)
            or _infer_source_kind_from_external_id(document.provenance.external_id)
        )
        section_hint = document.chapter_id or (
            " / ".join(document.section_path) if document.section_path else None
        )
        internal_rec = InternalSourceRecord(
            source_kind=resolved_source,
            external_id=document.provenance.external_id,
            title=document.title,
            mime_type=document.mime_type,
            folder_path=document.provenance.folder,
            course_id=document.course_id or document.subject_id,
            course_title=document.course_title,
            section_title=section_hint,
            source_url=document.provenance.source_url,
        )
        res = self._resolver.resolve_record(internal_rec)
        return EntityMatch(
            teaching_unit_id=res.teaching_unit or document.teaching_unit_id,
            subject_id=res.canonical_subject_id or document.subject_id,
            chapter_id=res.chapter or document.chapter_id,
            confidence=res.confidence,
            confirmed=not res.needs_review and res.canonical_subject_id is not None,
        )

    def link_exam_corrections(
        self,
        documents_with_types: list[tuple[Document, str]],
    ) -> list[ExamCorrectionMatch]:
        """Link examination and examination_correction documents using the shared resolver."""
        prepared: list[tuple[InternalSourceRecord, DocumentCategory, Any]] = []
        for doc, res_type in documents_with_types:
            try:
                cat = DocumentCategory(res_type)
            except ValueError:
                cat = DocumentCategory.OTHER
            resolved_source = (
                _from_codex_source_kind(doc.provenance.source)
                or _infer_source_kind_from_external_id(doc.provenance.external_id)
            )
            rec = InternalSourceRecord(
                source_kind=resolved_source,
                external_id=doc.provenance.external_id,
                title=doc.title,
                mime_type=doc.mime_type,
                folder_path=doc.provenance.folder,
                course_id=doc.subject_id,
                section_title=doc.chapter_id,
            )
            prepared.append((rec, cat, self._resolver.resolve_record(rec)))
        return self._resolver.match_exams_and_corrections(prepared)


def build_components(
    settings: Settings,
    credentials: CredentialStore,
    sessions: BrowserSessionManager,
    *,
    catalog_loader: Callable[[], list[CanonicalSubject]] | None = None,
) -> Components:
    """Composition factory compatible with Codex's `Runtime._load_components`.

    Inert at creation: performs no browser launch, OAuth flow, or network call
    until explicit adapter/connector invocation.
    """
    cfg = runtime_config_from_codex_settings(settings)
    cred_store = _CodexCredentialStoreAdapter(credentials)
    cred_manager = SecureCredentialManager(store=cred_store)

    ensai_auth = EnsaiAuthAdapter(cfg, cred_manager, session_manager=sessions)
    drive_oauth = GoogleOAuthAdapter(cfg, source=InternalSourceKind.ENSORA_DRIVE)
    cal_oauth = GoogleOAuthAdapter(cfg, source=InternalSourceKind.GOOGLE_CALENDAR)

    moodle_conn = MoodleConnector(cfg, auth_adapter=ensai_auth, credential_manager=cred_manager)
    pamp_conn = PamplemousseConnector(cfg, auth_adapter=ensai_auth)
    drive_conn = EnsoraDriveConnector(cfg, oauth_adapter=drive_oauth)
    cal_conn = GoogleCalendarConnector(cfg, oauth_adapter=cal_oauth)
    local_conn = LocalFileConnector(
        max_file_bytes=cfg.max_download_bytes,
        protected_roots=(
            settings.config_dir,
            settings.local_data_dir,
            settings.ensai_profile_dir,
            settings.google_credentials_path.parent,
            settings.google_token_path.parent,
        ),
    )

    resolver = AcademicEntityResolver(
        current_academic_year=settings.academic_year,
        student_cohort=settings.study_year,
        student_specialization=settings.specialization,
    )

    moodle_auth_bridge: SourceAuthenticationAdapter = (
        CodexMoodleRestAuthBridge(moodle_conn, cred_manager)
        if (cfg.moodle_ws_enabled or cfg.moodle_auth_mode == "rest")
        else CodexEnsaiAuthBridge(ensai_auth, InternalSourceKind.MOODLE, sessions, cred_manager)
    )

    return Components(
        adapters={
            CodexSourceKind.MOODLE: moodle_auth_bridge,
            CodexSourceKind.PAMPLEMOUSSE: CodexEnsaiAuthBridge(
                ensai_auth, InternalSourceKind.PAMPLEMOUSSE, sessions, cred_manager
            ),
            CodexSourceKind.ENSORA_DRIVE: CodexGoogleAuthBridge(
                drive_oauth, remote_probe=drive_conn.get_root_folder_metadata
            ),
            CodexSourceKind.GOOGLE_CALENDAR: CodexGoogleAuthBridge(
                cal_oauth, remote_probe=cal_conn.validate_calendar_access
            ),
        },
        connectors={
            CodexSourceKind.MOODLE: CodexConnectorBridge(
                moodle_conn,
                known_versions_loader=lambda db_path=settings.local_data_dir / "ensora.sqlite3": _load_known_document_revisions(
                    db_path, CodexSourceKind.MOODLE.value
                ),
            ),
            CodexSourceKind.PAMPLEMOUSSE: CodexConnectorBridge(
                pamp_conn,
                known_versions_loader=lambda db_path=settings.local_data_dir / "ensora.sqlite3": _load_known_record_revisions(
                    db_path, CodexSourceKind.PAMPLEMOUSSE.value
                ),
            ),
            CodexSourceKind.ENSORA_DRIVE: CodexConnectorBridge(
                drive_conn,
                known_external_ids_loader=lambda db_path=settings.local_data_dir / "ensora.sqlite3": _load_active_drive_documents(
                    db_path
                ),
                page_size=DRIVE_STREAMING_PAGE_SIZE,
            ),
            CodexSourceKind.GOOGLE_CALENDAR: CodexConnectorBridge(
                cal_conn,
                calendar_availability_only=not cfg.google_calendar_include_personal_details,
            ),
            CodexSourceKind.LOCAL: CodexConnectorBridge(
                local_conn,
                known_versions_loader=lambda db_path=settings.local_data_dir / "ensora.sqlite3": _load_known_document_revisions(
                    db_path, CodexSourceKind.LOCAL.value
                ),
            ),
        },
        parser=CodexParserBridge(DocumentParserRegistry()),
        classifier=CodexClassifierBridge(DocumentClassifier()),
        matcher=CodexEntityMatcherBridge(resolver, catalog_loader=catalog_loader),
    )


__all__ = [
    "build_components",
    "runtime_config_from_codex_settings",
    "RecordConversionError",
    "CodexEnsaiAuthBridge",
    "CodexMoodleRestAuthBridge",
    "CodexGoogleAuthBridge",
    "CodexConnectorBridge",
    "CodexParserBridge",
    "CodexClassifierBridge",
    "CodexEntityMatcherBridge",
    "StructuredAcademicImporter",
    "extract_concept_candidates",
    "extract_prerequisite_hints",
]

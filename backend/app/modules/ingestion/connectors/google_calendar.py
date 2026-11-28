"""Authorized read-only Google Calendar connector with ICS fallback.

Default calendar: "primary".
Strictly read-only: never creates, modifies, or deletes events.
Supports configurable date ranges, pagination, recurrence expansion, all-day events,
timezone normalization to ISO 8601 UTC, incremental sync via syncToken (with 410 Gone
recovery), cancelled event tombstoning, and privacy-preserving availability extraction
for the Smart Planner.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..auth.google_oauth import GoogleOAuthAdapter
from ..config import DEFAULT_GOOGLE_CALENDAR_ID, SourcesRuntimeConfig
from ..contracts import (
    AccessStatus,
    AuthState,
    AuthStatus,
    AvailabilityInterval,
    CalendarEventRecord,
    CalendarEventType,
    ChangePage,
    ConnectorCapabilities,
    FetchResult,
    SourceKind,
    SourceRecord,
    utc_now_iso,
)
from ..credentials import redact_sensitive_text
from .base import (
    AuthenticationRequiredError,
    PermissionDeniedError,
    RateLimiter,
    SourceConnector,
    TransientSourceError,
    retry_with_backoff,
)
from .pamplemousse import PARIS_TZ, normalize_timestamp_utc, parse_ics_events


class SyncTokenExpiredError(RuntimeError):
    """Raised when Google Calendar returns HTTP 410 Gone for an invalid syncToken."""


def _classify_calendar_http_error(exc: Exception) -> Exception:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status == 401:
        return AuthenticationRequiredError("Google Calendar API returned HTTP 401.")
    if status == 403:
        return PermissionDeniedError("Google Calendar API returned HTTP 403.")
    if status == 410:
        return SyncTokenExpiredError("Google Calendar syncToken expired (HTTP 410 Gone).")
    if status in (429, 500, 502, 503, 504):
        return TransientSourceError(f"Google Calendar transient HTTP {status}.", status_code=status)
    return exc


class GoogleCalendarConnector(SourceConnector):
    """Read-only Google Calendar connector prioritizing availability intervals."""

    source_kind = SourceKind.GOOGLE_CALENDAR

    def __init__(
        self,
        config: SourcesRuntimeConfig,
        *,
        oauth_adapter: GoogleOAuthAdapter | None = None,
        calendar_service_factory: Callable[[Any], Any] | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._config = config
        self._calendar_id = config.google_calendar_id or DEFAULT_GOOGLE_CALENDAR_ID
        self._oauth = oauth_adapter or GoogleOAuthAdapter(
            config, source=SourceKind.GOOGLE_CALENDAR
        )
        self._service_factory = calendar_service_factory
        self._rate_limiter = rate_limiter or RateLimiter(config.rate_limit_min_interval_sec)
        # Compact session-scoped paging state so multi-page cursors stay <= 32 chars (never exceed 512-char Key limit)
        self._paging_states: dict[str, dict[str, Any]] = {}
        self.last_resync_reset: bool = False

    @property
    def calendar_id(self) -> str:
        return self._calendar_id

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            source=SourceKind.GOOGLE_CALENDAR,
            supports_documents=False,
            supports_events=True,
            supports_assignments=False,
            supports_academic_records=False,
            supports_incremental=True,
            supports_full_scan=True,
            supports_download=False,
            read_only=True,
            fallback_formats=("ics",),
        )

    def _build_service(self) -> Any:
        creds = self._oauth.get_credentials(allow_refresh=True, interactive=False)
        if creds is None:
            raise AuthenticationRequiredError(
                "Google Calendar OAuth token missing or expired; run Google authorization first."
            )
        if self._service_factory is not None:
            return self._service_factory(creds)
        from googleapiclient.discovery import build  # type: ignore

        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def validate_calendar_access(self) -> dict[str, Any]:
        """Perform a read-only remote probe validating calendar API access."""
        service = self._build_service()

        def _op():
            self._rate_limiter.wait()
            try:
                return service.calendars().get(calendarId=self._calendar_id).execute()
            except Exception as exc:
                raise _classify_calendar_http_error(exc) from exc

        return retry_with_backoff(_op, max_retries=self._config.max_retries)

    def authorize(self, config: dict[str, Any] | None = None) -> AuthStatus:
        interactive = bool((config or {}).get("interactive", False))
        status = self._oauth.authenticate(interactive=interactive)
        if not status.authenticated:
            return status
        try:
            cal_meta = self.validate_calendar_access()
            return AuthStatus(
                source=SourceKind.GOOGLE_CALENDAR,
                state=AuthState.AUTHENTICATED,
                authenticated=True,
                detail=f"Validated read-only access to Google Calendar '{cal_meta.get('summary', self._calendar_id)}'.",
                scopes=status.scopes,
                metadata={
                    "calendar_id": cal_meta.get("id", self._calendar_id),
                    "time_zone": cal_meta.get("timeZone"),
                },
            )
        except Exception as exc:
            translated = _classify_calendar_http_error(exc)
            state = (
                AuthState.EXPIRED
                if isinstance(translated, AuthenticationRequiredError)
                else AuthState.ERROR
            )
            return AuthStatus(
                source=SourceKind.GOOGLE_CALENDAR,
                state=state,
                authenticated=False,
                detail=redact_sensitive_text(f"Google Calendar validation failed: {translated}"),
                requires_interactive_action=True,
            )

    def _parse_google_event(
        self,
        raw: dict[str, Any],
        *,
        calendar_tz: str = "Europe/Paris",
        calendar_id: str | None = None,
        include_descriptions: bool = False,
    ) -> CalendarEventRecord:
        calendar_id = calendar_id or self._calendar_id
        event_id = str(raw["id"])
        status_raw = str(raw.get("status") or "confirmed").lower()
        status = (
            "cancelled"
            if status_raw == "cancelled"
            else ("tentative" if status_raw == "tentative" else "confirmed")
        )
        transparency = str(raw.get("transparency") or "opaque").lower()
        busy = (status != "cancelled") and (transparency != "transparent")

        start_obj = raw.get("start") or {}
        end_obj = raw.get("end") or {}
        tz_name = start_obj.get("timeZone") or calendar_tz or "Europe/Paris"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = PARIS_TZ

        start_utc: str | None = None
        end_utc: str | None = None
        all_day_start: str | None = None
        all_day_end: str | None = None

        if "date" in start_obj:
            all_day_start = str(start_obj["date"])
            all_day_end = str(end_obj.get("date") or all_day_start)
            if all_day_end <= all_day_start:
                d = datetime.fromisoformat(all_day_start).date() + timedelta(days=1)
                all_day_end = d.isoformat()
        elif "dateTime" in start_obj:
            start_utc, _ = normalize_timestamp_utc(start_obj["dateTime"], default_tz=tz)
            end_utc, _ = normalize_timestamp_utc(end_obj.get("dateTime"), default_tz=tz)

        # Privacy-first: omit personal event descriptions unless explicitly requested
        summary = str(raw.get("summary") or ("Cancelled event" if status == "cancelled" else "Busy"))
        description = raw.get("description") if include_descriptions else None

        return CalendarEventRecord(
            source_kind=SourceKind.GOOGLE_CALENDAR,
            external_id=f"google_calendar:{calendar_id}:{event_id}",
            title=summary,
            event_type=CalendarEventType.PERSONAL,
            start_at=start_utc,
            end_at=end_utc,
            all_day_date=all_day_start,
            all_day_end_date=all_day_end,
            original_timezone=tz_name,
            status=status,
            room=raw.get("location") if include_descriptions else None,
            recurrence_key=raw.get("recurringEventId"),
            busy=busy,
            description=description,
            source_url=raw.get("htmlLink"),
            provenance={
                "source": SourceKind.GOOGLE_CALENDAR.value,
                "calendar_id": calendar_id,
                "google_event_id": event_id,
                "updated": raw.get("updated"),
                "retrieved_at": utc_now_iso(),
            },
        )

    def normalize(self, raw_item: dict[str, Any]) -> SourceRecord:
        """Convert a normalized CalendarEventRecord dict into a canonical SourceRecord."""
        ext_id = str(raw_item["external_id"])
        status = raw_item.get("status", "confirmed")
        access = AccessStatus.TOMBSTONED if status == "cancelled" else AccessStatus.ACTIVE
        version = (raw_item.get("provenance") or {}).get("updated") or hashlib.sha256(
            json.dumps(raw_item, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        return SourceRecord(
            source_kind=SourceKind.GOOGLE_CALENDAR,
            external_id=ext_id,
            title=str(raw_item.get("title") or "Calendar event"),
            record_kind="event",
            remote_version=str(version),
            remote_modified_at=(raw_item.get("provenance") or {}).get("updated"),
            source_url=raw_item.get("source_url"),
            access_status=access,
            payload=raw_item,
            provenance=raw_item.get("provenance") or {"source": SourceKind.GOOGLE_CALENDAR.value},
        )

    def import_ics_text(
        self,
        ics_text: str,
        *,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
    ) -> list[CalendarEventRecord]:
        """Import personal calendar events from an ICS fallback file."""
        return parse_ics_events(
            ics_text,
            source_kind=SourceKind.GOOGLE_CALENDAR,
            range_start=range_start,
            range_end=range_end,
        )

    def extract_availability_intervals(
        self,
        events: list[CalendarEventRecord],
        *,
        default_tz: ZoneInfo = PARIS_TZ,
    ) -> list[AvailabilityInterval]:
        """Convert calendar events into privacy-preserving busy/free intervals for Smart Planner.

        Strips personal titles, locations, and descriptions completely.
        """
        intervals: list[AvailabilityInterval] = []
        for ev in events:
            if ev.status == "cancelled":
                continue
            status = "free" if not ev.busy else ("tentative" if ev.status == "tentative" else "busy")
            if ev.all_day_date:
                tz = default_tz
                if ev.original_timezone:
                    try:
                        tz = ZoneInfo(ev.original_timezone)
                    except Exception:
                        tz = default_tz
                start_d = datetime.fromisoformat(ev.all_day_date).replace(tzinfo=tz)
                end_str = ev.all_day_end_date or ev.all_day_date
                end_d = datetime.fromisoformat(end_str).replace(tzinfo=tz)
                if end_d <= start_d:
                    end_d = start_d + timedelta(days=1)
                intervals.append(
                    AvailabilityInterval(
                        start_at=start_d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                        end_at=end_d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                        status=status,
                        all_day=True,
                        source_kind=ev.source_kind,
                        external_id=ev.external_id,
                    )
                )
            elif ev.start_at and ev.end_at:
                intervals.append(
                    AvailabilityInterval(
                        start_at=ev.start_at,
                        end_at=ev.end_at,
                        status=status,
                        all_day=False,
                        source_kind=ev.source_kind,
                        external_id=ev.external_id,
                    )
                )

        intervals.sort(key=lambda iv: (iv.start_at, iv.end_at))
        return intervals

    def _store_paging_state(
        self,
        *,
        page_token: str,
        sync_token: str | None,
        time_min: str | None,
        time_max: str | None,
        fingerprint: str,
        resync_reset: bool,
    ) -> str:
        """Store opaque multi-page state under a compact 24-char handle (safe for 512-char Key bounds)."""
        state = {
            "p": page_token,
            "s": sync_token,
            "tmin": time_min,
            "tmax": time_max,
            "fp": fingerprint,
            "reset": resync_reset,
        }
        key = hashlib.sha256(
            json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        if len(self._paging_states) >= 64:
            # Evict oldest handle
            oldest = next(iter(self._paging_states))
            self._paging_states.pop(oldest, None)
        handle = f"calpage:{key}"
        self._paging_states[handle] = state
        return handle

    def _resolve_paging_state(self, cursor: str) -> dict[str, Any] | None:
        if cursor in self._paging_states:
            return self._paging_states[cursor]
        if cursor.startswith("page:"):
            raw = cursor[len("page:") :]
            padded = raw + "=" * (-len(raw) % 4)
            try:
                decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
                if isinstance(decoded, dict) and "p" in decoded:
                    return decoded
            except Exception:
                pass
            return {"p": raw, "s": None, "tmin": None, "tmax": None, "fp": None, "reset": False}
        return None

    def list_changes(
        self,
        scope: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> ChangePage:
        """Synchronize Google Calendar events incrementally or by explicit date window.

        Preserves Google Calendar API v3's ``singleEvents=True`` invariant across both initial
        and incremental ``syncToken`` runs (omitting incompatible ``timeMin``, ``timeMax``, and
        ``orderBy`` parameters when ``syncToken`` is supplied). Multi-page cursors are encoded
        as compact ``calpage:<16-hex>`` handles so opaque tokens remain bounded and out of logs.
        When Google returns HTTP 410 Gone (expired ``syncToken``), performs a full replacement
        run and sets ``self.last_resync_reset = True`` + ``self.last_snapshot_info`` so the
        bridge attaches a ``CalendarSnapshot`` marker for stale-event retirement upon clean
        completion.

        Args:
            scope: Optional scope dictionary supporting ``calendar_id``, ``time_min``/``time_max``,
                ``availability_only``, ``include_descriptions``, ``max_results``, or offline ``ics_text``.
            cursor: Optional opaque ``calpage:<16-hex>`` handle or Google ``syncToken``.

        Returns:
            ``ChangePage`` containing normalized calendar event records, continuation cursor or
            terminal ``sync_token``, and warnings.
        """
        self.last_resync_reset = False
        self.last_snapshot_info: tuple[str, str | None, str | None] | None = None
        opts = scope or {}
        if "ics_text" in opts:
            events = self.import_ics_text(opts["ics_text"])
            records = tuple(self.normalize(ev.to_dict()) for ev in events)
            return ChangePage(items=records, next_cursor=None, complete=True)

        cal_id = str(opts.get("calendar_id") or self._calendar_id)
        include_descriptions = bool(opts.get("include_descriptions", False))
        availability_only = bool(opts.get("availability_only", False))
        max_results = max(1, min(250, int(opts.get("max_results", 100))))

        service = self._build_service()
        warnings: list[str] = []

        sync_token: str | None = None
        page_token: str | None = None
        frozen_tmin: str | None = None
        frozen_tmax: str | None = None
        resync_reset = False

        if cursor:
            if cursor.startswith("sync:"):
                sync_token = cursor[len("sync:") :]
            elif cursor.startswith("calpage:") or cursor.startswith("page:"):
                decoded = self._resolve_paging_state(cursor)
                if decoded is None:
                    raise ValueError("Expired or unknown Google Calendar paging handle.")
                page_token = decoded.get("p")
                sync_token = decoded.get("s")
                frozen_tmin = decoded.get("tmin")
                frozen_tmax = decoded.get("tmax")
                resync_reset = bool(decoded.get("reset", False))
                expected_fp = decoded.get("fp")
                mode = "incremental" if sync_token else "initial"
                current_fp = hashlib.sha256(f"{cal_id}|{mode}".encode("utf-8")).hexdigest()[:12]
                if expected_fp and expected_fp != current_fp:
                    raise ValueError(
                        "Calendar paging cursor does not match the current calendar/query context."
                    )
            else:
                sync_token = cursor

        now = datetime.now(timezone.utc)
        effective_tmin: str | None = None
        effective_tmax: str | None = None
        if not sync_token:
            effective_tmin = frozen_tmin or opts.get("time_min") or (now - timedelta(days=7)).isoformat().replace(
                "+00:00", "Z"
            )
            effective_tmax = frozen_tmax or opts.get("time_max") or (now + timedelta(days=60)).isoformat().replace(
                "+00:00", "Z"
            )

        def _query_events(
            use_sync_token: str | None,
            use_page_token: str | None,
            use_tmin: str | None,
            use_tmax: str | None,
        ) -> dict[str, Any]:
            params: dict[str, Any] = {
                "calendarId": cal_id,
                "maxResults": max_results,
                "showDeleted": True,
                # Keep singleEvents invariant across both initial and incremental syncToken queries
                "singleEvents": True,
            }
            if use_sync_token:
                # Google Calendar API forbids timeMin/timeMax/orderBy alongside syncToken (singleEvents IS compatible)
                params["syncToken"] = use_sync_token
            else:
                params["orderBy"] = "startTime"
                params["timeMin"] = use_tmin
                params["timeMax"] = use_tmax
            if use_page_token:
                params["pageToken"] = use_page_token

            def _op():
                self._rate_limiter.wait()
                try:
                    return service.events().list(**params).execute()
                except Exception as exc:
                    raise _classify_calendar_http_error(exc) from exc

            return retry_with_backoff(_op, max_retries=self._config.max_retries)

        active_sync_token = sync_token
        try:
            resp = _query_events(active_sync_token, page_token, effective_tmin, effective_tmax)
        except SyncTokenExpiredError:
            warnings.append("Google Calendar syncToken expired (HTTP 410); performed full range resync.")
            active_sync_token = None
            resync_reset = True
            effective_tmin = opts.get("time_min") or (now - timedelta(days=7)).isoformat().replace("+00:00", "Z")
            effective_tmax = opts.get("time_max") or (now + timedelta(days=60)).isoformat().replace("+00:00", "Z")
            resp = _query_events(None, None, effective_tmin, effective_tmax)

        self.last_resync_reset = resync_reset
        if active_sync_token is None:
            self.last_snapshot_info = (
                f"google_calendar:{cal_id}:",
                effective_tmin,
                effective_tmax,
            )
        else:
            self.last_snapshot_info = None
        cal_tz = str(resp.get("timeZone") or "Europe/Paris")
        parsed_events: list[CalendarEventRecord] = []
        for raw in resp.get("items") or []:
            ev = self._parse_google_event(
                raw,
                calendar_tz=cal_tz,
                calendar_id=cal_id,
                include_descriptions=include_descriptions,
            )
            if availability_only:
                ev = CalendarEventRecord(
                    source_kind=ev.source_kind,
                    external_id=ev.external_id,
                    title="Busy" if ev.busy else "Free",
                    event_type=ev.event_type,
                    start_at=ev.start_at,
                    end_at=ev.end_at,
                    all_day_date=ev.all_day_date,
                    all_day_end_date=ev.all_day_end_date,
                    original_timezone=ev.original_timezone,
                    status=ev.status,
                    busy=ev.busy,
                    recurrence_key=ev.recurrence_key,
                    provenance=ev.provenance,
                )
            parsed_events.append(ev)

        records = tuple(self.normalize(ev.to_dict()) for ev in parsed_events)
        next_page = resp.get("nextPageToken")
        next_sync = resp.get("nextSyncToken")

        if next_page:
            mode = "incremental" if active_sync_token else "initial"
            fp = hashlib.sha256(f"{cal_id}|{mode}".encode("utf-8")).hexdigest()[:12]
            paging_handle = self._store_paging_state(
                page_token=next_page,
                sync_token=active_sync_token,
                time_min=effective_tmin,
                time_max=effective_tmax,
                fingerprint=fp,
                resync_reset=resync_reset,
            )
            return ChangePage(
                items=records,
                next_cursor=paging_handle,
                complete=False,
                sync_token=None,
                warnings=tuple(warnings),
            )

        durable_sync = f"sync:{next_sync}" if next_sync else None
        return ChangePage(
            items=records,
            next_cursor=durable_sync,
            complete=True,
            sync_token=durable_sync,
            warnings=tuple(warnings),
        )

    def fetch_blob(self, item: SourceRecord) -> FetchResult:
        raise NotImplementedError("GoogleCalendarConnector does not store downloadable binary blobs.")

    def disconnect(self) -> AuthStatus:
        return self._oauth.disconnect(revoke_remote=True)

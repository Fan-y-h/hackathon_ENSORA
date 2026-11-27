#!/usr/bin/env python3
"""Redacted developer diagnostic & bounded validation CLI for Ensora+ P1 sources.

By default, prints ONLY redacted status summaries and record counters to stdout —
never dumps private student grades, personal calendar details, parsed text, or secrets.
Use `--export-json <FILE>` explicitly when a private local export (0o600) is desired.
Normal local and academic imports route through Codex's unified Runtime & SQLite ETL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.modules.ingestion import (  # noqa: E402
    EnsaiAuthAdapter,
    EnsoraDriveConnector,
    GoogleCalendarConnector,
    GoogleCloudSetupHelper,
    GoogleOAuthAdapter,
    MoodleConnector,
    OnboardingOrchestrator,
    PamplemousseConnector,
    SecureCredentialManager,
    SourceKind,
    SourcesRuntimeConfig,
    StructuredAcademicImporter,
)
from backend.app.modules.ingestion.codex_bridge import (  # noqa: E402
    _to_codex_records,
    runtime_config_from_codex_settings,
)


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _write_private_export(path: Path, payload: object) -> None:
    """Atomically write a private 0o600 export, refusing symlinks at target or parent."""
    target = path.expanduser()
    if target.is_symlink():
        raise PermissionError("Refusing to write private export through a symbolic link.")
    parent = target.parent
    if parent.is_symlink():
        raise PermissionError("Refusing to write private export into a symbolic link directory.")
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(parent))
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        if target.is_symlink():
            raise PermissionError("Refusing to replace a symbolic link with a private export.")
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _summarize_records(records: tuple[Any, ...] | list[Any]) -> dict[str, Any]:
    """Produce privacy-preserving aggregate counters without exposing titles/grades/descriptions."""
    by_kind: dict[str, int] = {}
    for rec in records:
        kind = getattr(rec, "record_kind", None) or getattr(rec, "record_type", "record")
        by_kind[str(kind)] = by_kind.get(str(kind), 0) + 1
    return {
        "total_items": len(records),
        "by_kind": by_kind,
    }


def _classify_download_error(rec: Any, exc: Exception) -> dict[str, Any]:
    """Return a strictly redacted failure diagnostic record (never prints private text or tokens)."""
    from backend.app.modules.ingestion.auth.ensai import sanitize_url_for_logs
    from backend.app.modules.ingestion.connectors.base import (
        AuthenticationRequiredError,
        PermissionDeniedError,
        TransientSourceError,
    )
    from backend.app.modules.ingestion.connectors.moodle import DownloadSizeLimitError
    from backend.app.modules.ingestion.credentials import redact_sensitive_text

    payload = getattr(rec, "payload", None) or {}
    detail: dict[str, Any] = {
        "external_id": str(getattr(rec, "external_id", "")),
        "resource_kind": str(payload.get("resource_kind") or getattr(rec, "record_kind", "document")),
        "error_type": type(exc).__name__,
    }
    raw_url = getattr(rec, "download_url", None) or getattr(rec, "source_url", None)
    if raw_url:
        detail["url_path"] = sanitize_url_for_logs(str(raw_url))
    if isinstance(exc, DownloadSizeLimitError):
        detail["reason"] = "size_limit_exceeded"
        detail["size_bytes"] = exc.size_bytes
        detail["limit_bytes"] = exc.limit_bytes
    elif isinstance(exc, AuthenticationRequiredError):
        detail["reason"] = "cas_login_required"
    elif isinstance(exc, PermissionDeniedError):
        detail["reason"] = "permission_denied"
    elif isinstance(exc, TransientSourceError):
        detail["reason"] = f"transient_http_{exc.status_code or 'error'}"
    else:
        msg = redact_sensitive_text(str(exc)).strip()
        if "exceeds configured size limit" in msg.lower() or "exceeded limit" in msg.lower():
            detail["reason"] = "size_limit_exceeded"
        elif "no downloadable url" in msg.lower():
            detail["reason"] = "no_download_url"
        else:
            detail["reason"] = msg[:160] or type(exc).__name__
    return detail


def _load_retry_external_ids(retry_file: Path | None, explicit_ids: list[str] | None) -> set[str]:
    """Collect explicit external IDs to selectively retry from CLI flags and/or a prior export JSON."""
    ids: set[str] = {str(x).strip() for x in (explicit_ids or []) if str(x).strip()}
    if retry_file is not None and retry_file.is_file():
        try:
            data = json.loads(retry_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            dv = data.get("download_verification") if isinstance(data.get("download_verification"), dict) else data
            for item in dv.get("failure_details") or []:
                if isinstance(item, dict) and item.get("external_id"):
                    ids.add(str(item["external_id"]))
            for ext_id in dv.get("unparsed_external_ids") or []:
                if ext_id:
                    ids.add(str(ext_id))
    return ids


def _confirm_live_operation(action: str, *, yes: bool) -> bool:
    if yes:
        return True
    reply = input(f"Confirm live authorized operation [{action}]? [y/N]: ").strip().lower()
    return reply in {"y", "yes"}


def _load_shared_config(config_dir: Path | None) -> tuple[Any, SourcesRuntimeConfig]:
    """Load Codex's canonical Settings and derive SourcesRuntimeConfig without drift."""
    from backend.app.core.settings import Settings

    env_path = (config_dir / "sources.env") if config_dir else None
    settings = Settings.load(env_path)
    return settings, runtime_config_from_codex_settings(settings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="p1_sources_cli",
        description="Ensora+ P1 redacted diagnostic, setup, and bounded source validation CLI.",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Override private config directory (default: ~/.config/ensora-plus).",
    )
    parser.add_argument(
        "--export-json",
        type=Path,
        default=None,
        help="Optional explicit private file (0o600) to write detailed local output.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_onboard = sub.add_parser("onboard", help="Run unified consent-aware setup and onboarding check.")
    p_onboard.add_argument(
        "--non-interactive",
        action="store_true",
        help="Strictly local-only: generate missing defaults and inspect local configuration with zero network/browser probes.",
    )
    p_onboard.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Optional override for runtime data dir.",
    )

    sub.add_parser("status", help="Inspect setup state, Playwright, Google Cloud, and auth status (redacted).")

    p_gcp = sub.add_parser("gcp-check", help="Inspect Google Cloud project ensora-516015 readiness.")
    p_gcp.add_argument("--enable-apis", action="store_true", help="Enable Drive & Calendar APIs after approval.")
    p_gcp.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")

    p_ensai = sub.add_parser("auth-ensai", help="Run interactive ENSAI CAS browser authentication or configure OS-keyring auto-login.")
    p_ensai.add_argument("--source", choices=["moodle", "pamplemousse", "both"], default="both")
    p_ensai.add_argument(
        "--configure-auto-login",
        action="store_true",
        help="One-time masked setup: store ENSAI credentials in OS keyring (with consent), enable ENSAI_AUTO_LOGIN=true in sources.env, and verify persistent session.",
    )
    p_ensai.add_argument(
        "--store-credentials",
        action="store_true",
        help="Store ENSAI credentials in the OS keyring via masked input (requires explicit consent).",
    )
    p_ensai.add_argument(
        "--replace-credentials",
        action="store_true",
        help="Securely replace stored ENSAI credentials in the OS keyring via masked input.",
    )
    p_ensai.add_argument(
        "--delete-credentials",
        action="store_true",
        help="Securely delete stored ENSAI credentials from the OS keyring.",
    )
    p_ensai.add_argument(
        "--enable-auto-login",
        action="store_true",
        help="Set ENSAI_AUTO_LOGIN=true in private sources.env (preserves all other settings; never stores passwords).",
    )
    p_ensai.add_argument(
        "--disable-auto-login",
        action="store_true",
        help="Set ENSAI_AUTO_LOGIN=false in private sources.env.",
    )
    p_ensai.add_argument(
        "--check-only",
        action="store_true",
        help="Non-interactive check verifying persistent session reuse (and headless auto-reauth if ENSAI_AUTO_LOGIN=true).",
    )
    p_ensai.add_argument(
        "--debug-session",
        action="store_true",
        help="Human-operated debugging mode: keeps Chromium open until confirmed, persists session, reopens profile headlessly, and reports all 5 verification stages.",
    )
    p_ensai.add_argument(
        "--keep-open",
        action="store_true",
        help="Keep Chromium open after protected resource verification until Enter is pressed.",
    )
    p_ensai.add_argument("--auto-submit", action="store_true")
    p_ensai.add_argument("--disconnect", action="store_true")
    p_ensai.add_argument("--yes", action="store_true", help="Non-interactive consent flag for scripted tests only.")

    p_google = sub.add_parser("auth-google", help="Run Google OAuth 2.0 read-only consent flow.")
    p_google.add_argument("--source", choices=["ensora_drive", "google_calendar"], default="ensora_drive")
    p_google.add_argument("--reauthorize", action="store_true")
    p_google.add_argument("--disconnect", action="store_true")

    p_val = sub.add_parser("validate", help="Validate consent-gated connections for explicitly selected sources.")
    p_val.add_argument(
        "--source",
        action="append",
        choices=["moodle", "pamplemousse", "ensora_drive", "google_calendar"],
        default=None,
        help="Source(s) to validate (must have durable read consent in runtime DB).",
    )
    p_val.add_argument("--data-dir", type=Path, default=None, help="Optional override for runtime data dir.")

    p_moodle = sub.add_parser("sync-moodle", help="Run authorized Moodle discovery/single-course validation.")
    p_moodle.add_argument("--course-id", action="append", default=None)
    p_moodle.add_argument("--single-course", action="store_true", default=True)
    p_moodle.add_argument("--all-courses", action="store_true")
    p_moodle.add_argument("--academic-year", type=str, default=None, help="Optional academic year filter (e.g. 2026-2027).")
    p_moodle.add_argument("--include-archived", action="store_true", help="Include archived/past courses.")
    p_moodle.add_argument("--verify-downloads", type=int, default=0, help="Download and parse up to N discovered resources end-to-end (redacted stats only).")
    p_moodle.add_argument("--verify-all-downloads", action="store_true", help="Download and parse ALL discovered downloadable resources end-to-end.")
    p_moodle.add_argument("--only-external-id", action="append", default=None, help="Selectively verify/retry only specific resource external_id(s).")
    p_moodle.add_argument("--retry-failed", type=Path, default=None, help="Retry only failed/unparsed external_ids from a prior --export-json report.")
    p_moodle.add_argument("--max-download-mb", type=int, default=None, help="Optional per-file download size limit in MiB for diagnostics.")
    p_moodle.add_argument("--non-interactive", action="store_true", help="Never open a visible browser window on session expiry.")
    p_moodle.add_argument("--yes", action="store_true")

    p_pamp = sub.add_parser("sync-pamplemousse", help="Validate Pamplemousse timetable/inscriptions/notes extraction.")
    p_pamp.add_argument("--max-items", type=int, default=200)
    p_pamp.add_argument("--all", action="store_true", help="Remove max-items cap and collect all pages.")
    p_pamp.add_argument("--academic-year", type=str, default=None, help="Optional academic year label (e.g. 2026-2027).")
    p_pamp.add_argument("--days-back", type=int, default=None, help="Optional timetable history window in days.")
    p_pamp.add_argument("--days-ahead", type=int, default=None, help="Optional timetable future window in days.")
    p_pamp.add_argument("--ics", type=Path, default=None)
    p_pamp.add_argument("--csv", type=Path, default=None)
    p_pamp.add_argument("--json", type=Path, default=None)
    p_pamp.add_argument("--non-interactive", action="store_true", help="Never open a visible browser window on session expiry.")
    p_pamp.add_argument("--yes", action="store_true")

    p_drive = sub.add_parser("discover-drive", help="Discover and optionally verify downloads/parsing inside Ensora shared Drive root.")
    p_drive.add_argument("--cohort", action="append", default=None)
    p_drive.add_argument("--max-files", type=int, default=25)
    p_drive.add_argument("--all", action="store_true", help="Exhaustive recursive discovery across all pages within the approved Ensora root.")
    p_drive.add_argument("--verify-downloads", type=int, default=0, help="Download and parse up to N discovered files/Workspace exports (redacted stats only).")
    p_drive.add_argument("--verify-all-downloads", action="store_true", help="Download and parse ALL discovered Drive files/Workspace exports.")
    p_drive.add_argument("--only-external-id", action="append", default=None, help="Selectively verify/retry only specific Drive external_id(s).")
    p_drive.add_argument("--retry-failed", type=Path, default=None, help="Retry only failed/unparsed Drive external_ids from a prior --export-json report.")
    p_drive.add_argument("--max-download-mb", type=int, default=None, help="Optional per-file download size limit in MiB for diagnostics.")
    p_drive.add_argument("--yes", action="store_true")

    p_cal = sub.add_parser("sync-calendar", help="Sync read-only Google Calendar availability.")
    p_cal.add_argument("--days", type=int, default=14)
    p_cal.add_argument("--days-back", type=int, default=0, help="Optional lookback window in days (default 0).")
    p_cal.add_argument("--availability-only", action="store_true", default=True)
    p_cal.add_argument("--ics", type=Path, default=None)
    p_cal.add_argument("--yes", action="store_true")

    # Unified Runtime ETL imports (persists directly to Codex SQLite database)
    p_local = sub.add_parser("import-local", help="Import local file/folder through the unified ETL runtime.")
    p_local.add_argument("--path", type=Path, required=True)
    p_local.add_argument("--data-dir", type=Path, default=None, help="Optional override for local runtime data dir.")

    p_acad = sub.add_parser("import-academic", help="Import structured academic YAML/JSON/CSV through unified ETL.")
    p_acad.add_argument("--path", type=Path, required=True)
    p_acad.add_argument("--dry-run", action="store_true", help="Validate without committing to SQLite.")
    p_acad.add_argument("--data-dir", type=Path, default=None, help="Optional override for local runtime data dir.")

    args = parser.parse_args(argv)
    settings, config = _load_shared_config(args.config_dir)

    if args.command == "status":
        # Strictly local-only by default (no browser launches or network calls — GEMINI-REV-017)
        report = OnboardingOrchestrator(config).inspect_only(allow_network=False)
        _print_json(report.to_dict())
        return 0

    if args.command == "onboard":
        if args.non_interactive:
            # Strictly local-only offline inspection (zero network/browser calls — GEMINI-REV-017)
            config.ensure_missing_defaults_in_env_file()
            report = OnboardingOrchestrator(config).inspect_only(allow_network=False)
            _print_json(report.to_dict())
            return 0
        # Interactive onboarding routes through Codex's unified consent-aware workflow (GEMINI-REV-017)
        from backend.app.core.runtime import Runtime
        import scripts.onboarding as unified_onboarding

        run_settings = settings.model_copy(
            update={"local_data_dir": args.data_dir.expanduser().absolute()}
        ) if args.data_dir else settings
        with Runtime(run_settings) as runtime:
            unified_onboarding.run_onboarding(runtime)
        return 0

    if args.command == "gcp-check":
        helper = GoogleCloudSetupHelper(config)
        report = helper.inspect()
        _print_json(report.to_dict())
        if args.enable_apis and report.missing_apis:
            if _confirm_live_operation(
                f"gcloud services enable {' '.join(report.missing_apis)} --project={report.project_id}",
                yes=args.yes,
            ):
                # Pass ONLY the exact approved missing subset and propagate nonzero exit code (GEMINI-REV-019)
                res = helper.enable_required_apis(approved=True, apis=report.missing_apis)
                _print_json({"returncode": res.returncode, "enabled_requested": list(report.missing_apis)})
                return res.returncode
        return 0

    if args.command == "auth-ensai":
        from backend.app.modules.ingestion.credentials import KeyringCredentialStore

        cred_mgr = SecureCredentialManager(
            KeyringCredentialStore(service_prefix=settings.ensai_keyring_service)
        )
        adapter = EnsaiAuthAdapter(config, cred_mgr)

        if args.disconnect:
            if args.delete_credentials:
                cred_mgr.clear_ensai_credentials()
            if args.disable_auto_login:
                config.set_non_sensitive_env_flag("ENSAI_AUTO_LOGIN", "false")
            _print_json(
                {
                    "disconnect": adapter.disconnect(clear_keyring=args.delete_credentials).to_dict(),
                    "credentials_stored": cred_mgr.has_ensai_credentials(),
                    "auto_login_enabled": False if args.disable_auto_login else config.ensai_allow_automated_credential_submit,
                }
            )
            return 0

        if args.delete_credentials:
            cred_mgr.clear_ensai_credentials()
            if args.disable_auto_login:
                config.set_non_sensitive_env_flag("ENSAI_AUTO_LOGIN", "false")
                config.ensai_allow_automated_credential_submit = False
            _print_json(
                {
                    "status": "CREDENTIALS_DELETED",
                    "credentials_stored": cred_mgr.has_ensai_credentials(),
                    "auto_login_enabled": config.ensai_allow_automated_credential_submit,
                    "profile_preserved": config.ensai_profile_dir.exists(),
                }
            )
            return 0

        if args.disable_auto_login:
            config.set_non_sensitive_env_flag("ENSAI_AUTO_LOGIN", "false")
            config.ensai_allow_automated_credential_submit = False
            if not (args.configure_auto_login or args.store_credentials or args.replace_credentials or args.check_only):
                _print_json(
                    {
                        "status": "AUTO_LOGIN_DISABLED",
                        "auto_login_enabled": False,
                        "credentials_stored": cred_mgr.has_ensai_credentials(),
                    }
                )
                return 0

        if args.configure_auto_login or args.store_credentials or args.replace_credentials:
            try:
                cred_mgr.prompt_missing_ensai_credentials(
                    username_hint=config.ensai_username_hint,
                    persist=True,
                    force_replace=bool(args.replace_credentials),
                    require_consent=not args.yes,
                )
            except (PermissionError, RuntimeError, ValueError) as exc:
                _print_json(
                    {
                        "status": "CREDENTIAL_SETUP_SKIPPED",
                        "detail": str(exc),
                        "keyring_available": cred_mgr.is_keyring_available(),
                        "credentials_stored": cred_mgr.has_ensai_credentials(),
                        "auto_login_enabled": config.ensai_allow_automated_credential_submit,
                    }
                )
                return 2

        if args.configure_auto_login or args.enable_auto_login:
            config.set_non_sensitive_env_flag("ENSAI_AUTO_LOGIN", "true")
            config.ensai_allow_automated_credential_submit = True

        if args.check_only:
            results: dict[str, Any] = {
                "keyring_available": cred_mgr.is_keyring_available(),
                "credentials_stored": cred_mgr.has_ensai_credentials(),
                "auto_login_enabled": config.ensai_allow_automated_credential_submit,
            }
            if args.source in ("moodle", "both"):
                results["moodle"] = adapter.check_source_connection(
                    SourceKind.MOODLE,
                    headless=True,
                    allow_auto_reauth=True,
                ).to_dict()
            if args.source in ("pamplemousse", "both"):
                results["pamplemousse"] = adapter.check_source_connection(
                    SourceKind.PAMPLEMOUSSE,
                    headless=True,
                    allow_auto_reauth=True,
                ).to_dict()
            results["shared_session_supported"] = adapter.shared_session_supported
            _print_json(results)
            ok = all(
                results[s]["authenticated"]
                for s in ("moodle", "pamplemousse")
                if s in results
            )
            return 0 if ok else 2

        if args.debug_session:
            allow_auto_flag: bool | None = (
                True if (args.auto_submit or args.configure_auto_login or args.enable_auto_login) else None
            )
            debug_report: dict[str, Any] = {
                "mode": "debug_session",
                "keyring_available": cred_mgr.is_keyring_available(),
                "credentials_stored": cred_mgr.has_ensai_credentials(),
                "auto_login_enabled": config.ensai_allow_automated_credential_submit,
            }
            targets = (
                [SourceKind.MOODLE, SourceKind.PAMPLEMOUSSE]
                if args.source == "both"
                else [SourceKind(args.source)]
            )
            for src in targets:
                debug_report[src.value] = adapter.verify_session_lifecycle(
                    src,
                    interactive=True,
                    keep_open_until_confirmed=True,
                    allow_automated_submit=allow_auto_flag,
                )
            _print_json(debug_report)
            ok = all(debug_report[src.value]["connected"] for src in targets)
            return 0 if ok else 2

        allow_auto_flag = (
            True if (args.auto_submit or args.configure_auto_login or args.enable_auto_login) else None
        )
        results = {
            "keyring_available": cred_mgr.is_keyring_available(),
            "credentials_stored": cred_mgr.has_ensai_credentials(),
            "auto_login_enabled": config.ensai_allow_automated_credential_submit,
        }
        if args.source in ("moodle", "both"):
            results["moodle"] = adapter.authenticate(
                SourceKind.MOODLE,
                interactive=True,
                allow_automated_submit=allow_auto_flag,
                keep_open_until_confirmed=bool(args.keep_open),
            ).to_dict()
        if args.source in ("pamplemousse", "both"):
            # Reuse the shared institutional profile first so CAS SSO TGT cookies carry over
            pamp_check = adapter.check_source_connection(
                SourceKind.PAMPLEMOUSSE,
                headless=True,
                allow_auto_reauth=True,
            )
            if not pamp_check.authenticated:
                results["pamplemousse"] = adapter.authenticate(
                    SourceKind.PAMPLEMOUSSE,
                    interactive=True,
                    allow_automated_submit=allow_auto_flag,
                    keep_open_until_confirmed=bool(args.keep_open),
                ).to_dict()
            else:
                results["pamplemousse"] = pamp_check.to_dict()
            results["shared_session_supported"] = adapter.shared_session_supported
        _print_json(results)
        return 0

    if args.command == "auth-google":
        src = SourceKind(args.source)
        oauth = GoogleOAuthAdapter(config, source=src)
        if args.disconnect:
            _print_json(oauth.disconnect(revoke_remote=True).to_dict())
            return 0
        status = oauth.reauthorize() if args.reauthorize else oauth.authenticate(interactive=True)
        _print_json(status.to_dict())
        return 0

    if args.command == "validate":
        from backend.app.contracts.sources import SourceKind as CodexSourceKind
        from backend.app.core.runtime import Runtime

        selected = [CodexSourceKind(s) for s in (args.source or [])]
        if not selected:
            parser.error("Specify at least one --source to validate (consent-gated).")
        run_settings = settings.model_copy(
            update={"local_data_dir": args.data_dir.expanduser().absolute()}
        ) if args.data_dir else settings
        results = {}
        with Runtime(run_settings) as runtime:
            for src in selected:
                res = runtime.auth.validate(src)
                results[src.value] = res.model_dump(mode="json")
        _print_json(results)
        return 0

    if args.command == "sync-moodle":
        from backend.app.modules.ingestion.connectors.base import AuthenticationRequiredError
        from backend.app.modules.ingestion.parsers.registry import DocumentParserRegistry

        if not _confirm_live_operation("Moodle ENSAI read-only sync", yes=args.yes):
            print("Aborted.")
            return 1
        interactive_sync = sys.stdin.isatty() and not args.non_interactive
        conn = MoodleConnector(config)
        all_items = []
        all_warnings: list[str] = []
        all_errors: list[str] = []
        cursor: str | None = None
        complete = True
        try:
            while True:
                page = conn.list_changes(
                    scope={
                        "course_ids": args.course_id,
                        "single_course_first": (not args.all_courses) and (not args.course_id),
                        "academic_year": args.academic_year,
                        "include_archived": args.include_archived,
                        "interactive": interactive_sync,
                    },
                    cursor=cursor,
                )
                all_items.extend(page.items)
                all_warnings.extend(page.warnings)
                all_errors.extend(page.errors)
                complete = page.complete
                if page.complete or not page.next_cursor:
                    break
                cursor = page.next_cursor
        except AuthenticationRequiredError as exc:
            _print_json(
                {
                    "source": "moodle",
                    "status": "REAUTH_REQUIRED",
                    "detail": str(exc),
                    "next_action": "Run interactive authentication: .venv/bin/python scripts/onboarding.py or .venv/bin/python scripts/p1_sources_cli.py auth-ensai --source moodle",
                }
            )
            return 2

        if args.max_download_mb is not None and args.max_download_mb > 0:
            config.max_download_bytes = int(args.max_download_mb) * 1024 * 1024
        retry_ids = _load_retry_external_ids(args.retry_failed, args.only_external_id)
        verify_cap: int | None = (
            None
            if (args.verify_all_downloads or retry_ids)
            else (args.verify_downloads if (args.verify_downloads and args.verify_downloads > 0) else 0)
        )

        download_verification: dict[str, Any] | None = None
        if verify_cap is None or verify_cap > 0:
            parser_reg = DocumentParserRegistry()
            attempted = 0
            downloaded_ok = 0
            parsed_ok = 0
            parsed_empty = 0
            unsupported_count = 0
            total_chunks = 0
            download_failures: list[str] = []
            failure_details: list[dict[str, Any]] = []
            unparsed_external_ids: list[str] = []
            by_mime: dict[str, int] = {}
            parsed_by_mime: dict[str, int] = {}
            unparsed_by_mime: dict[str, int] = {}
            for rec in all_items:
                if verify_cap is not None and attempted >= verify_cap:
                    break
                if retry_ids and rec.external_id not in retry_ids:
                    continue
                if rec.record_kind != "document" or (not rec.download_url and not (rec.payload or {}).get("inline_html")):
                    continue
                attempted += 1
                try:
                    blob = conn.fetch_blob(rec, interactive=interactive_sync)
                    downloaded_ok += 1
                    by_mime[blob.mime_type] = by_mime.get(blob.mime_type, 0) + 1
                    parsed = parser_reg.parse_bytes(
                        blob.content,
                        filename=blob.filename,
                        mime_type=blob.mime_type,
                    )
                    if parsed and parsed.blocks:
                        parsed_ok += 1
                        total_chunks += len(parsed.blocks)
                        parsed_by_mime[blob.mime_type] = parsed_by_mime.get(blob.mime_type, 0) + 1
                    else:
                        unparsed_external_ids.append(rec.external_id)
                        unparsed_by_mime[blob.mime_type] = unparsed_by_mime.get(blob.mime_type, 0) + 1
                        if parsed and parsed.parser_name == "unsupported":
                            unsupported_count += 1
                        else:
                            parsed_empty += 1
                except Exception as exc:
                    download_failures.append(type(exc).__name__)
                    failure_details.append(_classify_download_error(rec, exc))
            download_verification = {
                "attempted": attempted,
                "downloaded_ok": downloaded_ok,
                "parsed_ok": parsed_ok,
                "parsed_empty": parsed_empty,
                "unsupported_count": unsupported_count,
                "failed_count": len(download_failures),
                "parsed_blocks_total": total_chunks,
                "by_mime_type": by_mime,
                "parsed_by_mime": parsed_by_mime,
                "unparsed_by_mime": unparsed_by_mime,
                "failure_types": download_failures,
                "failure_details": failure_details,
                "unparsed_external_ids": unparsed_external_ids,
                "completeness": (
                    "complete"
                    if attempted > 0 and len(download_failures) == 0 and unsupported_count == 0 and parsed_empty == 0
                    else "partial"
                ),
            }

        summary = {
            "source": "moodle",
            **_summarize_records(all_items),
            "moodle_stats": dict(conn.last_stats),
            "complete": complete,
            "warning_count": len(all_warnings),
            "warnings": all_warnings,
            "error_count": len(all_errors),
            "errors": all_errors,
        }
        if download_verification is not None:
            summary["download_verification"] = download_verification
        if args.export_json:
            _write_private_export(
                args.export_json,
                {
                    "items": [r.to_dict() for r in all_items],
                    "stats": conn.last_stats,
                    "warnings": all_warnings,
                    "download_verification": download_verification,
                },
            )
            summary["exported_to"] = str(args.export_json)
        _print_json(summary)
        return 0

    if args.command == "sync-pamplemousse":
        from backend.app.modules.ingestion.connectors.base import AuthenticationRequiredError

        conn = PamplemousseConnector(config)
        interactive_sync = sys.stdin.isatty() and not args.non_interactive
        scope: dict[str, Any] = {
            "max_items": None if args.all else args.max_items,
            "academic_year": args.academic_year,
            "days_back": args.days_back,
            "days_ahead": args.days_ahead,
            "interactive": interactive_sync,
        }
        if args.ics:
            scope["ics_text"] = args.ics.read_text(encoding="utf-8", errors="replace")
        elif args.csv:
            scope["csv_text"] = args.csv.read_text(encoding="utf-8", errors="replace")
        elif args.json:
            scope["json_data"] = args.json.read_text(encoding="utf-8", errors="replace")
        else:
            if not _confirm_live_operation("Pamplemousse ENSAI read-only sync", yes=args.yes):
                print("Aborted.")
                return 1
        all_items = []
        all_warnings = []
        all_errors = []
        cursor = None
        complete = True
        try:
            while True:
                page = conn.list_changes(scope=scope, cursor=cursor)
                all_items.extend(page.items)
                all_warnings.extend(page.warnings)
                all_errors.extend(page.errors)
                complete = page.complete
                if page.complete or not page.next_cursor:
                    break
                cursor = page.next_cursor
        except AuthenticationRequiredError as exc:
            _print_json(
                {
                    "source": "pamplemousse",
                    "status": "REAUTH_REQUIRED",
                    "detail": str(exc),
                    "next_action": "Run interactive authentication: .venv/bin/python scripts/onboarding.py or .venv/bin/python scripts/p1_sources_cli.py auth-ensai --source pamplemousse",
                }
            )
            return 2
        summary = {
            "source": "pamplemousse",
            **_summarize_records(all_items),
            "pamplemousse_stats": dict(conn.last_stats),
            "complete": complete,
            "warning_count": len(all_warnings),
            "warnings": all_warnings,
            "error_count": len(all_errors),
            "errors": all_errors,
        }
        if args.export_json:
            _write_private_export(
                args.export_json,
                {"items": [r.to_dict() for r in all_items], "stats": conn.last_stats, "warnings": all_warnings},
            )
            summary["exported_to"] = str(args.export_json)
        _print_json(summary)
        return 0

    if args.command == "discover-drive":
        from backend.app.modules.ingestion.parsers.registry import DocumentParserRegistry

        if not _confirm_live_operation(
            f"Ensora Google Drive read-only discovery (root={config.ensora_drive_root_folder_id})",
            yes=args.yes,
        ):
            print("Aborted.")
            return 1
        conn = EnsoraDriveConnector(config)
        all_items = []
        all_warnings = []
        all_errors = []
        cursor = None
        complete = True
        max_files_opt = None if args.all else args.max_files
        while True:
            page = conn.list_changes(
                scope={"cohorts": args.cohort, "max_files": max_files_opt},
                cursor=cursor,
            )
            all_items.extend(page.items)
            all_warnings.extend(page.warnings)
            all_errors.extend(page.errors)
            complete = page.complete
            if page.complete or not page.next_cursor:
                break
            cursor = page.next_cursor

        by_mime: dict[str, int] = {}
        by_cohort: dict[str, int] = {}
        for rec in all_items:
            mt = rec.mime_type or "unknown"
            by_mime[mt] = by_mime.get(mt, 0) + 1
            ch = (rec.payload or {}).get("cohort_hint") or "Other"
            by_cohort[ch] = by_cohort.get(ch, 0) + 1

        if args.max_download_mb is not None and args.max_download_mb > 0:
            config.max_download_bytes = int(args.max_download_mb) * 1024 * 1024
        retry_ids = _load_retry_external_ids(args.retry_failed, args.only_external_id)
        verify_cap = (
            None
            if (args.verify_all_downloads or retry_ids)
            else (args.verify_downloads if (args.verify_downloads and args.verify_downloads > 0) else 0)
        )

        download_verification = None
        if verify_cap is None or verify_cap > 0:
            parser_reg = DocumentParserRegistry()
            attempted = 0
            downloaded_ok = 0
            parsed_ok = 0
            parsed_empty = 0
            unsupported_count = 0
            total_blocks = 0
            failure_types: list[str] = []
            failure_details = []
            unparsed_external_ids = []
            parsed_by_mime: dict[str, int] = {}
            unparsed_by_mime: dict[str, int] = {}
            for rec in all_items:
                if verify_cap is not None and attempted >= verify_cap:
                    break
                if retry_ids and rec.external_id not in retry_ids:
                    continue
                attempted += 1
                try:
                    blob = conn.fetch_blob(rec)
                    downloaded_ok += 1
                    parsed = parser_reg.parse_bytes(
                        blob.content,
                        filename=blob.filename,
                        mime_type=blob.mime_type,
                    )
                    if parsed and parsed.blocks:
                        parsed_ok += 1
                        total_blocks += len(parsed.blocks)
                        parsed_by_mime[blob.mime_type] = parsed_by_mime.get(blob.mime_type, 0) + 1
                    else:
                        unparsed_external_ids.append(rec.external_id)
                        unparsed_by_mime[blob.mime_type] = unparsed_by_mime.get(blob.mime_type, 0) + 1
                        if parsed and parsed.parser_name == "unsupported":
                            unsupported_count += 1
                        else:
                            parsed_empty += 1
                except Exception as exc:
                    failure_types.append(type(exc).__name__)
                    failure_details.append(_classify_download_error(rec, exc))
            download_verification = {
                "attempted": attempted,
                "downloaded_ok": downloaded_ok,
                "parsed_ok": parsed_ok,
                "parsed_empty": parsed_empty,
                "unsupported_count": unsupported_count,
                "failed_count": len(failure_types),
                "parsed_blocks_total": total_blocks,
                "parsed_by_mime": parsed_by_mime,
                "unparsed_by_mime": unparsed_by_mime,
                "failure_types": failure_types,
                "failure_details": failure_details,
                "unparsed_external_ids": unparsed_external_ids,
                "completeness": (
                    "complete"
                    if attempted > 0 and len(failure_types) == 0 and unsupported_count == 0 and parsed_empty == 0
                    else "partial"
                ),
            }

        summary = {
            "source": "ensora_drive",
            "root_folder_id": config.ensora_drive_root_folder_id,
            **_summarize_records(all_items),
            "drive_stats": dict(getattr(conn, "last_stats", {})),
            "by_mime_type": by_mime,
            "by_cohort": by_cohort,
            "complete": complete,
            "warning_count": len(all_warnings),
            "warnings": all_warnings,
            "error_count": len(all_errors),
            "errors": all_errors,
        }
        if download_verification is not None:
            summary["download_verification"] = download_verification
        if args.export_json:
            _write_private_export(
                args.export_json,
                {
                    "items": [r.to_dict() for r in all_items],
                    "drive_stats": dict(getattr(conn, "last_stats", {})),
                    "download_verification": download_verification,
                },
            )
            summary["exported_to"] = str(args.export_json)
        _print_json(summary)
        return 0

    if args.command == "sync-calendar":
        conn = GoogleCalendarConnector(config)
        if args.ics:
            events = conn.import_ics_text(args.ics.read_text(encoding="utf-8", errors="replace"))
            intervals = conn.extract_availability_intervals(events)
            summary = {
                "source": "google_calendar_ics",
                "events_count": len(events),
                "availability_intervals_count": len(intervals),
            }
            if args.export_json:
                _write_private_export(args.export_json, [iv.to_dict() for iv in intervals])
                summary["exported_to"] = str(args.export_json)
            _print_json(summary)
            return 0
        if not _confirm_live_operation("Google Calendar read-only sync", yes=args.yes):
            print("Aborted.")
            return 1
        now = datetime.now(timezone.utc)
        time_min = (now - timedelta(days=max(0, args.days_back))).isoformat().replace("+00:00", "Z")
        time_max = (now + timedelta(days=args.days)).isoformat().replace("+00:00", "Z")
        all_items = []
        all_warnings = []
        cursor = None
        complete = True
        while True:
            page = conn.list_changes(
                scope={
                    "time_min": time_min,
                    "time_max": time_max,
                    "availability_only": args.availability_only,
                },
                cursor=cursor,
            )
            all_items.extend(page.items)
            all_warnings.extend(page.warnings)
            complete = page.complete
            if page.complete or not page.next_cursor:
                break
            cursor = page.next_cursor

        busy_intervals: list[dict[str, Any]] = []
        free_count = 0
        cancelled_count = 0
        for rec in all_items:
            p = rec.payload or {}
            st = str(p.get("status") or "confirmed").lower()
            if st == "cancelled":
                cancelled_count += 1
                continue
            if p.get("busy", True):
                busy_intervals.append(
                    {
                        "start_at": p.get("start_at") or p.get("all_day_date"),
                        "end_at": p.get("end_at") or p.get("all_day_end_date"),
                        "status": "tentative" if st == "tentative" else "busy",
                        "all_day": bool(p.get("all_day_date")),
                    }
                )
            else:
                free_count += 1

        summary = {
            "source": "google_calendar",
            "calendar_id": conn.calendar_id,
            "time_min": time_min,
            "time_max": time_max,
            **_summarize_records(all_items),
            "busy_intervals_count": len(busy_intervals),
            "free_intervals_count": free_count,
            "cancelled_count": cancelled_count,
            # Strictly anonymous availability intervals only (zero titles, locations, or descriptions)
            "busy_intervals": busy_intervals,
            "complete": complete,
            "warning_count": len(all_warnings),
            "warnings": all_warnings,
        }
        if args.export_json:
            _write_private_export(args.export_json, {"items": [r.to_dict() for r in all_items]})
            summary["exported_to"] = str(args.export_json)
        _print_json(summary)
        return 0

    if args.command == "import-local":
        import time
        from sqlalchemy import select
        from backend.app.contracts.sources import SourceKind as CodexSourceKind, SyncScope
        from backend.app.core.database import runs
        from backend.app.core.runtime import Runtime

        run_settings = settings.model_copy(
            update={"local_data_dir": args.data_dir.expanduser().absolute()}
        ) if args.data_dir else settings
        with Runtime(run_settings) as runtime:
            if not runtime.consent.allowed(CodexSourceKind.LOCAL, "read"):
                runtime.consent.record(CodexSourceKind.LOCAL, "read", True)
            job_id = runtime.sync.submit(
                SyncScope(source=CodexSourceKind.LOCAL, folder_id=str(args.path.expanduser()))
            )
            while True:
                with runtime.engine.connect() as db:
                    row = db.execute(select(runs.c.status, runs.c.report).where(runs.c.id == job_id)).one()
                if row.status not in ("queued", "running"):
                    _print_json({"job_id": job_id, "status": row.status, "report": row.report})
                    return 0 if row.status == "succeeded" else 1
                time.sleep(0.05)

    if args.command == "import-academic":
        from backend.app.contracts.sources import SourceKind as CodexSourceKind
        from backend.app.core.runtime import Runtime
        from backend.app.modules.ingestion.contracts import SourceRecord as InternalSourceRecord

        run_settings = settings.model_copy(
            update={"local_data_dir": args.data_dir.expanduser().absolute()}
        ) if args.data_dir else settings
        with Runtime(run_settings) as runtime:
            # Enforce canonical protected-path exclusion before reading structured academic file (ROUND2-NEW-005)
            safe_path = runtime.validate_local_import_path(args.path)
            importer = StructuredAcademicImporter()
            acad_records = importer.import_file(safe_path)
            codex_items = []
            emitted: set[str] = set()
            for rec in acad_records:
                wrapped = InternalSourceRecord(
                    source_kind=SourceKind.ACADEMIC_IMPORT,
                    external_id=rec.external_id,
                    title=rec.title,
                    record_kind="academic",
                    payload=rec.to_dict(),
                    provenance=rec.provenance,
                )
                codex_items.extend(_to_codex_records(wrapped, emitted_parents=emitted))
            if not runtime.consent.allowed(CodexSourceKind.ACADEMIC_IMPORT, "read"):
                runtime.consent.record(CodexSourceKind.ACADEMIC_IMPORT, "read", True)
            results = runtime.pipeline.import_academics(
                runtime.connection_ids[CodexSourceKind.ACADEMIC_IMPORT],
                codex_items,
                dry_run=args.dry_run,
            )
        summary = {
            "source": "academic_import",
            "dry_run": args.dry_run,
            "submitted_facts": len(codex_items),
            "imported": sum(1 for r in results if r.get("status") == "records"),
            "unchanged": sum(1 for r in results if r.get("status") == "unchanged"),
            "errors": sum(1 for r in results if r.get("status") == "error"),
        }
        _print_json(summary)
        return 0 if summary["errors"] == 0 else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

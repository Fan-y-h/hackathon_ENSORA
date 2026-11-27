"""Interactive consent and source onboarding."""
import argparse
from getpass import GetPassWarning, getpass
import warnings
import sys

if __package__:
    from .bootstrap import configure, install_missing, use_project_python
else:
    from bootstrap import configure, install_missing, use_project_python


def yes(question):
    return input(f"{question} [y/N] ").strip().casefold() in {"y", "yes"}


def masked(prompt):
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        try:
            return getpass(prompt)
        except GetPassWarning:
            print("Masked entry is unavailable. Use the official sign-in window.")
            return ""


def google_ready(settings):
    from backend.app.modules.ingestion.auth.google_cloud import GoogleCloudSetupHelper
    from backend.app.modules.ingestion.codex_bridge import runtime_config_from_codex_settings

    helper = GoogleCloudSetupHelper(runtime_config_from_codex_settings(settings))
    report = helper.inspect()
    if report.project_accessible and report.missing_apis and yes(
            f"Enable {', '.join(report.missing_apis)} in project {report.project_id}?"):
        result = helper.enable_required_apis(approved=True, apis=report.missing_apis)
        if result.returncode != 0:
            print("Google Cloud API provisioning failed.")
        report = helper.inspect()
    if not report.credentials_validation.valid:
        for step in report.guided_next_steps:
            print(step)
    return report.credentials_validation.valid


def run_onboarding(runtime, selected_source=None):
    from pydantic import SecretStr
    from sqlalchemy import update

    from backend.app.contracts.authentication import ConnectionStatus
    from backend.app.contracts.sources import SourceKind, SyncScope
    from backend.app.core.database import LOCAL_USER, users

    google_setup = None
    for source in ([selected_source] if selected_source else SourceKind):
        result = None
        if runtime.consent.allowed(source, "read"):
            result = runtime.auth.validate(source)
            if result.status == ConnectionStatus.CONNECTED:
                print(f"{source.value}  CONNECTED")
                continue
        if not yes(f"Configure {source.value}?"):
            continue
        if not runtime.consent.allowed(source, "read"):
            if not yes(f"Allow read-only access and private local storage for {source.value}?"):
                continue
            runtime.consent.record(source, "read", True)
        if result is None:
            result = runtime.auth.validate(source)
        if result.status == ConnectionStatus.NOT_CONFIGURED and source in (
            SourceKind.ENSORA_DRIVE, SourceKind.GOOGLE_CALENDAR,
        ):
            if google_setup is None:
                google_setup = google_ready(runtime.settings)
            if not google_setup:
                continue
        may_authorize = result.status in (ConnectionStatus.REAUTH_REQUIRED, ConnectionStatus.NOT_CONFIGURED)
        if may_authorize and yes("Open the source authorization flow?"):
            inputs = {}
            if source in (SourceKind.MOODLE, SourceKind.PAMPLEMOUSSE):
                if runtime.settings.ensai_username:
                    inputs["username"] = runtime.settings.ensai_username
                elif yes("Provide the optional ENSAI identifier via masked entry?"):
                    identifier = masked("ENSAI identifier: ")
                    if identifier:
                        inputs["username"] = SecretStr(identifier)
                if (runtime.settings.ensai_auth_mode == "credentials" and runtime.credentials.is_available()) and yes(
                        "Enter the institutional password using masked entry with institutional authorization?"):
                    password = masked("ENSAI password: ")
                    if password:
                        inputs["password"] = SecretStr(password)
            action = runtime.auth.reauthorize if result.status == ConnectionStatus.REAUTH_REQUIRED else runtime.auth.authorize
            result = action(source, inputs)
        print(f"{source.value}  {result.status.value}  {result.reason or ''}".rstrip())
        if result.status == ConnectionStatus.CONNECTED and source in runtime.sync.connectors and source != SourceKind.LOCAL:
            if yes(f"Allow automatic synchronization for {source.value}?"):
                runtime.consent.record(source, "automatic_sync", True)
            if yes(f"Start the first synchronization for {source.value}?"):
                if __package__:
                    from .sync import wait_for_job
                else:
                    from sync import wait_for_job
                wait_for_job(runtime, runtime.sync.submit(SyncScope(source=source, full=True)))
    if selected_source is None:
        with runtime.engine.begin() as db:
            db.execute(update(users).where(users.c.id == LOCAL_USER).values(onboarded=True))


def main():
    use_project_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("moodle", "pamplemousse", "ensora_drive", "google_calendar",
                                             "local", "academic_import"))
    parser.add_argument("--reauthorize", action="store_true", help="Reauthorize an already consented source")
    parser.add_argument("--debug-auth", action="store_true", help="Emit redacted authentication state transitions")
    args = parser.parse_args()
    if args.reauthorize and not args.source:
        parser.error("--reauthorize requires --source")
    if not sys.stdin.isatty():
        raise SystemExit("Onboarding requires an interactive terminal.")
    install_missing()
    settings = configure()
    from backend.app.contracts.authentication import ConnectionStatus
    from backend.app.contracts.sources import SourceKind
    from backend.app.core.runtime import Runtime, RuntimeBusy

    if args.debug_auth:
        from backend.app.core.auth import enable_auth_diagnostics

        enable_auth_diagnostics()
    source = SourceKind(args.source) if args.source else None
    try:
        with Runtime(settings) as current:
            if args.reauthorize:
                result = current.auth.reauthorize(source)
                message = result.reason or ""
                print(f"{source.value}  {result.status.value}  {message}".rstrip())
                if result.reason == "consent_required":
                    print(f"Grant source read consent with scripts/onboarding.py --source {source.value}.")
                return int(result.status != ConnectionStatus.CONNECTED)
            run_onboarding(current, source)
    except RuntimeBusy as exc:
        raise SystemExit(str(exc)) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Start Ensora+ using the existing virtual environment."""
import argparse
import logging
import sys

from bootstrap import configure, install_missing, use_project_python


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--port", type=int, default=8765)
    arguments.add_argument("--skip-onboarding", action="store_true")
    options = arguments.parse_args()
    install_missing()
    settings = configure()

    from sqlalchemy import select
    import uvicorn
    from backend.app.contracts.authentication import ConnectionStatus
    from backend.app.core.auth import INSTITUTIONAL_SOURCES
    from backend.app.core.database import LOCAL_USER, users
    from backend.app.core.runtime import Runtime, RuntimeBusy
    from backend.app.main import create_app
    from onboarding import run_onboarding

    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    if not options.skip_onboarding and sys.stdin.isatty():
        try:
            with Runtime(settings) as runtime:
                with runtime.engine.connect() as db:
                    onboarded = db.scalar(select(users.c.onboarded).where(users.c.id == LOCAL_USER))
                if not onboarded:
                    run_onboarding(runtime)
                else:
                    for source in sorted(INSTITUTIONAL_SOURCES):
                        if (runtime.consent.allowed(source, "read") and
                                runtime.auth.status(source) == ConnectionStatus.REAUTH_REQUIRED):
                            print(f"{source.value} needs sign-in. With the server stopped, run "
                                  f"python3 scripts/onboarding.py --source {source.value} --reauthorize.")
        except RuntimeBusy as exc:
            raise SystemExit(str(exc)) from None
    app = create_app(settings)
    print(f"Local API documentation at http://127.0.0.1:{options.port}/docs")
    uvicorn.run(app, host="127.0.0.1", port=options.port, access_log=False, log_level="info")


if __name__ == "__main__":
    use_project_python()
    main()

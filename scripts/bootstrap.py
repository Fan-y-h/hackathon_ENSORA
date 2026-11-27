"""Install missing dependencies in the existing Ensora environment."""
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "backend/pyproject.toml"


def project_python():
    configured = os.environ.get("ENSORA_PYTHON")
    if configured:
        return Path(configured).expanduser()
    existing = ROOT / ".venv/bin/python"
    if not existing.is_file():
        candidate = Path.home() / "hackathon_ENSORA/.venv/bin/python"
        existing = candidate if candidate.is_file() else existing
    if not existing.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    return existing


def use_project_python():
    python = project_python()
    # Executable symlinks may point to the same system binary from two virtual environments.
    if Path(sys.executable).absolute() != python.absolute():
        os.execv(str(python), [str(python), *sys.argv])


def install_missing(include_tests=False):
    project = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["project"]
    requirements = project["dependencies"][:]
    if include_tests:
        requirements.extend(project["optional-dependencies"]["test"])
    try:
        from packaging.requirements import Requirement
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "packaging"], check=True)
        from packaging.requirements import Requirement

    missing = []
    for requirement in requirements:
        parsed = Requirement(requirement)
        if parsed.marker and not parsed.marker.evaluate():
            continue
        package = parsed.name
        try:
            installed = version(package)
        except PackageNotFoundError:
            missing.append(requirement)
        else:
            if not parsed.specifier.contains(installed, prereleases=True):
                missing.append(requirement)
    if missing:
        subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)


def configure():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from backend.app.core.settings import Settings
    settings = Settings.load()
    settings.prepare()
    return settings


if __name__ == "__main__":
    use_project_python()
    install_missing(include_tests="--test" in sys.argv)
    configure()
    if "--browser" in sys.argv:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            installed = Path(playwright.chromium.executable_path).is_file()
        if not installed:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    print("Backend dependencies and private directories are ready.")

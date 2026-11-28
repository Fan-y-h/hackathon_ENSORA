"""Canonical authorization policy for the Ensora shared Drive hierarchy."""

from pathlib import PurePosixPath

ENSORA_DRIVE_ALLOWED_TOP_LEVEL_FOLDERS = ("1A", "2A", "3A")
ENSORA_DRIVE_EXCLUDED_TOP_LEVEL_FOLDERS = ("Global",)


def folder_scope(path: str | None) -> str:
    """Return ``allowed``, ``global`` or ``unverified`` from persisted folder provenance.

    Connector paths are root-name relative (``<root>/1A/...``). Requiring the first
    descendant segment to be an explicitly authorized cohort prevents nested folders
    named ``1A`` under Global or another unauthorized top-level folder from passing.
    """
    if not isinstance(path, str) or not path.strip():
        return "unverified"
    parts = [part.strip() for part in PurePosixPath(path.replace("\\", "/")).parts
             if part not in {"", "/", "."}]
    if not parts:
        return "unverified"
    allowed = {name.casefold() for name in ENSORA_DRIVE_ALLOWED_TOP_LEVEL_FOLDERS}
    excluded = {name.casefold() for name in ENSORA_DRIVE_EXCLUDED_TOP_LEVEL_FOLDERS}
    # Connectors normally include the root display name, while legacy serialized
    # provenance can begin directly at the cohort segment.
    if parts[0].casefold() in allowed | excluded:
        top = parts[0].casefold()
    elif len(parts) > 1:
        top = parts[1].casefold()
    else:
        return "unverified"
    if top in excluded:
        return "global"
    if top in allowed:
        return "allowed"
    return "unverified"


def drive_document_in_scope(metadata: dict, external_id: str | None = None) -> bool:
    """Fail closed for Ensora Drive documents whose authorized cohort is unproven."""
    provenance = metadata.get("provenance") if isinstance(metadata, dict) else None
    provenance = provenance if isinstance(provenance, dict) else {}
    is_drive = (provenance.get("source") == "ensora_drive"
                or bool(external_id and external_id.startswith("ensora_drive:")))
    return not is_drive or folder_scope(provenance.get("folder")) == "allowed"


def drive_document_is_global(metadata: dict) -> bool:
    provenance = metadata.get("provenance") if isinstance(metadata, dict) else None
    provenance = provenance if isinstance(provenance, dict) else {}
    return folder_scope(provenance.get("folder")) == "global"

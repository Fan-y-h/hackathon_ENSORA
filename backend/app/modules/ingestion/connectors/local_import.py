"""Local file/folder import connector and structured academic curriculum/grade importer.

Guarantees the platform remains fully usable offline or when external services are unavailable.
Supports individual files, recursive directory imports (with symlink/traversal guards),
and structured academic imports (JSON, YAML, CSV) for curriculum, teaching units,
subject coefficients, recursive assessment weights, and grade records.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import stat as stat_mod
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from ..contracts import (
    AcademicRecord,
    AccessStatus,
    AuthState,
    AuthStatus,
    CalendarEventRecord,
    ChangePage,
    ConnectorCapabilities,
    FetchResult,
    SourceKind,
    SourceRecord,
    ValueStatus,
    utc_now_iso,
)
from .base import PermissionDeniedError, SourceConnector, guess_mime_type, sha256_bytes
from .pamplemousse import parse_decimal_strict, parse_ics_events

SUPPORTED_LOCAL_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".ipynb",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".txt",
        ".py",
        ".r",
        ".sql",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".js",
        ".ts",
        ".sh",
        ".tex",
        ".sas",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".ics",
    }
)


def _safe_yaml_load(text: str) -> Any:
    """Load YAML using PyYAML when installed, or built-in subset parser for offline use."""
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        return _parse_simple_yaml(text)


def _parse_yaml_scalar(token: str) -> Any:
    val = token.strip()
    if not val or val in {"null", "Null", "NULL", "~"}:
        return None
    if val in {"true", "True", "TRUE"}:
        return True
    if val in {"false", "False", "FALSE"}:
        return False
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    if val.startswith("{") and val.endswith("}"):
        return _parse_inline_mapping(val[1:-1])
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(part) for part in inner.split(",")]
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return val


def _parse_inline_mapping(inner: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not inner.strip():
        return out
    for part in inner.split(","):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip().strip("'\"")] = _parse_yaml_scalar(v)
    return out


def _strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:idx]
    return line


def _parse_simple_yaml(text: str) -> Any:
    """Pure-Python parser for standard indented YAML mappings, lists, and inline dicts."""
    raw_lines: list[tuple[int, str]] = []
    for line in text.splitlines():
        cleaned = _strip_yaml_comment(line).rstrip()
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        raw_lines.append((indent, cleaned.lstrip(" ")))

    if not raw_lines:
        return None

    def parse_block(index: int, min_indent: int) -> tuple[Any, int]:
        indent, first = raw_lines[index]
        if first.startswith("- ") or first == "-":
            items: list[Any] = []
            i = index
            while i < len(raw_lines):
                cur_indent, cur = raw_lines[i]
                if cur_indent < indent or not (cur.startswith("- ") or cur == "-"):
                    break
                if cur_indent > indent:
                    break
                rest = cur[2:].strip() if cur.startswith("- ") else ""
                if not rest:
                    val, i = parse_block(i + 1, indent + 1)
                    items.append(val)
                elif rest.startswith("{") and rest.endswith("}"):
                    items.append(_parse_inline_mapping(rest[1:-1]))
                    i += 1
                elif ":" in rest and not rest.startswith(('"', "'")):
                    key, val_str = rest.split(":", 1)
                    obj: dict[str, Any] = {}
                    key = key.strip().strip("'\"")
                    val_str = val_str.strip()
                    i += 1
                    if val_str:
                        obj[key] = _parse_yaml_scalar(val_str)
                    elif i < len(raw_lines) and raw_lines[i][0] > indent:
                        obj[key], i = parse_block(i, indent + 1)
                    else:
                        obj[key] = None
                    # Consume sibling keys of this list-item mapping
                    while i < len(raw_lines) and raw_lines[i][0] > indent:
                        sub_indent, sub_line = raw_lines[i]
                        if sub_line.startswith("- ") or ":" not in sub_line:
                            break
                        sk, sv = sub_line.split(":", 1)
                        sk = sk.strip().strip("'\"")
                        sv = sv.strip()
                        i += 1
                        if sv:
                            obj[sk] = _parse_yaml_scalar(sv)
                        elif i < len(raw_lines) and raw_lines[i][0] > sub_indent:
                            obj[sk], i = parse_block(i, sub_indent + 1)
                        else:
                            obj[sk] = None
                    items.append(obj)
                else:
                    items.append(_parse_yaml_scalar(rest))
                    i += 1
            return items, i

        mapping: dict[str, Any] = {}
        i = index
        while i < len(raw_lines):
            cur_indent, cur = raw_lines[i]
            if cur_indent < min_indent or cur_indent < indent:
                break
            if cur_indent > indent or ":" not in cur:
                break
            key, val_str = cur.split(":", 1)
            key = key.strip().strip("'\"")
            val_str = val_str.strip()
            i += 1
            if val_str:
                mapping[key] = _parse_yaml_scalar(val_str)
            elif i < len(raw_lines) and raw_lines[i][0] > cur_indent:
                mapping[key], i = parse_block(i, cur_indent + 1)
            else:
                mapping[key] = None
        return mapping, i

    result, _ = parse_block(0, 0)
    return result


def default_protected_directories() -> tuple[Path, ...]:
    """Canonical private Ensora+ config, secrets, session, and data directories excluded from local import."""
    home = Path.home()
    candidates = [
        home / ".config" / "ensora-plus",
        home / ".local" / "share" / "ensora-plus",
    ]
    for env_var in ("ENSORA_CONFIG_DIR", "LOCAL_DATA_DIR", "ENSAI_PROFILE_DIR"):
        raw = os.environ.get(env_var)
        if raw:
            candidates.append(Path(raw).expanduser())
    sources_env = os.environ.get("ENSORA_SOURCES_ENV")
    if sources_env:
        candidates.append(Path(sources_env).expanduser().parent)
    resolved: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        try:
            canon = path.expanduser().resolve()
        except OSError:
            canon = path.expanduser().absolute()
        if canon not in seen:
            seen.add(canon)
            resolved.append(canon)
    return tuple(resolved)


class LocalFileConnector(SourceConnector):
    """Read-only connector for explicitly selected local files and folders.

    Enforces:
    - Canonical protected-path exclusion (never scans/imports app config, secrets, sessions, or local data dirs).
    - Directory-descriptor-relative component-by-component O_NOFOLLOW opens verifying S_ISREG (no intermediate symlink races).
    - Stable root-namespaced external IDs preventing cross-root collisions.
    """

    source_kind = SourceKind.LOCAL_FILE

    def __init__(
        self,
        *,
        max_file_bytes: int = 100 * 1024 * 1024,
        protected_roots: Iterable[Path] | None = None,
    ) -> None:
        self._max_file_bytes = max_file_bytes
        base_protected = list(default_protected_directories())
        if protected_roots is not None:
            for p in protected_roots:
                try:
                    base_protected.append(Path(p).expanduser().resolve())
                except OSError:
                    base_protected.append(Path(p).expanduser().absolute())
        self._protected_roots: tuple[Path, ...] = tuple(dict.fromkeys(base_protected))

    def _assert_not_protected(self, candidate: Path) -> None:
        """Reject any path inside, equal to, or enclosing a protected application private directory."""
        resolved = candidate.expanduser().resolve()
        for protected in self._protected_roots:
            if resolved == protected:
                raise PermissionDeniedError(
                    "Blocked local access to protected application private directory."
                )
            try:
                resolved.relative_to(protected)
                raise PermissionDeniedError(
                    "Blocked local access inside protected application private directory."
                )
            except ValueError:
                pass
            # Also reject selecting a parent that directly contains the private directory (e.g. ~/.config)
            try:
                protected.relative_to(resolved)
                raise PermissionDeniedError(
                    "Blocked local root that encompasses protected application private directory."
                )
            except ValueError:
                pass

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            source=SourceKind.LOCAL_FILE,
            supports_documents=True,
            supports_events=True,  # .ics imports
            supports_assignments=False,
            supports_academic_records=True,  # structured json/yaml/csv
            supports_incremental=True,
            supports_full_scan=True,
            supports_download=True,
            read_only=True,
            fallback_formats=tuple(sorted(SUPPORTED_LOCAL_EXTENSIONS)),
        )

    def authorize(self, config: dict[str, Any] | None = None) -> AuthStatus:
        return AuthStatus(
            source=SourceKind.LOCAL_FILE,
            state=AuthState.AUTHENTICATED,
            authenticated=True,
            detail="Local file and folder import is always available offline.",
        )

    def _validate_path_within_root(self, candidate: Path, root: Path) -> Path:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
        try:
            resolved_candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise PermissionDeniedError(
                f"Blocked path traversal or escaping symlink outside selected root: {candidate}"
            ) from exc
        return resolved_candidate

    @staticmethod
    def _root_namespace(root: Path) -> str:
        """Stable 12-char namespace derived from canonical selected root path."""
        return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:12]

    def _read_contained_bytes(self, file_path: Path, boundary_root: Path) -> bytes:
        """Atomically traverse from pinned root dirfd via O_NOFOLLOW per component and require S_ISREG."""
        self._assert_not_protected(boundary_root)
        resolved_root = boundary_root.resolve()
        try:
            rel_parts = file_path.relative_to(boundary_root).parts
        except ValueError:
            # Fallback if file_path was passed resolved while boundary_root wasn't
            try:
                rel_parts = file_path.relative_to(resolved_root).parts
            except ValueError as exc:
                raise PermissionDeniedError("Blocked local file path outside selected root.") from exc

        if not rel_parts or any(part in ("", ".", "..") for part in rel_parts):
            raise PermissionDeniedError("Blocked invalid relative path inside local root.")

        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)

        try:
            cur_fd = os.open(str(resolved_root), dir_flags)
        except OSError as exc:
            raise PermissionDeniedError("Unable to open pinned local root directory safely.") from exc

        try:
            for part in rel_parts[:-1]:
                try:
                    nxt_fd = os.open(part, dir_flags, dir_fd=cur_fd)
                except OSError as exc:
                    raise PermissionDeniedError(
                        f"Blocked symlink or non-directory intermediate component '{part}'."
                    ) from exc
                os.close(cur_fd)
                cur_fd = nxt_fd

            leaf = rel_parts[-1]
            try:
                leaf_fd = os.open(leaf, file_flags, dir_fd=cur_fd)
            except OSError as exc:
                raise PermissionDeniedError(
                    f"Blocked symlink or inaccessible file '{leaf}' at open time."
                ) from exc
            try:
                st = os.fstat(leaf_fd)
                if not stat_mod.S_ISREG(st.st_mode):
                    raise PermissionDeniedError(f"Blocked non-regular file '{leaf}'.")
                if st.st_size > self._max_file_bytes:
                    raise ValueError(f"File exceeds size limit: {st.st_size} > {self._max_file_bytes} bytes.")
                with os.fdopen(leaf_fd, "rb") as stream:
                    leaf_fd = -1  # ownership transferred to stream
                    data = stream.read(self._max_file_bytes + 1)
            finally:
                if leaf_fd >= 0:
                    os.close(leaf_fd)
        finally:
            os.close(cur_fd)

        if len(data) > self._max_file_bytes:
            raise ValueError(f"File exceeds size limit: > {self._max_file_bytes} bytes.")
        return data

    def normalize(self, raw_item: dict[str, Any]) -> SourceRecord:
        path = Path(raw_item["path"])
        rel_path = str(raw_item.get("relative_path") or path.name)
        root_path = Path(raw_item["root_path"]) if raw_item.get("root_path") else path.parent
        root_ns = raw_item.get("root_namespace") or self._root_namespace(root_path)
        folder = str(Path(rel_path).parent)
        folder_path = "" if folder == "." else folder.replace("\\", "/")
        stat = path.stat()
        mtime_iso = (
            datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        content_hash = raw_item.get("content_hash") or sha256_bytes(
            self._read_contained_bytes(path, root_path)
        )
        mime_type = guess_mime_type(path.name)

        return SourceRecord(
            source_kind=SourceKind.LOCAL_FILE,
            external_id=f"local:{root_ns}:{rel_path}",
            title=path.name,
            record_kind="document",
            remote_version=content_hash,
            remote_modified_at=mtime_iso,
            mime_type=mime_type,
            size_bytes=stat.st_size,
            content_hash=content_hash,
            folder_path=folder_path or None,
            local_path=str(path),
            access_status=AccessStatus.ACTIVE,
            payload={
                "relative_path": rel_path,
                "extension": path.suffix.lower(),
                "root_path": str(root_path.resolve()),
                "root_namespace": root_ns,
            },
            provenance={
                "source": SourceKind.LOCAL_FILE.value,
                "relative_path": rel_path,
                "root_namespace": root_ns,
                "imported_at": utc_now_iso(),
            },
        )

    def list_changes(
        self,
        scope: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> ChangePage:
        """Scan an explicit local file or directory root safely.

        Namespaces document IDs by stable root identity, rejects escaping symlinks,
        and parses `.ics` files directly into normalized calendar event records.
        """
        opts = scope or {}
        raw_root = opts.get("root_path") or opts.get("file_path")
        if not raw_root:
            raise ValueError("LocalFileConnector requires scope['root_path'] or scope['file_path'].")

        root = Path(raw_root).expanduser()
        if not root.exists():
            raise FileNotFoundError(f"Selected local path does not exist: {root}")
        self._assert_not_protected(root)

        recursive = bool(opts.get("recursive", True))
        known_hashes: dict[str, str] = dict(opts.get("known_hashes") or {})
        allowed_exts = {
            e.lower() for e in (opts.get("allowed_extensions") or SUPPORTED_LOCAL_EXTENSIONS)
        }

        discovered: list[SourceRecord] = []
        warnings: list[str] = []

        if root.is_file():
            boundary_for_ns = root.parent.resolve()
            candidates = [(root, root.name, boundary_for_ns)]
        else:
            boundary_for_ns = root.resolve()
            candidates = []
            iterator = root.rglob("*") if recursive else root.glob("*")
            for p in sorted(iterator):
                if p.is_dir():
                    continue
                if any(part.startswith(".") for part in p.relative_to(root).parts):
                    continue
                candidates.append((p, str(p.relative_to(root)).replace("\\", "/"), boundary_for_ns))

        root_ns = self._root_namespace(boundary_for_ns)

        for file_path, rel_path, boundary_root in candidates:
            try:
                safe_path = self._validate_path_within_root(file_path, boundary_root)
                if file_path.is_symlink():
                    raise PermissionDeniedError(f"Blocked symlinked file inside local root: {file_path}")
                if safe_path.suffix.lower() not in allowed_exts:
                    continue
                size = safe_path.stat().st_size
                if size > self._max_file_bytes:
                    warnings.append(f"Skipped oversized file '{rel_path}' ({size} bytes).")
                    continue
                content = self._read_contained_bytes(file_path, boundary_root)
            except PermissionDeniedError as exc:
                warnings.append(str(exc))
                continue
            digest = sha256_bytes(content)

            # Parse .ics files directly into normalized calendar event SourceRecords
            if safe_path.suffix.lower() == ".ics":
                ics_events = parse_ics_events(
                    content.decode("utf-8-sig", errors="replace"),
                    source_kind=SourceKind.LOCAL_FILE,
                )
                for ev in ics_events:
                    ev_dict = ev.to_dict()
                    ev_ext_id = f"local:{root_ns}:{rel_path}:{ev.external_id}"
                    ev_dict["external_id"] = ev_ext_id
                    if known_hashes.get(ev_ext_id) == digest:
                        continue
                    discovered.append(
                        SourceRecord(
                            source_kind=SourceKind.LOCAL_FILE,
                            external_id=ev_ext_id,
                            title=ev.title,
                            record_kind="event",
                            remote_version=digest,
                            remote_modified_at=ev.start_at,
                            folder_path=str(Path(rel_path).parent) if Path(rel_path).parent != Path(".") else None,
                            access_status=(
                                AccessStatus.TOMBSTONED
                                if ev.status == "cancelled"
                                else AccessStatus.ACTIVE
                            ),
                            payload=ev_dict,
                            provenance={
                                "source": SourceKind.LOCAL_FILE.value,
                                "relative_path": rel_path,
                                "root_namespace": root_ns,
                                "format": "ics",
                                "imported_at": utc_now_iso(),
                            },
                        )
                    )
                continue

            ext_id = f"local:{root_ns}:{rel_path}"
            if known_hashes.get(ext_id) == digest:
                continue

            discovered.append(
                self.normalize(
                    {
                        "path": str(safe_path),
                        "relative_path": rel_path,
                        "root_path": str(boundary_root),
                        "root_namespace": root_ns,
                        "content_hash": digest,
                    }
                )
            )

        page_size = max(1, int(opts.get("page_size", 200)))
        offset = int(cursor) if (cursor and cursor.isdigit()) else 0
        sliced = discovered[offset : offset + page_size]
        next_offset = offset + len(sliced)
        complete = next_offset >= len(discovered)

        return ChangePage(
            items=tuple(sliced),
            next_cursor=None if complete else str(next_offset),
            complete=complete,
            warnings=tuple(warnings),
        )

    def fetch_blob(self, item: SourceRecord) -> FetchResult:
        if not item.local_path:
            raise ValueError(f"Local record {item.external_id} is missing local_path.")
        path = Path(item.local_path)
        root_raw = item.payload.get("root_path") if isinstance(item.payload, dict) else None
        boundary_root = Path(root_raw) if root_raw else path.parent
        content = self._read_contained_bytes(path, boundary_root)
        return FetchResult(
            external_id=item.external_id,
            content=content,
            content_hash=sha256_bytes(content),
            mime_type=item.mime_type or guess_mime_type(path.name),
            size_bytes=len(content),
            filename=path.name,
        )

    def import_ics_file(self, path: Path) -> list[CalendarEventRecord]:
        return parse_ics_events(path.read_text(encoding="utf-8", errors="replace"), source_kind=SourceKind.LOCAL_FILE)

    def disconnect(self) -> AuthStatus:
        return AuthStatus(
            source=SourceKind.LOCAL_FILE,
            state=AuthState.AUTHENTICATED,
            authenticated=True,
            detail="Local file connector has no remote session to revoke.",
        )


class StructuredAcademicImporter:
    """Imports academic curriculum hierarchies, UE/subject coefficients, nested weights & grades.

    Emits parent records strictly before child records (required for Codex persistence).
    Distinguishes missing values (None) from zero (Decimal('0')).
    Validates against negative weights, cycles, and fractional sibling sums > 1.
    """

    def import_file(self, path: Path) -> list[AcademicRecord]:
        suffix = path.suffix.lower()
        text = path.read_text(encoding="utf-8-sig")
        if suffix in (".yaml", ".yml"):
            return self.import_curriculum_dict(_safe_yaml_load(text), source_name=path.name)
        if suffix == ".json":
            data = json.loads(text)
            if isinstance(data, dict):
                return self.import_curriculum_dict(data, source_name=path.name)
            if isinstance(data, list):
                return self.import_flat_records(data, source_name=path.name)
            raise ValueError("Academic JSON must be an object or array.")
        if suffix == ".csv":
            return self.import_csv_text(text, source_name=path.name)
        raise ValueError(f"Unsupported structured academic format: {suffix}")

    def import_yaml_text(self, yaml_text: str, *, source_name: str = "inline.yaml") -> list[AcademicRecord]:
        data = _safe_yaml_load(yaml_text)
        if not isinstance(data, dict):
            raise ValueError("Curriculum YAML root must be a mapping.")
        return self.import_curriculum_dict(data, source_name=source_name)

    def import_curriculum_dict(
        self,
        data: dict[str, Any],
        *,
        source_name: str = "curriculum.json",
    ) -> list[AcademicRecord]:
        """Parse hierarchical curriculum matching docs/GRADE_TRACKER.md schema."""
        if not isinstance(data, dict):
            raise ValueError("Curriculum payload must be a dictionary.")

        records: list[AcademicRecord] = []
        seen_ids: set[str] = set()
        year = str(data.get("academic_year") or "").strip() or None
        cohort = str(data.get("cohort") or "").strip() or None
        semester = str(data["semester"]) if data.get("semester") is not None else None
        spec = str(data.get("specialization") or "").strip() or None

        def register(rec: AcademicRecord) -> None:
            if rec.external_id in seen_ids:
                raise ValueError(f"Duplicate academic node ID detected: '{rec.external_id}'")
            seen_ids.add(rec.external_id)
            records.append(rec)

        # Emit top-level parents first (year -> specialization -> semester)
        parent_id: str | None = None
        if year:
            year_id = f"academic:year:{year}"
            register(
                AcademicRecord(
                    source_kind=SourceKind.ACADEMIC_IMPORT,
                    external_id=year_id,
                    record_type="academic_year",
                    title=year,
                    academic_year=year,
                    cohort=cohort,
                    provenance={"file": source_name, "imported_at": utc_now_iso()},
                )
            )
            parent_id = year_id

        if spec:
            spec_id = f"academic:spec:{year or 'default'}:{spec}"
            register(
                AcademicRecord(
                    source_kind=SourceKind.ACADEMIC_IMPORT,
                    external_id=spec_id,
                    record_type="specialization",
                    title=spec,
                    parent_external_id=parent_id,
                    academic_year=year,
                    cohort=cohort,
                    specialization=spec,
                    provenance={"file": source_name, "imported_at": utc_now_iso()},
                )
            )
            parent_id = spec_id

        if semester:
            sem_id = f"academic:sem:{year or 'default'}:{spec or 'common'}:S{semester}"
            register(
                AcademicRecord(
                    source_kind=SourceKind.ACADEMIC_IMPORT,
                    external_id=sem_id,
                    record_type="semester",
                    title=f"Semester {semester}",
                    parent_external_id=parent_id,
                    academic_year=year,
                    cohort=cohort,
                    semester=semester,
                    specialization=spec,
                    provenance={"file": source_name, "imported_at": utc_now_iso()},
                )
            )
            parent_id = sem_id

        # Recursive helper for arbitrarily nested assessment groups & leaves
        def parse_assessments(
            items: list[dict[str, Any]],
            parent_ext_id: str,
            subject_code: str | None,
            depth: int = 0,
        ) -> None:
            if depth > 12:
                raise ValueError("Assessment hierarchy exceeded maximum safe nesting depth (12).")
            fraction_sum = Decimal("0")
            has_fraction = False
            for idx, item in enumerate(items, start=1):
                node_id = str(item.get("id") or f"{parent_ext_id}:a{idx}")
                title = str(item.get("name") or item.get("title") or node_id)
                raw_w = item.get("weight") if "weight" in item else item.get("coefficient")
                w = parse_decimal_strict(raw_w)
                if w is not None and w < 0:
                    raise ValueError(f"Negative weight not allowed for assessment '{node_id}'.")

                w_mode = item.get("weight_mode")
                if w_mode is None and w is not None:
                    w_mode = "fraction" if "weight" in item and w <= Decimal("1") else "coefficient"
                if w_mode == "fraction" and w is not None:
                    if w > Decimal("1"):
                        raise ValueError(f"Fractional weight > 1 for '{node_id}': {w}")
                    fraction_sum += w
                    has_fraction = True

                children = item.get("components") or item.get("assessments") or item.get("children") or []
                is_group = bool(children)
                grade = parse_decimal_strict(
                    item.get("grade") if "grade" in item else item.get("grade_value")
                )
                scale = parse_decimal_strict(item.get("scale_max") or item.get("grade_scale_max"))
                if grade is not None and grade < 0:
                    raise ValueError(f"Negative grade not allowed for '{node_id}'.")
                effective_scale = scale if scale is not None else (Decimal("20") if grade is not None else None)
                if grade is not None and effective_scale is not None and grade > effective_scale:
                    raise ValueError(f"Grade {grade} exceeds scale {effective_scale} for '{node_id}'.")

                register(
                    AcademicRecord(
                        source_kind=SourceKind.ACADEMIC_IMPORT,
                        external_id=node_id,
                        record_type="assessment_group" if is_group else "assessment",
                        title=title,
                        parent_external_id=parent_ext_id,
                        academic_year=year,
                        cohort=cohort,
                        semester=semester,
                        specialization=spec,
                        subject_code=subject_code,
                        weight=w,
                        weight_mode=w_mode,
                        weight_status=ValueStatus.CONFIRMED if w is not None else ValueStatus.MISSING,
                        grade_value=grade,
                        grade_scale_max=effective_scale,
                        grade_kind="actual" if grade is not None else None,
                        grade_status=ValueStatus.CONFIRMED if grade is not None else ValueStatus.MISSING,
                        observed_at=item.get("observed_at") or utc_now_iso(),
                        provenance={"file": source_name, "depth": depth, "imported_at": utc_now_iso()},
                    )
                )
                if is_group:
                    parse_assessments(children, node_id, subject_code, depth=depth + 1)

            if has_fraction and fraction_sum > Decimal("1.0001"):
                raise ValueError(
                    f"Fractional sibling weights under '{parent_ext_id}' sum to {fraction_sum} > 1."
                )

        for u_idx, unit in enumerate(data.get("units") or [], start=1):
            ue_id = str(unit.get("id") or f"ue_{u_idx}")
            ue_name = str(unit.get("name") or unit.get("title") or ue_id)
            ue_coef = parse_decimal_strict(
                unit.get("coefficient") if "coefficient" in unit else unit.get("weight")
            )
            if ue_coef is not None and ue_coef < 0:
                raise ValueError(f"Negative coefficient on teaching unit '{ue_id}'.")
            register(
                AcademicRecord(
                    source_kind=SourceKind.ACADEMIC_IMPORT,
                    external_id=ue_id,
                    record_type="teaching_unit",
                    title=ue_name,
                    parent_external_id=parent_id,
                    academic_year=year,
                    cohort=cohort,
                    semester=semester,
                    specialization=spec,
                    weight=ue_coef,
                    weight_mode="coefficient" if ue_coef is not None else None,
                    weight_status=ValueStatus.CONFIRMED if ue_coef is not None else ValueStatus.MISSING,
                    provenance={"file": source_name, "imported_at": utc_now_iso()},
                )
            )
            for s_idx, subj in enumerate(unit.get("subjects") or [], start=1):
                subj_id = str(subj.get("id") or f"{ue_id}_subj_{s_idx}")
                subj_name = str(subj.get("name") or subj.get("title") or subj_id)
                subj_code = subj.get("code") or subj_id
                subj_coef = parse_decimal_strict(
                    subj.get("coefficient") if "coefficient" in subj else subj.get("weight")
                )
                if subj_coef is not None and subj_coef < 0:
                    raise ValueError(f"Negative coefficient on subject '{subj_id}'.")
                register(
                    AcademicRecord(
                        source_kind=SourceKind.ACADEMIC_IMPORT,
                        external_id=subj_id,
                        record_type="subject",
                        title=subj_name,
                        parent_external_id=ue_id,
                        academic_year=year,
                        cohort=cohort,
                        semester=semester,
                        specialization=spec,
                        subject_code=str(subj_code),
                        weight=subj_coef,
                        weight_mode="coefficient" if subj_coef is not None else None,
                        weight_status=ValueStatus.CONFIRMED if subj_coef is not None else ValueStatus.MISSING,
                        provenance={"file": source_name, "imported_at": utc_now_iso()},
                    )
                )
                if subj.get("assessments"):
                    parse_assessments(subj["assessments"], subj_id, str(subj_code))

        # Support top-level standalone grade_records list if provided alongside curriculum
        for g_idx, g in enumerate(data.get("grades") or data.get("grade_records") or [], start=1):
            grade = parse_decimal_strict(g.get("value") if "value" in g else g.get("grade"))
            scale = parse_decimal_strict(g.get("maximum") or g.get("scale_max")) or Decimal("20")
            if grade is not None and (grade < 0 or grade > scale):
                raise ValueError(f"Out-of-range grade {grade}/{scale} in grade record #{g_idx}.")
            ext_id = str(g.get("id") or f"grade:{g.get('node_id') or g_idx}")
            register(
                AcademicRecord(
                    source_kind=SourceKind.ACADEMIC_IMPORT,
                    external_id=ext_id,
                    record_type="grade",
                    title=str(g.get("title") or g.get("node_id") or f"Grade {g_idx}"),
                    parent_external_id=g.get("node_id") or g.get("parent_external_id"),
                    academic_year=year,
                    semester=semester,
                    specialization=spec,
                    grade_value=grade,
                    grade_scale_max=scale if grade is not None else None,
                    grade_kind=str(g.get("kind") or "actual") if grade is not None else None,
                    grade_status=ValueStatus.CONFIRMED if grade is not None else ValueStatus.MISSING,
                    observed_at=g.get("observed_at") or utc_now_iso(),
                    provenance={"file": source_name, "imported_at": utc_now_iso()},
                )
            )

        return records

    def import_flat_records(
        self,
        items: list[dict[str, Any]],
        *,
        source_name: str = "records.json",
    ) -> list[AcademicRecord]:
        records: list[AcademicRecord] = []
        for idx, item in enumerate(items, start=1):
            weight = parse_decimal_strict(item.get("weight") if "weight" in item else item.get("coefficient"))
            grade = parse_decimal_strict(item.get("grade_value") if "grade_value" in item else item.get("grade"))
            scale = parse_decimal_strict(item.get("grade_scale_max") or item.get("scale_max"))
            if grade is not None and scale is None:
                scale = Decimal("20")
            records.append(
                AcademicRecord(
                    source_kind=SourceKind.ACADEMIC_IMPORT,
                    external_id=str(item.get("external_id") or item.get("id") or f"academic:row:{idx}"),
                    record_type=str(item.get("record_type") or ("grade" if grade is not None else "subject")),
                    title=str(item.get("title") or item.get("name") or f"Item {idx}"),
                    parent_external_id=item.get("parent_external_id") or item.get("parent_id"),
                    academic_year=item.get("academic_year"),
                    cohort=item.get("cohort"),
                    semester=str(item["semester"]) if item.get("semester") is not None else None,
                    specialization=item.get("specialization"),
                    subject_code=item.get("subject_code") or item.get("code"),
                    weight=weight,
                    weight_mode=item.get("weight_mode") or ("coefficient" if weight is not None else None),
                    weight_status=ValueStatus.CONFIRMED if weight is not None else ValueStatus.MISSING,
                    grade_value=grade,
                    grade_scale_max=scale,
                    grade_kind="actual" if grade is not None else None,
                    grade_status=ValueStatus.CONFIRMED if grade is not None else ValueStatus.MISSING,
                    observed_at=item.get("observed_at") or utc_now_iso(),
                    provenance={"file": source_name, "row": idx, "imported_at": utc_now_iso()},
                )
            )
        return records

    def import_csv_text(self, csv_text: str, *, source_name: str = "academic.csv") -> list[AcademicRecord]:
        sample = csv_text[:2048]
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")), delimiter=delimiter)
        rows = [
            {k.strip().lower(): (v.strip() if isinstance(v, str) else "") for k, v in row.items() if k}
            for row in reader
        ]
        return self.import_flat_records(rows, source_name=source_name)

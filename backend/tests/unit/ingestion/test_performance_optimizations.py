"""Regression and equivalence tests for P1 acquisition, parsing, and resolution optimizations."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import requests

from backend.app.modules.ingestion.auth.ensai import EnsaiAuthAdapter, SESSION_COOKIES_FILENAME
from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
from backend.app.modules.ingestion.codex_bridge import _load_known_record_revisions
from backend.app.modules.ingestion.config import (
    DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID,
    SourcesRuntimeConfig,
)
from backend.app.modules.ingestion.connectors.base import (
    AdaptiveRateLimiter,
    PermissionDeniedError,
    RateLimiter,
    TransientSourceError,
)
from backend.app.modules.ingestion.connectors.ensora_drive import (
    DRIVE_FOLDER_MIME,
    DRIVE_SHORTCUT_MIME,
    EnsoraDriveConnector,
)
from backend.app.modules.ingestion.connectors.moodle import MoodleConnector
from backend.app.modules.ingestion.connectors.pamplemousse import PamplemousseConnector
from backend.app.modules.ingestion.contracts import SourceKind, SourceRecord
from backend.app.modules.ingestion.parsers.pdf_parser import PdfDocumentParser
from backend.app.modules.ingestion.parsers.registry import DocumentParserRegistry
from backend.app.modules.ingestion.resolution.entity_resolver import (
    AcademicEntityResolver,
    CanonicalSubject,
)


class _DummyCreds:
    expired = False
    valid = True
    token = "dummy-access-token"
    refresh_token = "dummy-refresh-token"
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    granted_scopes = ["https://www.googleapis.com/auth/drive.readonly"]


class _FakeExec:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def execute(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _build_minimal_pdf(pages: list[str], *, title: str | None = None) -> bytes:
    """Build a valid multi-page PDF with Helvetica text streams readable by Poppler."""
    objs: list[bytes] = []
    # 1: Catalog, 2: Pages, 3: Font
    objs.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    page_obj_ids = [4 + 2 * i for i in range(len(pages))]
    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids).encode("ascii")
    objs.append(
        b"2 0 obj\n<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(pages)).encode("ascii") + b" >>\nendobj\n"
    )
    objs.append(b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    for i, text in enumerate(pages):
        pid = 4 + 2 * i
        cid = pid + 1
        objs.append(
            f"{pid} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {cid} 0 R >>\nendobj\n".encode("ascii")
        )
        lines = [ln for ln in text.splitlines() if ln.strip()]
        stream_lines = ["BT", "/F1 11 Tf", "50 760 Td", "15 TL"]
        for idx, ln in enumerate(lines):
            esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if idx == 0:
                stream_lines.append(f"({esc}) Tj")
            else:
                stream_lines.append(f"T* ({esc}) Tj")
        stream_lines.append("ET")
        stream_bytes = "\n".join(stream_lines).encode("latin-1", errors="replace")
        objs.append(
            f"{cid} 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream\nendobj\n"
        )

    info_ref = b""
    if title:
        info_id = 4 + 2 * len(pages)
        esc_title = title.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        objs.append(f"{info_id} 0 obj\n<< /Title ({esc_title}) >>\nendobj\n".encode("latin-1"))
        info_ref = f" /Info {info_id} 0 R".encode("ascii")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R".encode("ascii")
        + info_ref
        + f" >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


def test_load_known_record_revisions_reads_source_records_schema(tmp_path: Path) -> None:
    """_load_known_record_revisions queries Codex's canonical source_records JSON payload."""
    db_path = tmp_path / "ensora.sqlite3"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE connections (id TEXT PRIMARY KEY, source TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE source_records ("
            "id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, external_id TEXT NOT NULL, "
            "kind TEXT NOT NULL, payload JSON NOT NULL)"
        )
        conn.execute("INSERT INTO connections VALUES ('c_pamp', 'pamplemousse')")
        conn.execute("INSERT INTO connections VALUES ('c_other', 'moodle')")
        conn.execute(
            "INSERT INTO source_records VALUES (?, ?, ?, ?, ?)",
            (
                "r1",
                "c_pamp",
                "pamp:subj:ST201",
                "subject",
                json.dumps({"title": "Series temporelles", "provenance": {"remote_revision": "rev-1"}}),
            ),
        )
        conn.execute(
            "INSERT INTO source_records VALUES (?, ?, ?, ?, ?)",
            (
                "r2",
                "c_pamp",
                "pamp:grade:1",
                "grade",
                json.dumps({"title": "Partiel", "provenance": {"remote_revision": "rev-2"}}),
            ),
        )
        conn.execute(
            "INSERT INTO source_records VALUES (?, ?, ?, ?, ?)",
            (
                "r3",
                "c_other",
                "moodle:1",
                "subject",
                json.dumps({"provenance": {"remote_revision": "rev-other"}}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    revs = _load_known_record_revisions(db_path, "pamplemousse")
    assert revs == {"pamp:subj:ST201": "rev-1", "pamp:grade:1": "rev-2"}


def test_drive_fetch_blob_parent_cache_reduces_ancestry_calls_while_blocking_moved_and_trashed(
    tmp_path: Path,
) -> None:
    """Short-TTL parent verification cuts redundant files.get walks by >70% without weakening REV-001."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    root_id = DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID

    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "parents": [], "trashed": False},
        "f_2a": {"id": "f_2a", "name": "2A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "f_s3": {"id": "f_s3", "name": "S3", "mimeType": DRIVE_FOLDER_MIME, "parents": ["f_2a"], "trashed": False},
        "f_ml": {"id": "f_ml", "name": "ML", "mimeType": DRIVE_FOLDER_MIME, "parents": ["f_s3"], "trashed": False},
        "f_td": {"id": "f_td", "name": "TD", "mimeType": DRIVE_FOLDER_MIME, "parents": ["f_ml"], "trashed": False},
        "f_global": {"id": "f_global", "name": "Global", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
    }
    for i in range(10):
        tree[f"doc_{i}"] = {
            "id": f"doc_{i}",
            "name": f"td_{i}.pdf",
            "mimeType": "application/pdf",
            "parents": ["f_td"],
            "size": "16",
            "modifiedTime": f"2026-11-{i + 1:02d}T10:00:00Z",
            "trashed": False,
        }

    get_calls: list[str] = []

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            get_calls.append(fileId)
            return _FakeExec(tree[fileId])

        def list(self, *, q: str, **kwargs: Any) -> _FakeExec:
            pid = q.split("'")[1]
            return _FakeExec({"files": [v for v in tree.values() if pid in (v.get("parents") or []) and not v.get("trashed")]})

        def get_media(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            return _FakeExec(b"%PDF-1.4\n%%EOF")

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _DummyCreds())
    conn = EnsoraDriveConnector(cfg, oauth_adapter=oauth, drive_service_factory=lambda _: _Svc(), rate_limiter=RateLimiter(0.0))

    page = conn.list_changes()
    assert len(page.items) == 10
    get_calls.clear()

    for rec in page.items:
        blob = conn.fetch_blob(rec)
        assert blob.content.startswith(b"%PDF-")

    # 10 live file metadata checks + 4 ancestor walks ONLY on the first file = 14 (vs 50 uncached!)
    assert len(get_calls) == 14

    # Security check 1: moving a file into Global is caught immediately on live file metadata
    tree["doc_0"]["parents"] = ["f_global"]
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(page.items[0])

    # Security check 2: trashing a file inside approved folder is caught immediately
    tree["doc_1"]["trashed"] = True
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(page.items[1])

    # Security check 3: moving the parent folder outside root after fresh list_changes is caught
    tree["doc_0"]["parents"] = ["f_td"]
    tree["doc_1"]["trashed"] = False
    conn.list_changes()
    tree["f_td"]["parents"] = ["f_global"]
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(page.items[0])


def test_drive_discover_only_skips_disk_fsync_tracks_api_telemetry_and_skips_in_tree_shortcut_gets(
    tmp_path: Path,
) -> None:
    """discovery_only=True avoids drvpage disk snapshots, reports batch/shortcut telemetry, and shrinks failed batches."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    root_id = DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID

    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "parents": [], "trashed": False},
        "f_1a": {"id": "f_1a", "name": "1A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "f_global": {"id": "f_global", "name": "Global", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "f_sub1": {"id": "f_sub1", "name": "Maths", "mimeType": DRIVE_FOLDER_MIME, "parents": ["f_1a"], "trashed": False},
        "f_sub2": {"id": "f_sub2", "name": "Proba", "mimeType": DRIVE_FOLDER_MIME, "parents": ["f_1a"], "trashed": False},
        "doc_a": {"id": "doc_a", "name": "a.pdf", "mimeType": "application/pdf", "parents": ["f_sub1"], "modifiedTime": "2026-11-01T10:00:00Z", "trashed": False},
        "doc_b": {"id": "doc_b", "name": "b.pdf", "mimeType": "application/pdf", "parents": ["f_sub2"], "modifiedTime": "2026-11-02T10:00:00Z", "trashed": False},
        # In-tree shortcut pointing to already-visited folder f_1a -> should require ZERO files.get calls
        "sc_in": {"id": "sc_in", "name": "Link1A", "mimeType": DRIVE_SHORTCUT_MIME, "parents": ["f_sub1"], "shortcutDetails": {"targetId": "f_1a"}, "trashed": False},
        # Excluded shortcut pointing to Global -> must still be rejected
        "sc_gl": {"id": "sc_gl", "name": "LinkGlobal", "mimeType": DRIVE_SHORTCUT_MIME, "parents": ["f_sub2"], "shortcutDetails": {"targetId": "f_global"}, "trashed": False},
    }

    get_ids: list[str] = []

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            get_ids.append(fileId)
            return _FakeExec(tree[fileId])

        def list(self, *, q: str, **kwargs: Any) -> _FakeExec:
            # Force one batch fallback on multi-parent OR query to verify adaptive fallback counter
            if " or " in q:
                return _FakeExec(RuntimeError("Simulated batch query complexity failure"))
            pid = q.split("'")[1]
            return _FakeExec({"files": [v for v in tree.values() if pid in (v.get("parents") or []) and not v.get("trashed")]})

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _DummyCreds())
    conn = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=lambda _: _Svc(),
        rate_limiter=RateLimiter(0.0),
        batch_folder_queries=True,
    )

    items: list[SourceRecord] = []
    cursor: str | None = None
    while True:
        page = conn.list_changes(scope={"page_size": 1, "discovery_only": True}, cursor=cursor)
        items.extend(page.items)
        if page.complete or not page.next_cursor:
            break
        cursor = page.next_cursor

    assert len(items) == 2
    snap_dir = cfg.config_dir / "state" / "drive_paging"
    assert not snap_dir.exists() or list(snap_dir.glob("*.json")) == []
    assert conn.last_stats["batch_list_calls"] >= 1
    assert conn.last_stats["batch_fallback_count"] >= 1
    assert conn.last_stats["batch_fallback_calls"] >= 1
    # In-tree shortcut to f_1a skipped without files.get; only sc_gl triggered shortcut ancestry check
    assert conn.last_stats["shortcut_ancestry_get_calls"] == 1
    assert conn.last_stats["excluded_shortcuts_skipped"] == 1
    assert "f_1a" not in get_ids


def test_drive_fetch_blobs_concurrent_isolates_clients_per_thread_and_preserves_order(
    tmp_path: Path,
) -> None:
    """fetch_blobs_concurrent uses isolated thread-local clients and handles adaptive 429 pacing."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    root_id = DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID

    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "parents": [], "trashed": False},
        "f_1a": {"id": "f_1a", "name": "1A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
    }
    records: list[SourceRecord] = []
    for i in range(12):
        fid = f"f_{i:02d}"
        tree[fid] = {
            "id": fid,
            "name": f"doc_{i:02d}.pdf",
            "mimeType": "application/pdf",
            "parents": ["f_1a"],
            "size": "20",
            "trashed": False,
        }
        records.append(
            SourceRecord(
                source_kind=SourceKind.ENSORA_DRIVE,
                external_id=f"ensora_drive:{fid}",
                title=f"doc_{i:02d}.pdf",
                mime_type="application/pdf",
                payload={"drive_file_id": fid},
            )
        )

    client_threads: dict[int, set[int]] = {}
    client_lock = threading.Lock()
    client_seq = 0

    def _factory(_creds: Any) -> Any:
        nonlocal client_seq
        with client_lock:
            client_seq += 1
            cid = client_seq

        class _Files:
            def _touch(self) -> None:
                tid = threading.get_ident()
                with client_lock:
                    client_threads.setdefault(cid, set()).add(tid)

            def get(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
                self._touch()
                time.sleep(0.002)
                return _FakeExec(tree[fileId])

            def get_media(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
                self._touch()
                time.sleep(0.003)
                return _FakeExec(f"%PDF-1.4\n%{fileId}\n%%EOF".encode("ascii"))

        class _Svc:
            def files(self) -> _Files:
                return _Files()

        return _Svc()

    limiter = AdaptiveRateLimiter(0.0, sleeper=lambda _: None)
    limiter.notify_rate_limit(retry_after_sec=0.01)
    assert limiter.min_interval_sec > 0.0

    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _DummyCreds())
    conn = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=_factory,
        rate_limiter=AdaptiveRateLimiter(0.0, sleeper=lambda _: None),
    )

    rows = conn.fetch_blobs_concurrent(records, max_workers=4)
    assert len(rows) == 12
    assert [r.external_id for r, _, _ in rows] == [r.external_id for r in records]
    assert all(blob is not None and exc is None for _, blob, exc in rows)
    # Critical thread-safety guarantee: no Drive client was ever shared by >1 thread
    assert max(len(tids) for tids in client_threads.values()) == 1


def test_moodle_and_pamplemousse_pooled_cookie_sessions_reuse_and_invalidate_on_cookie_change(
    tmp_path: Path,
) -> None:
    """MoodleConnector and PamplemousseConnector reuse pooled requests.Session and rebuild on cookie update."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    auth = EnsaiAuthAdapter(cfg)

    from urllib.parse import urlparse

    for src, host, conn_cls in (
        (SourceKind.MOODLE, urlparse(cfg.moodle_base_url).netloc.lower(), MoodleConnector),
        (SourceKind.PAMPLEMOUSSE, urlparse(cfg.pamplemousse_base_url).netloc.lower(), PamplemousseConnector),
    ):
        prof = auth.profile_dir_for(src)
        prof.mkdir(parents=True, exist_ok=True)
        cookie_file = prof / SESSION_COOKIES_FILENAME
        cookie_file.write_text(
            json.dumps({"cookies": [{"name": "SID", "value": "v1", "domain": host, "path": "/"}]}),
            encoding="utf-8",
        )
        conn = conn_cls(cfg, auth_adapter=auth, rate_limiter=RateLimiter(0.0))
        first = conn._get_pooled_cookie_session()
        second = conn._get_pooled_cookie_session()
        assert first is not None and second is not None
        assert first[1] is True
        assert first[0] is second[0]  # Exact same pooled requests.Session reused!

        # Rewrite cookie file with updated value & mtime -> session must invalidate & rebuild
        cookie_file.write_text(
            json.dumps({"cookies": [{"name": "SID", "value": "v2-updated", "domain": host, "path": "/"}]}),
            encoding="utf-8",
        )
        st = cookie_file.stat()
        os.utime(cookie_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        third = conn._get_pooled_cookie_session()
        assert third is not None
        assert third[0] is not first[0]
        assert third[0].cookies.get("SID") == "v2-updated"
        conn.close()

    # Verify MoodleConnector.fetch_blobs_concurrent preserves ordering
    moodle_conn = MoodleConnector(
        cfg,
        auth_adapter=auth,
        blob_fetcher=lambda u: (f"bytes:{u}".encode("utf-8"), u.rsplit("/", 1)[-1], "application/pdf"),
        rate_limiter=RateLimiter(0.0),
    )
    recs = [
        SourceRecord(
            source_kind=SourceKind.MOODLE,
            external_id=f"moodle:res:{i}",
            title=f"chap_{i}.pdf",
            download_url=f"https://foad-moodle.ensai.fr/pluginfile.php/{i}/chap_{i}.pdf",
            payload={"filename": f"chap_{i}.pdf"},
        )
        for i in range(6)
    ]
    batch_res = moodle_conn.fetch_blobs_concurrent(recs, max_workers=4)
    assert [r.external_id for r, _, _ in batch_res] == [r.external_id for r in recs]
    assert all(blob is not None and exc is None for _, blob, exc in batch_res)


def test_entity_resolver_preindexed_exact_equivalence_and_tie_safety() -> None:
    """Pre-indexed AcademicEntityResolver preserves exact tiering, bilingual aliases, overrides, and tie safety."""
    subjects = [
        CanonicalSubject(subject_id="s_ts", title="Séries temporelles", code="ST201", cohort="2A", semester="S3"),
        CanonicalSubject(subject_id="s_ml", title="Apprentissage automatique", code="ML301", cohort="3A", semester="S5"),
        CanonicalSubject(subject_id="s_eco1", title="Économétrie 1", code="EC201", cohort="2A", semester="S3"),
        CanonicalSubject(subject_id="s_eco2", title="Économétrie 2", code="EC202", cohort="2A", semester="S4"),
    ]
    resolver = AcademicEntityResolver(subjects)

    # 1. Bilingual English folder matches French canonical subject
    rec_ml = SourceRecord(
        source_kind=SourceKind.ENSORA_DRIVE,
        external_id="ensora_drive:ml1",
        title="Lab1_Ridge.pdf",
        folder_path="Ensora/3A/Machine Learning/TD",
    )
    res_ml = resolver.resolve_record(rec_ml)
    assert res_ml.canonical_subject_id == "s_ml"
    assert res_ml.needs_review is False
    assert res_ml.confidence >= 0.90

    # 2. Explicit code match
    rec_code = SourceRecord(
        source_kind=SourceKind.MOODLE,
        external_id="moodle:st1",
        title="ST201 - Examen 2025.pdf",
        folder_path="Ensora/2A/S3",
    )
    res_code = resolver.resolve_record(rec_code)
    assert res_code.canonical_subject_id == "s_ts"
    assert res_code.matched_by == "explicit_code"
    assert res_code.needs_review is False

    # 3. Ambiguous tie between Econometrie 1 & Econometrie 2 -> MUST force human review!
    rec_amb = SourceRecord(
        source_kind=SourceKind.ENSORA_DRIVE,
        external_id="ensora_drive:eco_amb",
        title="Partiel_Econometrie.pdf",
        folder_path="Ensora/2A/Econometrie",
    )
    res_amb = resolver.resolve_record(rec_amb)
    assert res_amb.needs_review is True
    assert len(res_amb.candidate_subjects) >= 2

    # 4. Manual override always wins unconditionally
    resolver.manual_store.set_subject_override("ensora_drive:eco_amb", "s_eco2", chapter="Chapter 4")
    res_ov = resolver.resolve_record(rec_amb)
    assert res_ov.canonical_subject_id == "s_eco2"
    assert res_ov.matched_by == "manual_override"
    assert res_ov.needs_review is False
    assert res_ov.chapter == "Chapter 4"


def test_pdf_parser_stdin_pipe_and_registry_version_cache_and_parallel_batch() -> None:
    """Fast Poppler stdin-pipe PDF parser, version-aware registry cache, and parallel batch preserve exact output."""
    pdf_bytes = _build_minimal_pdf(
        [
            "Chapitre 1 Estimation statistique\nSoit $E[X] = mu$ pour les variables aleatoires.\nFigure 1: Convergence",
            "Chapitre 2 Tests d'hypotheses\nColonneA | ColonneB | ColonneC\nVal1 | Val2 | Val3",
        ],
        title="Cours Statistique",
    )
    parser = PdfDocumentParser()
    assert parser.version == "1.1.0"
    parsed = parser.parse(pdf_bytes, filename="stat.pdf", mime_type="application/pdf")
    assert parsed.error is None
    assert len(parsed.blocks) == 2
    assert parsed.blocks[0].locator.page == 1
    assert parsed.blocks[1].locator.page == 2
    assert "E[X] = mu" in parsed.math_expressions
    assert parsed.title == "Cours Statistique"
    assert len(parsed.tables) == 1
    assert len(parsed.figures) == 1

    # Parser-version-aware registry cache hit preserves distinct filename/provenance
    reg = DocumentParserRegistry(max_cache_entries=16)
    first = reg.parse_bytes(pdf_bytes, filename="copy_a.pdf", provenance={"external_id": "doc_a"})
    second = reg.parse_bytes(pdf_bytes, filename="copy_b.pdf", provenance={"external_id": "doc_b"})
    stats = reg.cache_stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 1
    assert second.provenance["external_id"] == "doc_b"
    assert second.metadata["filename"] == "copy_b.pdf"
    assert [b.text for b in first.blocks] == [b.text for b in second.blocks]

    # Parallel batch execution preserves exact order and block content
    batch_items = [
        (_build_minimal_pdf([f"Chapitre {i} Analyse\nContenu page 1 doc {i}"]), f"doc_{i}.pdf", "application/pdf", {"idx": i})
        for i in range(6)
    ]
    reg_par = DocumentParserRegistry(max_cache_entries=32)
    parallel_docs = reg_par.parse_batch_parallel(batch_items, max_workers=3)
    assert len(parallel_docs) == 6
    for i, doc in enumerate(parallel_docs):
        assert doc.error is None
        assert doc.provenance["idx"] == i
        assert f"doc {i}" in doc.blocks[0].text


def test_opt04_runtime_pamplemousse_known_revisions_slices_isolation_and_bypass(tmp_path: Path) -> None:
    """OPT-04 regression (Documentation Codex request):
    - Seeds a real migrated P1 SQLite database via Codex's Runtime & ETL across timetable, catalog, and grades.
    - Verifies `_load_known_record_revisions` returns exact non-null revisions for `pamplemousse` only (source isolated).
    - Verifies `CodexConnectorBridge` injects `known_versions` on incremental runs across all 3 slices,
      omits them on `full=True` and `retry_failed=True`, and emits full academic hierarchy when one grade changes.
    """
    from datetime import datetime, timezone
    from decimal import Decimal
    from backend.app.contracts.sources import (
        CalendarEvent,
        Provenance,
        PublishedGrade,
        SourceKind as CodexSourceKind,
        Subject,
        SyncScope,
        TeachingUnit,
    )
    from backend.app.core.database import migrate, open_database
    from backend.app.core.runtime import Runtime
    from backend.app.core.settings import Settings
    from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge
    from backend.app.modules.ingestion.contracts import ChangePage

    cfg_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    cfg_dir.mkdir()
    data_dir.mkdir()
    settings = Settings(config_dir=cfg_dir, local_data_dir=data_dir)
    db_path = data_dir / "ensora.sqlite3"
    engine = open_database(db_path)
    migrate(engine)
    engine.dispose()

    with Runtime(settings) as runtime:
        pamp_conn_id = runtime.connection_ids[CodexSourceKind.PAMPLEMOUSSE]
        moodle_conn_id = runtime.connection_ids[CodexSourceKind.MOODLE]
        runtime.consent.record(CodexSourceKind.PAMPLEMOUSSE, "read", True)
        runtime.consent.record(CodexSourceKind.MOODLE, "read", True)

        # Seed timetable event, catalog UE + Subject, grade (with revision), and null-revision record
        runtime.pipeline.import_academics(
            pamp_conn_id,
            [
                CalendarEvent(
                    provenance=Provenance(
                        external_id="pamp:edt:1",
                        remote_revision="rev-edt-1",
                        source=CodexSourceKind.PAMPLEMOUSSE,
                        academic_year="2026-2027",
                    ),
                    title="CM Econometrie",
                    starts_at=datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc),
                    ends_at=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
                ),
                TeachingUnit(
                    provenance=Provenance(
                        external_id="pamp:ue:1",
                        remote_revision="rev-ue-1",
                        source=CodexSourceKind.PAMPLEMOUSSE,
                        academic_year="2026-2027",
                    ),
                    title="UE Statistique",
                ),
                Subject(
                    provenance=Provenance(
                        external_id="pamp:subj:1",
                        remote_revision="rev-subj-1",
                        source=CodexSourceKind.PAMPLEMOUSSE,
                        academic_year="2026-2027",
                    ),
                    title="Econometrie",
                    parent_key="pamp:ue:1",
                ),
                PublishedGrade(
                    provenance=Provenance(
                        external_id="pamp:grade:1",
                        remote_revision="rev-grade-1",
                        source=CodexSourceKind.PAMPLEMOUSSE,
                        academic_year="2026-2027",
                    ),
                    title="Partiel Econometrie",
                    parent_key="pamp:subj:1",
                    value=Decimal("15.5"),
                    maximum=Decimal("20"),
                    grade_kind="assessment",
                ),
                Subject(
                    provenance=Provenance(
                        external_id="pamp:subj:nullrev",
                        remote_revision=None,
                        source=CodexSourceKind.PAMPLEMOUSSE,
                        academic_year="2026-2027",
                    ),
                    title="Option Sans Revision",
                ),
            ],
        )
        # Seed a separate Moodle source_record to verify strict source isolation
        runtime.pipeline.import_academics(
            moodle_conn_id,
            [
                Subject(
                    provenance=Provenance(
                        external_id="moodle:subj:isolated",
                        remote_revision="rev-moodle-99",
                        source=CodexSourceKind.MOODLE,
                    ),
                    title="Isolated Moodle Subject",
                )
            ],
        )

    loaded = _load_known_record_revisions(db_path, CodexSourceKind.PAMPLEMOUSSE.value)
    assert loaded == {
        "pamp:edt:1": "rev-edt-1",
        "pamp:ue:1": "rev-ue-1",
        "pamp:subj:1": "rev-subj-1",
        "pamp:grade:1": "rev-grade-1",
    }
    assert "pamp:subj:nullrev" not in loaded
    assert "moodle:subj:isolated" not in loaded

    # Verify CodexConnectorBridge passes known_versions across all 3 slices and bypasses on full/retry_failed
    observed_scopes: list[dict[str, Any]] = []

    class _SpyPampConnector:
        source_kind = SourceKind.PAMPLEMOUSSE
        last_stats: dict[str, Any] = {}

        def list_changes(self, scope: dict[str, Any] | None = None, cursor: str | None = None) -> ChangePage:
            observed_scopes.append(dict(scope or {}))
            return ChangePage(items=(), next_cursor=None, complete=True)

    bridge = CodexConnectorBridge(
        _SpyPampConnector(),  # type: ignore[arg-type]
        known_versions_loader=lambda: _load_known_record_revisions(db_path, CodexSourceKind.PAMPLEMOUSSE.value),
    )

    for slice_name in ("timetable", "grades", "catalog"):
        bridge.list_changes(
            SyncScope(source=CodexSourceKind.PAMPLEMOUSSE, pamplemousse_scope=slice_name),
            cursor=None,
        )
        assert observed_scopes[-1].get("sections") == slice_name
        assert observed_scopes[-1].get("known_versions") == loaded

    bridge.list_changes(SyncScope(source=CodexSourceKind.PAMPLEMOUSSE, full=True), cursor=None)
    assert "known_versions" not in observed_scopes[-1]

    bridge.list_changes(SyncScope(source=CodexSourceKind.PAMPLEMOUSSE, retry_failed=True), cursor=None)
    assert "known_versions" not in observed_scopes[-1]


def test_drive_fetch_blob_preseeded_ancestors_cut_distinct_sibling_folder_walks(tmp_path: Path) -> None:
    """OPT-03 follow-up (Performance Gemini finding): sibling leaf folders sharing verified grandparents stop at first cached ancestor."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    root_id = DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID

    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "parents": [], "trashed": False},
        "f_2a": {"id": "f_2a", "name": "2A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "f_s3": {"id": "f_s3", "name": "S3", "mimeType": DRIVE_FOLDER_MIME, "parents": ["f_2a"], "trashed": False},
        "f_ml": {"id": "f_ml", "name": "ML", "mimeType": DRIVE_FOLDER_MIME, "parents": ["f_s3"], "trashed": False},
    }
    records: list[SourceRecord] = []
    for i in range(25):
        leaf_id = f"leaf_{i:02d}"
        doc_id = f"doc_{i:02d}"
        tree[leaf_id] = {
            "id": leaf_id,
            "name": f"TP_{i:02d}",
            "mimeType": DRIVE_FOLDER_MIME,
            "parents": ["f_ml"],
            "trashed": False,
        }
        tree[doc_id] = {
            "id": doc_id,
            "name": f"tp_{i:02d}.pdf",
            "mimeType": "application/pdf",
            "parents": [leaf_id],
            "size": "16",
            "trashed": False,
        }
        records.append(
            SourceRecord(
                source_kind=SourceKind.ENSORA_DRIVE,
                external_id=f"ensora_drive:{doc_id}",
                title=f"tp_{i:02d}.pdf",
                mime_type="application/pdf",
                payload={"drive_file_id": doc_id},
            )
        )

    get_calls: list[str] = []

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            get_calls.append(fileId)
            return _FakeExec(tree[fileId])

        def get_media(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            return _FakeExec(b"%PDF-1.4\n%%EOF")

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _DummyCreds())
    conn = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=lambda _: _Svc(),
        rate_limiter=RateLimiter(0.0),
    )

    for rec in records:
        conn.fetch_blob(rec)

    # First file: 1 file + 4 parents = 5 calls; remaining 24 sibling leaf folders: 1 file + 1 leaf check each = 48 -> total 53 (vs 125 uncached, -57.6%!)
    assert len(get_calls) == 53


def test_codex_bridge_institutional_auth_fails_closed_without_protected_service_evidence() -> None:
    """Priority 0 auth regression: _to_codex_auth_result(institutional=True) fails closed unless origin_verified and no CAS redirect."""
    from backend.app.contracts.authentication import ConnectionStatus
    from backend.app.modules.ingestion.codex_bridge import _to_codex_auth_result
    from backend.app.modules.ingestion.contracts import AuthState, AuthStatus

    # 1. Missing protected-service probe metadata -> MUST fail closed with service_verification_required
    unverified = _to_codex_auth_result(
        AuthStatus(
            source=SourceKind.MOODLE,
            state=AuthState.AUTHENTICATED,
            authenticated=True,
            detail="unverified session",
            metadata={},
        ),
        institutional=True,
    )
    assert unverified.status == ConnectionStatus.REAUTH_REQUIRED
    assert unverified.reason == "service_verification_required"
    assert unverified.service_access == "unknown"

    # 2. CAS redirect detected despite authenticated flag -> MUST fail closed
    redirected = _to_codex_auth_result(
        AuthStatus(
            source=SourceKind.PAMPLEMOUSSE,
            state=AuthState.AUTHENTICATED,
            authenticated=True,
            detail="cas redirect seen",
            metadata={"origin_verified": True, "cas_redirect_detected": True},
        ),
        institutional=True,
    )
    assert redirected.status == ConnectionStatus.REAUTH_REQUIRED
    assert redirected.reason == "service_verification_required"

    # 3. Positive protected-service verification without CAS redirect -> CONNECTED
    verified = _to_codex_auth_result(
        AuthStatus(
            source=SourceKind.MOODLE,
            state=AuthState.AUTHENTICATED,
            authenticated=True,
            detail="protected resource verified",
            metadata={"origin_verified": True, "cas_redirect_detected": False},
        ),
        institutional=True,
    )
    assert verified.status == ConnectionStatus.CONNECTED
    assert verified.session_state == "active"
    assert verified.service_access == "verified"

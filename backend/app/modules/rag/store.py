"""Derived local SQLite FTS5 + dense vector store for Ensora+ RAG indexing.

Architectural & security invariants:
- Persists ONLY derived retrieval structures (FTS5 index + per-space float32 vectors +
  minimal filter metadata) in an isolated local SQLite file (``rag_index.sqlite3``,
  mode ``0o600``, WAL journal mode). NEVER mutates Codex's P1 ``ensora.sqlite3`` schema.
- Reads P1 ``documents``, ``document_versions``, ``chunks``, ``connections``, and
  ``consents`` strictly read-only, enforcing:
  1. Active read consent on the source connection (latest consent row ``granted = 1``).
  2. Active document status (``coalesce(access_status, 'active') == 'active'``).
  3. Non-null ``active_version_id`` (never indexing failed-download shells).
  4. Approved Ensora Drive cohort scope (``1A``/``2A``/``3A`` only; top-level ``Global``
     excluded via Codex's canonical ``backend.app.core.drive_scope.folder_scope``).
- Keeps embedding vectors strictly partitioned by ``embedding_space_id`` so changing
  embedding models never mixes incompatible vector spaces.
- Re-verifies authorization at query and citation-resolution time so revoked, deleted,
  tombstoned, or Global-excluded documents never leak excerpts even before the next
  index refresh runs.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Mapping, Sequence

from ...core.drive_scope import folder_scope
from .contracts import Citation, EmbeddingProvider, IndexReceipt, QueryScope
from .embeddings import LocalMultilingualHashEmbedder, pack_vector, unpack_vector

RAG_INDEX_SCHEMA_VERSION = "1.1.0"
_INDEX_BATCH_DOCUMENTS = 25
_VECTOR_CACHE_BYTES = 48 * 1024 * 1024


def _is_exam_correction(resource_type: str | None) -> bool:
    return (resource_type or "").lower() == "examination_correction"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _estimate_tokens(text: str) -> int:
    # Fast deterministic token approximation (~4 chars/token for French/English + math/code)
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class IndexableChunk:
    """Normalized chunk unit prepared for FTS5 and dense vector indexing."""

    chunk_id: str
    ordinal: int
    text: str
    section: tuple[str, ...]
    locator: dict[str, str | int]
    block_kind: str = "paragraph"
    structured: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.structured is None:
            object.__setattr__(self, "structured", {})


@dataclass(frozen=True)
class IndexableDocument:
    """Authorized active document version ready for derived RAG indexing."""

    document_id: str
    version_id: str
    sha256: str
    source: str
    title: str
    chunks: tuple[IndexableChunk, ...]
    subject_id: str | None = None
    chapter_id: str | None = None
    academic_year: str | None = None
    resource_type: str | None = None
    folder: str | None = None
    source_url: str | None = None
    extraction_revision: str | None = None


def _read_p1_authorized_state(
    p1_db_path: Path,
    *,
    user_id: str,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Read currently authorized active documents from Codex's P1 SQLite database (read-only).

    Returns:
        Tuple ``(authorized_docs_by_id, allowed_connection_ids)`` where each entry holds
        normalized document + active-version metadata (excluding chunks until needed).
    """
    if not p1_db_path.is_file():
        return {}, set()
    with closing(sqlite3.connect(p1_db_path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not {"connections", "consents", "documents", "document_versions", "chunks"} <= tables:
            return {}, set()

        conn.execute("BEGIN")
        # Determine currently read-consented connections using latest consent row per connection
        consent_rows = conn.execute(
            """
            SELECT c.id AS connection_id, c.source AS source,
                   (
                       SELECT cs.granted
                       FROM consents cs
                       WHERE cs.connection_id = c.id AND cs.purpose = 'read'
                       ORDER BY cs.rowid DESC
                       LIMIT 1
                   ) AS granted
            FROM connections c
            WHERE c.user_id = ?
            """,
            (user_id,),
        ).fetchall()
        allowed_conns: dict[str, str] = {
            str(r["connection_id"]): str(r["source"])
            for r in consent_rows
            if r["granted"] in (1, True)
        }
        if not allowed_conns:
            return {}, set()

        placeholders = ",".join("?" for _ in allowed_conns)
        doc_rows = conn.execute(
            f"""
            SELECT d.id AS document_id,
                   d.connection_id AS connection_id,
                   d.active_version_id AS version_id,
                   d.metadata_json AS metadata_json,
                   v.sha256 AS sha256, v.parser_version, v.status
            FROM documents d
            JOIN document_versions v ON v.id = d.active_version_id
            WHERE d.connection_id IN ({placeholders})
              AND d.active_version_id IS NOT NULL
            """,
            tuple(allowed_conns.keys()),
        ).fetchall()

        authorized: dict[str, dict[str, Any]] = {}
        for row in doc_rows:
            source = allowed_conns[str(row["connection_id"])]
            raw_meta = row["metadata_json"]
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta or {})
            access_status = str(meta.get("access_status") or "active")
            if access_status != "active":
                continue
            prov = meta.get("provenance") or {}
            folder = prov.get("folder")
            if source == "ensora_drive" and folder_scope(folder) != "allowed":
                continue
            override = meta.get("user_override") or {}
            clf = meta.get("classification") or {}
            subject_id = override.get("subject_id") or meta.get("subject_id")
            chapter_id = override.get("chapter_id") or meta.get("chapter_id")
            resource_type = override.get("resource_type") or clf.get("resource_type") or meta.get("resource_kind")
            authorized[str(row["document_id"])] = {
                "document_id": str(row["document_id"]),
                "version_id": str(row["version_id"]),
                "sha256": str(row["sha256"]),
                "extraction_revision": json.dumps([row["parser_version"], row["status"]]),
                "source": source,
                "title": str(meta.get("title") or row["document_id"]),
                "subject_id": str(subject_id) if subject_id else None,
                "chapter_id": str(chapter_id) if chapter_id else None,
                "academic_year": str(prov.get("academic_year")) if prov.get("academic_year") else None,
                "resource_type": str(resource_type) if resource_type else None,
                "folder": str(folder) if folder else None,
                "source_url": str(prov.get("source_url")) if prov.get("source_url") else None,
            }
        return authorized, set(allowed_conns.keys())


def _load_p1_version_chunks(p1_db_path: Path, version_ids: Sequence[str]) -> dict[str, list[IndexableChunk]]:
    """Load persisted P1 chunks for the requested ``version_ids`` strictly read-only."""
    if not version_ids or not p1_db_path.is_file():
        return {}
    out: dict[str, list[IndexableChunk]] = {vid: [] for vid in version_ids}
    with closing(sqlite3.connect(p1_db_path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        # Batch in slices of 400 to stay safely below SQLite's bind parameter limit
        for start in range(0, len(version_ids), 400):
            batch = version_ids[start : start + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = conn.execute(
                f"""
                SELECT id, version_id, ordinal, payload
                FROM chunks
                WHERE version_id IN ({placeholders})
                ORDER BY version_id, ordinal ASC
                """,
                tuple(batch),
            ).fetchall()
            for r in rows:
                payload = json.loads(r["payload"]) if isinstance(r["payload"], str) else (r["payload"] or {})
                text = str(payload.get("text") or "").strip()
                if not text:
                    continue
                raw_sec = payload.get("section") or []
                section = tuple(str(s) for s in raw_sec if s)
                raw_loc = payload.get("locator") or {}
                locator: dict[str, str | int] = {
                    str(k): v for k, v in raw_loc.items() if isinstance(v, (str, int))
                }
                structured = payload.get("structured")
                out[str(r["version_id"])].append(
                    IndexableChunk(
                        chunk_id=str(r["id"]),
                        ordinal=int(r["ordinal"]),
                        text=text,
                        section=section,
                        locator=locator,
                        block_kind=str(payload.get("block_kind") or locator.get("block_kind") or "paragraph"),
                        structured=dict(structured) if isinstance(structured, dict) else {},
                    )
                )
    return out


class LocalRagIndexStore:
    """Derived local SQLite FTS5 + multi-space dense vector index for Ensora+.

    Supports both:
    - Incremental synchronization directly from Codex's P1 SQLite database
      (``sync_from_p1_db``), respecting live read consent, active versions, and
      ``1A/2A/3A`` Drive scope.
    - Direct ``upsert_document()`` / ``remove_document()`` operations for synthetic
      unit tests and offline RAG evaluation harnesses.
    """

    def __init__(
        self,
        index_db_path: Path | str,
        *,
        embedder: EmbeddingProvider | None = None,
        p1_db_path: Path | None = None,
    ) -> None:
        self._path_str = str(index_db_path)
        self._is_memory = self._path_str == ":memory:"
        self._index_path = None if self._is_memory else Path(index_db_path)
        self._p1_db_path = p1_db_path
        self._embedder: EmbeddingProvider = embedder or LocalMultilingualHashEmbedder()
        # Cache decoded vectors by their immutable SQLite payload. Scope and space
        # filtering still run against SQLite for every query and every store instance.
        entry_size = 8 * self._embedder.dimension + 512
        self._decode_vector = lru_cache(maxsize=_VECTOR_CACHE_BYTES // entry_size)(
            lambda blob: memoryview(unpack_vector(blob)).toreadonly()
        )
        self._lock = threading.RLock()
        self._mem_conn: sqlite3.Connection | None = None
        if self._index_path is not None:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def embedder(self) -> EmbeddingProvider:
        """Return the currently active embedding provider."""
        return self._embedder

    @property
    def embedding_space_id(self) -> str:
        """Return the active embedding space identifier."""
        return self._embedder.space_id

    @property
    def p1_db_path(self) -> Path | None:
        return self._p1_db_path

    def _connect(self) -> sqlite3.Connection:
        if self._is_memory:
            if self._mem_conn is None:
                conn = sqlite3.connect(":memory:", check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                self._mem_conn = conn
            return self._mem_conn
        assert self._index_path is not None
        conn = sqlite3.connect(str(self._index_path), timeout=20.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            os.chmod(self._index_path, 0o600)
        except OSError:
            pass
        return conn

    def _release(self, conn: sqlite3.Connection) -> None:
        if not self._is_memory:
            conn.close()

    def close(self) -> None:
        """Close persistent in-memory SQLite connection if open."""
        with self._lock:
            self._decode_vector.cache_clear()
            if self._mem_conn is not None:
                self._mem_conn.close()
                self._mem_conn = None

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS rag_index_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS rag_indexed_documents (
                        document_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        version_id TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        source TEXT NOT NULL,
                        title TEXT NOT NULL,
                        subject_id TEXT,
                        chapter_id TEXT,
                        academic_year TEXT,
                        resource_type TEXT,
                        folder TEXT,
                        source_url TEXT,
                        meta_fingerprint TEXT NOT NULL,
                        indexed_at TEXT NOT NULL,
                        extraction_revision TEXT
                    );

                    CREATE INDEX IF NOT EXISTS ix_rag_docs_user_subject
                        ON rag_indexed_documents(user_id, subject_id);
                    CREATE INDEX IF NOT EXISTS ix_rag_docs_user_year
                        ON rag_indexed_documents(user_id, academic_year);
                    CREATE INDEX IF NOT EXISTS ix_rag_docs_user_type
                        ON rag_indexed_documents(user_id, resource_type);

                    CREATE TABLE IF NOT EXISTS rag_chunks (
                        chunk_id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES rag_indexed_documents(document_id) ON DELETE CASCADE,
                        version_id TEXT NOT NULL,
                        ordinal INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        section_json TEXT NOT NULL,
                        locator_json TEXT NOT NULL,
                        block_kind TEXT NOT NULL,
                        structured_json TEXT NOT NULL,
                        token_estimate INTEGER NOT NULL,
                        content_sha256 TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS ix_rag_chunks_doc_ord
                        ON rag_chunks(document_id, ordinal);

                    CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
                        chunk_id UNINDEXED,
                        document_id UNINDEXED,
                        title,
                        section_text,
                        text,
                        math_text,
                        tokenize = 'unicode61 remove_diacritics 2'
                    );

                    CREATE TABLE IF NOT EXISTS rag_embeddings (
                        chunk_id TEXT NOT NULL REFERENCES rag_chunks(chunk_id) ON DELETE CASCADE,
                        embedding_space_id TEXT NOT NULL,
                        dimension INTEGER NOT NULL,
                        vector_blob BLOB NOT NULL,
                        PRIMARY KEY (chunk_id, embedding_space_id)
                    );

                    CREATE INDEX IF NOT EXISTS ix_rag_emb_space
                        ON rag_embeddings(embedding_space_id);
                    """
                )
                conn.execute("BEGIN IMMEDIATE")
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(rag_indexed_documents)")}
                if "extraction_revision" not in columns:
                    conn.execute("ALTER TABLE rag_indexed_documents ADD COLUMN extraction_revision TEXT")
                conn.execute(
                    "INSERT OR REPLACE INTO rag_index_meta(key, value) VALUES (?, ?)",
                    ("schema_version", RAG_INDEX_SCHEMA_VERSION),
                )
                conn.commit()
            finally:
                self._release(conn)

    @staticmethod
    def _doc_meta_fingerprint(
        *,
        version_id: str,
        sha256: str,
        title: str,
        subject_id: str | None,
        chapter_id: str | None,
        academic_year: str | None,
        resource_type: str | None,
        folder: str | None,
        source_url: str | None,
    ) -> str:
        raw = json.dumps(
            [version_id, sha256, title, subject_id, chapter_id, academic_year, resource_type, folder, source_url],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def upsert_document(
        self,
        doc: IndexableDocument,
        *,
        user_id: str = "e0aa03f9-37fc-455b-97bb-648a35868d19",
    ) -> int:
        """Write a single document atomically and return the indexed chunk count."""
        return self.upsert_documents((doc,), user_id=user_id)

    def upsert_documents(
        self,
        docs: Sequence[IndexableDocument],
        *,
        user_id: str = "e0aa03f9-37fc-455b-97bb-648a35868d19",
    ) -> int:
        """Commit bounded batches of complete documents, preparing vectors before each transaction."""
        indexed_chunks = 0
        for start in range(0, len(docs), _INDEX_BATCH_DOCUMENTS):
            batch = docs[start : start + _INDEX_BATCH_DOCUMENTS]
            prepared = [(doc, *self._prepare_document(doc)) for doc in batch]
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("BEGIN")
                    for doc, meta_fp, vectors in prepared:
                        self._write_document(conn, doc, user_id, meta_fp, vectors)
                    conn.execute(
                        "INSERT OR REPLACE INTO rag_index_meta(key, value) VALUES (?, ?)",
                        ("active_embedding_space_id", self._embedder.space_id),
                    )
                    conn.commit()
                    indexed_chunks += sum(len(doc.chunks) for doc in batch)
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    self._release(conn)
        return indexed_chunks

    def _prepare_document(self, doc: IndexableDocument) -> tuple[str, list[bytes]]:
        if doc.source == "ensora_drive" and folder_scope(doc.folder) != "allowed":
            raise PermissionError(
                "Refusing to index Ensora Drive document outside approved 1A/2A/3A scope."
            )
        meta_fp = self._doc_meta_fingerprint(
            version_id=doc.version_id,
            sha256=doc.sha256,
            title=doc.title,
            subject_id=doc.subject_id,
            chapter_id=doc.chapter_id,
            academic_year=doc.academic_year,
            resource_type=doc.resource_type,
            folder=doc.folder,
            source_url=doc.source_url,
        )

        chunk_texts_for_embed = [
            " ".join(part for part in (doc.title, " / ".join(ch.section), ch.text) if part)
            for ch in doc.chunks
        ]
        vectors = self._embedder.embed_texts(chunk_texts_for_embed) if doc.chunks else []
        if len(vectors) != len(doc.chunks) or any(
            len(vec) != self._embedder.dimension for vec in vectors
        ):
            raise ValueError("Embedding output does not match the document chunks and vector space.")
        return meta_fp, [pack_vector(vec) for vec in vectors]

    def _write_document(
        self, conn: sqlite3.Connection, doc: IndexableDocument, user_id: str,
        meta_fp: str, vectors: Sequence[bytes],
    ) -> None:
        space_id = self._embedder.space_id
        dim = self._embedder.dimension
        # The FTS document ID is unindexed. Avoid scanning existing FTS rows when
        # adding a document for the first time. The row replacement cascades chunks.
        if conn.execute(
            "SELECT 1 FROM rag_indexed_documents WHERE document_id = ?", (doc.document_id,)
        ).fetchone():
            conn.execute("DELETE FROM rag_chunks_fts WHERE document_id = ?", (doc.document_id,))
        conn.execute(
            """
            INSERT OR REPLACE INTO rag_indexed_documents(
                document_id, user_id, version_id, sha256, source, title,
                subject_id, chapter_id, academic_year, resource_type,
                folder, source_url, meta_fingerprint, indexed_at, extraction_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.document_id,
                user_id,
                doc.version_id,
                doc.sha256,
                doc.source,
                doc.title,
                doc.subject_id,
                doc.chapter_id,
                doc.academic_year,
                doc.resource_type,
                doc.folder,
                doc.source_url,
                meta_fp,
                _utc_now_iso(),
                doc.extraction_revision,
            ),
        )

        for ch, vec in zip(doc.chunks, vectors):
            sec_text = " / ".join(ch.section)
            math_exprs = ch.structured.get("math_expressions") or []
            math_text = " ".join(str(m) for m in math_exprs if m)
            c_hash = hashlib.sha256(ch.text.encode("utf-8")).hexdigest()[:24]
            conn.execute(
                """
                INSERT INTO rag_chunks(
                    chunk_id, document_id, version_id, ordinal, text,
                    section_json, locator_json, block_kind, structured_json,
                    token_estimate, content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ch.chunk_id,
                    doc.document_id,
                    doc.version_id,
                    ch.ordinal,
                    ch.text,
                    json.dumps(list(ch.section), ensure_ascii=False),
                    json.dumps(ch.locator, ensure_ascii=False),
                    ch.block_kind,
                    json.dumps(ch.structured, ensure_ascii=False),
                    _estimate_tokens(ch.text),
                    c_hash,
                ),
            )
            conn.execute(
                """
                INSERT INTO rag_chunks_fts(
                    chunk_id, document_id, title, section_text, text, math_text
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ch.chunk_id,
                    doc.document_id,
                    doc.title,
                    sec_text,
                    ch.text,
                    math_text,
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO rag_embeddings(
                    chunk_id, embedding_space_id, dimension, vector_blob
                ) VALUES (?, ?, ?, ?)
                """,
                (ch.chunk_id, space_id, dim, vec),
            )

    def remove_documents(self, document_ids: Sequence[str]) -> int:
        """Delete revoked, tombstoned, or excluded documents from FTS5 and vector tables."""
        if not document_ids:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                removed = 0
                for start in range(0, len(document_ids), 400):
                    batch = document_ids[start : start + 400]
                    ph = ",".join("?" for _ in batch)
                    conn.execute(f"DELETE FROM rag_chunks_fts WHERE document_id IN ({ph})", tuple(batch))
                    cur = conn.execute(
                        f"DELETE FROM rag_indexed_documents WHERE document_id IN ({ph})",
                        tuple(batch),
                    )
                    removed += max(0, cur.rowcount)
                conn.commit()
                self._decode_vector.cache_clear()
                return removed
            except Exception:
                conn.rollback()
                raise
            finally:
                self._release(conn)

    def sync_from_p1_db(
        self,
        p1_db_path: Path | None = None,
        *,
        user_id: str = "e0aa03f9-37fc-455b-97bb-648a35868d19",
    ) -> IndexReceipt:
        """Incrementally synchronize derived RAG index from Codex's P1 SQLite database.

        - Removes any previously indexed document whose source consent was withdrawn,
          which was tombstoned/revoked, or which falls outside approved Drive scope.
        - Reuses unchanged ``(version_id, sha256)`` chunks and embeddings for the active
          ``embedding_space_id`` without re-embedding.
        - Updates metadata-only changes (e.g. a newly confirmed ``subject_id`` or
          ``chapter_id`` from academic reconciliation!) in place without re-embedding.
        - Rebuilds vectors for the active ``embedding_space_id`` if the user switched
          embedding models, never mixing incompatible vector spaces.
        """
        started = time.perf_counter()
        db_path = p1_db_path or self._p1_db_path
        if db_path is None:
            raise ValueError("p1_db_path must be supplied to sync_from_p1_db().")
        self._p1_db_path = db_path

        authorized, _ = _read_p1_authorized_state(db_path, user_id=user_id)
        space_id = self._embedder.space_id
        dim = self._embedder.dimension
        warnings: list[str] = []

        with self._lock:
            conn = self._connect()
            try:
                existing_rows = conn.execute(
                    """
                    SELECT d.document_id, d.version_id, d.sha256, d.title,
                           d.meta_fingerprint, d.extraction_revision,
                           (
                               SELECT COUNT(*)
                               FROM rag_chunks c
                               WHERE c.document_id = d.document_id
                           ) AS chunk_count,
                           (
                               SELECT COUNT(*)
                               FROM rag_chunks c
                               JOIN rag_embeddings e
                                 ON e.chunk_id = c.chunk_id AND e.embedding_space_id = ?
                               WHERE c.document_id = d.document_id
                           ) AS emb_count
                    FROM rag_indexed_documents d
                    WHERE d.user_id = ?
                    """,
                    (space_id, user_id),
                ).fetchall()
                existing: dict[str, sqlite3.Row] = {str(r["document_id"]): r for r in existing_rows}
            finally:
                self._release(conn)

        stale_ids = sorted(set(existing.keys()) - set(authorized.keys()))
        removed_count = self.remove_documents(stale_ids) if stale_ids else 0

        to_full_index: list[dict[str, Any]] = []
        to_meta_update: list[tuple[dict[str, Any], str]] = []
        to_embed_only: list[str] = []
        skipped_unchanged = 0

        for doc_id, meta in authorized.items():
            new_fp = self._doc_meta_fingerprint(
                version_id=meta["version_id"],
                sha256=meta["sha256"],
                title=meta["title"],
                subject_id=meta["subject_id"],
                chapter_id=meta["chapter_id"],
                academic_year=meta["academic_year"],
                resource_type=meta["resource_type"],
                folder=meta["folder"],
                source_url=meta["source_url"],
            )
            prev = existing.get(doc_id)
            if prev is None or any(
                prev[key] != meta[key] for key in ("version_id", "sha256", "title", "extraction_revision")
            ):
                to_full_index.append(meta)
            else:
                needs_meta = str(prev["meta_fingerprint"]) != new_fp
                needs_emb = int(prev["chunk_count"]) > 0 and int(prev["emb_count"]) < int(prev["chunk_count"])
                if needs_meta:
                    to_meta_update.append((meta, new_fp))
                if needs_emb:
                    to_embed_only.append(doc_id)
                if not needs_meta and not needs_emb:
                    skipped_unchanged += 1

        # 1. Fast metadata-only updates (e.g. subject_id reconciled without redownloading/re-embedding)
        if to_meta_update:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("BEGIN")
                    for meta, new_fp in to_meta_update:
                        conn.execute(
                            """
                            UPDATE rag_indexed_documents
                            SET title = ?, subject_id = ?, chapter_id = ?, academic_year = ?,
                                resource_type = ?, folder = ?, source_url = ?, meta_fingerprint = ?,
                                indexed_at = ?
                            WHERE document_id = ?
                            """,
                            (
                                meta["title"],
                                meta["subject_id"],
                                meta["chapter_id"],
                                meta["academic_year"],
                                meta["resource_type"],
                                meta["folder"],
                                meta["source_url"],
                                new_fp,
                                _utc_now_iso(),
                                meta["document_id"],
                            ),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    self._release(conn)

        # 2. Vector-space migration for unchanged chunks when switching embedding model
        indexed_chunks = 0
        if to_embed_only:
            with self._lock:
                conn = self._connect()
                try:
                    ph = ",".join("?" for _ in to_embed_only)
                    chunk_rows = conn.execute(
                        f"""
                        SELECT c.chunk_id, d.title, c.section_json, c.text
                        FROM rag_chunks c
                        JOIN rag_indexed_documents d ON d.document_id = c.document_id
                        WHERE c.document_id IN ({ph})
                        """,
                        tuple(to_embed_only),
                    ).fetchall()
                finally:
                    self._release(conn)
            if chunk_rows:
                embed_inputs = [
                    " ".join(
                        p
                        for p in (
                            str(r["title"]),
                            " / ".join(json.loads(r["section_json"] or "[]")),
                            str(r["text"]),
                        )
                        if p
                    )
                    for r in chunk_rows
                ]
                vecs = self._embedder.embed_texts(embed_inputs)
                with self._lock:
                    conn = self._connect()
                    try:
                        conn.execute("BEGIN")
                        for r, vec in zip(chunk_rows, vecs):
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO rag_embeddings(
                                    chunk_id, embedding_space_id, dimension, vector_blob
                                ) VALUES (?, ?, ?, ?)
                                """,
                                (str(r["chunk_id"]), space_id, dim, pack_vector(vec)),
                            )
                        conn.execute(
                            "INSERT OR REPLACE INTO rag_index_meta(key, value) VALUES (?, ?)",
                            ("active_embedding_space_id", space_id),
                        )
                        conn.commit()
                        indexed_chunks += len(chunk_rows)
                    except Exception:
                        conn.rollback()
                        raise
                    finally:
                        self._release(conn)

        # 3. Full indexing for newly added or version-updated active documents
        for start in range(0, len(to_full_index), _INDEX_BATCH_DOCUMENTS):
            batch = to_full_index[start : start + _INDEX_BATCH_DOCUMENTS]
            version_chunks = _load_p1_version_chunks(
                db_path, [m["version_id"] for m in batch]
            )
            documents = []
            for meta in batch:
                chunks = tuple(version_chunks.get(meta["version_id"], ()))
                doc = IndexableDocument(
                    document_id=meta["document_id"],
                    version_id=meta["version_id"],
                    sha256=meta["sha256"],
                    source=meta["source"],
                    title=meta["title"],
                    chunks=chunks,
                    subject_id=meta["subject_id"],
                    chapter_id=meta["chapter_id"],
                    academic_year=meta["academic_year"],
                    resource_type=meta["resource_type"],
                    folder=meta["folder"],
                    source_url=meta["source_url"],
                    extraction_revision=meta["extraction_revision"],
                )
                documents.append(doc)
            indexed_chunks += self.upsert_documents(documents, user_id=user_id)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO rag_index_meta(key, value) VALUES (?, ?)",
                    ("last_sync_at", _utc_now_iso()),
                )
                conn.commit()
            finally:
                self._release(conn)

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return IndexReceipt(
            indexed_documents=len(authorized) - skipped_unchanged,
            skipped_unchanged=skipped_unchanged,
            removed_documents=removed_count,
            indexed_chunks=indexed_chunks,
            embedding_space_id=space_id,
            embedding_kind=self._embedder.embedding_kind,
            index_version=RAG_INDEX_SCHEMA_VERSION,
            elapsed_ms=elapsed_ms,
            warnings=tuple(warnings),
        )

    def live_scope_revisions(self, scope: QueryScope) -> dict[str, tuple[str, str]] | None:
        """Read live versions and extraction revisions eligible for a retrieval scope."""
        if self._p1_db_path is None:
            return None
        authorized, _ = _read_p1_authorized_state(self._p1_db_path, user_id=scope.user_id)
        return {
            doc_id: (meta["version_id"], meta["extraction_revision"])
            for doc_id, meta in authorized.items()
            if (not scope.subject_id or meta["subject_id"] == scope.subject_id)
            and (not scope.chapter_id or meta["chapter_id"] == scope.chapter_id)
            and (not scope.academic_year or meta["academic_year"] == scope.academic_year)
            and (not scope.resource_types or meta["resource_type"] in scope.resource_types)
            and (not scope.exam_mode or not _is_exam_correction(meta["resource_type"]))
        }

    def live_authorized_versions(self, *, user_id: str) -> dict[str, str] | None:
        """Read user-authorized active versions, or return None for standalone fixtures."""
        revisions = self.live_scope_revisions(QueryScope(user_id=user_id))
        if revisions is None:
            return None
        return {document_id: revision[0] for document_id, revision in revisions.items()}

    def live_authorized_document_ids(self, *, user_id: str) -> set[str] | None:
        """Read user-authorized active document IDs."""
        versions = self.live_authorized_versions(user_id=user_id)
        return set(versions) if versions is not None else None

    def active_version_map(self, *, user_id: str) -> dict[str, str]:
        """Return ``{document_id: active_version_id}`` for currently authorized documents.

        Reads live P1 state when bound to a P1 SQLite DB, or falls back to indexed
        documents in standalone/in-memory fixture mode. Used for artifact staleness
        and live source-revocation checks.
        """
        if self._p1_db_path is not None:
            authorized, _ = _read_p1_authorized_state(self._p1_db_path, user_id=user_id)
            return {doc_id: str(meta["version_id"]) for doc_id, meta in authorized.items()}
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT document_id, version_id FROM rag_indexed_documents WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
            finally:
                self._release(conn)
        return {str(r["document_id"]): str(r["version_id"]) for r in rows}

    def _scope_where_clause(
        self,
        scope: QueryScope,
        *,
        allowed_doc_ids: set[str] | None,
    ) -> tuple[str, list[Any]]:
        clauses = ["d.user_id = ?"]
        params: list[Any] = [scope.user_id]

        if allowed_doc_ids is not None:
            if not allowed_doc_ids:
                return "1 = 0", []
            ph = ",".join("?" for _ in allowed_doc_ids)
            clauses.append(f"d.document_id IN ({ph})")
            params.extend(sorted(allowed_doc_ids))
        if scope.document_ids:
            ph = ",".join("?" for _ in scope.document_ids)
            clauses.append(f"d.document_id IN ({ph})")
            params.extend(scope.document_ids)
        if scope.subject_id:
            clauses.append("d.subject_id = ?")
            params.append(scope.subject_id)
        if scope.chapter_id:
            clauses.append("d.chapter_id = ?")
            params.append(scope.chapter_id)
        if scope.academic_year:
            clauses.append("d.academic_year = ?")
            params.append(scope.academic_year)
        if scope.resource_types:
            ph = ",".join("?" for _ in scope.resource_types)
            clauses.append(f"d.resource_type IN ({ph})")
            params.extend(scope.resource_types)
        if scope.sources:
            ph = ",".join("?" for _ in scope.sources)
            clauses.append(f"d.source IN ({ph})")
            params.extend(scope.sources)
        if scope.exam_mode:
            # Exam mode strictly prohibits disclosing solutions or examination corrections
            clauses.append("LOWER(COALESCE(d.resource_type, '')) != 'examination_correction'")
        return " AND ".join(clauses), params

    def search_fts5(
        self,
        fts_query: str,
        scope: QueryScope,
        *,
        allowed_doc_ids: set[str] | None,
        limit: int = 30,
    ) -> list[tuple[sqlite3.Row, float]]:
        """Execute a pre-filtered SQLite FTS5 BM25 query.

        Returns rows paired with a positive normalized BM25 relevance score
        (higher is better; SQLite's raw ``bm25()`` is negative).
        """
        if not fts_query.strip():
            return []
        where_sql, params = self._scope_where_clause(scope, allowed_doc_ids=allowed_doc_ids)
        if where_sql == "1 = 0":
            return []
        sql = f"""
            SELECT c.chunk_id, c.document_id, c.version_id, c.ordinal, c.text,
                   c.section_json, c.locator_json, c.block_kind, c.structured_json,
                   d.title, d.source, d.subject_id, d.chapter_id, d.academic_year,
                   d.resource_type, d.source_url, d.extraction_revision,
                   bm25(rag_chunks_fts, 0.0, 0.0, 3.0, 2.0, 1.0, 2.5) AS raw_bm25
            FROM rag_chunks_fts
            JOIN rag_chunks c ON c.chunk_id = rag_chunks_fts.chunk_id
            JOIN rag_indexed_documents d ON d.document_id = c.document_id
            WHERE rag_chunks_fts MATCH ? AND ({where_sql})
            ORDER BY raw_bm25 ASC, c.chunk_id ASC
            LIMIT ?
        """
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, (fts_query, *params, int(limit))).fetchall()
            except sqlite3.OperationalError:
                return []
            finally:
                self._release(conn)
        out: list[tuple[sqlite3.Row, float]] = []
        for r in rows:
            # Convert negative SQLite BM25 rank into a bounded positive score in (0, 1]
            raw = float(r["raw_bm25"])
            pos = max(0.0, -raw)
            norm = pos / (1.0 + pos) if pos > 0 else 0.001
            out.append((r, norm))
        return out

    def load_scoped_vectors(
        self,
        scope: QueryScope,
        *,
        allowed_doc_ids: set[str] | None,
    ) -> list[tuple[sqlite3.Row, Any]]:
        """Load pre-filtered chunk rows and float32 vectors for the active embedding space."""
        where_sql, params = self._scope_where_clause(scope, allowed_doc_ids=allowed_doc_ids)
        if where_sql == "1 = 0":
            return []
        sql = f"""
            SELECT c.chunk_id, c.document_id, c.version_id, c.ordinal, c.text,
                   c.section_json, c.locator_json, c.block_kind, c.structured_json,
                   d.title, d.source, d.subject_id, d.chapter_id, d.academic_year,
                   d.resource_type, d.source_url, d.extraction_revision, e.vector_blob
            FROM rag_chunks c
            JOIN rag_indexed_documents d ON d.document_id = c.document_id
            JOIN rag_embeddings e
              ON e.chunk_id = c.chunk_id AND e.embedding_space_id = ?
            WHERE {where_sql}
            ORDER BY c.chunk_id ASC
        """
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, (self._embedder.space_id, *params)).fetchall()
            finally:
                self._release(conn)
        return [
            (row, self._decode_vector(row["vector_blob"]))
            for row in rows
            if len(row["vector_blob"]) == 4 * self._embedder.dimension
        ]

    def load_neighbor_chunks(
        self,
        document_id: str,
        ordinals: Sequence[int],
    ) -> list[sqlite3.Row]:
        """Fetch adjacent chunk rows by ``(document_id, ordinal)`` for proof/code expansion."""
        if not ordinals:
            return []
        ph = ",".join("?" for _ in ordinals)
        sql = f"""
            SELECT c.chunk_id, c.document_id, c.version_id, c.ordinal, c.text,
                   c.section_json, c.locator_json, c.block_kind, c.structured_json,
                   d.title, d.source, d.subject_id, d.chapter_id, d.academic_year,
                   d.resource_type, d.source_url, d.extraction_revision
            FROM rag_chunks c
            JOIN rag_indexed_documents d ON d.document_id = c.document_id
            WHERE c.document_id = ? AND c.ordinal IN ({ph})
            ORDER BY c.ordinal ASC
        """
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute(sql, (document_id, *[int(o) for o in ordinals])).fetchall()
            finally:
                self._release(conn)

    def list_document_chunks_ordered(
        self,
        scope: QueryScope,
        *,
        allowed_doc_ids: set[str] | None,
        max_chunks: int = 200,
    ) -> list[sqlite3.Row]:
        """Enumerate ordered chunks across all scoped documents for full-course summaries/sheets."""
        where_sql, params = self._scope_where_clause(scope, allowed_doc_ids=allowed_doc_ids)
        if where_sql == "1 = 0":
            return []
        sql = f"""
            SELECT c.chunk_id, c.document_id, c.version_id, c.ordinal, c.text,
                   c.section_json, c.locator_json, c.block_kind, c.structured_json,
                   d.title, d.source, d.subject_id, d.chapter_id, d.academic_year,
                   d.resource_type, d.source_url, d.extraction_revision
            FROM rag_chunks c
            JOIN rag_indexed_documents d ON d.document_id = c.document_id
            WHERE {where_sql}
            ORDER BY d.document_id ASC, c.ordinal ASC
            LIMIT ?
        """
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute(sql, (*params, int(max_chunks))).fetchall()
            finally:
                self._release(conn)

    def resolve_citation(
        self,
        chunk_id: str,
        *,
        user_id: str = "e0aa03f9-37fc-455b-97bb-648a35868d19",
        citation_id: str | None = None,
        exam_mode: bool = False,
    ) -> Citation | None:
        """Resolve a persisted chunk citation with live authorization, active-version, and exam-mode guards.

        If the document was revoked, tombstoned, excluded, or superseded by a newer active
        version before reindex — or if ``exam_mode=True`` and the cited resource is an
        ``examination_correction`` — returns ``Citation(..., excerpt="", revoked=True)``
        rather than leaking source text.
        """
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT c.chunk_id, c.document_id, c.version_id, c.text, c.locator_json,
                           d.title, d.source, d.subject_id, d.academic_year, d.resource_type,
                           d.folder, d.source_url, d.extraction_revision
                    FROM rag_chunks c
                    JOIN rag_indexed_documents d ON d.document_id = c.document_id
                    WHERE c.chunk_id = ? AND d.user_id = ?
                    """,
                    (chunk_id, user_id),
                ).fetchone()
            finally:
                self._release(conn)
        if row is None:
            return None

        doc_id = str(row["document_id"])
        row_version_id = str(row["version_id"])
        cid = citation_id or f"cite:{chunk_id}"
        locator = json.loads(row["locator_json"] or "{}")

        # Re-verify live P1 authorization & active version before revealing any excerpt
        live_meta = row
        missing_live_doc = False
        if self._p1_db_path is not None:
            authorized, _ = _read_p1_authorized_state(self._p1_db_path, user_id=user_id)
            live_meta = authorized.get(doc_id) or row
            missing_live_doc = doc_id not in authorized
        is_revoked_or_superseded = (
            missing_live_doc or live_meta["version_id"] != row_version_id
            or live_meta["extraction_revision"] != row["extraction_revision"]
            or (str(row["source"]) == "ensora_drive" and folder_scope(row["folder"]) != "allowed")
        )
        is_exam_blocked = exam_mode and (
            _is_exam_correction(live_meta["resource_type"]) or _is_exam_correction(row["resource_type"])
        )
        if is_revoked_or_superseded or is_exam_blocked:
            return Citation(
                citation_id=cid,
                document_id=doc_id,
                version_id=row_version_id,
                chunk_id=str(row["chunk_id"]),
                title="Unavailable during exam" if is_exam_blocked else "Unavailable source",
                locator={},
                excerpt="",
                source_url=None,
                revoked=True,
            )

        excerpt = " ".join(str(row["text"]).split())
        if len(excerpt) > 420:
            excerpt = excerpt[:419].rstrip() + "…"
        return Citation(
            citation_id=cid,
            document_id=doc_id,
            version_id=str(row["version_id"]),
            chunk_id=str(row["chunk_id"]),
            title=str(row["title"]),
            locator=locator,
            excerpt=excerpt,
            source_url=str(row["source_url"]) if row["source_url"] else None,
            subject_id=str(row["subject_id"]) if row["subject_id"] else None,
            academic_year=str(row["academic_year"]) if row["academic_year"] else None,
            resource_type=str(row["resource_type"]) if row["resource_type"] else None,
            revoked=False,
        )

    def stats(self, *, user_id: str = "e0aa03f9-37fc-455b-97bb-648a35868d19") -> dict[str, Any]:
        """Return privacy-safe aggregate counts for the derived RAG index."""
        with self._lock:
            conn = self._connect()
            try:
                doc_count = conn.execute(
                    "SELECT COUNT(*) FROM rag_indexed_documents WHERE user_id = ?",
                    (user_id,),
                ).fetchone()[0]
                chunk_count = conn.execute(
                    """
                    SELECT COUNT(*) FROM rag_chunks c
                    JOIN rag_indexed_documents d ON d.document_id = c.document_id
                    WHERE d.user_id = ?
                    """,
                    (user_id,),
                ).fetchone()[0]
                emb_count = conn.execute(
                    """
                    SELECT COUNT(*) FROM rag_embeddings e
                    JOIN rag_chunks c ON c.chunk_id = e.chunk_id
                    JOIN rag_indexed_documents d ON d.document_id = c.document_id
                    WHERE d.user_id = ? AND e.embedding_space_id = ?
                    """,
                    (user_id, self._embedder.space_id),
                ).fetchone()[0]
            finally:
                self._release(conn)
        return {
            "indexed_documents": int(doc_count),
            "indexed_chunks": int(chunk_count),
            "indexed_embeddings_active_space": int(emb_count),
            "embedding_space_id": self._embedder.space_id,
            "embedding_kind": self._embedder.embedding_kind,
            "schema_version": RAG_INDEX_SCHEMA_VERSION,
        }

    def document_index_status(
        self,
        *,
        user_id: str = "e0aa03f9-37fc-455b-97bb-648a35868d19",
        include_documents: bool = True,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Report explicit download/parse/index lifecycle states per document (privacy-safe).

        Distinguishes downloaded, parsed, partial, failed, unsupported, pending_index,
        stale, indexed, excluded_scope, and revoked documents without leaking paths or text.
        """
        counts: dict[str, int] = {
            "indexed": 0,
            "pending_index": 0,
            "stale": 0,
            "downloaded_unparsed": 0,
            "parsed_partial": 0,
            "parse_failed": 0,
            "unsupported": 0,
            "excluded_scope": 0,
            "revoked": 0,
        }
        items: list[dict[str, Any]] = []
        base_stats = self.stats(user_id=user_id)

        if self._p1_db_path is None or not self._p1_db_path.is_file():
            return {
                "p1_connected": False,
                "summary": counts,
                "index_stats": base_stats,
                "documents": items,
            }

        with self._lock:
            conn = self._connect()
            try:
                idx_rows = conn.execute(
                    """
                    SELECT document_id, version_id, extraction_revision,
                           (SELECT COUNT(*) FROM rag_chunks c WHERE c.document_id = d.document_id) AS indexed_chunks
                    FROM rag_indexed_documents d
                    WHERE d.user_id = ?
                    """,
                    (user_id,),
                ).fetchall()
                indexed_map = {str(r["document_id"]): dict(r) for r in idx_rows}
            finally:
                self._release(conn)

        with closing(sqlite3.connect(self._p1_db_path.as_uri() + "?mode=ro", uri=True)) as p1:
            p1.row_factory = sqlite3.Row
            tables = {
                row[0]
                for row in p1.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            if not {"connections", "consents", "documents", "document_versions", "chunks"} <= tables:
                return {
                    "p1_connected": False,
                    "summary": counts,
                    "index_stats": base_stats,
                    "documents": items,
                }
            consent_rows = p1.execute(
                """
                SELECT c.id AS connection_id, c.source AS source,
                       (
                           SELECT cs.granted
                           FROM consents cs
                           WHERE cs.connection_id = c.id AND cs.purpose = 'read'
                           ORDER BY cs.rowid DESC
                           LIMIT 1
                       ) AS granted
                FROM connections c
                WHERE c.user_id = ?
                """,
                (user_id,),
            ).fetchall()
            conn_info = {
                str(r["connection_id"]): (str(r["source"]), r["granted"] in (1, True))
                for r in consent_rows
            }
            doc_rows = p1.execute(
                """
                SELECT d.id AS document_id,
                       d.connection_id AS connection_id,
                       d.active_version_id AS version_id,
                       d.metadata_json AS metadata_json,
                       v.parser_version AS parser_version,
                       v.status AS parse_status,
                       (SELECT COUNT(*) FROM chunks ch WHERE ch.version_id = d.active_version_id) AS p1_chunks
                FROM documents d
                LEFT JOIN document_versions v ON v.id = d.active_version_id
                ORDER BY d.rowid DESC
                """,
            ).fetchall()

        for row in doc_rows:
            cid = str(row["connection_id"])
            if cid not in conn_info:
                continue
            source, read_granted = conn_info[cid]
            raw_meta = row["metadata_json"]
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta or {})
            access_status = str(meta.get("access_status") or "active")
            prov = meta.get("provenance") or {}
            folder = prov.get("folder")
            parse_status = str(row["parse_status"] or "pending").lower()
            p1_chunks = int(row["p1_chunks"] or 0)
            doc_id = str(row["document_id"])
            ver_id = str(row["version_id"]) if row["version_id"] else None
            ext_rev = json.dumps([row["parser_version"], row["parse_status"]]) if ver_id else None
            idx_row = indexed_map.get(doc_id)

            if not read_granted or access_status != "active":
                state = "revoked"
            elif source == "ensora_drive" and folder_scope(folder) != "allowed":
                state = "excluded_scope"
            elif ver_id is None:
                state = "downloaded_unparsed"
            elif parse_status == "unsupported":
                state = "unsupported"
            elif parse_status in ("failed", "error"):
                state = "parse_failed"
            elif p1_chunks == 0:
                state = "downloaded_unparsed"
            elif idx_row is None:
                state = "parsed_partial" if parse_status == "partial" else "pending_index"
            elif idx_row["version_id"] != ver_id or idx_row["extraction_revision"] != ext_rev:
                state = "stale"
            else:
                state = "indexed"

            counts[state] = counts.get(state, 0) + 1
            if include_documents and len(items) < max(1, int(limit)):
                override = meta.get("user_override") or {}
                clf = meta.get("classification") or {}
                items.append(
                    {
                        "document_id": doc_id,
                        "version_id": ver_id,
                        "title": "Unavailable source" if state in ("revoked", "excluded_scope") else str(meta.get("title") or doc_id),
                        "source": source,
                        "subject_id": None if state in ("revoked", "excluded_scope") else (override.get("subject_id") or meta.get("subject_id")),
                        "resource_type": None if state in ("revoked", "excluded_scope") else (override.get("resource_type") or clf.get("resource_type") or meta.get("resource_kind")),
                        "parse_status": parse_status,
                        "p1_chunks": p1_chunks,
                        "indexed_chunks": int(idx_row["indexed_chunks"]) if idx_row else 0,
                        "index_state": state,
                    }
                )

        return {
            "p1_connected": True,
            "summary": counts,
            "index_stats": base_stats,
            "documents": items,
        }

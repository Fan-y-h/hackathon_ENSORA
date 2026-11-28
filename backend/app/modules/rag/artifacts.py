"""Persistent versioned RAG artifact store and strict P3 DTO export adapters.

Stores generated summaries, revision sheets, quizzes, flashcards, exercises, concept maps,
and study recommendations inside the isolated derived local SQLite database
(``rag_index.sqlite3``, mode ``0o600``, WAL journal mode) without touching Codex's P1
``ensora.sqlite3`` schema.

Invariants:
- **Reproducible cache key**: Deterministic SHA-256 digest of scope, generation parameters,
  ``source_snapshot_hash``, and explicit provider/model overrides.
- **Immutable versioning & user-edit preservation**: Regenerations and manual student edits
  always create a new sibling version linked via ``parent_artifact_id``; prior versions and
  provenance metadata are never overwritten.
- **Live staleness & revocation enforcement**: Loading any stored artifact against a
  ``LocalRagIndexStore`` checks every cited ``document_id`` / ``version_id`` against live
  authorized P1 versions, withholding derived content when evidence becomes unavailable.
- **Strict P3 DTO compatibility**: Export helpers produce exact ``extra="forbid"`` payloads
  compatible with P3 ``DeckCreate`` / ``CardCreate``, ``QuizCreate`` / ``QuestionIn``, and
  ``NoteCreate`` schemas from ``origin/Fan-y-h@479e09a``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Mapping
import uuid

from .contracts import (
    ArtifactGenerationSpec,
    ArtifactKind,
    Citation,
    EvidenceStatus,
    GeneratedArtifact,
)
from .store import LocalRagIndexStore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def compute_artifact_cache_key(
    spec: ArtifactGenerationSpec,
    *,
    source_snapshot_hash: str,
    embedding_space_id: str,
    ai_disabled: bool = False,
) -> str:
    """Compute a deterministic user-scoped SHA-256 cache key for a generated artifact."""
    payload = {
        "user_id": spec.scope.user_id,
        "kind": spec.kind,
        "topic": spec.topic.strip(),
        "difficulty": spec.difficulty,
        "item_count": int(spec.item_count),
        "detail_level": spec.detail_level,
        "user_instructions": spec.user_instructions.strip(),
        "subject_id": spec.scope.subject_id,
        "chapter_id": spec.scope.chapter_id,
        "concept_ids": list(spec.scope.concept_ids),
        "document_ids": list(spec.scope.document_ids),
        "academic_year": spec.scope.academic_year,
        "resource_types": list(spec.scope.resource_types),
        "sources": list(spec.scope.sources),
        "locale": spec.scope.locale,
        "exam_mode": bool(spec.scope.exam_mode),
        "provider_override": spec.provider_override,
        "model_override": spec.model_override,
        "ai_disabled": bool(ai_disabled),
        "embedding_space_id": embedding_space_id,
        "source_snapshot_hash": source_snapshot_hash,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredArtifactRecord:
    """Versioned persisted RAG artifact with live staleness and revocation status."""

    artifact_id: str
    user_id: str
    version: int
    parent_artifact_id: str | None
    cache_key: str
    artifact: GeneratedArtifact
    user_edited: bool
    stale: bool
    stale_reasons: tuple[str, ...]
    created_at: str
    updated_at: str
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "user_id": self.user_id,
            "version": self.version,
            "parent_artifact_id": self.parent_artifact_id,
            "cache_key": self.cache_key,
            "user_edited": self.user_edited,
            "stale": self.stale,
            "stale_reasons": list(self.stale_reasons),
            "cache_hit": self.cache_hit,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            **self.artifact.to_dict(),
        }


def _citation_from_dict(raw: Mapping[str, Any]) -> Citation:
    return Citation(
        citation_id=str(raw.get("citation_id") or ""),
        document_id=str(raw.get("document_id") or ""),
        version_id=str(raw.get("version_id") or ""),
        chunk_id=str(raw.get("chunk_id") or ""),
        title=str(raw.get("title") or ""),
        locator=dict(raw.get("locator") or {}),
        excerpt=str(raw.get("excerpt") or ""),
        source_url=str(raw["source_url"]) if raw.get("source_url") else None,
        subject_id=str(raw["subject_id"]) if raw.get("subject_id") else None,
        academic_year=str(raw["academic_year"]) if raw.get("academic_year") else None,
        resource_type=str(raw["resource_type"]) if raw.get("resource_type") else None,
        revoked=bool(raw.get("revoked", False)),
    )


class LocalArtifactStore:
    """Versioned local SQLite store for RAG-generated study artifacts."""

    def __init__(self, db_path: Path | str, *, store: LocalRagIndexStore | None = None) -> None:
        self._store = store
        self._path_str = str(db_path)
        self._is_memory = self._path_str == ":memory:"
        self._db_path = None if self._is_memory else Path(db_path)
        self._lock = threading.RLock()
        self._mem_conn: sqlite3.Connection | None = None
        if self._db_path is not None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._is_memory:
            if self._mem_conn is None:
                conn = sqlite3.connect(":memory:", check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                self._mem_conn = conn
            return self._mem_conn
        assert self._db_path is not None
        conn = sqlite3.connect(str(self._db_path), timeout=20.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            os.chmod(self._db_path, 0o600)
        except OSError:
            pass
        return conn

    def _release(self, conn: sqlite3.Connection) -> None:
        if not self._is_memory:
            conn.close()

    def close(self) -> None:
        """Close persistent in-memory SQLite connection if open."""
        with self._lock:
            if self._mem_conn is not None:
                self._mem_conn.close()
                self._mem_conn = None

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS rag_generated_artifacts (
                        artifact_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        artifact_kind TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        parent_artifact_id TEXT,
                        cache_key TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content_markdown TEXT NOT NULL,
                        structured_payload_json TEXT NOT NULL,
                        citations_json TEXT NOT NULL,
                        evidence_status TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        embedding_space_id TEXT NOT NULL,
                        source_snapshot_hash TEXT NOT NULL,
                        coverage_ratio REAL NOT NULL,
                        omitted_sections_json TEXT NOT NULL,
                        warnings_json TEXT NOT NULL,
                        ai_generated INTEGER NOT NULL,
                        user_edited INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS ix_rag_artifacts_cache
                        ON rag_generated_artifacts(user_id, cache_key, version DESC);
                    CREATE INDEX IF NOT EXISTS ix_rag_artifacts_kind
                        ON rag_generated_artifacts(user_id, artifact_kind, updated_at DESC);
                    """
                )
                conn.commit()
            finally:
                self._release(conn)

    def _row_to_record(
        self,
        row: sqlite3.Row,
        *,
        store: LocalRagIndexStore | None = None,
        store_user_id: str | None = None,
        cache_hit: bool = False,
    ) -> StoredArtifactRecord:
        raw_citations = json.loads(row["citations_json"] or "[]")
        citations = [_citation_from_dict(c) for c in raw_citations if isinstance(c, dict)]
        stale_reasons: list[str] = []

        store = store if store is not None else self._store
        if citations:
            check_uid = store_user_id or str(row["user_id"])
            active_versions = store.active_version_map(user_id=check_uid) if store is not None else {}
            checked: list[Citation] = []
            for c in citations:
                current_ver = active_versions.get(c.document_id)
                if c.revoked or current_ver != c.version_id:
                    if store is None:
                        reason = "source_verification_unavailable"
                    elif current_ver is None or c.revoked:
                        reason = "cited_source_revoked"
                    else:
                        reason = "cited_source_updated"
                    if reason not in stale_reasons:
                        stale_reasons.append(reason)
                    checked.append(
                        replace(
                            c,
                            title="Unavailable source",
                            locator={},
                            excerpt="",
                            source_url=None,
                            subject_id=None,
                            academic_year=None,
                            resource_type=None,
                            revoked=True,
                        )
                    )
                else:
                    checked.append(c)
            citations = checked

        # Generated fields can paraphrase any evidence, so masking only excerpts is insufficient.
        withheld = bool(stale_reasons)
        artifact = GeneratedArtifact(
            artifact_kind=row["artifact_kind"],  # type: ignore[arg-type]
            title="Unavailable artifact" if withheld else str(row["title"]),
            content_markdown="" if withheld else str(row["content_markdown"]),
            structured_payload={} if withheld else json.loads(row["structured_payload_json"] or "{}"),
            citations=tuple(citations),
            evidence_status="insufficient" if withheld else row["evidence_status"],  # type: ignore[arg-type]
            provider=str(row["provider"]),
            model=str(row["model"]),
            embedding_space_id=str(row["embedding_space_id"]),
            source_snapshot_hash=str(row["source_snapshot_hash"]),
            coverage_ratio=0.0 if withheld else float(row["coverage_ratio"]),
            omitted_sections=() if withheld else tuple(json.loads(row["omitted_sections_json"] or "[]")),
            warnings=tuple(json.loads(row["warnings_json"] or "[]")),
            ai_generated=bool(row["ai_generated"]),
        )
        return StoredArtifactRecord(
            artifact_id=str(row["artifact_id"]),
            user_id=str(row["user_id"]),
            version=int(row["version"]),
            parent_artifact_id=str(row["parent_artifact_id"]) if row["parent_artifact_id"] else None,
            cache_key=str(row["cache_key"]),
            artifact=artifact,
            user_edited=bool(row["user_edited"]),
            stale=bool(stale_reasons),
            stale_reasons=tuple(stale_reasons),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            cache_hit=cache_hit,
        )

    def save(
        self,
        artifact: GeneratedArtifact,
        *,
        user_id: str,
        cache_key: str,
        parent_artifact_id: str | None = None,
        user_edited: bool = False,
    ) -> StoredArtifactRecord:
        """Persist a new immutable artifact version and return its ``StoredArtifactRecord``."""
        now = _utc_now_iso()
        artifact_id = str(uuid.uuid4())
        with self._lock:
            conn = self._connect()
            try:
                prev_row = conn.execute(
                    """
                    SELECT artifact_id, version
                    FROM rag_generated_artifacts
                    WHERE user_id = ? AND cache_key = ?
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                    (user_id, cache_key),
                ).fetchone()
                next_version = (int(prev_row["version"]) + 1) if prev_row is not None else 1
                resolved_parent = parent_artifact_id or (
                    str(prev_row["artifact_id"]) if prev_row is not None else None
                )
                conn.execute(
                    """
                    INSERT INTO rag_generated_artifacts(
                        artifact_id, user_id, artifact_kind, version, parent_artifact_id,
                        cache_key, title, content_markdown, structured_payload_json,
                        citations_json, evidence_status, provider, model, embedding_space_id,
                        source_snapshot_hash, coverage_ratio, omitted_sections_json,
                        warnings_json, ai_generated, user_edited, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        user_id,
                        artifact.artifact_kind,
                        next_version,
                        resolved_parent,
                        cache_key,
                        artifact.title,
                        artifact.content_markdown,
                        json.dumps(artifact.structured_payload, ensure_ascii=False),
                        json.dumps([c.to_dict() for c in artifact.citations], ensure_ascii=False),
                        artifact.evidence_status,
                        artifact.provider,
                        artifact.model,
                        artifact.embedding_space_id,
                        artifact.source_snapshot_hash,
                        float(artifact.coverage_ratio),
                        json.dumps(list(artifact.omitted_sections), ensure_ascii=False),
                        json.dumps(list(artifact.warnings), ensure_ascii=False),
                        1 if artifact.ai_generated else 0,
                        1 if user_edited else 0,
                        now,
                        now,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM rag_generated_artifacts WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()
            finally:
                self._release(conn)
        assert row is not None
        return self._row_to_record(row, store=None, cache_hit=False)

    def get_latest_by_cache_key(
        self,
        cache_key: str,
        *,
        user_id: str,
        store: LocalRagIndexStore | None = None,
        store_user_id: str | None = None,
    ) -> StoredArtifactRecord | None:
        """Return the latest persisted artifact for ``(user_id, cache_key)`` if present."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT *
                    FROM rag_generated_artifacts
                    WHERE user_id = ? AND cache_key = ?
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                    (user_id, cache_key),
                ).fetchone()
            finally:
                self._release(conn)
        if row is None:
            return None
        return self._row_to_record(row, store=store, store_user_id=store_user_id, cache_hit=True)

    def get(
        self,
        artifact_id: str,
        *,
        user_id: str,
        store: LocalRagIndexStore | None = None,
        store_user_id: str | None = None,
    ) -> StoredArtifactRecord | None:
        """Fetch one persisted artifact version with live staleness/revocation checks."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM rag_generated_artifacts WHERE artifact_id = ? AND user_id = ?",
                    (artifact_id, user_id),
                ).fetchone()
            finally:
                self._release(conn)
        if row is None:
            return None
        return self._row_to_record(row, store=store, store_user_id=store_user_id, cache_hit=False)

    def list_artifacts(
        self,
        *,
        user_id: str,
        artifact_kind: ArtifactKind | None = None,
        limit: int = 50,
        store: LocalRagIndexStore | None = None,
        store_user_id: str | None = None,
    ) -> list[StoredArtifactRecord]:
        """List persisted artifacts for ``user_id`` (newest first) with live staleness checks."""
        with self._lock:
            conn = self._connect()
            try:
                if artifact_kind:
                    rows = conn.execute(
                        """
                        SELECT * FROM rag_generated_artifacts
                        WHERE user_id = ? AND artifact_kind = ?
                        ORDER BY created_at DESC, version DESC
                        LIMIT ?
                        """,
                        (user_id, artifact_kind, max(1, int(limit))),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM rag_generated_artifacts
                        WHERE user_id = ?
                        ORDER BY created_at DESC, version DESC
                        LIMIT ?
                        """,
                        (user_id, max(1, int(limit))),
                    ).fetchall()
            finally:
                self._release(conn)
        return [
            self._row_to_record(r, store=store, store_user_id=store_user_id, cache_hit=False)
            for r in rows
        ]

    def save_user_edit(
        self,
        artifact_id: str,
        *,
        user_id: str,
        title: str | None = None,
        content_markdown: str | None = None,
        structured_payload: dict[str, Any] | None = None,
        store_user_id: str | None = None,
    ) -> StoredArtifactRecord:
        """Create a new sibling version preserving the original generated artifact and history."""
        existing = self.get(artifact_id, user_id=user_id, store_user_id=store_user_id)
        if existing is None:
            raise KeyError(f"Artifact '{artifact_id}' not found for user.")
        if existing.stale:
            raise ValueError("Artifact evidence is no longer available.")
        prev = existing.artifact
        edited = GeneratedArtifact(
            artifact_kind=prev.artifact_kind,
            title=(title if title is not None else prev.title),
            content_markdown=(content_markdown if content_markdown is not None else prev.content_markdown),
            structured_payload=(
                dict(structured_payload) if structured_payload is not None else dict(prev.structured_payload)
            ),
            citations=prev.citations,
            evidence_status=prev.evidence_status,
            provider=prev.provider,
            model=prev.model,
            embedding_space_id=prev.embedding_space_id,
            source_snapshot_hash=prev.source_snapshot_hash,
            coverage_ratio=prev.coverage_ratio,
            omitted_sections=prev.omitted_sections,
            warnings=prev.warnings,
            ai_generated=prev.ai_generated,
        )
        return self.save(
            edited,
            user_id=user_id,
            cache_key=existing.cache_key,
            parent_artifact_id=existing.artifact_id,
            user_edited=True,
        )

    def list_versions(
        self,
        artifact_id: str,
        *,
        user_id: str,
        store: LocalRagIndexStore | None = None,
        store_user_id: str | None = None,
    ) -> list[StoredArtifactRecord]:
        """Return all persisted versions sharing the lineage of ``artifact_id`` (ordered by version)."""
        with self._lock:
            conn = self._connect()
            try:
                anchor = conn.execute(
                    "SELECT cache_key FROM rag_generated_artifacts WHERE artifact_id = ? AND user_id = ?",
                    (artifact_id, user_id),
                ).fetchone()
                if anchor is None:
                    return []
                rows = conn.execute(
                    """
                    SELECT * FROM rag_generated_artifacts
                    WHERE user_id = ? AND cache_key = ?
                    ORDER BY version ASC, created_at ASC
                    """,
                    (user_id, str(anchor["cache_key"])),
                ).fetchall()
            finally:
                self._release(conn)
        return [
            self._row_to_record(r, store=store, store_user_id=store_user_id, cache_hit=False)
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Strict P3 DTO export adapters (matching origin/Fan-y-h@479e09a extra="forbid")
# ---------------------------------------------------------------------------

_DIFFICULTY_TO_P3: dict[str, str] = {
    "beginner": "easy",
    "easy": "easy",
    "intermediate": "medium",
    "medium": "medium",
    "advanced": "hard",
    "hard": "hard",
}
_VALID_P3_QUESTION_TYPES = frozenset(
    {"single_choice", "multiple_choice", "true_false", "fill_blank", "numeric", "open"}
)


def to_p3_flashcard_deck_payload(
    artifact: GeneratedArtifact,
    *,
    subject_id: str | None = None,
    concept_id: str | None = None,
) -> dict[str, Any]:
    """Export a flashcard ``GeneratedArtifact`` into P3 ``DeckCreate`` + ``CardCreate`` payloads."""
    raw_cards = artifact.structured_payload.get("cards") or artifact.structured_payload.get("items") or []
    cards: list[dict[str, Any]] = []
    seen_fronts: set[str] = set()
    for item in raw_cards:
        if not isinstance(item, Mapping):
            continue
        front = " ".join(str(item.get("front") or item.get("question") or "").split())[:5000]
        back = " ".join(str(item.get("back") or item.get("answer") or "").split())[:5000]
        if not front or not back:
            continue
        norm_front = front.casefold()
        if norm_front in seen_fronts:
            continue
        seen_fronts.add(norm_front)
        cards.append(
            {
                "front": front,
                "back": back,
                "concept_id": str(item.get("concept_id") or concept_id) if (item.get("concept_id") or concept_id) else None,
            }
        )
    deck_title = str(artifact.structured_payload.get("deck_title") or artifact.title or "Generated Deck").strip()[:200]
    return {
        "deck": {
            "title": deck_title or "Generated Deck",
            "subject_id": subject_id,
        },
        "cards": cards,
    }


def _coerce_p3_question_answer(
    q_type: str,
    answer: Any,
    options: list[dict[str, str]],
) -> tuple[str, list[dict[str, str]], Any]:
    """Ensure exported quiz questions always satisfy P3 ``validate_answer_key`` invariants."""
    opt_ids = [o["id"] for o in options]
    if q_type == "single_choice":
        if len(opt_ids) >= 2 and isinstance(answer, str) and answer in opt_ids:
            return q_type, options, answer
    elif q_type == "multiple_choice":
        if (
            len(opt_ids) >= 2
            and isinstance(answer, list)
            and answer
            and all(isinstance(a, str) and a in opt_ids for a in answer)
            and len(set(answer)) == len(answer)
        ):
            return q_type, options, answer
    elif q_type == "true_false":
        if isinstance(answer, bool):
            return q_type, [], answer
    elif q_type == "fill_blank":
        if isinstance(answer, str) and answer.strip():
            return q_type, [], [answer.strip()]
        if isinstance(answer, list) and answer and all(isinstance(a, str) and a.strip() for a in answer):
            return q_type, [], [a.strip() for a in answer]
    elif q_type == "numeric":
        if isinstance(answer, Mapping) and answer.get("value") is not None:
            try:
                float(str(answer["value"]).replace(",", ".").strip())
                tol = float(str(answer.get("tolerance", 0)).replace(",", ".").strip())
                if tol >= 0:
                    return q_type, [], {"value": str(answer["value"]), "tolerance": str(answer.get("tolerance", 0))}
            except ValueError:
                pass
    elif q_type == "open":
        rubric = str(answer).strip() if answer is not None else None
        return "open", [], (rubric or None)

    # Safe fallback: never emit a malformed question that would fail P3 validation
    fallback_rubric = None
    if isinstance(answer, str) and answer.strip():
        fallback_rubric = answer.strip()
    elif answer is not None:
        fallback_rubric = json.dumps(answer, ensure_ascii=False)
    return "open", [], fallback_rubric


def to_p3_quiz_payload(
    artifact: GeneratedArtifact,
    *,
    subject_id: str | None = None,
    quiz_type: str = "practice",
    time_limit_min: int | None = None,
    origin: str = "manual",
) -> dict[str, Any]:
    """Export a quiz ``GeneratedArtifact`` into a strict P3 ``QuizCreate`` payload."""
    raw_items = artifact.structured_payload.get("items") or artifact.structured_payload.get("questions") or []
    questions: list[dict[str, Any]] = []
    seen_prompts: set[str] = set()
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        prompt = " ".join(str(item.get("prompt") or item.get("question") or "").split())[:10000]
        if not prompt:
            continue
        norm_prompt = prompt.casefold()
        if norm_prompt in seen_prompts:
            continue
        seen_prompts.add(norm_prompt)
        raw_type = str(item.get("type") or item.get("kind") or "open").strip().lower()
        q_type = raw_type if raw_type in _VALID_P3_QUESTION_TYPES else "open"
        options: list[dict[str, str]] = []
        seen_opt_ids: set[str] = set()
        for idx, opt in enumerate(item.get("options") or [], start=1):
            if isinstance(opt, Mapping) and str(opt.get("text") or "").strip():
                oid = str(opt.get("id") or f"opt{idx}").strip()[:32] or f"opt{idx}"
                if oid in seen_opt_ids:
                    oid = f"opt{idx}"
                seen_opt_ids.add(oid)
                options.append({"id": oid, "text": str(opt["text"]).strip()[:2000]})
            elif isinstance(opt, str) and opt.strip():
                oid = f"opt{idx}"
                seen_opt_ids.add(oid)
                options.append({"id": oid, "text": opt.strip()[:2000]})
        raw_answer = item.get("answer") if "answer" in item else item.get("answer_key")
        q_type, options, validated_answer = _coerce_p3_question_answer(q_type, raw_answer, options[:20])
        diff_raw = str(item.get("difficulty") or "").strip().lower()
        difficulty = _DIFFICULTY_TO_P3.get(diff_raw)
        points = float(item.get("points") or 1.0)
        if points <= 0 or points > 100:
            points = 1.0
        valid_dims = {"theory", "memory", "reasoning", "application", "implementation", "autonomy", "retention"}
        dim = str(item.get("dimension") or "theory").strip().lower()
        if dim not in valid_dims:
            dim = "theory"
        questions.append(
            {
                "type": q_type,
                "prompt": prompt,
                "options": options[:20],
                "answer": validated_answer,
                "explanation": str(item["explanation"]).strip()[:10000] if item.get("explanation") else None,
                "points": points,
                "concept_ids": [str(cid) for cid in (item.get("concept_ids") or [])[:20] if str(cid).strip()],
                "dimension": dim,
                "difficulty": difficulty,
                "partial_credit": bool(item.get("partial_credit", False)) if q_type == "multiple_choice" else False,
            }
        )
    resolved_type = quiz_type if quiz_type in ("practice", "diagnostic", "exam") else "practice"
    resolved_origin = origin if origin in ("manual", "historical_exam") else "manual"
    return {
        "title": (artifact.title or "Generated Quiz").strip()[:200],
        "subject_id": subject_id,
        "type": resolved_type,
        "time_limit_min": time_limit_min,
        "origin": resolved_origin,
        "questions": questions[:200],
    }


def to_p3_note_payload(
    artifact: GeneratedArtifact,
    *,
    notebook_id: str,
    concept_id: str | None = None,
    chapter_id: str | None = None,
    origin: str = "studio",
) -> dict[str, Any]:
    """Export a summary/revision-sheet ``GeneratedArtifact`` into a strict P3 ``NoteCreate`` payload."""
    resolved_origin = origin if origin in ("manual", "tutor", "studio") else "studio"
    citations_out = [
        {
            "document_id": c.document_id,
            "version_id": c.version_id,
            "locator": dict(c.locator) if not c.revoked else None,
            "excerpt": ("" if c.revoked else c.excerpt)[:5000],
        }
        for c in artifact.citations[:200]
    ]
    return {
        "notebook_id": notebook_id,
        "title": (artifact.title or "Study Note").strip()[:200],
        "markdown": (artifact.content_markdown or "")[:200_000],
        "concept_id": concept_id,
        "chapter_id": chapter_id,
        "citations": citations_out,
        "origin": resolved_origin,
    }

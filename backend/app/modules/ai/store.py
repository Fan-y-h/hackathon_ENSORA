"""Isolated local SQLite persistence for AI preferences, token budgets, conversations, and artifact links.

Stored in the derived ``rag_index.sqlite3`` file (mode ``0o600``, WAL journal mode)
so neither P1's ``ensora.sqlite3`` nor P3's ``ensora.db`` schemas require migration.
Every row is strictly scoped by ``user_id``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any
import uuid


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _today_utc() -> str:
    return date.today().isoformat()


DEFAULT_PREFERENCES: dict[str, Any] = {
    "enabled": True,
    "cloud_data_consent": False,
    "allow_extractive_fallback": True,
    "preferred_provider": None,
    "preferred_model": None,
    "daily_token_budget": 100_000,
}


class LocalAIStateStore:
    """Per-user non-secret AI preferences, daily token accounting, conversations, and links."""

    def __init__(self, db_path: Path | str) -> None:
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
                    CREATE TABLE IF NOT EXISTS ai_user_preferences (
                        user_id TEXT PRIMARY KEY,
                        preferences_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS ai_token_usage (
                        user_id TEXT NOT NULL,
                        usage_date TEXT NOT NULL,
                        tokens_used INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (user_id, usage_date)
                    );

                    CREATE TABLE IF NOT EXISTS ai_conversations (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        subject_id TEXT,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_ai_conv_user
                        ON ai_conversations(user_id, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS ai_conversation_messages (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content_markdown TEXT NOT NULL,
                        citations_json TEXT NOT NULL,
                        evidence_status TEXT,
                        provider TEXT,
                        model TEXT,
                        generation_mode TEXT,
                        is_fallback INTEGER NOT NULL DEFAULT 0,
                        fallback_reason TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_ai_msg_conv
                        ON ai_conversation_messages(conversation_id, created_at ASC);

                    CREATE TABLE IF NOT EXISTS ai_artifact_links (
                        artifact_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        subject_id TEXT,
                        linked_deck_id TEXT,
                        linked_quiz_id TEXT,
                        is_fallback INTEGER NOT NULL DEFAULT 0,
                        fallback_reason TEXT,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                conn.commit()
            finally:
                self._release(conn)

    # --- Preferences & daily token budget -------------------------------------------------
    def get_preferences(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT preferences_json FROM ai_user_preferences WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                used = conn.execute(
                    "SELECT tokens_used FROM ai_token_usage WHERE user_id = ? AND usage_date = ?",
                    (user_id, _today_utc()),
                ).fetchone()
            finally:
                self._release(conn)
        merged = dict(DEFAULT_PREFERENCES)
        if row is not None:
            stored = json.loads(row["preferences_json"] or "{}")
            if isinstance(stored, dict):
                merged.update({k: v for k, v in stored.items() if k in DEFAULT_PREFERENCES})
        merged["tokens_used_today"] = int(used["tokens_used"]) if used is not None else 0
        return merged

    def update_preferences(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_preferences(user_id)
        for key, value in patch.items():
            if key in DEFAULT_PREFERENCES:
                current[key] = value
        to_store = {k: current[k] for k in DEFAULT_PREFERENCES}
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO ai_user_preferences(user_id, preferences_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        preferences_json = excluded.preferences_json,
                        updated_at = excluded.updated_at
                    """,
                    (user_id, json.dumps(to_store, ensure_ascii=True), _utc_now_iso()),
                )
                conn.commit()
            finally:
                self._release(conn)
        return self.get_preferences(user_id)

    def record_token_usage(self, user_id: str, tokens: int) -> int:
        delta = max(0, int(tokens))
        if delta == 0:
            return self.get_preferences(user_id)["tokens_used_today"]
        today = _today_utc()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO ai_token_usage(user_id, usage_date, tokens_used)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, usage_date) DO UPDATE SET
                        tokens_used = ai_token_usage.tokens_used + excluded.tokens_used
                    """,
                    (user_id, today, delta),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT tokens_used FROM ai_token_usage WHERE user_id = ? AND usage_date = ?",
                    (user_id, today),
                ).fetchone()
            finally:
                self._release(conn)
        return int(row["tokens_used"]) if row is not None else delta

    # --- Conversations & Messages ---------------------------------------------------------
    def create_conversation(
        self,
        user_id: str,
        *,
        subject_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        conv_id = str(uuid.uuid4())
        now = _utc_now_iso()
        resolved_title = (title or "").strip()[:200] or (
            f"Discussion — {subject_id}" if subject_id else "Nouvelle discussion"
        )
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO ai_conversations(id, user_id, subject_id, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (conv_id, user_id, subject_id, resolved_title, now, now),
                )
                conn.commit()
            finally:
                self._release(conn)
        return {
            "id": conv_id,
            "title": resolved_title,
            "subject_id": subject_id,
            "message_count": 0,
            "created_at": now,
            "updated_at": now,
        }

    def get_conversation(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT c.id, c.title, c.subject_id, c.created_at, c.updated_at,
                           (SELECT COUNT(*) FROM ai_conversation_messages m WHERE m.conversation_id = c.id) AS message_count
                    FROM ai_conversations c
                    WHERE c.id = ? AND c.user_id = ?
                    """,
                    (conversation_id, user_id),
                ).fetchone()
            finally:
                self._release(conn)
        return dict(row) if row is not None else None

    def list_conversations(self, user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT c.id, c.title, c.subject_id, c.created_at, c.updated_at,
                           (SELECT COUNT(*) FROM ai_conversation_messages m WHERE m.conversation_id = c.id) AS message_count
                    FROM ai_conversations c
                    WHERE c.user_id = ?
                    ORDER BY c.updated_at DESC, c.id DESC
                    LIMIT ?
                    """,
                    (user_id, max(1, min(int(limit), 200))),
                ).fetchall()
            finally:
                self._release(conn)
        return [dict(r) for r in rows]

    def append_message(
        self,
        *,
        user_id: str,
        conversation_id: str,
        role: str,
        content_markdown: str,
        citations: list[dict[str, Any]] | None = None,
        evidence_status: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        generation_mode: str | None = None,
        is_fallback: bool = False,
        fallback_reason: str | None = None,
    ) -> dict[str, Any]:
        msg_id = str(uuid.uuid4())
        now = _utc_now_iso()
        cites = list(citations or [])
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO ai_conversation_messages(
                        id, conversation_id, user_id, role, content_markdown, citations_json,
                        evidence_status, provider, model, generation_mode, is_fallback,
                        fallback_reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg_id,
                        conversation_id,
                        user_id,
                        role,
                        content_markdown,
                        json.dumps(cites, ensure_ascii=False),
                        evidence_status,
                        provider,
                        model,
                        generation_mode,
                        1 if is_fallback else 0,
                        fallback_reason,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE ai_conversations SET updated_at = ? WHERE id = ? AND user_id = ?",
                    (now, conversation_id, user_id),
                )
                conn.commit()
            finally:
                self._release(conn)
        return {
            "id": msg_id,
            "conversation_id": conversation_id,
            "role": role,
            "content_markdown": content_markdown,
            "citations": cites,
            "evidence_status": evidence_status,
            "provider": provider,
            "model": model,
            "generation_mode": generation_mode,
            "is_fallback": is_fallback,
            "fallback_reason": fallback_reason,
            "created_at": now,
        }

    def list_messages(self, user_id: str, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT id, conversation_id, role, content_markdown, citations_json,
                           evidence_status, provider, model, generation_mode, is_fallback,
                           fallback_reason, created_at
                    FROM ai_conversation_messages
                    WHERE conversation_id = ? AND user_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (conversation_id, user_id),
                ).fetchall()
            finally:
                self._release(conn)
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item["citations"] = json.loads(item.pop("citations_json") or "[]")
            item["is_fallback"] = bool(item["is_fallback"])
            out.append(item)
        return out

    # --- Artifact metadata links (subject, deck, quiz, fallback) --------------------------
    def save_artifact_link(
        self,
        *,
        artifact_id: str,
        user_id: str,
        subject_id: str | None = None,
        linked_deck_id: str | None = None,
        linked_quiz_id: str | None = None,
        is_fallback: bool = False,
        fallback_reason: str | None = None,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO ai_artifact_links(
                        artifact_id, user_id, subject_id, linked_deck_id, linked_quiz_id,
                        is_fallback, fallback_reason, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(artifact_id) DO UPDATE SET
                        subject_id = COALESCE(excluded.subject_id, ai_artifact_links.subject_id),
                        linked_deck_id = COALESCE(excluded.linked_deck_id, ai_artifact_links.linked_deck_id),
                        linked_quiz_id = COALESCE(excluded.linked_quiz_id, ai_artifact_links.linked_quiz_id),
                        is_fallback = excluded.is_fallback,
                        fallback_reason = excluded.fallback_reason,
                        updated_at = excluded.updated_at
                    """,
                    (
                        artifact_id,
                        user_id,
                        subject_id,
                        linked_deck_id,
                        linked_quiz_id,
                        1 if is_fallback else 0,
                        fallback_reason,
                        _utc_now_iso(),
                    ),
                )
                conn.commit()
            finally:
                self._release(conn)

    def get_artifact_link(self, user_id: str, artifact_id: str) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT subject_id, linked_deck_id, linked_quiz_id, is_fallback, fallback_reason
                    FROM ai_artifact_links
                    WHERE artifact_id = ? AND user_id = ?
                    """,
                    (artifact_id, user_id),
                ).fetchone()
            finally:
                self._release(conn)
        if row is None:
            return {
                "subject_id": None,
                "linked_deck_id": None,
                "linked_quiz_id": None,
                "is_fallback": False,
                "fallback_reason": None,
            }
        data = dict(row)
        data["is_fallback"] = bool(data["is_fallback"])
        return data

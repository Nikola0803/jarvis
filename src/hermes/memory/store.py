"""Persistent memory store for Hermes.

Three layers:
  - profile   : static facts about the owner (name, businesses, preferences)
  - facts     : things learned in conversation ("Ivan is main client, reply fast")
  - episodes  : summarised conversation snapshots for long-term recall

All stored in SQLite -- same db as the message log.
Retrieved by keyword match; no embeddings needed to start.
"""
from __future__ import annotations
import sqlite3
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("hermes.memory")

_DDL = """
CREATE TABLE IF NOT EXISTS memory_profile (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_facts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    subject   TEXT NOT NULL,
    relation  TEXT NOT NULL,
    value     TEXT NOT NULL,
    source    TEXT DEFAULT 'conversation',
    created   TEXT NOT NULL,
    confirmed INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS memory_episodes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    summary   TEXT NOT NULL,
    keywords  TEXT NOT NULL,
    created   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_subject ON memory_facts(subject);
CREATE INDEX IF NOT EXISTS idx_episodes_created ON memory_episodes(created);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, db_path: str):
        self._db = db_path
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self._db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._conn() as conn:
            conn.executescript(_DDL)

    # ── Profile (owner identity, never changes unless updated) ────────────────

    def set_profile(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO memory_profile(key, value, updated) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                (key, value, _now()),
            )

    def get_profile(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM memory_profile WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def all_profile(self) -> dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM memory_profile").fetchall()
            return {r["key"]: r["value"] for r in rows}

    # ── Facts (learned from conversations) ───────────────────────────────────

    def add_fact(self, subject: str, relation: str, value: str, source: str = "conversation") -> None:
        with self._conn() as conn:
            # Avoid duplicates: skip if same subject+relation+value exists
            existing = conn.execute(
                "SELECT id FROM memory_facts WHERE subject=? AND relation=? AND value=?",
                (subject, relation, value),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO memory_facts(subject, relation, value, source, created) VALUES(?,?,?,?,?)",
                    (subject, relation, value, source, _now()),
                )
                log.debug("Memory fact: [%s] %s -> %s", subject, relation, value)

    def search_facts(self, query: str, limit: int = 10) -> list[dict]:
        words = set(re.sub(r"[^\w\s]", "", query.lower()).split())
        if not words:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT subject, relation, value, created FROM memory_facts ORDER BY created DESC LIMIT 200"
            ).fetchall()
        scored = []
        for r in rows:
            text = f"{r['subject']} {r['relation']} {r['value']}".lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                scored.append((score, dict(r)))
        scored.sort(key=lambda x: -x[0])
        return [x[1] for x in scored[:limit]]

    def recent_facts(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT subject, relation, value, created FROM memory_facts "
                "ORDER BY created DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Episodes (conversation snapshots) ────────────────────────────────────

    def add_episode(self, summary: str, keywords: list[str]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO memory_episodes(summary, keywords, created) VALUES(?,?,?)",
                (summary, " ".join(keywords), _now()),
            )

    def search_episodes(self, query: str, limit: int = 5) -> list[str]:
        words = set(re.sub(r"[^\w\s]", "", query.lower()).split())
        if not words:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT summary, keywords FROM memory_episodes ORDER BY created DESC LIMIT 100"
            ).fetchall()
        scored = []
        for r in rows:
            text = f"{r['summary']} {r['keywords']}".lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                scored.append((score, r["summary"]))
        scored.sort(key=lambda x: -x[0])
        return [x[1] for x in scored[:limit]]


# Global singleton
_store: MemoryStore | None = None


def get_store(db_path: str | None = None) -> MemoryStore:
    global _store
    if _store is None:
        if not db_path:
            raise RuntimeError("MemoryStore not initialised -- pass db_path on first call")
        _store = MemoryStore(db_path)
    return _store

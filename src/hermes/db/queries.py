from __future__ import annotations
from datetime import datetime
import aiosqlite
from ..llm.base import LLMResponse


async def log_message(
    db_path: str,
    user_text: str,
    response: LLMResponse,
    platform: str | None = None,
    user_id: str | None = None,
) -> None:
    month = datetime.utcnow().strftime("%Y-%m")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO messages (direction, platform, user_id, provider, model,
               user_text, agent_text, input_tok, output_tok, cost_usd)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("in", platform, user_id, response.provider, response.model, user_text,
             response.text, response.input_tokens, response.output_tokens, response.cost_usd),
        )
        await db.execute(
            """INSERT INTO monthly_spend (month, provider, total_usd) VALUES (?,?,?)
               ON CONFLICT(month, provider) DO UPDATE SET total_usd = total_usd + excluded.total_usd""",
            (month, response.provider, response.cost_usd),
        )
        await db.commit()


async def get_conversation_history(
    db_path: str,
    platform: str,
    user_id: str,
    limit: int = 10,
) -> list[dict]:
    """Return the last `limit` exchanges for one user as chat messages.

    Each stored row is one exchange (user_text + agent_text); we expand it into
    a user turn followed by an assistant turn, in chronological order. Only
    exchanges after the user's most recent /reset marker are included.
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT after_message_id FROM conversation_resets "
            "WHERE platform=? AND user_id=? ORDER BY id DESC LIMIT 1",
            (platform, user_id),
        ) as cur:
            reset_row = await cur.fetchone()
        after_id = reset_row["after_message_id"] if reset_row else 0

        async with db.execute(
            "SELECT user_text, agent_text FROM messages "
            "WHERE platform=? AND user_id=? AND id > ? "
            "ORDER BY id DESC LIMIT ?",
            (platform, user_id, after_id, limit),
        ) as cur:
            rows = await cur.fetchall()

    history: list[dict] = []
    for row in reversed(rows):  # back to chronological order
        if row["user_text"]:
            history.append({"role": "user", "content": row["user_text"]})
        if row["agent_text"]:
            history.append({"role": "assistant", "content": row["agent_text"]})
    return history


async def clear_conversation(db_path: str, platform: str, user_id: str) -> None:
    """Start a fresh conversation for this user without deleting the audit log."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COALESCE(MAX(id), 0) FROM messages WHERE platform=? AND user_id=?",
            (platform, user_id),
        ) as cur:
            (last_id,) = await cur.fetchone()
        await db.execute(
            "INSERT INTO conversation_resets (platform, user_id, after_message_id) VALUES (?,?,?)",
            (platform, user_id, last_id),
        )
        await db.commit()


async def get_recent_messages(db_path: str, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM messages ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


_CREATE_REMINDERS = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    due_at TEXT, text TEXT NOT NULL, project TEXT, done INTEGER DEFAULT 0, source TEXT DEFAULT 'zero'
)
"""


async def add_reminder(db_path: str, text: str, due_at: str | None = None,
                       project: str | None = None, source: str = "zero") -> int:
    """Add a reminder/task. due_at is an ISO-8601 string or None. Returns its id."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_CREATE_REMINDERS)
        cur = await db.execute(
            "INSERT INTO reminders (text, due_at, project, source) VALUES (?,?,?,?)",
            (text.strip(), (due_at or None), (project or None), source),
        )
        await db.commit()
        return cur.lastrowid


async def list_reminders(db_path: str, include_done: bool = False, limit: int = 50) -> list[dict]:
    """Open reminders (soonest due first, then newest). include_done adds completed ones."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_CREATE_REMINDERS)
        db.row_factory = aiosqlite.Row
        where = "" if include_done else "WHERE done = 0"
        async with db.execute(
            f"SELECT id, text, due_at, project, done, created_at FROM reminders {where} "
            "ORDER BY done ASC, (due_at IS NULL) ASC, due_at ASC, id DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def complete_reminder(db_path: str, reminder_id: int) -> bool:
    """Mark a reminder done. Returns True if a row changed."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_CREATE_REMINDERS)
        cur = await db.execute("UPDATE reminders SET done = 1 WHERE id = ? AND done = 0", (reminder_id,))
        await db.commit()
        return cur.rowcount > 0


async def get_monthly_spend(db_path: str) -> dict[str, float]:
    month = datetime.utcnow().strftime("%Y-%m")
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT provider, total_usd FROM monthly_spend WHERE month=?", (month,)
        ) as cur:
            rows = await cur.fetchall()
    return {r["provider"]: r["total_usd"] for r in rows}

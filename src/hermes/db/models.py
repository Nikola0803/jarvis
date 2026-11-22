import aiosqlite
from pathlib import Path

CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    direction   TEXT NOT NULL,       -- 'in' | 'out'
    platform    TEXT,                -- 'telegram' | 'whatsapp' | 'discord' | 'slack' | 'hud'
    user_id     TEXT,                -- platform-scoped user identifier
    provider    TEXT,                -- 'deepseek' | 'claude' | 'gemini'
    model       TEXT,
    user_text   TEXT,
    agent_text  TEXT,
    input_tok   INTEGER DEFAULT 0,
    output_tok  INTEGER DEFAULT 0,
    cost_usd    REAL DEFAULT 0.0
)
"""

CREATE_SPEND = """
CREATE TABLE IF NOT EXISTS monthly_spend (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    month       TEXT NOT NULL,       -- 'YYYY-MM'
    provider    TEXT NOT NULL,
    total_usd   REAL DEFAULT 0.0,
    UNIQUE(month, provider)
)
"""

# A "forget everything before now" marker per (platform, user_id). Message rows
# are never deleted (they feed spend/analytics); conversation history simply
# starts after the last message id that existed at reset time. Using the message
# id instead of a timestamp avoids SQLite's 1-second granularity dropping a
# message sent in the same second as the reset.
CREATE_RESETS = """
CREATE TABLE IF NOT EXISTS conversation_resets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    platform          TEXT NOT NULL,
    user_id           TEXT NOT NULL,
    after_message_id  INTEGER NOT NULL DEFAULT 0,
    reset_at          DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

# Reminders / tasks ZERO manages by voice and the HUD shows at a glance.
CREATE_REMINDERS = """
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    due_at     TEXT,              -- ISO8601 local time, nullable
    text       TEXT NOT NULL,
    project    TEXT,              -- optional project/tag
    done       INTEGER DEFAULT 0,
    source     TEXT DEFAULT 'zero'
)
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(platform, user_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_resets_user ON conversation_resets(platform, user_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_reminders_open ON reminders(done, due_at)",
]


async def _migrate_messages(db: aiosqlite.Connection) -> None:
    """Add columns introduced after the first release, for existing DBs."""
    async with db.execute("PRAGMA table_info(messages)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "platform" not in cols:
        await db.execute("ALTER TABLE messages ADD COLUMN platform TEXT")
    if "user_id" not in cols:
        await db.execute("ALTER TABLE messages ADD COLUMN user_id TEXT")


async def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(CREATE_MESSAGES)
        await db.execute(CREATE_SPEND)
        await db.execute(CREATE_RESETS)
        await db.execute(CREATE_REMINDERS)
        await _migrate_messages(db)
        for stmt in CREATE_INDEXES:
            await db.execute(stmt)
        await db.commit()

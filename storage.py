"""SQLite saqlash: suhbat tarixi, kelgan xabarlar (inbox), sozlamalar, istisnolar."""
import sqlite3
import time

from config import DB_PATH, HISTORY_LIMIT

_db = sqlite3.connect(DB_PATH)
_db.row_factory = sqlite3.Row
_db.executescript(
    """
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        role TEXT NOT NULL,          -- user | assistant
        content TEXT NOT NULL,
        ts REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_history_chat ON history(chat_id, id);

    CREATE TABLE IF NOT EXISTS inbox (
        chat_id INTEGER PRIMARY KEY,
        name TEXT,
        username TEXT,
        summary TEXT,
        urgent INTEGER DEFAULT 0,
        last_text TEXT,
        updated REAL,
        handled INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS ignored (user_id INTEGER PRIMARY KEY, label TEXT);
    CREATE TABLE IF NOT EXISTS muted (chat_id INTEGER PRIMARY KEY, until REAL);
    """
)
_db.commit()


# ---------- tarix ----------
def add_history(chat_id: int, role: str, content: str) -> None:
    _db.execute(
        "INSERT INTO history(chat_id, role, content, ts) VALUES (?,?,?,?)",
        (chat_id, role, content, time.time()),
    )
    _db.commit()


def get_history(chat_id: int, limit: int = HISTORY_LIMIT) -> list[dict]:
    rows = _db.execute(
        "SELECT role, content FROM history WHERE chat_id=? ORDER BY id DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ---------- inbox ----------
def upsert_inbox(chat_id, name, username, summary, urgent, last_text) -> None:
    _db.execute(
        """INSERT INTO inbox(chat_id, name, username, summary, urgent, last_text, updated, handled)
           VALUES (?,?,?,?,?,?,?,0)
           ON CONFLICT(chat_id) DO UPDATE SET
             name=excluded.name, username=excluded.username, summary=excluded.summary,
             urgent=CASE WHEN inbox.handled=1 THEN excluded.urgent
                         ELSE MAX(inbox.urgent, excluded.urgent) END,
             last_text=excluded.last_text, updated=excluded.updated, handled=0""",
        (chat_id, name, username, summary, int(urgent), last_text, time.time()),
    )
    _db.commit()


def mark_handled(chat_id: int) -> None:
    _db.execute("UPDATE inbox SET handled=1, urgent=0 WHERE chat_id=?", (chat_id,))
    _db.commit()


def pending_inbox() -> list[sqlite3.Row]:
    return _db.execute(
        "SELECT * FROM inbox WHERE handled=0 ORDER BY urgent DESC, updated DESC"
    ).fetchall()


def today_count() -> int:
    start = time.time() - 24 * 3600
    return _db.execute(
        "SELECT COUNT(*) FROM history WHERE role='user' AND ts>=?", (start,)
    ).fetchone()[0]


# ---------- sozlamalar ----------
def is_enabled() -> bool:
    row = _db.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
    return row is None or row["value"] == "1"


def set_enabled(on: bool) -> None:
    _db.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('enabled', ?)",
        ("1" if on else "0",),
    )
    _db.commit()


# ---------- istisnolar ----------
def is_ignored(user_id: int) -> bool:
    return _db.execute("SELECT 1 FROM ignored WHERE user_id=?", (user_id,)).fetchone() is not None


def add_ignored(user_id: int, label: str) -> None:
    _db.execute("INSERT OR REPLACE INTO ignored(user_id, label) VALUES (?,?)", (user_id, label))
    _db.commit()


def remove_ignored(user_id: int) -> bool:
    cur = _db.execute("DELETE FROM ignored WHERE user_id=?", (user_id,))
    _db.commit()
    return cur.rowcount > 0


def list_ignored() -> list[sqlite3.Row]:
    return _db.execute("SELECT * FROM ignored").fetchall()


# ---------- vaqtincha jim (egasi o'zi gaplashayotganda) ----------
def mute(chat_id: int, seconds: float) -> None:
    _db.execute(
        "INSERT OR REPLACE INTO muted(chat_id, until) VALUES (?,?)",
        (chat_id, time.time() + seconds),
    )
    _db.commit()


def unmute(chat_id: int) -> None:
    _db.execute("DELETE FROM muted WHERE chat_id=?", (chat_id,))
    _db.commit()


def is_muted(chat_id: int) -> bool:
    row = _db.execute("SELECT until FROM muted WHERE chat_id=?", (chat_id,)).fetchone()
    return row is not None and row["until"] > time.time()

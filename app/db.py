"""SQLite: kết nối, khởi tạo bảng, tiện ích truy vấn."""
import json
import os
import sqlite3
import threading
import time

from .config import settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  last_login INTEGER
);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  zalo_name TEXT NOT NULL,
  phone TEXT UNIQUE NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  blocked INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL DEFAULT '',
  ai_mode TEXT NOT NULL DEFAULT 'auto',
  needs_human INTEGER NOT NULL DEFAULT 0,
  hidden_by_user INTEGER NOT NULL DEFAULT 0,
  last_text TEXT NOT NULL DEFAULT '',
  last_role TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  sources TEXT NOT NULL DEFAULT '[]',
  escalated INTEGER NOT NULL DEFAULT 0,
  fallback INTEGER NOT NULL DEFAULT 0,
  admin_id INTEGER,
  created_at INTEGER NOT NULL,
  edited_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS documents(
  doc_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'chung',
  version INTEGER NOT NULL DEFAULT 1,
  file_name TEXT NOT NULL DEFAULT '',
  chunk_count INTEGER NOT NULL DEFAULT 0,
  chars INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks(
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def now() -> int:
    return int(time.time() * 1000)


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(settings.data_dir, exist_ok=True)
            c = sqlite3.connect(os.path.join(settings.data_dir, "kb.sqlite3"), check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.executescript(SCHEMA)
            c.commit()
            _conn = c
        return _conn


def reset_for_tests() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def q(sql: str, params: tuple | list = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def q1(sql: str, params: tuple | list = ()) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def ex(sql: str, params: tuple | list = ()) -> int:
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid


def ex_many(statements: list[tuple[str, tuple | list]]) -> None:
    with _lock:
        c = conn()
        try:
            for sql, params in statements:
                c.execute(sql, params)
            c.commit()
        except Exception:
            c.rollback()
            raise


DEFAULT_SETTINGS = {
    "name": "Trợ lý tri thức",
    "ai_enabled": True,
    "threshold": 0.35,
    "top_k": 5,
    "escalate": "Mình chưa tìm thấy thông tin này trong tài liệu nên đã chuyển câu hỏi cho nhân viên hỗ trợ. Bạn vui lòng chờ phản hồi trong cuộc trò chuyện này nhé.",
    "wait": "Nhân viên hỗ trợ sẽ trả lời bạn trong cuộc trò chuyện này. Bạn vui lòng chờ nhé.",
    "ai_error": "Hệ thống AI đang gián đoạn nên câu hỏi đã được chuyển cho nhân viên hỗ trợ.",
    "suggest": [],
    "welcome": "Hỏi về quy trình, chính sách hay tài liệu. Câu trả lời kèm nguồn để bạn kiểm tra.",
}


def get_settings() -> dict:
    out = dict(DEFAULT_SETTINGS)
    for r in q("SELECT key, value FROM settings"):
        try:
            out[r["key"]] = json.loads(r["value"])
        except ValueError:
            pass
    return out


def save_settings(values: dict) -> dict:
    allowed = {k: v for k, v in values.items() if k in DEFAULT_SETTINGS}
    ex_many([("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (k, json.dumps(v, ensure_ascii=False))) for k, v in allowed.items()])
    return get_settings()

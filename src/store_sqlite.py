"""ローカル永続化（SQLite）。投稿ログ・処理済み返信・返信下書き・リードを管理。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .config import DATA_DIR

DB_PATH = DATA_DIR / "threads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    media_id   TEXT PRIMARY KEY,
    text       TEXT,
    profile    TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    views      INTEGER DEFAULT 0,
    likes      INTEGER DEFAULT 0,
    replies    INTEGER DEFAULT 0,
    insights_at TEXT
);
CREATE TABLE IF NOT EXISTS processed_replies (
    reply_id   TEXT PRIMARY KEY,
    post_id    TEXT,
    username   TEXT,
    text       TEXT,
    seen_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS draft_replies (
    reply_id   TEXT PRIMARY KEY,
    post_id    TEXT,
    username   TEXT,
    in_text    TEXT,
    draft_text TEXT,
    status     TEXT DEFAULT 'pending',   -- pending / approved / sent / skipped
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    sent_at    TEXT
);
CREATE TABLE IF NOT EXISTS leads (
    reply_id   TEXT PRIMARY KEY,
    post_id    TEXT,
    username   TEXT,
    text       TEXT,
    keyword    TEXT,
    notified   INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS scheduled_posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    text         TEXT,
    region       TEXT,
    scheduled_at TEXT,                 -- JSTのISO文字列。この時刻以降に自動投稿
    status       TEXT DEFAULT 'scheduled',  -- scheduled / posted / failed / canceled
    media_id     TEXT,
    error        TEXT,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    posted_at    TEXT
);
CREATE TABLE IF NOT EXISTS members (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname   TEXT,
    me_birth   TEXT,
    him_birth  TEXT,
    note       TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS readings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id  INTEGER,
    month      TEXT,
    worry      TEXT,
    reading    TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS line_users (
    user_id      TEXT PRIMARY KEY,
    display_name TEXT,
    me_birth     TEXT,
    him_birth    TEXT,
    bot          TEXT DEFAULT 'on',   -- on / hold(クロージング中) / off
    note         TEXT,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS line_chats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT,
    role       TEXT,   -- user / assistant
    text       TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS web_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event      TEXT,   -- view(診断ページ表示) / submit(診断完了) / line_click(ボタン押下) / line_follow(友だち追加)
    vid        TEXT,   -- 訪問者の匿名ID（localStorage由来。人数のユニーク化に使う）
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS web_diag (
    code       TEXT PRIMARY KEY,   -- Web診断で発行する鑑定番号（LINEで送ってもらう合言葉）
    me_birth   TEXT,
    him_birth  TEXT,
    status     TEXT,
    period     TEXT,
    type_name  TEXT,
    used       INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    used_at    TEXT
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        try:  # 旧スキーマ（vid列なし）からの移行
            c.execute("ALTER TABLE web_events ADD COLUMN vid TEXT")
        except sqlite3.OperationalError:
            pass


# ---- posts ----
def save_post(media_id: str, text: str, profile: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO posts(media_id, text, profile) VALUES (?,?,?)",
            (media_id, text, profile),
        )


def update_insights(media_id: str, views: int, likes: int, replies: int) -> None:
    with conn() as c:
        c.execute(
            "UPDATE posts SET views=?, likes=?, replies=?, insights_at=CURRENT_TIMESTAMP WHERE media_id=?",
            (views, likes, replies, media_id),
        )


# ---- replies ----
def is_reply_seen(reply_id: str) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM processed_replies WHERE reply_id=?", (reply_id,)).fetchone() is not None


def mark_reply_seen(reply_id: str, post_id: str, username: str, text: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO processed_replies(reply_id, post_id, username, text) VALUES (?,?,?,?)",
            (reply_id, post_id, username, text),
        )


# ---- draft replies ----
def add_draft(reply_id: str, post_id: str, username: str, in_text: str, draft_text: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO draft_replies(reply_id, post_id, username, in_text, draft_text) VALUES (?,?,?,?,?)",
            (reply_id, post_id, username, in_text, draft_text),
        )


def pending_drafts() -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute("SELECT * FROM draft_replies WHERE status='pending' ORDER BY created_at").fetchall()


def set_draft_status(reply_id: str, status: str, *, sent: bool = False) -> None:
    with conn() as c:
        if sent:
            c.execute("UPDATE draft_replies SET status=?, sent_at=CURRENT_TIMESTAMP WHERE reply_id=?", (status, reply_id))
        else:
            c.execute("UPDATE draft_replies SET status=? WHERE reply_id=?", (status, reply_id))


def update_draft_text(reply_id: str, draft_text: str) -> None:
    with conn() as c:
        c.execute("UPDATE draft_replies SET draft_text=? WHERE reply_id=?", (draft_text, reply_id))


# ---- leads ----
def add_lead(reply_id: str, post_id: str, username: str, text: str, keyword: str) -> bool:
    """新規リードなら True。"""
    with conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO leads(reply_id, post_id, username, text, keyword) VALUES (?,?,?,?,?)",
            (reply_id, post_id, username, text, keyword),
        )
        return cur.rowcount > 0


def mark_lead_notified(reply_id: str) -> None:
    with conn() as c:
        c.execute("UPDATE leads SET notified=1 WHERE reply_id=?", (reply_id,))


def recent_leads(limit: int = 50) -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute("SELECT * FROM leads ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


# ---- scheduled posts / candidates ----
def add_candidate(text: str, region: str | None) -> int:
    """未承認の候補として保存（status=pending_review、投稿日時は未定）。"""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO scheduled_posts(text, region, scheduled_at, status) VALUES (?,?,NULL,'pending_review')",
            (text, region),
        )
        return cur.lastrowid


def approve_candidate(post_id: int, scheduled_at: str, text: str | None = None) -> None:
    """未承認候補を承認して予約（status=scheduled＋投稿日時）。本文の手直しも反映。"""
    with conn() as c:
        if text is not None:
            c.execute(
                "UPDATE scheduled_posts SET status='scheduled', scheduled_at=?, text=? "
                "WHERE id=? AND status='pending_review'",
                (scheduled_at, text, post_id),
            )
        else:
            c.execute(
                "UPDATE scheduled_posts SET status='scheduled', scheduled_at=? "
                "WHERE id=? AND status='pending_review'",
                (scheduled_at, post_id),
            )


def add_scheduled(text: str, region: str | None, scheduled_at: str) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO scheduled_posts(text, region, scheduled_at, status) VALUES (?,?,?,'scheduled')",
            (text, region, scheduled_at),
        )
        return cur.lastrowid


def list_scheduled(status: str | None = None, limit: int = 200) -> list[sqlite3.Row]:
    with conn() as c:
        if status:
            return c.execute(
                "SELECT * FROM scheduled_posts WHERE status=? ORDER BY scheduled_at LIMIT ?", (status, limit)
            ).fetchall()
        return c.execute("SELECT * FROM scheduled_posts ORDER BY scheduled_at DESC LIMIT ?", (limit,)).fetchall()


def due_scheduled(now_iso: str) -> list[sqlite3.Row]:
    """status=scheduled かつ scheduled_at <= now のものを返す。"""
    with conn() as c:
        return c.execute(
            "SELECT * FROM scheduled_posts WHERE status='scheduled' AND scheduled_at <= ? ORDER BY scheduled_at",
            (now_iso,),
        ).fetchall()


def mark_scheduled(post_id: int, status: str, *, media_id: str | None = None, error: str | None = None) -> None:
    with conn() as c:
        c.execute(
            "UPDATE scheduled_posts SET status=?, media_id=?, error=?, "
            "posted_at=CASE WHEN ?='posted' THEN CURRENT_TIMESTAMP ELSE posted_at END WHERE id=?",
            (status, media_id, error, status, post_id),
        )


def cancel_scheduled(post_id: int) -> None:
    """予約中・未承認の候補を取消/却下する。"""
    with conn() as c:
        c.execute(
            "UPDATE scheduled_posts SET status='canceled' WHERE id=? AND status IN ('scheduled','pending_review')",
            (post_id,),
        )


def update_scheduled(post_id: int, text: str, scheduled_at: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE scheduled_posts SET text=?, scheduled_at=? WHERE id=? AND status='scheduled'",
            (text, scheduled_at, post_id),
        )


# ---- members（サブスク会員） ----
def add_member(nickname: str, me_birth: str, him_birth: str, note: str = "") -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO members(nickname, me_birth, him_birth, note) VALUES (?,?,?,?)",
            (nickname, me_birth, him_birth, note),
        )
        return cur.lastrowid


def list_members() -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute("SELECT * FROM members ORDER BY nickname").fetchall()


def delete_member(member_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM members WHERE id=?", (member_id,))


# ---- readings（鑑定の控え） ----
def add_reading(member_id, month: str, worry: str, reading: str) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO readings(member_id, month, worry, reading) VALUES (?,?,?,?)",
            (member_id, month, worry, reading),
        )
        return cur.lastrowid


def list_readings(member_id=None, limit: int = 200) -> list[sqlite3.Row]:
    with conn() as c:
        if member_id is not None:
            return c.execute(
                "SELECT * FROM readings WHERE member_id=? ORDER BY created_at DESC LIMIT ?",
                (member_id, limit),
            ).fetchall()
        return c.execute("SELECT * FROM readings ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


# ---- Web無料診断（計測イベント） ----
def add_web_event(event: str, vid: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO web_events(event, vid) VALUES (?,?)", (event, vid))


def list_web_events(limit: int = 20000) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM web_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---- Web無料診断（鑑定番号） ----
def add_web_diag(code: str, me_birth: str, him_birth: str, status: str,
                 period: str, type_name: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO web_diag(code, me_birth, him_birth, status, period, type_name) VALUES (?,?,?,?,?,?)",
            (code, me_birth, him_birth, status, period, type_name),
        )


def get_web_diag(code: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM web_diag WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None


def mark_web_diag_used(code: str) -> None:
    with conn() as c:
        c.execute("UPDATE web_diag SET used=1, used_at=CURRENT_TIMESTAMP WHERE code=?", (code,))


def list_web_diag(limit: int = 1000) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM web_diag ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---- LINEボット（ユーザー・会話履歴） ----
def get_line_user(user_id: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM line_users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def upsert_line_user(user_id: str, **fields) -> None:
    allowed = {"display_name", "me_birth", "him_birth", "bot", "note"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    with conn() as c:
        if c.execute("SELECT 1 FROM line_users WHERE user_id=?", (user_id,)).fetchone():
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                c.execute(f"UPDATE line_users SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                          (*fields.values(), user_id))
        else:
            cols = ["user_id", *fields.keys()]
            c.execute(f"INSERT INTO line_users({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                      (user_id, *fields.values()))


def add_line_chat(user_id: str, role: str, text: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO line_chats(user_id, role, text) VALUES (?,?,?)", (user_id, role, text))


def recent_line_chats(user_id: str, limit: int = 12) -> list[dict]:
    """直近のやりとりを古い順で返す（プロンプト組み立て用）。"""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM line_chats WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def all_line_chats(days: int = 45) -> list[dict]:
    """全相談者の直近days日のやりとりを（相談者→時系列）順で返す（お客様の声学習用）。"""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM line_chats WHERE created_at >= datetime('now', ?) ORDER BY user_id, id",
            (f"-{int(days)} day",),
        ).fetchall()
    return [dict(r) for r in rows]

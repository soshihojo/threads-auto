"""かすみのデータ保存。椿の表には一切書かない。

STORE_BACKEND=sheets（本番）… 椿と同じスプレッドシートに、kasumi_ で始まる別シートを作って書く。
                              書き込みの部品（リトライ・キャッシュ）は store_sheets のものを使う。
それ以外（手元・テスト）  … data/kasumi.db（椿の threads.db とは別ファイル）。

ヒアリングの答えは要約せず、原文のまま kasumi_chats に残す。
"""
from __future__ import annotations

import random
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import DATA_DIR, env

JST = ZoneInfo("Asia/Tokyo")

TABLES: dict[str, list[str]] = {
    # stage: new（友だち追加だけ）→ hearing（縁の読みを送ってヒアリング中）→ done（終わりのあいさつ済み）
    # bot: on / hold（店主が手で対応する。つらさの強い言葉が出たら自動で hold）
    "kasumi_users": ["user_id", "display_name", "me_birth", "oshi_birth", "en_type", "state", "worry",
                     "stage", "turns", "step_day", "step_next_at", "step_wait", "bot", "source",
                     "created_at", "updated_at"],
    "kasumi_chats": ["id", "user_id", "role", "text", "created_at"],
    "kasumi_diag": ["code", "me_birth", "oshi_birth", "state", "worry", "en_type", "used",
                    "created_at", "used_at", "source", "user_id"],
    # kind：step の key（oshi_years など）。ボタンで答えたもの
    "kasumi_answers": ["id", "user_id", "kind", "answer", "created_at"],
    # event：view / view_pc / submit / line_click / follow / unfollow / reading / closing / push / danger
    "kasumi_events": ["id", "event", "vid", "created_at"],
}
_KEYS = {"kasumi_users": "user_id", "kasumi_diag": "code"}

_BACKEND = (env("STORE_BACKEND") or "sqlite").lower()
DB_PATH: Path = DATA_DIR / "kasumi.db"


def now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S")


def _new_id() -> str:
    return f"{time.time_ns()}{random.randint(100, 999)}"


# ---------------- 汎用の3操作（追記・全件・1行更新） ----------------
if _BACKEND == "sheets":
    from .. import store_sheets as _S

    for _name, _cols in TABLES.items():
        _S.TABLES.setdefault(_name, _cols)   # _ws がヘッダを知るため。椿の表は触らない

    def init() -> None:
        for name in TABLES:
            _S._ws(name)    # 無ければ作る（ヘッダもここで入る）

    def _append(table: str, row: dict) -> None:
        _S._append(table, row)

    def _rows(table: str) -> list[dict]:
        return _S._records(table)

    def _update(table: str, key: str, updates: dict) -> bool:
        r = _S._find_row(table, _KEYS[table], key)
        if r is None:
            return False
        _S._update_cells(table, r, updates)
        return True

else:
    @contextmanager
    def _conn():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def init() -> None:
        with _conn() as c:
            for name, cols in TABLES.items():
                c.execute(f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(f'{col} TEXT' for col in cols)})")

    def _append(table: str, row: dict) -> None:
        cols = TABLES[table]
        with _conn() as c:
            c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                      [str(row.get(col, "")) for col in cols])

    def _rows(table: str) -> list[dict]:
        with _conn() as c:
            return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def _update(table: str, key: str, updates: dict) -> bool:
        cols = [k for k in updates if k in TABLES[table]]
        if not cols:
            return False
        with _conn() as c:
            cur = c.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in cols)} WHERE {_KEYS[table]}=?",
                            [str(updates[k]) for k in cols] + [key])
            return cur.rowcount > 0


# ---------------- 診断 ----------------
def add_diag(code: str, me_birth: str, oshi_birth: str, state: str, worry: str,
             en_type: str, source: str = "") -> None:
    _append("kasumi_diag", {"code": code, "me_birth": me_birth, "oshi_birth": oshi_birth,
                            "state": state, "worry": worry, "en_type": en_type, "used": "0",
                            "created_at": now(), "used_at": "", "source": source, "user_id": ""})


def get_diag(code: str) -> dict | None:
    for r in _rows("kasumi_diag"):
        if str(r.get("code")) == str(code):
            return r
    return None


def code_exists(code: str) -> bool:
    return get_diag(code) is not None


def mark_diag_used(code: str, user_id: str) -> None:
    _update("kasumi_diag", code, {"used": "1", "used_at": now(), "user_id": user_id})


# ---------------- LINEの人 ----------------
def get_user(user_id: str) -> dict | None:
    for r in _rows("kasumi_users"):
        if r.get("user_id") == user_id:
            return r
    return None


def list_users() -> list[dict]:
    return _rows("kasumi_users")


def upsert_user(user_id: str, **fields) -> dict:
    fields = {k: ("" if v is None else str(v)) for k, v in fields.items() if k in TABLES["kasumi_users"]}
    fields["updated_at"] = now()
    if get_user(user_id):
        _update("kasumi_users", user_id, fields)
    else:
        row = {"user_id": user_id, "stage": "new", "turns": "0", "step_day": "0", "bot": "on",
               "created_at": now(), **fields}
        _append("kasumi_users", row)
    return get_user(user_id) or {"user_id": user_id, **fields}


# ---------------- やりとり・回答・計測 ----------------
def add_chat(user_id: str, role: str, text: str) -> None:
    _append("kasumi_chats", {"id": _new_id(), "user_id": user_id, "role": role, "text": text,
                             "created_at": now()})


def recent_chats(user_id: str, limit: int = 16) -> list[dict]:
    return [r for r in _rows("kasumi_chats") if r.get("user_id") == user_id][-limit:]


def add_answer(user_id: str, kind: str, answer: str) -> None:
    _append("kasumi_answers", {"id": _new_id(), "user_id": user_id, "kind": kind, "answer": answer,
                               "created_at": now()})


def add_event(event: str, vid: str = "") -> None:
    _append("kasumi_events", {"id": _new_id(), "event": event, "vid": vid[:64], "created_at": now()})


def count_events(event: str, created_prefix: str) -> int:
    """created_at が created_prefix（例 "2026-10"）で始まる event の件数。"""
    return sum(1 for r in _rows("kasumi_events")
               if r.get("event") == event and str(r.get("created_at", "")).startswith(created_prefix))

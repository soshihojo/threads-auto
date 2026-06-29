"""Google Sheets バックエンド（store_sqlite.py と同じ公開APIを提供）。

ホスティング時、ダッシュボード(Streamlit Cloud)とGitHub Actionsが
同じスプレッドシートを共有ストアとして使うための実装。
低頻度運用前提（候補は6時間に数件・リードも少数）。

認証: 環境変数
  GOOGLE_SERVICE_ACCOUNT_JSON … サービスアカウントJSONの「ファイルパス」か「JSON文字列」
  GOOGLE_SHEET_KEY            … スプレッドシートのキー（URLの /d/<KEY>/ 部分）
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import re

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

from .config import env

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
JST = ZoneInfo("Asia/Tokyo")

# レイアウト: A列と1行目を空白にする。
# ヘッダは2行目・データはB列(3行目)から書き込む。
ROW_OFFSET = 1                    # ヘッダ上の空行数
COL_OFFSET = 1                    # データ左の空列数
HEADER_ROW = ROW_OFFSET + 1       # ヘッダ行 = 2
FIRST_DATA_ROW = HEADER_ROW + 1   # データ開始行 = 3
FIRST_COL = COL_OFFSET + 1        # データ開始列 = 2 (B)


def _col(idx0: int) -> str:
    """テーブル内0始まり列番号 → 実シートの列文字（例: 0→"B"）。"""
    return re.sub(r"\d+$", "", rowcol_to_a1(1, FIRST_COL + idx0))

# 各ワークシートのヘッダ定義
TABLES = {
    "posts": ["media_id", "text", "profile", "created_at", "views", "likes", "replies", "insights_at"],
    "processed_replies": ["reply_id", "post_id", "username", "text", "seen_at"],
    "draft_replies": ["reply_id", "post_id", "username", "in_text", "draft_text", "status", "created_at", "sent_at"],
    "leads": ["reply_id", "post_id", "username", "text", "keyword", "notified", "created_at"],
    "scheduled_posts": ["id", "text", "region", "scheduled_at", "status", "media_id", "error", "created_at", "posted_at"],
}


def _now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S")


# Sheets APIの「読み取り60回/分・ユーザー」上限(429)対策：
# ① 429が出たら指数バックオフでリトライ（クォータは分単位で回復する）
# ② 同一プロセス内でデータ行の読み取りをキャッシュ。書き込みで無効化し、
#    init_db()（Streamlitは描画ごとに先頭で呼ぶ）で全クリア＝描画ごとに最新化。
_CACHE: dict[str, list] = {}
_RETRY_DELAYS = (0, 5, 12, 25, 45)  # 秒


def _api(fn, *args, **kwargs):
    """gspreadのネットワーク呼び出しを429リトライ付きで実行する。"""
    last = None
    for delay in _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code == 429:
                last = e
                continue
            raise
    raise last


def _creds_info() -> dict:
    raw = env("GOOGLE_SERVICE_ACCOUNT_JSON", required=True).strip()
    # JSON文字列を直接渡しているケース（ホスティング時のSecrets）
    if raw.startswith("{"):
        return json.loads(raw)
    # それ以外はファイルパス（ローカル）。長すぎる文字列で OSError にならないよう保護
    try:
        p = Path(raw)
        if p.exists():
            return json.loads(p.read_text())
    except OSError:
        pass
    return json.loads(raw)


@lru_cache(maxsize=1)
def _spreadsheet():
    gc = gspread.authorize(Credentials.from_service_account_info(_creds_info(), scopes=SCOPES))
    return gc.open_by_key(env("GOOGLE_SHEET_KEY", required=True))


@lru_cache(maxsize=None)
def _ws(name: str):
    sh = _spreadsheet()
    headers = TABLES[name]
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=1000, cols=FIRST_COL + len(headers))
    # ヘッダ行（2行目・B列〜）が空なら入れる
    hdr_rng = f"{_col(0)}{HEADER_ROW}:{_col(len(headers) - 1)}{HEADER_ROW}"
    existing = _api(ws.get, hdr_rng)
    if not existing or not any(existing[0]):
        _api(ws.update, range_name=f"{_col(0)}{HEADER_ROW}", values=[headers])
    return ws


def _data_rows(name: str) -> list[list]:
    """データ行（FIRST_DATA_ROW以降・B列〜）の生の値を返す。読み取りはキャッシュ。"""
    if name in _CACHE:
        return _CACHE[name]
    headers = TABLES[name]
    rng = f"{_col(0)}{FIRST_DATA_ROW}:{_col(len(headers) - 1)}"
    rows = _api(_ws(name).get, rng) or []
    _CACHE[name] = rows
    return rows


def _records(name: str) -> list[dict]:
    headers = TABLES[name]
    out = []
    for row in _data_rows(name):
        if not any(c != "" for c in row):
            continue  # 空行はスキップ
        out.append({h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)})
    return out


def _append(name: str, row: dict) -> None:
    headers = TABLES[name]
    # table_range をヘッダ(B2)にすると、その表の最終行の次（B列〜）に追記される。
    # RAW: SheetsがISO日時(...T...)を日付へ自動変換するのを防ぎ、文字列をそのまま保存する
    #（scheduled_at の文字列比較が壊れないようにするため必須）。
    _api(
        _ws(name).append_row,
        [row.get(h, "") for h in headers],
        value_input_option="RAW",
        table_range=f"{_col(0)}{HEADER_ROW}",
    )
    _CACHE.pop(name, None)  # 書き込みでキャッシュ無効化


def _find_row(name: str, key_col: str, key_val) -> int | None:
    """key_col==key_val の実シート行番号（1始まり）を返す。無ければNone。"""
    headers = TABLES[name]
    col_idx = headers.index(key_col)
    for i, row in enumerate(_data_rows(name)):
        if col_idx < len(row) and str(row[col_idx]) == str(key_val):
            return FIRST_DATA_ROW + i
    return None


def _update_cells(name: str, row_idx: int, updates: dict) -> None:
    headers = TABLES[name]
    ws = _ws(name)
    for col, val in updates.items():
        a1 = rowcol_to_a1(row_idx, FIRST_COL + headers.index(col))
        # RAW: ISO日時の日付自動変換を防ぐ（_append と同じ理由）
        _api(ws.update, range_name=a1, values=[[val]], value_input_option="RAW")
    _CACHE.pop(name, None)  # 書き込みでキャッシュ無効化


def init_db() -> None:
    _CACHE.clear()  # 描画ごとに最新化（Streamlitは先頭で毎回呼ぶ）
    for name in TABLES:
        _ws(name)  # 無ければ作成


# ---- posts ----
def save_post(media_id: str, text: str, profile: str) -> None:
    if _find_row("posts", "media_id", media_id):
        return
    _append("posts", {"media_id": media_id, "text": text, "profile": profile,
                      "created_at": _now(), "views": 0, "likes": 0, "replies": 0})


def update_insights(media_id: str, views: int, likes: int, replies: int) -> None:
    idx = _find_row("posts", "media_id", media_id)
    if idx:
        _update_cells("posts", idx, {"views": views, "likes": likes, "replies": replies, "insights_at": _now()})


# ---- replies ----
def is_reply_seen(reply_id: str) -> bool:
    return _find_row("processed_replies", "reply_id", reply_id) is not None


def mark_reply_seen(reply_id: str, post_id: str, username: str, text: str) -> None:
    if is_reply_seen(reply_id):
        return
    _append("processed_replies", {"reply_id": reply_id, "post_id": post_id,
                                  "username": username, "text": text, "seen_at": _now()})


# ---- draft replies ----
def add_draft(reply_id: str, post_id: str, username: str, in_text: str, draft_text: str) -> None:
    if _find_row("draft_replies", "reply_id", reply_id):
        return
    _append("draft_replies", {"reply_id": reply_id, "post_id": post_id, "username": username,
                             "in_text": in_text, "draft_text": draft_text, "status": "pending", "created_at": _now()})


def pending_drafts() -> list[dict]:
    return [r for r in _records("draft_replies") if r.get("status") == "pending"]


def set_draft_status(reply_id: str, status: str, *, sent: bool = False) -> None:
    idx = _find_row("draft_replies", "reply_id", reply_id)
    if idx:
        updates = {"status": status}
        if sent:
            updates["sent_at"] = _now()
        _update_cells("draft_replies", idx, updates)


def update_draft_text(reply_id: str, draft_text: str) -> None:
    idx = _find_row("draft_replies", "reply_id", reply_id)
    if idx:
        _update_cells("draft_replies", idx, {"draft_text": draft_text})


# ---- leads ----
def add_lead(reply_id: str, post_id: str, username: str, text: str, keyword: str) -> bool:
    if _find_row("leads", "reply_id", reply_id):
        return False
    _append("leads", {"reply_id": reply_id, "post_id": post_id, "username": username,
                     "text": text, "keyword": keyword, "notified": 0, "created_at": _now()})
    return True


def mark_lead_notified(reply_id: str) -> None:
    idx = _find_row("leads", "reply_id", reply_id)
    if idx:
        _update_cells("leads", idx, {"notified": 1})


def recent_leads(limit: int = 50) -> list[dict]:
    rows = sorted(_records("leads"), key=lambda r: r.get("created_at", ""), reverse=True)
    return rows[:limit]


# ---- scheduled posts / candidates ----
def _next_id() -> int:
    ids = [int(r["id"]) for r in _records("scheduled_posts") if str(r.get("id", "")).strip().isdigit()]
    return (max(ids) + 1) if ids else 1


def add_candidate(text: str, region: str | None) -> int:
    new_id = _next_id()
    _append("scheduled_posts", {"id": new_id, "text": text, "region": region or "", "scheduled_at": "",
                               "status": "pending_review", "created_at": _now()})
    return new_id


def approve_candidate(post_id: int, scheduled_at: str, text: str | None = None) -> None:
    idx = _find_row("scheduled_posts", "id", post_id)
    if not idx:
        return
    updates = {"status": "scheduled", "scheduled_at": scheduled_at}
    if text is not None:
        updates["text"] = text
    _update_cells("scheduled_posts", idx, updates)


def add_scheduled(text: str, region: str | None, scheduled_at: str) -> int:
    new_id = _next_id()
    _append("scheduled_posts", {"id": new_id, "text": text, "region": region or "",
                               "scheduled_at": scheduled_at, "status": "scheduled", "created_at": _now()})
    return new_id


def list_scheduled(status: str | None = None, limit: int = 200) -> list[dict]:
    rows = _records("scheduled_posts")
    if status:
        rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda r: str(r.get("scheduled_at", "")))
    else:
        rows.sort(key=lambda r: str(r.get("scheduled_at", "")), reverse=True)
    return rows[:limit]


def due_scheduled(now_iso: str) -> list[dict]:
    rows = [r for r in _records("scheduled_posts")
            if r.get("status") == "scheduled" and str(r.get("scheduled_at", "")) and str(r["scheduled_at"]) <= now_iso]
    rows.sort(key=lambda r: str(r.get("scheduled_at", "")))
    return rows


def mark_scheduled(post_id: int, status: str, *, media_id: str | None = None, error: str | None = None) -> None:
    idx = _find_row("scheduled_posts", "id", post_id)
    if not idx:
        return
    updates = {"status": status}
    if media_id is not None:
        updates["media_id"] = media_id
    if error is not None:
        updates["error"] = error
    if status == "posted":
        updates["posted_at"] = _now()
    _update_cells("scheduled_posts", idx, updates)


def cancel_scheduled(post_id: int) -> None:
    idx = _find_row("scheduled_posts", "id", post_id)
    if not idx:
        return
    rows = {r["id"]: r for r in _records("scheduled_posts")}
    cur = rows.get(post_id) or rows.get(str(post_id))
    if cur and cur.get("status") in ("scheduled", "pending_review"):
        _update_cells("scheduled_posts", idx, {"status": "canceled"})


def update_scheduled(post_id: int, text: str, scheduled_at: str) -> None:
    idx = _find_row("scheduled_posts", "id", post_id)
    if idx:
        _update_cells("scheduled_posts", idx, {"text": text, "scheduled_at": scheduled_at})

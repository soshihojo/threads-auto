"""通知（Chatwork）。手挙げリードを即フォローできるよう知らせる。"""
from __future__ import annotations

import requests

from .config import env


def chatwork(message: str) -> bool:
    token = env("CHATWORK_API_TOKEN")
    room = env("CHATWORK_ROOM_ID")
    if not token or not room:
        print("[notify] Chatwork未設定のため通知スキップ:\n" + message)
        return False
    try:
        resp = requests.post(
            f"https://api.chatwork.com/v2/rooms/{room}/messages",
            headers={"X-ChatWorkToken": token},
            data={"body": message},
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[notify] Chatwork通知失敗: {e}")
        return False


def lead_message(username: str, text: str, keyword: str, permalink: str | None = None) -> str:
    lines = [
        "[info][title]🔥 Threads手挙げリード[/title]",
        f"ユーザー: @{username}",
        f"検知ワード: {keyword}",
        f"コメント: {text}",
    ]
    if permalink:
        lines.append(f"投稿: {permalink}")
    lines.append("→ DM/返信で早めにフォローを[/info]")
    return "\n".join(lines)

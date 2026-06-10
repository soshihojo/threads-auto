"""予約投稿の実行。予約時刻(JST)が来たものを自動投稿する。
GitHub Actions から定期的に run_due() を叩く想定。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from . import store
from .config import active_profile
from .threads_client import ThreadsClient

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    return datetime.now(JST)


def now_jst_iso() -> str:
    # 比較用。秒まで。例: 2026-06-09T20:00:00
    return now_jst().strftime("%Y-%m-%dT%H:%M:%S")


def run_due(client: ThreadsClient) -> dict:
    """期限が来た予約投稿を配信。結果サマリを返す。"""
    store.init_db()
    profile = active_profile()
    due = store.due_scheduled(now_jst_iso())
    stats = {"due": len(due), "posted": 0, "failed": 0}
    for row in due:
        text = row["text"]
        region = row["region"]
        try:
            loc_id = client.first_location_id(region) if (profile.get("tag_location") and region) else None
            media_id = client.publish_text(text, location_id=loc_id)
            store.mark_scheduled(row["id"], "posted", media_id=media_id)
            store.save_post(media_id, text, profile["name"])
            stats["posted"] += 1
            print(f"✅ 予約投稿 配信: id={row['id']} media_id={media_id}")
        except Exception as e:
            store.mark_scheduled(row["id"], "failed", error=str(e))
            stats["failed"] += 1
            print(f"❌ 予約投稿 失敗: id={row['id']} {e}")
    return stats

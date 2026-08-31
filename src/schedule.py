"""予約投稿の実行。予約時刻(JST)が来たものを自動投稿する。
GitHub Actions から定期的に run_due() を叩く想定。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from . import store
from .config import active_profile, profile_for
from .content import sanitize
from .threads_client import ThreadsClient

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    return datetime.now(JST)


def now_jst_iso() -> str:
    # 比較用。秒まで。例: 2026-06-09T20:00:00
    return now_jst().strftime("%Y-%m-%dT%H:%M:%S")


def run_due(client: ThreadsClient, account: str | None = None) -> dict:
    """期限が来た予約投稿を配信。結果サマリを返す。

    ★2026-08-31：account を渡したら、そのアカウント宛の行だけ流す。
      渡さんかったら今まで通り全部（＝一本目しか無かった頃の動き）。
    """
    store.init_db()
    profile = profile_for(account)
    due = store.due_scheduled(now_jst_iso(), account)
    stats = {"due": len(due), "posted": 0, "failed": 0}
    for row in due:
        text = sanitize(row["text"])  # 投稿直前に装飾記号(**等)を必ず除去
        # ★2026-08-09の事故対策：同一本文を配信済みなら絶対に再配信せん。
        #   手貼りの予約行が id 重複（345が2行）しとって、「済み」マークが
        #   別の行に書かれ続け、15分ごとに同じ投稿が42回配信された。
        #   原因が何であれ（id重複・マーク失敗・シートの手編集）、
        #   「同じ本文は二度と出さん」を最後の壁にする。
        if store.was_posted_before(text):
            store.mark_scheduled(row["id"], "skipped", account=account,
                                 error="同一本文を配信済み（再配信ループ防止）")
            print(f"⏭  予約スキップ（配信済み本文）: id={row['id']}")
            continue
        try:
            media_id = client.publish_thread(text)
            store.mark_scheduled(row["id"], "posted", media_id=media_id, account=account)
            store.save_post(media_id, text, profile["name"])
            stats["posted"] += 1
            print(f"✅ 予約投稿 配信: id={row['id']} media_id={media_id}")
        except Exception as e:
            store.mark_scheduled(row["id"], "failed", error=str(e), account=account)
            stats["failed"] += 1
            print(f"❌ 予約投稿 失敗: id={row['id']} {e}")
    return stats

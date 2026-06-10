"""インサイト取得と学習アーカイブ。
投稿の反応を取得してDBへ保存し、伸びた投稿をルール集の archive へ追記する。"""
from __future__ import annotations

from datetime import datetime

from . import store
from .config import ROOT, active_profile
from .threads_client import ThreadsClient


def collect_insights(client: ThreadsClient, lookback: int = 25) -> list[dict]:
    """直近投稿のインサイトを取得してDB更新。結果リストを返す。"""
    store.init_db()
    results: list[dict] = []
    for post in client.my_threads(limit=lookback):
        mid = post["id"]
        try:
            ins = client.media_insights(mid)
        except Exception as e:
            print(f"[analytics] insights取得失敗 {mid}: {e}")
            continue
        views = ins.get("views", 0)
        likes = ins.get("likes", 0)
        replies = ins.get("replies", 0)
        store.save_post(mid, post.get("text", ""), active_profile()["name"])
        store.update_insights(mid, views, likes, replies)
        results.append({
            "media_id": mid,
            "text": post.get("text", ""),
            "views": views, "likes": likes, "replies": replies,
            "engagement": round((likes + replies) / views, 4) if views else 0,
            "permalink": post.get("permalink"),
        })
    return results


def archive_winners(results: list[dict], *, min_engagement: float = 0.05, top: int = 3) -> int:
    """エンゲージ率が高い投稿を rules_dir/99_post_archive.md に追記。追記件数を返す。"""
    winners = sorted(
        [r for r in results if r["engagement"] >= min_engagement],
        key=lambda r: r["engagement"], reverse=True,
    )[:top]
    if not winners:
        return 0
    archive = ROOT / active_profile()["rules_dir"] / "99_post_archive.md"
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"\n### {today} 自社の伸びた投稿（自動記録）"]
    for r in winners:
        lines.append(
            f"- エンゲージ率{r['engagement']:.1%}（views {r['views']} / いいね {r['likes']} / リプ {r['replies']}）\n"
            f"  > {r['text']}"
        )
    with open(archive, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(winners)

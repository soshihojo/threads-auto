"""インサイト取得と学習アーカイブ。
投稿の反応を取得してDBへ保存し、伸びた投稿をルール集の archive へ追記する。"""
from __future__ import annotations

from datetime import datetime

from . import store
from .config import ROOT, active_profile
from .llm import complete
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


# ===================== 学習ループ（勝ち要素の抽象化→ルール自動更新） =====================

LEARNED_FILE = "05_learned.md"

_LEARN_SYS = """あなたは地域店舗向けThreads運用の分析担当です。
このアカウントのゴールは「いいね」ではなく【手挙げリード（コメントで興味を示す店舗オーナー）の獲得】です。
実データ（伸びた投稿・スベった投稿とその反応数）から、次の投稿生成に効く"必勝要素"を抽象化して言語化します。
- 個別の投稿をそのままコピペするのではなく、再現可能な「型・切り口・冒頭フックの特徴」に一般化する
- 手挙げ（リプ/リード）を生んだ要因を最優先で抽出する。逆にviewsは出たのに無反応だった投稿の「なぜスベったか」も言語化する
- 既存の学びは、まだ有効なら引き継ぎ、データと矛盾するものは捨て、新しい発見を足す（重複は統合）
- 箇条書きで最大12項目。各項目は具体的に。マーケ横文字は避け、店主の現場語で
- 出力はMarkdownの本文のみ（見出し・前置き・コードフェンス不要）"""


def _leads_by_post() -> dict[str, int]:
    # Sheetsバックエンドはdict、SQLiteはsqlite3.Rowを返す。両者とも L["post_id"] で引ける。
    counts: dict[str, int] = {}
    for L in store.recent_leads(limit=300):
        try:
            pid = str(L["post_id"])
        except (KeyError, IndexError):
            continue
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def learn_and_update_rules(
    client: ThreadsClient, *, lookback: int = 60, min_posts: int = 8, top: int = 5, bottom: int = 5
) -> dict:
    """反応データを集計し、勝ち/スベりをClaudeに抽象化させて rules_dir/05_learned.md を上書き更新。"""
    results = collect_insights(client, lookback=lookback)
    leads = _leads_by_post()
    for r in results:
        r["leads"] = leads.get(str(r["media_id"]), 0)

    scored = [r for r in results if r["views"] > 0]
    if len(scored) < min_posts:
        return {"updated": False, "reason": f"学習に必要な投稿数が不足（{len(scored)}/{min_posts}件）"}

    # 勝ち = リード数→エンゲージ率の順。スベり = その逆（views出てるのに無反応）
    winners = sorted(scored, key=lambda r: (r["leads"], r["engagement"]), reverse=True)[:top]
    losers = sorted(scored, key=lambda r: (r["leads"], r["engagement"]))[:bottom]

    learned_path = ROOT / active_profile()["rules_dir"] / LEARNED_FILE
    current = learned_path.read_text(encoding="utf-8") if learned_path.exists() else "（まだ学習記録なし）"

    def _fmt(r: dict) -> str:
        return (f"- [views {r['views']} / いいね {r['likes']} / リプ {r['replies']} / リード {r['leads']}]\n"
                f"  本文: {r['text']}")

    user = (
        "## これまでの学び（引き継ぎ・更新の土台）\n" + current + "\n\n"
        "## 今回の実データ\n"
        "### 手挙げ・反応を取れた投稿（勝ち）\n" + "\n".join(_fmt(r) for r in winners) + "\n\n"
        "### 表示は出たのに反応が薄かった投稿（スベり）\n" + "\n".join(_fmt(r) for r in losers) + "\n\n"
        "上記をふまえ、次の投稿生成に効く必勝要素へ更新してください。"
    )
    learn_system = active_profile().get("learn_system") or _LEARN_SYS
    body = complete(learn_system, user, max_tokens=1500, temperature=0.4)

    today = datetime.now().strftime("%Y-%m-%d")
    header = (
        f"# 学習で得た必勝要素（自動更新 / 最終更新 {today}）\n\n"
        "※ このファイルは `learn` コマンドが実データから自動生成・上書きします。手で編集しても次回の学習で上書きされます。\n"
        "投稿生成時、03_winning_elements.md と合わせてここの知見も反映すること。\n\n"
    )
    learned_path.write_text(header + body.strip() + "\n", encoding="utf-8")
    return {"updated": True, "winners": len(winners), "losers": len(losers),
            "analyzed": len(scored), "path": str(learned_path)}

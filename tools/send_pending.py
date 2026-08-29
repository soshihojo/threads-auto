# -*- coding: utf-8 -*-
"""pending のまま取り残された返信を、直接送る。

★★2026-08-29 新設。四件が半月放置されとった（8/12・8/14×2・8/23）。
　原因：自動送信に失敗しても processed_replies の既読の印が残っとって、
　　　　次の巡回で拾い直されんかった。★そこは replies.py 側で直した。
　★せやけど、この四件が付いた投稿は、もう窓（lookback_posts）の外や（359〜403位）。
　　既読を外しても、巡回では二度と拾えん。★せやから、ここから直接送る。

　使い方（GitHub Actions から。★手元のトークンは切れとることがある）：
　　gh workflow run send-pending.yml
"""
from __future__ import annotations
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.threads_client import ThreadsClient          # noqa: E402
from src.config import env, load_config               # noqa: E402
from src import store                                 # noqa: E402


def main() -> int:
    store.init_db()
    pend = [r for r in store.list_drafts(status="pending")] \
        if hasattr(store, "list_drafts") else \
        [r for r in store._records("draft_replies") if str(r.get("status")) == "pending"]
    print(f"pending {len(pend)}件")
    if not pend:
        print("送るもんは無い。")
        return 0

    gap = int(load_config().get("safety", {}).get("min_seconds_between_actions", 30))
    c = ThreadsClient(env("THREADS_ACCESS_TOKEN"), env("THREADS_USER_ID"))
    ok = ng = 0
    for i, r in enumerate(pend):
        rid, u = str(r.get("reply_id")), r.get("username")
        d = str(r.get("draft_text") or "").strip()
        if not d:
            print(f"  ⏭ @{u} 下書きが空")
            continue
        if i:
            time.sleep(gap)      # ★連投は凍結の的になる。必ず間を空ける
        try:
            c.reply_to(rid, d)
            store.set_draft_status(rid, "sent", sent=True)
            ok += 1
            print(f"  ✅ @{u}  {d[:44]}")
        except Exception as e:
            ng += 1
            print(f"  ❌ @{u}: {e}")
    print(f"\n送れた {ok}件 / 失敗 {ng}件")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())

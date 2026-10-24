# -*- coding: utf-8 -*-
"""投稿ごとに、診断 → LINE → 購入 まで何人進んだかを数える。

★2026-10-25 新設（店主の指示）。
　コメント返信の診断リンクに、投稿ごとの印（a=cr1590 など）を付けるようにした。
　その印が web_diag.source に残るんで、ここで投稿ごとに束ねる。

　使い方：
　　.venv/bin/python tools/post_sales.py            … 印の付いた投稿を、購入の多い順に
　　.venv/bin/python tools/post_sales.py --days 7   … 直近7日の診断だけ

★数え方の断り書き
　・診断＝診断ページで送信した数（web_diag の行）。
　・LINE＝その鑑定番号をLINEに送ってきた人の数。
　・購入＝そのLINEの人が、あとで10桁のオーダー番号を送ってきた数（商品は区別せん）。
　・★印は 2026-10-25 から付け始めた。それより前の診断は「cr」だけで、投稿までは分からん。
"""
from __future__ import annotations
import argparse, re, sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import store_sheets as ss  # noqa: E402

_ORDER = re.compile(r"(?<!\d)\d{10}(?!\d)")
_CODE = re.compile(r"鑑定番号\s*(\d+)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="直近◯日の診断だけ数える（0＝全部）")
    a = ap.parse_args()
    since = (datetime.now() - timedelta(days=a.days)).strftime("%Y-%m-%d") if a.days else ""

    sched = {str(r.get("id")): str(r.get("text") or "") for r in ss._records("scheduled_posts")}
    # 鑑定番号 → LINEの人、その人が買うたか
    code_user: dict[str, str] = {}
    bought: set[str] = set()
    for c in ss._records("line_chats"):
        if c.get("role") != "user":
            continue
        t = str(c.get("text") or "")
        m = _CODE.search(t)
        if m and m.group(1) not in code_user:
            code_user[m.group(1)] = c["user_id"]
        if _ORDER.search(t):
            bought.add(c["user_id"])

    agg: dict[str, dict] = defaultdict(lambda: {"diag": 0, "line": 0, "buy": 0})
    for w in ss._records("web_diag"):
        src = str(w.get("source") or "")
        if not src.startswith("cr") or src == "cr":
            continue
        if since and str(w.get("created_at") or "") < since:
            continue
        g = agg[src]
        g["diag"] += 1
        uid = code_user.get(str(w.get("code")))
        if uid:
            g["line"] += 1
            if uid in bought:
                g["buy"] += 1

    if not agg:
        print("まだ投稿ごとの印が付いた診断はありません（2026-10-25 から付け始め）")
        return
    print(f"{'印':<16}{'診断':>5}{'LINE':>6}{'購入':>5}  投稿の一行目")
    for src, g in sorted(agg.items(), key=lambda kv: (-kv[1]["buy"], -kv[1]["line"], -kv[1]["diag"])):
        pid = src[2:]
        head = sched.get(pid, "").split("\n")[0][:30] if pid in sched else "（予約表に無い投稿）"
        print(f"{src:<16}{g['diag']:>5}{g['line']:>6}{g['buy']:>5}  {head}")


if __name__ == "__main__":
    main()

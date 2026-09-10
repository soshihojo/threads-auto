# -*- coding: utf-8 -*-
"""過去のコメントに【書かれた本当の時刻】を入れ直す。

　　.venv/bin/python tools/backfill_reply_times.py --from 2026-08-01 --to 2026-09-09
　　.venv/bin/python tools/backfill_reply_times.py --dry-run

★なんで要るか
　　コメント数は今まで seen_at（巡回が見つけた時刻）で数えとった。巡回が止まった日は
　　その日のコメントが翌日に付け替わる。実例：8/10は巡回が3コマしか動かず9件しか
　　記録されてへんのに、返信は26件送っとった。翌8/11の朝5時に20件まとめて拾って121件。
★Threads APIは元から timestamp を返しとる。保存してへんかっただけや。
　　投稿を一本ずつたどって返信を取り直し、reply_id で突き合わせて posted_at を埋める。
★消された投稿の返信は取れん。そこは seen_at のままになる。
"""
from __future__ import annotations
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import store_sheets as ss                    # noqa: E402
from src.config import env                            # noqa: E402
from src.threads_client import ThreadsClient          # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2026-08-01")
    ap.add_argument("--to", dest="d_to", default="2026-12-31")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ss._CACHE.clear()

    # 埋める対象＝posted_at が空の行
    todo = {}
    for r in ss._records("processed_replies"):
        if str(r.get("posted_at") or "").strip():
            continue
        d = str(r.get("seen_at"))[:10]
        if a.d_from <= d <= a.d_to:
            todo[str(r.get("reply_id"))] = str(r.get("post_id"))
    if not todo:
        print("埋めるもんは無い")
        return 0
    posts = sorted({v for v in todo.values() if v})
    print(f"対象 {len(todo)}件／投稿 {len(posts)}本をたどる")

    b_ids = {str(r.get("media_id")) for r in ss._records("scheduled_posts_b") if r.get("media_id")}
    ca = ThreadsClient(env("THREADS_ACCESS_TOKEN", required=True), env("THREADS_USER_ID"))
    try:
        cb = ThreadsClient(env("THREADS_ACCESS_TOKEN_B", required=True), env("THREADS_USER_ID_B"))
    except Exception:
        cb = None

    found, gone = {}, 0
    for i, pid in enumerate(posts, 1):
        c = cb if (pid in b_ids and cb) else ca
        try:
            for rep in c.replies(pid, top_level_only=False):
                rid = str(rep.get("id"))
                if rid in todo and rep.get("timestamp"):
                    found[rid] = str(rep["timestamp"])
        except Exception:
            gone += 1
        if i % 25 == 0:
            print(f"  …{i}/{len(posts)}本（拾えた {len(found)}件）")
    print(f"拾えた {len(found)}件／取れん投稿 {gone}本")

    moved = Counter()
    rows = {str(r.get("reply_id")): r for r in ss._records("processed_replies")}
    for rid, ts in found.items():
        old = str(rows[rid].get("seen_at"))[:10]
        if ts[:10] != old:
            moved[f"{old} → {ts[:10]}"] += 1
    for k, n in moved.most_common(15):
        print(f"  {k}: {n}件")

    if a.dry_run:
        print(f"[dry-run] {len(found)}件を書く予定")
        return 0
    ws = ss._ws("processed_replies")
    col = ss._col(ss.TABLES["processed_replies"].index("posted_at"))
    # ★_records は空行を飛ばすんで、その並びで行番号を数えたら【全部ずれる】。
    #   生の行（_data_rows）で数えること。reply_id は先頭の列や。
    idx = {}
    for i, row in enumerate(ss._data_rows("processed_replies")):
        if row and str(row[0]).strip():
            idx[str(row[0])] = i
    reqs = [{"range": f"{col}{ss.FIRST_DATA_ROW + idx[rid]}", "values": [[ts]]}
            for rid, ts in found.items() if rid in idx]
    for n in range(0, len(reqs), 500):        # 一度に投げ過ぎて落とさん
        ss._api(ws.batch_update, reqs[n:n + 500], value_input_option="RAW")
        print(f"  書いた {min(n + 500, len(reqs))}/{len(reqs)}")
    ss._CACHE.pop("processed_replies", None)
    print(f"✅ {len(reqs)}件に本当の時刻を入れた")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

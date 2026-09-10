# -*- coding: utf-8 -*-
"""デイリーレポートのスプシを自動で埋める（日を跨いだら前日ぶんを確定させる）。

　使い方：
　　.venv/bin/python tools/daily_report.py                  … 直近7日を書き直す（既定・昨日まで）
　　.venv/bin/python tools/daily_report.py --days 40
　　.venv/bin/python tools/daily_report.py --from 2026-08-01 --to 2026-09-09
　　.venv/bin/python tools/daily_report.py --no-insights    … Threads APIを叩かん（集計だけ）
　　.venv/bin/python tools/daily_report.py --dry-run        … 書かずに表だけ出す

★なんで「直近7日を書き直す」のか（1日ぶんだけ足す作りにせん理由）
　・GitHub Actions の schedule は間引かれる。実際 poll.yml が7時間半死んだことがある。
　　★1日ぶんだけ足す作りやと、発火せんかった日が永久に空白のまま残る。
　　★★7日ぶん書き直す作りなら、次に走った時に穴が勝手に埋まる。
　・表示回数は投稿した直後には確定せん。あとから伸びる。
　　★昨日の数字を一回書いて終わりにすると、実際より低い数字が残る。

★ポスト数の数え方（2026-09-10）
　　insights_at が空のまま2日以上たった投稿は【Threads上にもう無い】ものとして数えん。
　　実例：8/9の深夜に41本の連投があって、その41本は削除済みでAPIが404を返す。
　　本数だけ数えると1本あたり表示回数が172まで落ちて、実態と違う数字になる。

★列は固定で持たん。2行目の見出しを読んで書き込む列を決める。
　　店主が列を足したり消したりした翌日に、黙って隣の列へ書き込む事故を防ぐため。
　　率の列（コメント率・診断率など）は【空いとる所だけ】式を入れる。手で直した式は触らん。

★フォロワー総数
　　Threads APIの followers_count は【総数しか返さん】。since/until を付けても値が変わらん。
　　日別の増加を出す道は「毎日の総数をひかえて差を取る」以外に無いんで、
　　本表に列が無い今も「_フォロワー」タブへ記録だけ残す。後で列を足せる。
"""
from __future__ import annotations
import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import store_sheets as ss                                    # noqa: E402
from src.config import env                                            # noqa: E402
from src.store_sheets import SCOPES, _creds_info                      # noqa: E402
from src.threads_client import ThreadsClient                          # noqa: E402
from tools.uriage import (KANTEI, OFFER_MARKS, SHIOMI, _shiomi_names,  # noqa: E402
                          is_shiomi_row)

JST = timezone(timedelta(hours=9))
FOLLOWER_TAB = "_フォロワー"
HEADER_ROW, FIRST_ROW = 2, 3
# ★2026-09-10：列は固定で持たん。【2行目の見出しを読んで列を決める】。
#   店主が列を足したり消したりした翌日に、黙って隣の列へ書き込む事故を防ぐため。
#   実際この日、フォロー数を消して診断率を足す組み替えがあった。
DATA_COLS = {"日": "日", "ポスト数": "ポスト数", "ポスト表示回数": "表示",
             "コメント数": "コメ", "診断完了数": "診断", "LINE追加数": "追加",
             "ブロック数": "ブロック", "オファー到達数": "オファー",
             "購入数": "購入", "売上": "売上"}
# 率の列は（分子の見出し, 分母の見出し）で持つ。列がずれても式が壊れん
RATE_COLS = {"コメント率": ("コメント数", "ポスト表示回数"),
             "診断率": ("診断完了数", "コメント数"),
             "LINE追加率": ("LINE追加数", "診断完了数"),
             "ブロック率": ("ブロック数", "LINE追加数"),
             "オファー到達率": ("オファー到達数", "LINE追加数"),
             "購入率": ("購入数", "オファー到達数")}
# insights_at が空のままこの日数を過ぎたら「もう無い投稿」とみなす
GRACE_DAYS = 2


# ---------------------------------------------------------------- 表示回数
def refresh_insights(days: int) -> None:
    """直近 days 日の投稿のうち、まだ数字を取れてへんものを取りにいく。"""
    since = (datetime.now(JST).date() - timedelta(days=days)).isoformat()
    targets = [r for r in ss._records("posts")
               if str(r.get("created_at"))[:10] >= since
               and not str(r.get("insights_at") or "").strip()]
    if not targets:
        print("[report] 表示回数は全部取れとる")
        return
    b_ids = {str(r.get("media_id")) for r in ss._records("scheduled_posts_b") if r.get("media_id")}
    clients: dict[str, ThreadsClient | None] = {}

    def client(is_b: bool) -> ThreadsClient | None:
        key = "b" if is_b else "a"
        if key not in clients:
            try:
                clients[key] = ThreadsClient(
                    env(f"THREADS_ACCESS_TOKEN{'_B' if is_b else ''}", required=True),
                    env(f"THREADS_USER_ID{'_B' if is_b else ''}"))
            except Exception as e:
                print(f"[report] {key}口のトークンが無い: {str(e)[:80]}")
                clients[key] = None
        return clients[key]

    got = gone = 0
    for r in targets:
        mid = str(r.get("media_id"))
        c = client(mid in b_ids)
        if c is None:
            continue
        try:
            ins = c.media_insights(mid)
        except Exception as e:
            # 「Object with ID does not exist」＝消された投稿。追いかけても無駄
            gone += 1
            if gone <= 3:
                print(f"[report] 取れん {mid}: {str(e)[:90]}")
            continue
        ss.update_insights(mid, ins.get("views", 0), ins.get("likes", 0), ins.get("replies", 0))
        got += 1
    print(f"[report] 表示回数を {got}本ぶん取り直した（取れんかった {gone}本）")


# ---------------------------------------------------------------- 集計
def collect(d_from: str, d_to: str) -> list[dict]:
    ok = lambda d: d_from <= d <= d_to                                # noqa: E731
    limit = (datetime.now(JST).date() - timedelta(days=GRACE_DAYS)).isoformat()

    posts, views = Counter(), Counter()
    for r in ss._records("posts"):
        d = str(r.get("created_at"))[:10]
        if not ok(d):
            continue
        has = bool(str(r.get("insights_at") or "").strip())
        if not has and d < limit:
            continue                                                  # もう無い投稿
        posts[d] += 1
        try:
            views[d] += int(float(r.get("views") or 0))
        except (TypeError, ValueError):
            pass

    # ★コメントは【書かれた本当の時刻】で数える。posted_at が無い古い行だけ seen_at に落とす。
    #   seen_at は巡回が見つけた時刻やから、巡回が止まった日は翌日へ付け替わる。
    #   実例：8/10は巡回が3コマしか動かずコメント9件、翌8/11の朝5時に20件まとめて拾って121件。
    com = Counter()
    for r in ss._records("processed_replies"):
        d = (str(r.get("posted_at") or "") or str(r.get("seen_at") or ""))[:10]
        if ok(d):
            com[d] += 1

    ev = defaultdict(Counter)
    for r in ss._records("web_events"):
        d = str(r.get("created_at"))[:10]
        if ok(d):
            ev[d][r.get("event")] += 1

    users = {u["user_id"]: str(u.get("display_name") or "") for u in ss._records("line_users")}
    ledger = _shiomi_names()
    off, buy = defaultdict(set), defaultdict(list)
    for r in ss._records("line_chats"):
        d = str(r.get("created_at"))[:10]
        if not ok(d):
            continue
        t = str(r.get("text") or "")
        if r.get("role") == "user":
            if re.search(r"(?<!\d)\d{10}(?!\d)", re.sub(r"[\s\-]", "", t)):
                buy[d].append(r["user_id"])
        elif any(m in t for m in OFFER_MARKS):
            off[d].add(r["user_id"])

    out = []
    d = date.fromisoformat(d_from)
    while d.isoformat() <= d_to:
        k = d.isoformat()
        us = buy.get(k, [])
        s = sum(1 for u in us if is_shiomi_row(u, users.get(u, ""), ledger))
        out.append({"日": k, "ポスト数": posts[k], "表示": views[k], "コメ": com[k],
                    "診断": ev[k]["submit"], "追加": ev[k]["line_follow"],
                    "ブロック": ev[k]["line_unfollow"], "オファー": len(off.get(k, ())),
                    "購入": len(us), "売上": s * SHIOMI + (len(us) - s) * KANTEI})
        d += timedelta(days=1)
    return out


def _as_date(v) -> str:
    """B列の値を YYYY-MM-DD に均す。

    ★Sheetsは日付をシリアル値（1899-12-30からの日数）で返してくる。
      ここを素で str() しとったせいで既存の行が一つも見つからず、
      同じ日を下にもう一回足す動きになっとった（2026-09-10に踏んだ）。
    """
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
    t = str(v or "").strip().replace("/", "-")
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}" if m else ""


def _columns(ws) -> dict[str, str]:
    """2行目の見出しを読んで {見出し: 列記号} を返す。"""
    head = ws.row_values(HEADER_ROW)
    out = {}
    for i, name in enumerate(head):
        name = str(name).strip()
        if name:
            out.setdefault(name, rowcol_to_a1(1, i + 1).rstrip("1"))
    missing = [h for h in DATA_COLS if h not in out]
    if missing:
        raise SystemExit(f"❌ 2行目に見出しが見つからん: {missing}\n"
                         f"   見つかった見出し: {list(out)}")
    return out


def write(sh, rows: list[dict], dry: bool) -> None:
    ws = sh.sheet1
    col = _columns(ws)
    dcol = col["日"]

    have = ws.get(f"{dcol}{FIRST_ROW}:{dcol}", value_render_option="UNFORMATTED_VALUE")
    row_of, last = {}, FIRST_ROW - 1
    for i, v in enumerate(have):
        d = _as_date(v[0]) if v else ""
        if d:
            row_of[d] = FIRST_ROW + i
            last = FIRST_ROW + i
    nxt = last + 1

    added = []
    for r in rows:
        if r["日"] not in row_of:
            row_of[r["日"]] = nxt
            added.append(r["日"])
            nxt += 1
    lines = sorted(row_of[r["日"]] for r in rows)
    lo, hi = lines[0], lines[-1]

    reqs = []
    for head, key in DATA_COLS.items():
        for r in rows:
            reqs.append({"range": f"{col[head]}{row_of[r['日']]}", "values": [[r[key]]]})

    # 率の列は【毎回入れ直す】。
    # ★2026-09-10：最初は「空いとる所だけ」にしとった。★それやと列を組み替えた時に
    #   直らん。列を消すとSheetsが中身をひとつ隣へ寄せるんで、式やった所に
    #   【古い数値】が残る。空やないから飛ばされて、診断率が2200%のまま居座った。
    #   ★★率の列の中身はこの道具が決めるもんや。毎回上書きするのが筋。
    #     見出しから分子と分母を引くんで、列が動いても正しい式になる。
    for head, (num, den) in RATE_COLS.items():
        if head not in col:
            continue
        for r in rows:
            n = row_of[r["日"]]
            reqs.append({"range": f"{col[head]}{n}",
                         "values": [[f'=IFERROR({col[num]}{n}/{col[den]}{n},"-")']]})

    if dry:
        print(f"[report] --dry-run：{len(reqs)}セル書く予定（新しい行 {added or 'なし'}）")
        return
    ws.batch_update(reqs, value_input_option="USER_ENTERED")
    print(f"[report] {len(rows)}日ぶん・{len(reqs)}セル書いた"
          f"（行 {lo}〜{hi}、新しい行 {added or 'なし'}）")


def snapshot_followers(sh, dry: bool) -> None:
    """フォロワー総数を毎日ひかえておく。

    ★Threads APIの followers_count は【総数しか返さん】。since/until を付けても
      値が動かん（4日ぶん試して確認済み）。せやから日別の増加は、
      毎日の総数をひかえて差を取る以外に出しようがない。
      ★今は本表にフォロー数の列は無いが、記録だけは残す。
        後から列を足した時に、その日から先は差分で埋められるようにするため。
    """
    today = datetime.now(JST).date().isoformat()
    try:
        ws = sh.worksheet(FOLLOWER_TAB)
    except gspread.WorksheetNotFound:
        if dry:
            return
        ws = sh.add_worksheet(title=FOLLOWER_TAB, rows=400, cols=2)
        ws.update(range_name="A1", values=[["記録日", "フォロワー総数"]])
    if any(str(r[0])[:10] == today for r in ws.get("A2:B") if r):
        return
    try:
        c = ThreadsClient(env("THREADS_ACCESS_TOKEN", required=True), env("THREADS_USER_ID"))
        n = int(c._get(f"{c.user_id}/threads_insights",
                       {"metric": "followers_count"})["data"][0]["total_value"]["value"])
    except Exception as e:
        print(f"[report] フォロワー総数が取れん: {str(e)[:110]}")
        return
    if not dry:
        ws.append_row([today, n], value_input_option="USER_ENTERED")
    print(f"[report] フォロワー総数 {n} を記録した")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="d_from")
    p.add_argument("--to", dest="d_to")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--insight-days", type=int, default=3)
    p.add_argument("--no-insights", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    key = os.environ.get("REPORT_SHEET_KEY", "").strip()
    if not key:
        print("❌ REPORT_SHEET_KEY が無い（レポート用スプシのID）")
        return 1

    d_to = a.d_to or (datetime.now(JST).date() - timedelta(days=1)).isoformat()
    d_from = a.d_from or (date.fromisoformat(d_to) - timedelta(days=a.days - 1)).isoformat()

    ss._CACHE.clear()
    if not a.no_insights:
        refresh_insights(a.insight_days)
        ss._CACHE.clear()

    rows = collect(d_from, d_to)
    print(f"\n期間 {d_from} 〜 {d_to}")
    print("日付        投稿 表示回数  コメ  診断  追加 ブロ オファー 購入   売上")
    for r in rows:
        print(f"{r['日']} {r['ポスト数']:4d} {r['表示']:8,d} {r['コメ']:5d} {r['診断']:5d} "
              f"{r['追加']:5d} {r['ブロック']:4d} {r['オファー']:7d} {r['購入']:4d} {r['売上']:8,d}")
    print()

    gc = gspread.authorize(Credentials.from_service_account_info(_creds_info(), scopes=SCOPES))
    sh = gc.open_by_key(key)
    write(sh, rows, a.dry_run)
    snapshot_followers(sh, a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

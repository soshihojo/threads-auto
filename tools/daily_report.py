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

★フォロー数（E列）
　　Threads APIの followers_count は【総数しか返さん】。since/until を付けても値が変わらん。
　　せやから走るたびに総数を「_フォロワー」タブへ記録して、前日との差で日別の増加を出す。
　　★記録が2日ぶん貯まるまでE列は空のまま。過去に手で入れた値は上書きせん。
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
# 列の割り当て。数式の列（G/J/L/N/P）はここに入れん＝値で塗り潰さん
COLS = {"B": "日", "C": "ポスト数", "D": "表示", "F": "コメ", "H": "診断",
        "I": "追加", "K": "ブロック", "M": "オファー", "O": "購入", "Q": "売上"}
FORMULAS = {"G": '=IFERROR(F{r}/D{r},"-")', "J": '=IFERROR(I{r}/H{r},"-")',
            "L": '=IFERROR(K{r}/I{r},"-")', "N": '=IFERROR(M{r}/I{r},"-")',
            "P": '=IFERROR(O{r}/M{r},"-")'}
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

    com = Counter(str(r.get("seen_at"))[:10] for r in ss._records("processed_replies")
                  if ok(str(r.get("seen_at"))[:10]))

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


# ---------------------------------------------------------------- フォロワー
def follower_gains(sh, dry: bool) -> dict[str, int]:
    """総数を今日ぶん記録して、日別の増加を返す。{日付: 増えた数}"""
    try:
        ws = sh.worksheet(FOLLOWER_TAB)
    except gspread.WorksheetNotFound:
        if dry:
            return {}
        ws = sh.add_worksheet(title=FOLLOWER_TAB, rows=400, cols=2)
        ws.update(range_name="A1", values=[["記録日", "フォロワー総数"]])
    snaps = {str(r[0])[:10]: int(r[1]) for r in ws.get("A2:B") if len(r) >= 2 and str(r[1]).strip()}

    today = datetime.now(JST).date().isoformat()
    if today not in snaps:
        try:
            c = ThreadsClient(env("THREADS_ACCESS_TOKEN", required=True), env("THREADS_USER_ID"))
            n = int(c._get(f"{c.user_id}/threads_insights",
                           {"metric": "followers_count"})["data"][0]["total_value"]["value"])
            snaps[today] = n
            if not dry:
                ws.append_row([today, n], value_input_option="USER_ENTERED")
            print(f"[report] フォロワー総数 {n} を記録した")
        except Exception as e:
            print(f"[report] フォロワー総数が取れん: {str(e)[:110]}")

    # 記録日Dの総数は「D-1日の終わり」の値。せやから D-1日の増加 = snap(D) - snap(D-1)
    gains = {}
    for d, n in snaps.items():
        prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
        if prev in snaps:
            gains[prev] = n - snaps[prev]
    return gains


# ---------------------------------------------------------------- 書き込み
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


def write(sh, rows: list[dict], gains: dict[str, int], dry: bool) -> None:
    ws = sh.sheet1
    have = ws.get(f"B{FIRST_ROW}:B", value_render_option="UNFORMATTED_VALUE")
    row_of = {}
    for i, v in enumerate(have):
        d = _as_date(v[0]) if v else ""
        if d:
            row_of[d] = FIRST_ROW + i
    nxt = FIRST_ROW + len(have)

    reqs, added = [], []
    for r in rows:
        d = r["日"]
        if d not in row_of:
            row_of[d] = nxt
            added.append(d)
            nxt += 1
    lines = sorted(row_of[r["日"]] for r in rows)
    lo, hi = lines[0], lines[-1]

    # 数式は「空いとる所だけ」入れる。店主が式を直しとったら触らん
    cur_f = {c: ws.get(f"{c}{lo}:{c}{hi}", value_render_option="FORMULA") for c in FORMULAS}
    # フォロー数も「値が出せる日だけ」入れる。手で入れた値を空で潰さん
    cur_e = ws.get(f"E{lo}:E{hi}", value_render_option="UNFORMATTED_VALUE")

    for col, key in COLS.items():
        for r in rows:
            reqs.append({"range": f"{col}{row_of[r['日']]}", "values": [[r[key]]]})
    for col, tpl in FORMULAS.items():
        for r in rows:
            i = row_of[r["日"]] - lo
            got = cur_f[col][i] if i < len(cur_f[col]) else []
            if not (got and str(got[0]).strip()):
                reqs.append({"range": f"{col}{row_of[r['日']]}",
                             "values": [[tpl.format(r=row_of[r["日"]])]]})
    for r in rows:
        g = gains.get(r["日"])
        if g is None:
            continue
        i = row_of[r["日"]] - lo
        got = cur_e[i] if i < len(cur_e) else []
        if not (got and str(got[0]).strip()):
            reqs.append({"range": f"E{row_of[r['日']]}", "values": [[g]]})

    if dry:
        print(f"[report] --dry-run：{len(reqs)}セル書く予定（新しい行 {added or 'なし'}）")
        return
    ws.batch_update(reqs, value_input_option="USER_ENTERED")
    print(f"[report] {len(rows)}日ぶん・{len(reqs)}セル書いた"
          f"（行 {lo}〜{hi}、新しい行 {added or 'なし'}）")


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
    write(sh, rows, follower_gains(sh, a.dry_run), a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

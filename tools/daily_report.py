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
LEGEND_TAB = "_指標の定義"
HEADER_ROW, FIRST_ROW = 2, 3

# ★★★2026-09-10 全面見直し。
#   前は率の分子と分母が【別の集団】を数えとった。オファーは何日も前に追加した人に
#   出るのに、その日のLINE追加数で割っとった。せやから 1150% みたいな数字が並んだ。
#   ★実測：オファーの97%・購入の95%は【追加から7日以内】に起きとる（中央値0日）。
#   　　　　せやから追加後の指標は【その日追加した人を7日追う】形（コホート）にする。
#   　　　　これで率は必ず100%以下に収まって、「どの日の集客が良かったか」が読める。
COHORT_DAYS = 7

# 列は見出しで決める。順番もここが正。走るたびに2行目へ書き戻す
HEADERS = ["日", "ポスト数", "ポスト表示回数", "1本あたり表示", "コメント数", "コメント率",
           "診断完了数", "LINE追加数", "LINE追加率",
           "オファー到達数", "オファー到達率", "購入数", "購入率", "ブロック数", "ブロック率",
           "コホート売上", "当日売上", "コホート確定"]
DATA_COLS = {"日": "日", "ポスト数": "ポスト数", "ポスト表示回数": "表示", "コメント数": "コメ",
             "診断完了数": "診断", "LINE追加数": "追加",
             "オファー到達数": "オファー", "購入数": "購入", "ブロック数": "ブロック",
             "コホート売上": "コホート売上", "当日売上": "当日売上", "コホート確定": "確定"}
# 率の列は（分子の見出し, 分母の見出し）。列が動いても式が追随する
RATE_COLS = {"1本あたり表示": ("ポスト表示回数", "ポスト数"),
             "コメント率": ("コメント数", "ポスト表示回数"),
             "LINE追加率": ("LINE追加数", "診断完了数"),
             "オファー到達率": ("オファー到達数", "LINE追加数"),
             "購入率": ("購入数", "LINE追加数"),
             "ブロック率": ("ブロック数", "LINE追加数")}
# 見た目（走るたびに揃える）。★2026-09-10：全列に明示的に当てること。
#   列の割り当てを変えた時、【前の割り当ての書式がその列に残る】。実際この日、
#   前にコメント率やった F列に％書式が残っとって、コメント数41が「4100.00%」と出た。
#   指定した列だけ直しても直らん。全部を上書きする。
_N = {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}
_P = {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}
_Y = {"numberFormat": {"type": "CURRENCY", "pattern": "¥#,##0"}}
FORMATS = {"日": {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}},
           "ポスト数": _N, "ポスト表示回数": _N, "1本あたり表示": _N,
           "コメント数": _N, "コメント率": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}},
           "診断完了数": _N, "LINE追加数": _N, "LINE追加率": _P,
           "オファー到達数": _N, "オファー到達率": _P,
           "購入数": _N, "購入率": _P, "ブロック数": _N, "ブロック率": _P,
           "コホート売上": _Y, "当日売上": _Y,
           "コホート確定": {"numberFormat": {"type": "TEXT"}}}

LEGEND = [
    ["列", "意味", "数え方", "なんでこう数えるか"],
    ["ポスト数", "その日に出した投稿", "posts。★削除済みは数えん",
     "insights_atが空のまま2日たった投稿はThreads上にもう無い。8/9深夜の41本連投が実例で、"
     "本数だけ数えると1本あたり表示が172まで落ちて実態と違う数字になる"],
    ["ポスト表示回数", "その日の投稿の総表示", "posts.views の合計", ""],
    ["1本あたり表示", "表示回数 ÷ ポスト数", "式", "★集客が痩せた時にいちばん先に動く。本数を増やして誤魔化しても、ここは下がる"],
    ["コメント数", "その日の投稿に付いたコメント", "processed_replies を【投稿の日】に寄せる",
     "表示回数と同じ「その日の投稿」の話に揃える。巡回が止まった日に翌日へ付け替わる問題も消える"],
    ["コメント率", "コメント数 ÷ 表示回数", "式", "投稿の刺さり方。1本あたり表示とは別の軸で見る"],
    ["診断完了数", "その日に診断を終えた人", "web_events の submit", ""],
    ["LINE追加数", "その日に友だち追加した人", "line_users の新規", ""],
    ["LINE追加率", "追加数 ÷ 診断完了数", "式", "診断からLINEへの落ち。数分の差やから同じ日で割ってええ"],
    ["オファー到達数", "★その日追加した人のうち、7日以内にオファーを受けた人数", "コホート",
     "★オファーは何日も前に追加した人に出る。その日のオファー数をその日の追加数で割っとったから"
     "1150%みたいな数字が出とった。実測でオファーの97%は7日以内や"],
    ["オファー到達率", "オファー到達数 ÷ LINE追加数", "式", "必ず100%以下になる。会話がオファーまで届く割合"],
    ["購入数", "★その日追加した人のうち、7日以内に買うた人数", "コホート", "実測で購入の95%は7日以内"],
    ["購入率", "購入数 ÷ LINE追加数", "式", "★追加1人あたりの成約率。ここが集客の質そのもの"],
    ["ブロック数", "★その日追加した人のうち、7日以内にブロックした人数", "コホート",
     "line_users の note『ブロック/解除』と updated_at。web_events側は人に紐付かん"],
    ["ブロック率", "ブロック数 ÷ LINE追加数", "式", "会話が削っとらんかの見張り"],
    ["コホート売上", "★その日追加した人が7日以内に払った額", "コホート",
     "★『その日の集客がいくらになったか』。日をまたぐ入金もその日の手柄に付ける"],
    ["当日売上", "その日に入った額", "カレンダー", "こっちは店の実感に合う数字。合計はこの列で見る"],
    ["コホート確定", "7日たったか", "", "★直近7日は追いかけ途中や。その行の率はまだ伸びる。読む時は必ずここを見る"],
    ["", "", "", ""],
    ["売上の内訳", "鑑定3,980円／潮見9,800円", "10桁のオーダー番号の数",
     "月詠みの月額と構えは入っとらん。STORESの管理画面が正で、この列は傾向を見るためのもん"],
]

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
    today = datetime.now(JST).date()
    limit = (today - timedelta(days=GRACE_DAYS)).isoformat()

    # ── その日に出した投稿の成績 ──
    posts, views, post_day = Counter(), Counter(), {}
    for r in ss._records("posts"):
        d = str(r.get("created_at"))[:10]
        has = bool(str(r.get("insights_at") or "").strip())
        if not has and d < limit:
            continue                                                  # もう無い投稿
        post_day[str(r.get("media_id"))] = d
        if not ok(d):
            continue
        posts[d] += 1
        try:
            views[d] += int(float(r.get("views") or 0))
        except (TypeError, ValueError):
            pass

    # コメントは【付いた投稿を出した日】に寄せる。表示回数と同じ集団の話に揃うし、
    # 巡回が止まった日に翌日へ付け替わる問題も消える
    com = Counter()
    for r in ss._records("processed_replies"):
        d = post_day.get(str(r.get("post_id")))
        if d and ok(d):
            com[d] += 1

    diag = Counter()
    for r in ss._records("web_events"):
        d = str(r.get("created_at"))[:10]
        if r.get("event") == "submit" and ok(d):
            diag[d] += 1

    # ── 追加した人を7日追う（コホート） ──
    join, block = {}, {}
    for r in ss._records("line_users"):
        u, d = r.get("user_id"), str(r.get("created_at"))[:10]
        if u and d:
            join[u] = d
        if u and "ブロック" in str(r.get("note") or ""):
            b = str(r.get("updated_at"))[:10]
            if b:
                block[u] = b
    ledger = _shiomi_names()
    names = {r["user_id"]: str(r.get("display_name") or "") for r in ss._records("line_users")}
    offer, buy = {}, {}
    for r in sorted(ss._records("line_chats"), key=lambda r: str(r.get("created_at"))):
        u, d = r.get("user_id"), str(r.get("created_at"))[:10]
        t = str(r.get("text") or "")
        if r.get("role") == "user":
            if re.search(r"(?<!\d)\d{10}(?!\d)", re.sub(r"[\s\-]", "", t)):
                buy.setdefault(u, []).append(d)
        elif any(m in t for m in OFFER_MARKS):
            offer.setdefault(u, d)

    def within(day: str, when: str | None) -> bool:
        """追加日から COHORT_DAYS 日以内に起きたか。"""
        if not when or when < day:
            return False
        return (date.fromisoformat(when) - date.fromisoformat(day)).days <= COHORT_DAYS

    cohort = defaultdict(list)
    for u, d in join.items():
        cohort[d].append(u)

    # ── 当日売上（カレンダー）。店の実感に合う方の数字 ──
    price = lambda u: SHIOMI if is_shiomi_row(u, names.get(u, ""), ledger) else KANTEI  # noqa: E731
    day_sales = Counter()
    for u, ds in buy.items():
        for d in ds:
            day_sales[d] += price(u)

    out = []
    d = date.fromisoformat(d_from)
    while d.isoformat() <= d_to:
        k = d.isoformat()
        us = cohort.get(k, [])
        n_off = sum(1 for u in us if within(k, offer.get(u)))
        n_buy = n_blk = 0
        yen = 0
        for u in us:
            hit = next((x for x in buy.get(u, []) if within(k, x)), None)
            if hit:
                n_buy += 1
                yen += price(u)
            if within(k, block.get(u)):
                n_blk += 1
        mature = (today - d).days >= COHORT_DAYS
        out.append({"日": k, "ポスト数": posts[k], "表示": views[k], "コメ": com[k],
                    "診断": diag[k], "追加": len(us), "オファー": n_off, "購入": n_buy,
                    "ブロック": n_blk, "コホート売上": yen, "当日売上": day_sales[k],
                    "確定": "✓" if mature else f"集計中（あと{COHORT_DAYS - (today - d).days}日）"})
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


def _columns(ws, dry: bool) -> dict[str, str]:
    """2行目の見出しを揃えてから {見出し: 列記号} を返す。

    ★2026-09-10：見出しは【この道具が持つ】ことにした。前は手で並べ替えられた列を
      読むだけやったんで、列を消された時に古い数値が式の場所に残って
      診断率が2200%のまま居座る、みたいなことが起きた。走るたびに書き戻す。
    """
    head = [str(x).strip() for x in ws.row_values(HEADER_ROW)]
    want = [""] + HEADERS                      # A列は空け、B列から
    if head[:len(want)] != want and not dry:
        ss_range = f"{_colname(1)}{HEADER_ROW}"
        ws.update(range_name=ss_range, values=[HEADERS])
        print(f"[report] 2行目の見出しを揃えた（{len(HEADERS)}列）")
    return {h: _colname(i + 1) for i, h in enumerate(HEADERS)}


def _colname(i: int) -> str:
    """0始まりの列番号を A,B,C… に。"""
    return rowcol_to_a1(1, i + 1).rstrip("1")


def write(sh, rows: list[dict], dry: bool) -> None:
    ws = sh.sheet1
    col = _columns(ws, dry)
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
    # 率の列は毎回入れ直す。列を組み替えた時に古い数値が残るのを直せんくなるため
    for head, (num, den) in RATE_COLS.items():
        for r in rows:
            n = row_of[r["日"]]
            reqs.append({"range": f"{col[head]}{n}",
                         "values": [[f'=IFERROR({col[num]}{n}/{col[den]}{n},"-")']]})

    if dry:
        print(f"[report] --dry-run：{len(reqs)}セル書く予定（新しい行 {added or 'なし'}）")
        return
    ws.batch_update(reqs, value_input_option="USER_ENTERED")
    for head, fmt in FORMATS.items():
        ws.format(f"{col[head]}{FIRST_ROW}:{col[head]}{hi}", fmt)
    print(f"[report] {len(rows)}日ぶん・{len(reqs)}セル書いた"
          f"（行 {lo}〜{hi}、新しい行 {added or 'なし'}）")


def write_legend(sh, dry: bool) -> None:
    """指標の意味を別タブに置く。数字だけ見て読み違えんように。"""
    if dry:
        return
    try:
        ws = sh.worksheet(LEGEND_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=LEGEND_TAB, rows=len(LEGEND) + 10, cols=4)
    ws.update(range_name="A1", values=LEGEND)
    ws.format("A1:D1", {"textFormat": {"bold": True}})
    print(f"[report] 「{LEGEND_TAB}」タブを更新した")


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
    print("日付        投稿 表示回数 1本 コメ 診断 追加 ｵﾌｧｰ 購入 ﾌﾞﾛｯｸ  ｺﾎｰﾄ売上  当日売上 確定")
    for r in rows:
        per = r["表示"] // r["ポスト数"] if r["ポスト数"] else 0
        print(f"{r['日']} {r['ポスト数']:4d} {r['表示']:8,d} {per:4d} {r['コメ']:4d} {r['診断']:4d} "
              f"{r['追加']:4d} {r['オファー']:4d} {r['購入']:4d} {r['ブロック']:5d} "
              f"{r['コホート売上']:9,d} {r['当日売上']:9,d}  {r['確定']}")
    print()

    gc = gspread.authorize(Credentials.from_service_account_info(_creds_info(), scopes=SCOPES))
    sh = gc.open_by_key(key)
    write(sh, rows, a.dry_run)
    write_legend(sh, a.dry_run)
    snapshot_followers(sh, a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

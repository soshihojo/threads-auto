# -*- coding: utf-8 -*-
"""投稿のTSVを組む。★「投稿を作って」と言われたら、必ずこれを通す。

　使い方：
　　本文を一行空きで並べたファイルを作って、
　　　.venv/bin/python tools/make_posts.py 下書き.txt
　　これで、スプシにそのまま貼れるTSVが出る。

★★なんで手で組まんのか
　・id と時刻の対応を、手で作ると必ずどこかでズレる
　　（実例 2026-08-26：時刻を文字列で並べたら "10:00" が "1:00" より前に来て、
　　　30本ぜんぶ id と本文がずれた。日時として解析せなあかん）
　・シートは【行順】に貼る。時刻順やない
　・8列そろえんと、貼った時に隣の列を壊す
　★ここを機械にやらせて、こっちは中身だけ考える。
"""
from __future__ import annotations
import argparse, csv, io, re, sys, statistics
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import store_sheets as ss  # noqa: E402

# ★2026-08-31：末尾に account を足して9列にした。Threadsを二本まわすため。
#   ★空欄で貼ったら、今まで通り一本目（config.yaml の default_account）に流れる。
COLS = 9                     # id / text / scheduled_at / status / media_id / error / created_at / posted_at / account
JARGON = ("宿曜", "二十七宿", "命宮", "栄親", "安壊", "危成", "業胎", "本命宿", "月宿", "七曜")
SHUKU = ("昴宿", "畢宿", "觜宿", "参宿", "井宿", "鬼宿", "柳宿", "星宿", "張宿", "翼宿", "軫宿",
         "角宿", "亢宿", "氐宿", "房宿", "心宿", "尾宿", "箕宿", "斗宿", "女宿", "虚宿", "危宿",
         "室宿", "壁宿", "奎宿", "婁宿", "胃宿")
NG = ("**", "##", "必ず", "絶対", "保証", "あいつ", "あの男", "先着")
DEVICE = re.compile(r"(何月生まれ|生まれ月|月生まれ)")


def _next_id() -> int:
    ids = [int(r["id"]) for r in ss._records("scheduled_posts")
           if str(r.get("id", "")).strip().isdigit()]
    return (max(ids) + 1) if ids else 1


def _last_scheduled() -> datetime | None:
    """いちばん先の予約時刻を返す（そこに繋げるため）。"""
    def pd(s):
        s = str(s)[:19].replace("T", " ")
        for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(s, f)
            except ValueError:
                pass
    ts = [pd(r["scheduled_at"]) for r in ss._records("scheduled_posts")
          if str(r.get("status")) == "scheduled" and str(r.get("scheduled_at")).strip()]
    ts = [x for x in ts if x]
    return max(ts) if ts else None


def check(posts: list[str]) -> list[str]:
    """出す前の検品。★ここで止まったら、中身を直してから出す。"""
    bad = []
    for i, p in enumerate(posts, 1):
        n = len(p.strip())
        for k in JARGON + SHUKU:
            if k in p:
                bad.append(f"{i}本目：宿曜語「{k}」が入っとる")
        for k in NG:
            if k in p:
                bad.append(f"{i}本目：NG語「{k}」が入っとる")
        if n > 140:
            bad.append(f"{i}本目：{n}字。★100字以下がいちばん伸びる（実測 views中央319）")
    rate = sum(1 for p in posts if DEVICE.search(p)) / max(1, len(posts))
    if rate < 0.6:
        bad.append(f"★「生まれ月」の率が {rate*100:.0f}%。6割以上にする"
                   "（あり=中央292／なし=中央160。伸びた上位12本のうち10本がこれ）")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="本文を一行空きで並べたファイル")
    ap.add_argument("--every", type=int, default=90, help="何分おきか（既定90＝1時間半）")
    ap.add_argument("--start", default="", help="開始時刻 YYYY-MM-DD HH:MM（既定＝最後の予約の次）")
    ap.add_argument("--out", default="", help="控えの置き場（既定 note_out/投稿◯本_MMDD.tsv）")
    ap.add_argument("--account", default="",
                    help="どのThreadsアカウントに流すか（空欄＝一本目）。二本目なら b")
    a = ap.parse_args()
    ss._CACHE.clear()

    posts = [p.strip() for p in Path(a.file).read_text(encoding="utf-8").split("\n\n\n") if p.strip()]
    if not posts:
        posts = [p.strip() for p in Path(a.file).read_text(encoding="utf-8").split("\n\n") if p.strip()]

    bad = check(posts)
    if bad:
        print("❌ 検品で止まった。直してからもう一回：")
        for b in bad:
            print("   " + b)
        return 1

    start = (datetime.strptime(a.start, "%Y-%m-%d %H:%M") if a.start
             else (_last_scheduled() or datetime.now()) + timedelta(minutes=a.every))
    first = _next_id()

    out = io.StringIO()
    w = csv.writer(out, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    for i, p in enumerate(posts):
        t = start + timedelta(minutes=a.every * i)
        w.writerow([first + i, p, t.strftime("%Y-%m-%d %H:%M:%S"), "scheduled", "", "", "", "", a.account])
    tsv = out.getvalue()

    # ★検算：全行9列か。行数が合うか
    rows = list(csv.reader(io.StringIO(tsv), delimiter="\t"))
    assert len(rows) == len(posts) and all(len(r) == COLS for r in rows), "列がそろってへん"

    dst = Path(a.out) if a.out else (Path(__file__).resolve().parents[1] / "note_out" /
                                     f"投稿{len(posts)}本_{start:%m%d}.tsv")
    dst.parent.mkdir(exist_ok=True)
    dst.write_text(tsv, encoding="utf-8")

    ns = [len(p) for p in posts]
    end = start + timedelta(minutes=a.every * (len(posts) - 1))
    print(f"✅ {len(posts)}本　id {first}〜{first+len(posts)-1}")
    print(f"　 {start:%m/%d %H:%M} 〜 {end:%m/%d %H:%M}（{a.every}分おき・約{len(posts)/((end-start).days+1):.0f}本/日）")
    print(f"　 字数 中央{statistics.median(ns):.0f}（{min(ns)}〜{max(ns)}）／"
          f"生まれ月 {100*sum(1 for p in posts if DEVICE.search(p))//len(posts)}%")
    print(f"　 控え: {dst}")
    print(f"　 ★貼り始め: シートの最終行の次（B{len(ss._records('scheduled_posts'))+ss.FIRST_DATA_ROW}）")
    print("\n" + "─" * 60 + "\n")
    print(tsv, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import argparse, csv, difflib, io, re, sys, statistics
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


def _next_id(account: str | None = None) -> int:
    ids = [int(r["id"]) for r in ss._records(ss.sched_table(account))
           if str(r.get("id", "")).strip().isdigit()]
    return (max(ids) + 1) if ids else 1


def _last_scheduled(account: str | None = None) -> datetime | None:
    """いちばん先の予約時刻を返す（そこに繋げるため）。"""
    def pd(s):
        s = str(s)[:19].replace("T", " ")
        for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(s, f)
            except ValueError:
                pass
    ts = [pd(r["scheduled_at"]) for r in ss._records(ss.sched_table(account))
          if str(r.get("status")) == "scheduled" and str(r.get("scheduled_at")).strip()]
    ts = [x for x in ts if x]
    return max(ts) if ts else None


# ★★★2026-08-31 新設：切り口の重複チェック。
#
#   なんで要るか。★椿と椿さん、二本とも同じ人格（profile=tsubaki）で走らせる。
#   ★★同じ切り口を両方で流したら、Threads は重複と見て片方の露出を落とす。
#   ★★★ほんで、同じ読者に二回届く。興ざめする。実害はそっちの方が大きい。
#
#   何と比べるか。★【両方のアカウントの】過去の投稿と、まだ出てへん予約の全部や。
#     ・posts          … 配信済み（両アカウント。profile が同じでも中身で見る）
#     ・scheduled_posts   … 椿の未配信
#     ・scheduled_posts_b … 椿さんの未配信
#
#   どう測るか。★一行目（フック）と、本文まるごとの二本立て。
#     ★フックが似とったら、中身がちごても「また同じやつか」と見える。
#     ★★せやからフックの方を厳しめに見る。
HOOK_LIMIT = 0.72       # 一行目の似とる率。これ以上で警告
BODY_LIMIT = 0.62       # 本文まるごとの似とる率
_MONTH = re.compile(r"([0-9１-９]{1,2})月生まれ")


def _norm(s: str) -> str:
    """比べる用に均す。記号と空白を落として、数字だけ残す。"""
    return re.sub(r"[\s、。！？!?…—・「」『』（）()♡★☆🌙😊]", "", str(s or ""))


def _hook(p: str) -> str:
    return _norm(p.strip().splitlines()[0] if p.strip() else "")


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _existing(skip: set[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """(どこの投稿か, 本文) を集める。★両アカウントぶん。

    skip … (シート名, id) の集合。★差し替え対象の行は、自分自身と比べても
            しゃあないんで外す（★これが無いと、書き直すたびに自分と被る）。
    """
    skip = skip or set()
    out = []
    for r in ss._records("posts"):
        txt = str(r.get("text") or "").strip()
        if txt:
            out.append((f"配信済み({r.get('profile') or '?'})", txt))
    for table, label in (("scheduled_posts", "椿の予約"), ("scheduled_posts_b", "椿さんの予約")):
        try:
            rows = ss._records(table)
        except Exception:
            continue        # ★シートがまだ無い時は黙って飛ばす
        for r in rows:
            if (table, str(r.get("id"))) in skip:
                continue
            if str(r.get("status")) in ("scheduled", "pending_review"):
                txt = str(r.get("text") or "").strip()
                if txt:
                    out.append((f"{label} id={r.get('id')}", txt))
    return out


def check_dup(posts: list[str], skip: set[tuple[str, str]] | None = None) -> list[str]:
    """新しい下書きが、過去のもんと被ってへんか見る。★両アカウントをまたいで見る。"""
    warn = []
    old = _existing(skip)
    olds = [(w, _norm(txt), _hook(txt)) for w, txt in old]
    print(f"　 重複チェック：過去 {len(olds)}本と突き合わせる（両アカウント）")

    for i, p in enumerate(posts, 1):
        pn, ph = _norm(p), _hook(p)
        worst = None
        for where, on, oh in olds:
            hs = _sim(ph, oh) if ph and oh else 0.0
            bs = _sim(pn, on)
            if hs >= HOOK_LIMIT or bs >= BODY_LIMIT:
                score = max(hs, bs)
                if worst is None or score > worst[0]:
                    worst = (score, where, on[:38], hs, bs)
        if worst:
            _, where, head, hs, bs = worst
            warn.append(f"{i}本目：{where} と被っとる"
                        f"（フック{hs*100:.0f}% / 本文{bs*100:.0f}%）→ {head}…")

    # ★今回の30本の中どうしも見る（同じ切り口を二本入れてまうことがある）
    for i in range(len(posts)):
        for j in range(i + 1, len(posts)):
            hs = _sim(_hook(posts[i]), _hook(posts[j]))
            bs = _sim(_norm(posts[i]), _norm(posts[j]))
            if hs >= HOOK_LIMIT or bs >= BODY_LIMIT:
                warn.append(f"{i+1}本目と{j+1}本目が中で被っとる"
                            f"（フック{hs*100:.0f}% / 本文{bs*100:.0f}%）")
    return warn


def check(posts: list[str], account: str | None = None) -> list[str]:
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
    # ★★2026-08-31：「生まれ月」の要求率は【アカウントごと】に持たせた。
    #   ★椿は 0.6（実測で効いとる装置やから外さん）。
    #   ★★椿さんは 0.0（生まれ月は椿の主戦場。二本で同じ装置を回したら切り口が枯れる）。
    from src.config import account_conf
    need = float(account_conf(account).get("device_min", 0.6))
    rate = sum(1 for p in posts if DEVICE.search(p)) / max(1, len(posts))
    if need > 0 and rate < need:
        bad.append(f"★「生まれ月」の率が {rate*100:.0f}%。{need*100:.0f}%以上にする"
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
    ap.add_argument("--replace", default="",
                    help="差し替え。'1-10' のように、持ち回る既存の id を指定する。"
                         "★id と時刻はそのまま使い、その行は重複チェックの対象から外す")
    a = ap.parse_args()
    ss._CACHE.clear()

    posts = [p.strip() for p in Path(a.file).read_text(encoding="utf-8").split("\n\n\n") if p.strip()]
    if not posts:
        posts = [p.strip() for p in Path(a.file).read_text(encoding="utf-8").split("\n\n") if p.strip()]

    acc = a.account or None
    table = ss.sched_table(acc)

    bad = check(posts, acc)
    if bad:
        print("❌ 検品で止まった。直してからもう一回：")
        for b in bad:
            print("   " + b)
        return 1

    # ★★重複チェックは【止める】。警告で流したら、結局そのまま貼ってまうからや。
    # ★差し替えの時は、持ち回る id を重複チェックから外す（自分と比べても意味が無い）
    keep_ids: list[str] = []
    if a.replace:
        m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", a.replace)
        keep_ids = ([str(i) for i in range(int(m.group(1)), int(m.group(2)) + 1)] if m
                    else [x.strip() for x in a.replace.split(",") if x.strip()])
        if len(keep_ids) != len(posts):
            print(f"❌ 差し替えの id が {len(keep_ids)}件、本文が {len(posts)}本。数が合わん")
            return 1
    dup = check_dup(posts, {(table, i) for i in keep_ids})
    if dup:
        print("❌ 切り口が被っとる。直してからもう一回：")
        for d in dup:
            print("   " + d)
        print("\n   ★★同じ切り口を二本のアカウントで流したら、Threadsが片方を落とす。")
        print("   ★同じ読者にも二回届く。そっちの方が痛い。")
        return 1

    out = io.StringIO()
    w = csv.writer(out, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    if keep_ids:
        # ★差し替え：既存の id と時刻を、そのまま持ち回る
        by_id = {str(r.get("id")): r for r in ss._records(table)}
        missing = [i for i in keep_ids if i not in by_id]
        if missing:
            print(f"❌ シート {table} に id {missing} が無い")
            return 1
        rows_at = [by_id[i].get("scheduled_at") for i in keep_ids]
        for i, p in zip(keep_ids, posts):
            w.writerow([i, p, by_id[i].get("scheduled_at"), "scheduled", "", "", "", "", a.account])
        start = datetime.strptime(str(rows_at[0])[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        first = int(keep_ids[0])
    else:
        start = (datetime.strptime(a.start, "%Y-%m-%d %H:%M") if a.start
                 else (_last_scheduled(acc) or datetime.now()) + timedelta(minutes=a.every))
        first = _next_id(acc)
        for i, p in enumerate(posts):
            tt = start + timedelta(minutes=a.every * i)
            w.writerow([first + i, p, tt.strftime("%Y-%m-%d %H:%M:%S"), "scheduled", "", "", "", "", a.account])
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
    label = "椿（tsubaki_honne）" if table == "scheduled_posts" else "椿さん（tsubakisan_honne）"
    print(f"　 ★貼り先のシート: 【{table}】　{label}")
    if keep_ids:
        print(f"　 ★★差し替え：id {keep_ids[0]}〜{keep_ids[-1]} の行に【上書き】する")
        print(f"　 ★貼り始め: B{ss.FIRST_DATA_ROW + [str(r.get('id')) for r in ss._records(table)].index(keep_ids[0])}")
    else:
        print(f"　 ★貼り始め: B{len(ss._records(table))+ss.FIRST_DATA_ROW}")
    print("\n" + "─" * 60 + "\n")
    print(tsv, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

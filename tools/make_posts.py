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


def check(posts: list[str], account: str | None = None,
          device_min: float | None = None) -> list[str]:
    """出す前の検品。★ここで止まったら、中身を直してから出す。"""
    bad = []
    for i, p in enumerate(posts, 1):
        for k in JARGON + SHUKU:
            if k in p:
                bad.append(f"{i}本目：宿曜語「{k}」が入っとる")
        for k in NG:
            if k in p:
                bad.append(f"{i}本目：NG語「{k}」が入っとる")
        # ★★2026-09-28：字数は【一枚ずつ】数える。
        #   ★「===続き===」で割った投稿は、Threads上では別々の投稿として出る
        #     （threads_client.publish_thread が自分への返信として順に出す）。
        #   ★★せやから本文をまるごと数えたら、中身に関係なく三枚組は全部引っかかる。
        #     ★実際ここで止まって、続き付きの20本が一本も通らんかった。
        #   ★★★一枚あたりで見る。フィードに流れるのは一枚目やから、そこが要や。
        parts = [x.strip() for x in re.split(r"\n?===続き===\n?", p) if x.strip()]
        for j, part in enumerate(parts, 1):
            n = len(part)
            if n > 140:
                where = f"{i}本目" if len(parts) == 1 else f"{i}本目の{j}枚目"
                bad.append(f"{where}：{n}字。★100字以下がいちばん伸びる（実測 views中央319）")
    # ★★2026-08-31：「生まれ月」の要求率は【アカウントごと】に持たせた。
    #   ★椿は 0.6（実測で効いとる装置やから外さん）。
    #   ★★椿さんは 0.0（生まれ月は椿の主戦場。二本で同じ装置を回したら切り口が枯れる）。
    from src.config import account_conf
    need = (float(device_min) if device_min is not None
            else float(account_conf(account).get("device_min", 0.6)))
    rate = sum(1 for p in posts if DEVICE.search(p)) / max(1, len(posts))
    if need > 0 and rate < need:
        bad.append(f"★「生まれ月」の率が {rate*100:.0f}%。{need*100:.0f}%以上にする"
                   "（あり=中央292／なし=中央160。伸びた上位12本のうち10本がこれ）")
    return bad


# ★★★2026-09-28 新設：置く時刻を「時の一覧」で決める。
#   ★なんで要るか。等間隔に並べると、実測で数字の出てへん時間にも均等に落ちる。
#     ★90分おきで20本組んだら、6本が 3時・9時・13時・16時・19時 に入った。
#     ★★19時の中央viewsは80、7時は595。七倍ちがう所に三割入れたら、
#       新しい型が効いたのか、時間帯で沈んだのかが分からんようになる。
#   ★★一日に置ける数は一覧の長さで決まる（8時なら8本/日）。
#     ★20〜24本/日の上限（8/20の事故）より少ない所で回す形や。
DEAD_HOURS = (19, 3, 16, 9, 13)     # 中央views 80〜194。ここは捨てる
def _slot_times(start: datetime, n: int, hours_csv: str, every: int) -> list[datetime]:
    """n本ぶんの予約時刻を返す。hours_csv が空なら、今まで通り every 分おき。"""
    if not str(hours_csv).strip():
        return [start + timedelta(minutes=every * i) for i in range(n)]
    hours = sorted({int(x) for x in re.split(r"[,\s]+", hours_csv.strip()) if x != ""})
    if not hours or not all(0 <= h <= 23 for h in hours):
        raise SystemExit(f"❌ --hours が読めん: {hours_csv!r}（例 1,2,7,11,15,20,22,23）")
    dead = [h for h in hours if h in DEAD_HOURS]
    if dead:
        print(f"　 ⚠ --hours に数字の出てへん時間が入っとる: {dead}（中央views 80〜194）")
    out: list[datetime] = []
    # ★2026-10-01：開始時刻が枠の上に乗ってへん時は、その時刻を一本目にする。
    #   ★「今から10分後に出したい」が通らんかった。--start 13:12 を渡しても、
    #     13時の枠は過ぎとるから飛ばされて、一本目が15時になっとった。
    #   ★★--start は【ここから始める】いう指定や。そこは素直に一本目にする。
    #     二本目から、指定の時刻の枠に乗せていく。
    if start.minute or start.hour not in hours:
        out.append(start)
    day = start.date()
    while len(out) < n:
        for h in hours:
            t = datetime(day.year, day.month, day.day, h, 0)
            if t < start:               # ★開始より前の枠は飛ばす（今日の残りから埋める）
                continue
            out.append(t)
            if len(out) >= n:
                break
        day += timedelta(days=1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="本文を一行空きで並べたファイル")
    ap.add_argument("--every", type=int, default=90, help="何分おきか（既定90＝1時間半）")
    ap.add_argument("--start", default="", help="開始時刻 YYYY-MM-DD HH:MM（既定＝最後の予約の次）")
    ap.add_argument("--out", default="", help="控えの置き場（既定 note_out/投稿◯本_MMDD.tsv）")
    ap.add_argument("--account", default="",
                    help="どのThreadsアカウントに流すか（空欄＝一本目）。二本目なら b")
    # ★★2026-09-28：この一回だけ「生まれ月」の縛りを外す口。
    #   ★config.yaml の device_min は椿の実測で決まった値やから、触らん。
    #   ★★試しの回（生まれ月ゼロで組む回）だけ、ここで 0 を渡す。
    #     どの回で外したかが、打った手として残るようにしてある。
    ap.add_argument("--device-min", type=float, default=None,
                    help="「生まれ月」の最低率を、この回だけ上書きする（0で縛りを外す）")
    # ★★★2026-09-28 新設：置く時間を【時刻の一覧】で指定できるようにした。
    #   ★今までは --every で等間隔に並べるだけやった。90分おきやと、20本のうち6本が
    #     実測で数字の出てへん時間（3時・9時・13時・16時・19時）に落ちる。
    #   ★★中央viewsの実測（03_winning_elements.md）：
    #       寄せる … 7時595／22時458／2時411／15時404／23時403／20時399／1時345／11時319
    #       捨てる … 19時80／3時169／16時177／9時179／13時194
    #     ★7時と19時で七倍ちがう。新しい型を試す回に、捨てる時間へ三割入れたら測れん。
    #   ★★★--hours 1,2,7,11,15,20,22,23 のように渡したら、その時にだけ置く。
    ap.add_argument("--hours", default="",
                    help="置く時刻を時で指定（例 1,2,7,11,15,20,22,23）。--every より優先")
    # ★★2026-10-01 新設：貼ったあとで【時刻だけ】差し替える口。
    #   ★店主が50本を貼った直後に「今から10分後に始めたい」となった。
    #     ★本文も id も合うとるんやから、動かすんは scheduled_at の一列だけでええ。
    #   ★★本文を貼り直させたらあかん。複数行の本文を貼り直すんが、いちばん崩れる。
    #   ★せやから --replace と一緒に渡したら、時刻を組み直して【D列だけ】出す。
    ap.add_argument("--retime", action="store_true",
                    help="--replace の id の【時刻だけ】組み直す（本文は貼り直さん）")
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

    bad = check(posts, acc, device_min=a.device_min)
    if a.device_min is not None:
        print(f"　 ★この回だけ「生まれ月」の縛りを {a.device_min:.0%} に下げて組む")
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
    dup = [] if a.retime else check_dup(posts, {(table, i) for i in keep_ids})
    if a.retime:
        print("　 ★時刻だけの差し替えや。本文は変えてへんから、重複チェックは通さん")
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
        if a.retime:
            base = (datetime.strptime(a.start, "%Y-%m-%d %H:%M") if a.start else datetime.now())
            rows_at = [t.strftime("%Y-%m-%d %H:%M:%S")
                       for t in _slot_times(base, len(keep_ids), a.hours, a.every)]
        else:
            rows_at = [by_id[i].get("scheduled_at") for i in keep_ids]
        for i, p, at in zip(keep_ids, posts, rows_at):
            w.writerow([i, p, at, "scheduled", "", "", "", "", a.account])
        def _pd(s):
            return datetime.strptime(str(s)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        start, last = _pd(rows_at[0]), _pd(rows_at[-1])
        first = int(keep_ids[0])
    else:
        start = (datetime.strptime(a.start, "%Y-%m-%d %H:%M") if a.start
                 else (_last_scheduled(acc) or datetime.now()) + timedelta(minutes=a.every))
        first = _next_id(acc)
        times = _slot_times(start, len(posts), a.hours, a.every)
        for i, (p, tt) in enumerate(zip(posts, times)):
            w.writerow([first + i, p, tt.strftime("%Y-%m-%d %H:%M:%S"), "scheduled", "", "", "", "", a.account])
        start, last = times[0], times[-1]
    tsv = out.getvalue()

    # ★検算：全行9列か。行数が合うか
    rows = list(csv.reader(io.StringIO(tsv), delimiter="\t"))
    assert len(rows) == len(posts) and all(len(r) == COLS for r in rows), "列がそろってへん"

    dst = Path(a.out) if a.out else (Path(__file__).resolve().parents[1] / "note_out" /
                                     f"投稿{len(posts)}本_{start:%m%d}.tsv")
    dst.parent.mkdir(exist_ok=True)
    dst.write_text(tsv, encoding="utf-8")

    ns = [len(p) for p in posts]
    end = last
    days = max(1, (end.date() - start.date()).days + 1)
    how = f"{a.hours} 時に置く" if a.hours else f"{a.every}分おき"
    print(f"✅ {len(posts)}本　id {first}〜{first+len(posts)-1}")
    print(f"　 {start:%m/%d %H:%M} 〜 {end:%m/%d %H:%M}（{how}・約{len(posts)/days:.0f}本/日）")
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
    if keep_ids and a.retime:
        head = ss.FIRST_DATA_ROW + [str(r.get("id")) for r in ss._records(table)].index(keep_ids[0])
        col = ss._col(2)   # scheduled_at は3列目（id / text / scheduled_at）
        print(f"\n★時刻だけ差し替える。貼るんは【{col}{head}】から、この{len(rows_at)}行だけや。")
        print("　（本文は触らんでええ。この列を上から貼るだけ）")
        print("\n" + "─" * 60 + "\n")
        print("\n".join(rows_at))
        return 0
    print("\n" + "─" * 60 + "\n")
    print(tsv, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

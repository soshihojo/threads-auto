# -*- coding: utf-8 -*-
"""売上とオファーの効きを測る。

★2026-08-27：オファーを【鑑定書を土台に潮見を薦める】形に変えた。
　その前後で売上が変わったかを見るために作った。

　使い方：
　　.venv/bin/python tools/uriage.py            … 直近3週間を日別に
　　.venv/bin/python tools/uriage.py --from 8-20 --to 8-26   … 期間を切って合計

★数え方の断り書き（ここを読まんと数字を読み違える）
　・買うた数＝LINEに届いた【10桁のオーダー番号】の数。これは正確や。
　・★どっちの商品かは、kantei_out/_潮見を買うた人.txt の名簿でしか分からん。
　　　名簿に無い人は、鑑定書（3,980円）として数える。
　　　★せやから【潮見を売ったのに名簿へ書き忘れたら、売上は低う出る】。買うたら必ず書く。
　・★ほんまの売上はSTORESの管理画面が正や。ここの数字は、傾向を見るためのもんや。
"""
from __future__ import annotations
import argparse, re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import store_sheets as ss  # noqa: E402

KANTEI, SHIOMI = 3980, 9800
LEDGER = Path(__file__).resolve().parents[1] / "kantei_out" / "_潮見を買うた人.txt"
# オファーを送ったかどうかの目印（本文が変わっても拾えるように複数持つ）
# ★★★2026-08-30：オファーの数え方を【商品URL】に変えた。
#   前は「9,980円 → 3,980円」の文字で数えとった。★この表記は8/15からのもんで、
#   8/7〜8/14 に出したオファーが丸ごと抜けとった（実測で227回ぶん）。
#   値引き表記は今後も変わる。★URLは商品そのものやから、書き方が変わっても残る。
KANTEI_URLS = ("6a777f09db80bae422c65694", "6a75ab0eae8e9e00447387f2")  # 二つ目は旧URL
SHIOMI_URL = "6a7d88b780c8d813567b3a3f"
OFFER_MARKS = KANTEI_URLS + (SHIOMI_URL,)
NEW_OFFER_MARK = "ウチが薦めるんは、こっちや"      # ★新しい型の目印


def _norm(s: str) -> str:
    """名寄せ用に均す。★台帳は手書きなんで、表示名と完全一致せん（実例：
    台帳「ゆきえ」＝LINE「ひろかわゆきえ」、台帳「みき」＝LINE「みきん❦」）。"""
    s = str(s or "")
    for w in ("さん", "ちゃん", "くん", "様"):
        s = s.replace(w, "")
    return re.sub(r"[^0-9a-zA-Z\u3040-\u30ff\u4e00-\u9fff]", "", s).lower()


def _is_shiomi(display: str, ledger: set[str]) -> bool:
    """台帳（三列目のLINE表示名）と【完全一致】した人だけ潮見と数える。

    ★2026-08-27：最初は部分一致にしとったら「久美」が「青木久美子」を巻き込んだ。
      売上を多う見せる方の誤りやから、ここは厳しい方に倒す。
      その代わり、台帳には【LINEの表示名をそのまま】書くこと。
    """
    return _norm(display) in ledger if display else False


def is_shiomi_row(user_id: str, display: str, ledger: set[str]) -> bool:
    """user_id が台帳にあればそれで、無ければ表示名で判定する。"""
    return (str(user_id) in ledger) or _is_shiomi(display, ledger)


def _shiomi_names() -> set[str]:
    if not LEDGER.exists():
        return set()
    out = set()
    for ln in LEDGER.read_text(encoding="utf-8").splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        parts = ln.split("\t")
        if len(parts) >= 2:
            # ★2026-08-29：四列目に user_id があったら、それだけを使う。
            #   表示名が同じ人が二人おる時（実例「じゅんこ」）、名前で照合したら別人まで拾う。
            if len(parts) >= 4 and parts[3].strip().startswith("U"):
                out.add(parts[3].strip())
                continue
            # 無い時は今まで通り、呼び名と表示名で照合する
            for cell in parts[1:3]:
                if cell.strip():
                    out.add(_norm(cell))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2026-08-06")
    ap.add_argument("--to", dest="d_to", default="2026-12-31")
    a = ap.parse_args()
    ss._CACHE.clear()

    users = {u["user_id"]: str(u.get("display_name") or "") for u in ss._records("line_users")}
    shiomi = _shiomi_names()

    buys: list[tuple[str, str]] = []       # (日付, user_id)
    offers: list[tuple[str, str]] = []     # (日付, user_id)
    new_offers: set[str] = set()
    for r in ss._records("line_chats"):
        d = str(r.get("created_at"))[:10]
        if not (a.d_from <= d <= a.d_to):
            continue
        t = str(r.get("text") or "")
        if r.get("role") == "user":
            # ★★2026-08-30：前は fullmatch やった＝「番号だけ」の人しか数えられん。
            #   実際は「オーダー番号 9763585092」「購入しました\n8137027212」と
            #   書いてくる人がぎょうさんおる。★実測で28件が抜けとった。
            m = re.search(r"(?<!\d)\d{10}(?!\d)", re.sub(r"[\s\-]", "", t))
            if m:
                buys.append((d, r["user_id"]))
        else:
            if any(m in t for m in OFFER_MARKS):
                offers.append((d, r["user_id"]))
                if NEW_OFFER_MARK in t:
                    new_offers.add(r["user_id"])

    # 同じ人が同じ日に複数オファーを受けても1回として数える
    off_day = defaultdict(set)
    for d, u in offers:
        off_day[d].add(u)
    buy_day = defaultdict(list)
    for d, u in buys:
        buy_day[d].append(u)

    days = sorted(set(off_day) | set(buy_day))
    print(f"期間 {a.d_from} 〜 {a.d_to}　（潮見の名簿 {len(shiomi)}人）")
    print("日付        オファー  買うた  うち潮見   売上(円)  成約率")
    tot_o = tot_b = tot_s = tot_y = 0
    for d in days:
        o = len(off_day.get(d, ()))
        us = buy_day.get(d, [])
        s = sum(1 for u in us if is_shiomi_row(u, users.get(u, ""), shiomi))
        y = s * SHIOMI + (len(us) - s) * KANTEI
        rate = f"{100*len(us)/o:5.1f}%" if o else "    —"
        print(f"{d}  {o:7d}  {len(us):5d}  {s:6d}  {y:9,d}  {rate}")
        tot_o += o; tot_b += len(us); tot_s += s; tot_y += y
    print("─" * 58)
    rate = f"{100*tot_b/tot_o:5.1f}%" if tot_o else "    —"
    print(f"合計      {tot_o:7d}  {tot_b:5d}  {tot_s:6d}  {tot_y:9,d}  {rate}")
    if tot_b:
        print(f"　一人あたり単価 {tot_y//tot_b:,}円　／　潮見の割合 {100*tot_s/tot_b:.0f}%")
    if tot_o:
        print(f"　オファー一通あたり {tot_y//tot_o:,}円")
    if new_offers:
        print(f"\n★新しい型（潮見を薦める）のオファーを受けた人：{len(new_offers)}人")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

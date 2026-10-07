"""月額会員（月詠み）の層と、相談の通数の勘定。

★2026-10-07 新設。月詠みを「相談し放題」から【週の一手＋通数上限】へ改めるための土台。

なんで要るか（実測。tools の集計と src/store.consult_counts で誰でも引き直せる）:
  ・会員1人あたりの相談は【中央値19通／月】（43会員月）。平均28.4通、最大125通。
  ・月10通を上限にすると、上限に当たるのは【72%】の会員月。
  ・その10通目に届く日は【中央値12日】。★月の前半で窓が閉まる人が4割強おる。
  ・50万円＝84人（5,980円）を相談し放題でやると月2,940通。1日98通。捌けん。

せやから二つを同時にやる。
  ① 通数に上限を置く（原価を止める）
  ② 週の一手を毎週こっちから届ける（上限に当たった後の空白を埋める）
★②が無いまま①だけ入れたら、7日目に見捨てられる商品になる。

★★★今おる会員は据え置きや。plan が空欄の人は「し放題」として扱う。
　　値上げも、中身の縮小も、遡らせん。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .config import load_config

JST = ZoneInfo("Asia/Tokyo")

# plan が空欄の会員は、ここに寄せる（＝今までの「し放題」のまま）
GRANDFATHERED = "shihoudai"


def _cfg() -> dict:
    return (load_config().get("members") or {})


def plans() -> dict:
    return (_cfg().get("plans") or {})


def default_plan() -> str:
    return str(_cfg().get("default_plan") or GRANDFATHERED)


def plan_of(member) -> str:
    """その会員の層のキーを返す。★空欄は据え置き組（し放題）や。"""
    try:
        raw = str((member or {})["plan"] or "").strip()
    except Exception:
        raw = ""
    if not raw:
        return GRANDFATHERED
    return raw if raw in plans() else GRANDFATHERED


def plan_info(plan: str) -> dict:
    """層の設定（label / price / consult_limit / weekly）を返す。"""
    p = plans().get(plan) or plans().get(GRANDFATHERED) or {}
    return {
        "key": plan,
        "label": str(p.get("label") or plan),
        "price": int(p.get("price") or 0),
        "consult_limit": int(p.get("consult_limit") or 0),
        "weekly": bool(p.get("weekly")),
        # ★毎月、三十日の暦を引き直して届ける層か
        "koyomi": bool(p.get("koyomi")),
        # ★席数（0なら上限なし）。し放題を売る層は必ず切る
        "seats": int(p.get("seats") or 0),
    }


def this_month(now: datetime | None = None) -> str:
    return (now or datetime.now(JST)).strftime("%Y-%m")


def consult_status(member, used: int) -> dict:
    """その会員の、今月の相談の残り具合を返す。

    返すもの:
      plan / label / limit（0なら無制限） / used / remaining / blocked / upsell

    ・blocked … 今月の枠を使い切っとる。★返信を作る対象から外す合図
    ・upsell  … 残りが僅かになった。★上の層を薦める合図（止めるためやのうて昇格のため）
    """
    plan = plan_of(member)
    info = plan_info(plan)
    limit = info["consult_limit"]
    used = max(0, int(used or 0))
    if limit <= 0:
        return {**info, "plan": plan, "limit": 0, "used": used,
                "remaining": None, "blocked": False, "upsell": False}
    remaining = max(0, limit - used)
    at = int(_cfg().get("upsell_at_remaining") or 0)
    return {**info, "plan": plan, "limit": limit, "used": used,
            "remaining": remaining, "blocked": remaining <= 0,
            "upsell": 0 < remaining <= at}


def status_line(st: dict) -> str:
    """画面に一行で出す用。"""
    if not st["limit"]:
        return f"{st['label']}・相談し放題（今月 {st['used']}通）"
    if st["blocked"]:
        return f"{st['label']}・今月の枠は使い切り（{st['used']}/{st['limit']}通）"
    return f"{st['label']}・今月 {st['used']}/{st['limit']}通（残り{st['remaining']}）"


# ★★★枠を使い切った時に、10通目の返信の末尾へ足す一行。
#   ★黙って止めたらあかん。「無視された」になる。終わりやと言うて、次を示す。
#   ★★Markdownも★も使わん（顧客に出す文やから）。
LIMIT_NOTICE = (
    "ほんで一つ言うとく。今月の相談枠は、これで使い切りや。\n"
    "月曜の「週の一手」は、このあとも毎週ちゃんと届くからな。\n"
    "次の相談は更新日からや。"
)


def limit_notice(st: dict) -> str:
    """その返信で枠を使い切るなら、末尾に足す一行を返す（要らん時は空）。"""
    if not st["limit"]:
        return ""
    return LIMIT_NOTICE if st["remaining"] <= 1 else ""

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
        # ★席数（0なら上限なし）。★2026-10-11に潮暦の20席を外した（config.yaml）
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


# ★★★2026-10-11 新設（店主の判断）：入会して最初の一通は【サービスの説明を改めてする挨拶】にする。
#   ★相談の返事から入らん。何が付いとって、どう使うて、どう止めるかを、先に一回渡す。
#   ★★この一通は十通に数えん。数えるんは💬会員相談で下書きを作った回だけ（consult_counts）。
#     せやから、この挨拶は会員相談の画面を通さず、LINEからそのまま送る。
#   ★書かんこと：「こっちから必ず出す」（2026-10-08 禁句）／「入らんでええ」の逃げ道／
#     「今回のは自動更新」（毎回自動更新や）／時間帯の挨拶／Markdown
WELCOME_BODY = {
    "tsukiyomi": (
        "一つ。週に一回、月曜に「週の一手」が付く。\n"
        "その週の盤面と、動いてええか、手を止めとくか。\n\n"
        "二つ。聞きたいことは、月に十通まで投げてええ。\n"
        "鑑定書で視たことは全部頭に入っとるから、一から説明せんでええ。"
    ),
    "koyomi": (
        "一つ。週に一回、月曜に「週の一手」が付く。\n"
        "その週の盤面と、動いてええか、手を止めとくか。\n\n"
        "二つ。毎月、三十日の暦を引き直して渡す。\n"
        "動いてええ日と、手を止める日が日付で入っとる。送る日には、送る文の見本も付く。\n\n"
        "三つ。聞きたいことは、上限なしで投げてええ。\n"
        "鑑定書で視たことは全部頭に入っとるから、一から説明せんでええ。"
    ),
}


def welcome_message(call_name: str, plan: str) -> str:
    """入会して最初に送る、サービスの説明の挨拶（十通に数えん一通）。

    call_name … 相談者の呼び名（敬称ごと。例「ヒロさん」）
    plan      … tsukiyomi / koyomi
    """
    info = plan_info(plan)
    body = WELCOME_BODY.get(plan)
    if not body:
        raise ValueError(f"挨拶の文面が無い層や: {plan}")
    # ★通数に上限がある層だけ、この一通を数えんことを言うとく（上限なしの層に言うても意味が無い）
    not_counted = "このあいさつは、相談の十通には入れへん。\n" if info["consult_limit"] else ""
    return (
        f"{call_name}。\n\n"
        f"{info['label']}へ、ようこそ。ここからの付き合い方を、改めて言うとくな。\n\n"
        f"{body}\n\n"
        f"{info['label']}は月{info['price']:,}円で、自動更新やから、毎月の手続きは要らん。"
        "止める時だけ、更新日の3営業日前までに、ここに一言ちょうだい。そしたらこっちで止めるで。\n\n"
        f"{not_counted}"
        "聞きたいことができたら、いつでも送ってきてな🌙"
    )


def limit_notice(st: dict) -> str:
    """その返信で枠を使い切るなら、末尾に足す一行を返す（要らん時は空）。"""
    if not st["limit"]:
        return ""
    return LIMIT_NOTICE if st["remaining"] <= 1 else ""

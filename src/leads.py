"""手挙げ（リード）検知。返信本文に設定キーワードが含まれるか判定する。"""
from __future__ import annotations

import re

from .config import active_profile

# 生まれ月コメント（「2月」「２月」「9月生まれ」「彼は12月」等の短文）
_MONTH_RE = re.compile(r"^[^\d０-９]{0,6}([1１][0-2０-２]|[1-9１-９])\s*月[^\n]{0,8}$")

# ★★★2026-08-22 新設：番号を選ばせるCTAへの回答コメント（「1」「②」「3番」等）。
#   願いを番号で選ばせる型（復縁／音信不通／片思い…）を足したんで、その受け皿が要る。
#   ★これが無いと、番号だけのコメントは手挙げとして拾われん。
#     実際、追加する前は「1」「２」「③」「2番」——ぜんぶ素通りしとった。
#     ★★CTAだけ足しても、拾えんかったら一件もリードにならん。ここが本体や。
#   ★★★短文だけに限る。長い文の中にたまたま数字が入っとるだけの回を拾たらあかん。
#     「3年待ってます」みたいなコメントを番号と誤って取ると、返信の中身がずれる。
_CHOICE_RE = re.compile(
    r"^[^\d０-９①-⑨]{0,4}"                      # 頭に少しだけ飾りがあってもええ（「私は」等）
    r"(?:([1-9１-９])|([①-⑨])|([1-9１-９]\ufe0f?\u20e3))"   # 半角/全角/丸数字/キーキャップ
    r"\s*(?:ばん|番)?"
    # ★数字の後ろが単位なら、番号やない。「か月」は「か」だけで弾くと
    #   「2かな」まで巻き込むんで、二文字そのままで見る
    r"(?!か月)(?![年月日人回個名週時分秒歳才件本枚円])"
    r"[^\n]{0,10}$"                              # 後ろも少しだけ（「です」「かな」等）
)
_MARU = "①②③④⑤⑥⑦⑧⑨"


# ★★★2026-10-04 新設：星座のコメントも手挙げとして拾う。
#
#   なんで要るか。★新規の人に届く装置として、生まれ月はもう枯れとる（飽和）。
#   ★★星座は、うちで一本も使うてへん（1,366本のうちゼロ）。読者の側は馴染みがある。
#   ★★★ほんで、ここが肝心や——手挙げと判定されへんコメントには、
#     返信に【診断のURLが付かん】（replies.py の is_lead の時だけ付ける作り）。
#     ★星座で募って、返信にURLが付かんかったら、コメントは増えても診断は増えん。
#     ★★投稿の型を変える前に、受け皿の方を直しとかなあかん。
#
#   ★短文だけに限る。「座」が書いてある時だけ拾う（「魚」「蟹」の一字で誤爆せんため）。
_ZODIAC_RE = re.compile(
    r"^[^\n]{0,8}?(牡羊|おひつじ|オヒツジ|ひつじ|牡牛|おうし|オウシ|双子|ふたご|フタゴ|"
    r"蟹|かに|カニ|獅子|しし|シシ|乙女|おとめ|オトメ|天秤|てんびん|テンビン|"
    r"蠍|さそり|サソリ|射手|いて|イテ|山羊|やぎ|ヤギ|水瓶|みずがめ|ミズガメ|魚|うお|ウオ)\s*座"
    r"[^\n]{0,12}$")


def match_zodiac(text: str) -> str | None:
    """星座のコメントなら、その星座（「乙女座」等）を返す。短文だけ見る。"""
    t = (text or "").strip()
    if not t or "\n" in t:
        return None
    m = _ZODIAC_RE.match(t)
    return f"{m.group(1)}座" if m else None


def match_choice(text: str) -> str | None:
    """番号で選ばせるCTAへの回答なら、選ばれた番号（"1"〜"9"）を返す。

    ★数字が一個だけ入っとる短文に限る。二個以上入っとったら番号やないと見る
      （「1月2日」「2人」みたいなんを拾わんため）。
    """
    t = (text or "").strip()
    if not t or "\n" in t:
        return None
    if _MONTH_RE.match(t):        # 生まれ月の方が先。「2月」を番号2と取らん
        return None
    m = _CHOICE_RE.match(t)
    if not m:
        return None
    raw = m.group(1) or m.group(2) or m.group(3) or ""
    if m.group(2):                # 丸数字
        return str(_MARU.index(m.group(2)) + 1)
    d = re.sub(r"[^\d０-９]", "", raw)
    if not d:
        return None
    n = d.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    # 本文に数字が二個以上あったら、番号の回答やない
    if len(re.sub(r"[^\d０-９]", "", t)) > 1:
        return None
    return n if n in "123456789" else None


# ★★★2026-09-28 新設：「言葉で鑑定を求めとるか」だけを見る。
#   ★なんで match_keyword と分けるか。あれは番号（①②③）も生まれ月も手挙げとして返す。
#     ★三択の回答は毎回「①」やから、match_keyword で見たら【全部が手挙げ】になる。
#   ★★返信の回数上限の例外に使うのは、こっちや。
#     「視てほしい」「鑑定して」と書いてきた一件だけを、上限の外に出す。
def asks_for_reading(text: str) -> str | None:
    """本文に lead_keywords の言葉が入っとればそれを返す。番号・生まれ月は見ん。"""
    if not text:
        return None
    lowered = text.lower()
    for kw in (active_profile().get("lead_keywords") or []):
        if kw.lower() in lowered:
            return kw
    return None


def match_keyword(text: str) -> str | None:
    """本文にlead_keywordsのいずれかが含まれれば、そのキーワードを返す。
    生まれ月投稿への回答コメント（「2月」等の短文）も手挙げ扱い（lead_month_comments）。"""
    if not text:
        return None
    profile = active_profile()
    keywords = profile.get("lead_keywords") or []
    lowered = text.lower()
    for kw in keywords:
        if kw.lower() in lowered:
            return kw
    if profile.get("lead_month_comments") and _MONTH_RE.match(text.strip()):
        return "生まれ月"
    if profile.get("lead_choice_comments"):
        n = match_choice(text)
        if n:
            return f"願い{n}"
    if profile.get("lead_zodiac_comments"):
        z = match_zodiac(text)
        if z:
            return z
    return None

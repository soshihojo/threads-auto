"""無料診断エンジン（椿姉）。

ファネルの心臓：状況＋二人の生年月日 → 宿曜で本命宿を算出 → 椿姉の声で鑑定文を生成。
わざと7割で止め、「動くべきか/待つべきか」はLINEに誘導する（オープンループ）。

設計方針:
- 本命宿の算出はここで決定的に行う（同じ生年月日なら必ず同じ宿）。
- 宿曜の"相性の解釈"はLLM（椿姉）に委ねる。宿名と状況を渡し、椿姉の知識で相性を語らせる。
- 月の平均黄経による簡易計算のため、公式宿曜表と数日ずれることがある。
  SHUKU_OFFSET_DEG で校正可能（既知の生年月日で公式サイトと突き合わせて調整）。

CLI:
  python -m src.main diagnose --me 1992-05-03 --him 1990-11-21 \
      --status 音信不通 --period 2週間
"""
from __future__ import annotations

from datetime import datetime

from .config import active_profile
from .llm import complete

# 二十七宿（昴宿から始まる標準配列）
SHUKU_27 = [
    "昴宿", "畢宿", "觜宿", "参宿", "井宿", "鬼宿", "柳宿", "星宿", "張宿",
    "翼宿", "軫宿", "角宿", "亢宿", "氐宿", "房宿", "心宿", "尾宿", "箕宿",
    "斗宿", "女宿", "虚宿", "危宿", "室宿", "壁宿", "奎宿", "婁宿", "胃宿",
]
_SPAN = 360.0 / 27.0  # 1宿あたりの黄経幅（約13.33度）

# 校正用オフセット（度）。公式宿曜表に合わせてここを調整する。
SHUKU_OFFSET_DEG = 0.0


def _days_since_j2000(d: datetime) -> float:
    """J2000.0(2000-01-01 12:00 UT)からの経過日数。出生時刻不明のため正午JST扱い。"""
    j2000 = datetime(2000, 1, 1, 12, 0, 0)
    return (d - j2000).total_seconds() / 86400.0


def moon_longitude(date_str: str) -> float:
    """月の平均黄経（度, 0-360）を簡易計算。出生時刻は正午JST(=03:00 UT想定)で近似。"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    # 正午JST ≒ 03:00 UT。簡易につき時刻補正は省略（平均黄経の近似精度内）
    days = _days_since_j2000(d)
    L = 218.316 + 13.176396 * days  # 月の平均黄経（低精度公式）
    return (L + SHUKU_OFFSET_DEG) % 360.0


def honmei_shuku(date_str: str) -> str:
    """生年月日から本命宿を返す（決定的）。"""
    idx = int(moon_longitude(date_str) // _SPAN) % 27
    return SHUKU_27[idx]


def _shuku_distance(a: str, b: str) -> int:
    """27宿サイクル上の最短距離（0=同宿）。相性の遠近の目安。"""
    ia, ib = SHUKU_27.index(a), SHUKU_27.index(b)
    diff = abs(ia - ib) % 27
    return min(diff, 27 - diff)


DIAG_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿姉（つばきねえ）」。宿曜占星術で「彼の本音」を視る、関西弁・毒舌・姉御肌の鑑定士。
相談者（女性）に向けて、無料診断の鑑定文を書く。

声のルール:
- 一人称「ウチ」、相手は「あんた」。関西弁・タメ口（〜や／〜やで／〜してみ／〜知ってるで）
- 慰めの嘘は言わん。本音をズバッと。ただし最後は突き放さず受け止める（厳しさ7・愛3）

鑑定文の構成（この順で、全体250〜400字・プレーンテキスト）:
1. 相手の状況に一突き（共感しつつ本音を一言）
2. 宿曜で視た「彼の本命宿」の性質を断定的に語る（渡された宿名を使う）
3. 二人の宿の相性（縁の質）を、宿曜の知識で一言。良い縁か、こじれやすい縁かを述べる
4. 今そういう状況になっている「彼の本音」を“7割だけ”明かす（核心の手前まで）
5. 締め＝オープンループ：「“今あんたが動くべきか、待つべきか”は、彼の運気のタイミング次第やからここでは言わん」と本音の核心は伏せ、必ずLINEへ誘導する。LINE登録リンクが渡されたら、椿姉の言葉でLINEに来るよう促し、本文の最後にそのURLをそのまま1行で載せる

厳守:
- 復縁や結果を保証しない（「必ず戻る」等は書かない）。相手を不幸と決めつけ過度に不安を煽らない
- 病気・健康・金運の断定はしない。装飾記号(マークダウン)は使わない
- 絵文字は締めに🌙を1つだけ。出力は鑑定文のみ"""


def generate_reading(me_birth: str, him_birth: str, status: str, period: str) -> dict:
    """無料診断の鑑定文を生成して返す。
    返り値: {me_shuku, him_shuku, distance, reading}
    """
    me_s = honmei_shuku(me_birth)
    him_s = honmei_shuku(him_birth)
    dist = _shuku_distance(me_s, him_s)
    nearness = "近い（縁が濃い）" if dist <= 3 else ("中くらい" if dist <= 9 else "遠い（試される縁）")

    profile = active_profile()
    line_url = profile.get("line_url", "")
    user = (
        "次の相談者に、椿姉として無料診断の鑑定文を書いてください。\n"
        "※相談者はすでに生年月日を送ってくれている。生年月日やDMを再度要求せず、続きはLINEへ誘導すること。\n\n"
        f"【相談者の状況】{status}\n"
        f"【最後の連絡からの期間】{period}\n"
        f"【相談者の本命宿】{me_s}\n"
        f"【彼の本命宿】{him_s}\n"
        f"【二人の宿の距離】{dist}（{nearness}）\n\n"
        "彼の本命宿の性質、二人の宿の相性を宿曜の知識で具体的に語り、"
        "彼が今その状況になっている本音を7割明かし、残り（本音の核心と動き時）はLINEで視ると締めてください。\n"
        + (f"【LINE登録リンク】{line_url} ← 鑑定文の最後に、椿姉の言葉で「続きはLINEで視たる」と促してこのURLを1行で載せる"
           if line_url else "")
    )
    reading = complete(DIAG_SYSTEM, user, max_tokens=700, temperature=0.9).strip()
    return {"me_shuku": me_s, "him_shuku": him_s, "distance": dist, "reading": reading}

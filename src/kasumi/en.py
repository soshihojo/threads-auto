"""あなたと推しの生年月日から、縁タイプ（宿曜の六つの相性）を出す。

本命宿は椿と同じ計算（diagnosis.honmei_shuku）を使う。
相性は「三九の秘法」：自分の宿から相手の宿までの距離 d（0〜26）で決まる。
  d=0 → 命 ／ d=9,18 → 業胎
  それ以外は d を9で割った余り p で、1:栄 2:衰 3:安 4:危 5:成 6:壊 7:友 8:親
  栄と親、衰と友、安と壊、危と成は、向きを入れ替えた同じ関係なので一組にする。
"""
from __future__ import annotations

from ..diagnosis import SHUKU_27, honmei_shuku

_PAIR_BY_POS = {1: "栄親", 8: "栄親", 2: "友衰", 7: "友衰",
                3: "安壊", 6: "安壊", 4: "危成", 5: "危成"}


def relation_from_distance(d: int) -> str:
    d %= 27
    if d == 0:
        return "命"
    if d % 9 == 0:
        return "業胎"
    return _PAIR_BY_POS[d % 9]


def relation(me_birth: str, oshi_birth: str) -> str:
    """生年月日（YYYY-MM-DD）2つから、相性の名前（栄親・友衰・安壊・危成・業胎・命）を返す。"""
    a = SHUKU_27.index(honmei_shuku(me_birth))
    b = SHUKU_27.index(honmei_shuku(oshi_birth))
    return relation_from_distance(b - a)

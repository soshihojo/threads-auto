"""手挙げ（リード）検知。返信本文に設定キーワードが含まれるか判定する。"""
from __future__ import annotations

from .config import active_profile


def match_keyword(text: str) -> str | None:
    """本文にlead_keywordsのいずれかが含まれれば、そのキーワードを返す。"""
    if not text:
        return None
    keywords = active_profile().get("lead_keywords") or []
    lowered = text.lower()
    for kw in keywords:
        if kw.lower() in lowered:
            return kw
    return None

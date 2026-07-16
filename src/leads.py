"""手挙げ（リード）検知。返信本文に設定キーワードが含まれるか判定する。"""
from __future__ import annotations

import re

from .config import active_profile

# 生まれ月コメント（「2月」「２月」「9月生まれ」「彼は12月」等の短文）
_MONTH_RE = re.compile(r"^[^\d０-９]{0,6}([1１][0-2０-２]|[1-9１-９])\s*月[^\n]{0,8}$")


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
    return None

"""月十通の層で、返信の末尾に「今月の相談 ◯通目」を足す（2026-10-25 店主の指示）。"""
from src import membership


def _st(used, limit=10):
    return {"limit": limit, "used": used, "remaining": max(0, limit - used)}


def test_new_reply_counts_as_next():
    assert membership.count_line(_st(1)) == "（今月の相談 2通目／10通まで・残り8通）"
    assert membership.count_line(_st(9)) == "（今月の相談 10通目／10通まで・残り0通）"


def test_redo_is_not_counted_twice():
    assert membership.count_line(_st(2), redo=True) == "（今月の相談 2通目／10通まで・残り8通）"


def test_unlimited_plan_gets_nothing():
    assert membership.count_line({"limit": 0, "used": 5, "remaining": None}) == ""


def test_count_line_is_stripped_from_history():
    body = "本文やで🌙\n\n（今月の相談 3通目／10通まで・残り7通）"
    assert membership.strip_count_line(body) == "本文やで🌙"
    assert membership.strip_count_line("本文だけ") == "本文だけ"

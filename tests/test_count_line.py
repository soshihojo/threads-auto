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


def test_cycle_follows_join_date():
    from datetime import date
    # 10/24入会：10/24〜11/23が一周期、更新は11/24
    assert membership.cycle(date(2026, 10, 24), date(2026, 10, 25)) == (date(2026, 10, 24), date(2026, 11, 24))
    assert membership.cycle(date(2026, 10, 24), date(2026, 11, 23)) == (date(2026, 10, 24), date(2026, 11, 24))
    assert membership.cycle(date(2026, 10, 24), date(2026, 11, 24)) == (date(2026, 11, 24), date(2026, 12, 24))
    # 31日入会は月末に寄せる
    assert membership.cycle(date(2026, 1, 31), date(2026, 2, 28))[0] == date(2026, 2, 28)


def test_status_for_counts_only_this_cycle_and_shows_renew():
    from datetime import datetime
    m = {"plan": "tsukiyomi", "note": "月詠み入会 2026-10-24／5,980円", "created_at": "2026-10-24T10:04:00"}
    dates = ["2026-10-20T10:00:00", "2026-10-24T10:07:54", "2026-10-25T09:55:13"]
    st = membership.consult_status_for(m, dates, now=datetime(2026, 10, 25, 21, 0))
    assert st["used"] == 2 and st["renew"].isoformat() == "2026-11-24"
    assert membership.count_line(st) == "（11月24日の更新まで　相談 3通目／10通・残り7通）"


def test_model_written_count_lines_are_removed():
    t = "本文🌙\n\n（10月・2通目や）\n\n（今月の相談 6通目／10通まで・残り4通）"
    assert membership.strip_count_line(t) == "本文🌙"
    assert membership.strip_count_line("本文\n\n（11月24日の更新まで　相談 3通目／10通・残り7通）") == "本文"

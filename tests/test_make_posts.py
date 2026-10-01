"""投稿のTSVを組む所（tools/make_posts.py）の検品を見る。

★★★2026-09-28：続き付き（===続き=== で割るツリー投稿）を手で20本組もうとして、
  検品が一本も通さんかった。字数を【本文まるごと】で数えとったせいや。
  Threads上では一枚ずつ別の投稿として出るんやから、一枚ずつ数えなあかん。

★もう一つ、置く時刻の話。90分おきに並べると、実測で数字の出てへん時間
  （19時80・3時169・16時177・9時179・13時194／中央views）にも均等に落ちる。
  20本のうち6本がそこに入った。新しい型を試す回にそれをやったら、
  型が効かんかったんか時間帯で沈んだんかが分からんようになる。
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.make_posts import DEAD_HOURS, _slot_times, check  # noqa: E402

THREAD = (
    "別れたあと、彼が最初に手放すもんは決まっとる。\n"
    "===続き===\n"
    "写真やない。トークの履歴でもない。\n\n"
    "先に消えるんは「また行こな」て言うてた店の予定や。\n"
    "気持ちより先に、予定から消える。\n"
    "===続き===\n"
    "あんたが今いちばん手放せてへんのはどれや。番号だけでええ。\n\n"
    "①写真\n②トークの履歴\n③二人で行った店"
)


def test_thread_post_is_measured_per_part():
    """一枚ずつは短いのに合計が140字を超える投稿を、通す。"""
    assert len(THREAD) > 140          # 前提：まるごと数えたら引っかかる長さ
    assert check([THREAD], device_min=0.0) == []


def test_long_part_is_still_caught():
    """一枚が140字を超えとったら、何枚目かを言うて止める。"""
    bad = check(["短い前ふり。\n===続き===\n" + "あ" * 141], device_min=0.0)
    assert bad and "2枚目" in bad[0]


def test_device_min_override_lets_a_zero_batch_through():
    """生まれ月ゼロで組む回は、この回だけ縛りを外せる。"""
    assert check([THREAD], device_min=0.0) == []
    assert any("生まれ月" in b for b in check([THREAD], device_min=0.6))


def test_slots_skip_the_hours_that_do_not_sell():
    start = datetime(2026, 9, 29, 11, 0)
    ts = _slot_times(start, 20, "1,2,7,11,15,20,22,23", 90)
    assert len(ts) == 20
    assert ts[0] == start
    assert not [t for t in ts if t.hour in DEAD_HOURS]
    assert all((ts[i + 1] - ts[i]).total_seconds() >= 3600 for i in range(19))


def test_slots_fall_back_to_the_old_even_spacing():
    start = datetime(2026, 9, 29, 11, 0)
    ts = _slot_times(start, 3, "", 90)
    assert [t.strftime("%H:%M") for t in ts] == ["11:00", "12:30", "14:00"]


def test_bad_hours_string_stops_instead_of_guessing():
    with pytest.raises(SystemExit):
        _slot_times(datetime(2026, 9, 29, 11, 0), 2, "25,99", 90)


def test_an_off_grid_start_becomes_the_first_slot():
    """「今から10分後に出したい」が通る形か。--start をそのまま一本目にする。"""
    start = datetime(2026, 10, 1, 13, 12)
    ts = _slot_times(start, 4, "1,2,7,11,15,20,22,23", 90)
    assert ts[0] == start
    assert [t.strftime("%m/%d %H:%M") for t in ts[1:]] == ["10/01 15:00", "10/01 20:00", "10/01 22:00"]


def test_a_start_that_sits_on_a_slot_is_not_doubled():
    start = datetime(2026, 10, 1, 15, 0)
    ts = _slot_times(start, 3, "1,2,7,11,15,20,22,23", 90)
    assert [t.strftime("%m/%d %H:%M") for t in ts] == ["10/01 15:00", "10/01 20:00", "10/01 22:00"]

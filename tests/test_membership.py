"""月詠みの層と通数の勘定（src/membership.py）。

★守りたいこと
  ① plan が空欄の会員は【し放題のまま】。既存の会員の中身を縮めたらあかん。
  ② 上限に当たった人は blocked になる。返信を作る対象から外すため。
  ③ 枠を使い切る回には、終わりやと伝える一行が付く。黙って止めると「無視された」になる。
  ④ その一行に Markdown も ★ も混ざらん（顧客に出す文やから）。
"""
import re

import pytest

from src import membership as mb


def test_空欄のプランは据え置きのし放題になる():
    """★今おる会員は plan 列が空や。そこを既定の tsukiyomi に寄せたら、
    知らんうちに相談が月10通へ縮む。それは遡及の値下げと同じ事故や。"""
    assert mb.plan_of({"plan": ""}) == mb.GRANDFATHERED
    assert mb.plan_of({"plan": None}) == mb.GRANDFATHERED
    assert mb.plan_of({}) == mb.GRANDFATHERED
    st = mb.consult_status({"plan": ""}, used=40)
    assert st["limit"] == 0
    assert st["blocked"] is False
    assert st["remaining"] is None


def test_知らんプラン名も据え置きに落ちる():
    """台帳に手で打ち間違いが入っても、安全な側（し放題）へ落ちる。"""
    assert mb.plan_of({"plan": "tukiyomi"}) == mb.GRANDFATHERED


def test_月詠みは十通で止まる():
    m = {"plan": "tsukiyomi"}
    assert mb.consult_status(m, used=0)["remaining"] == 10
    assert mb.consult_status(m, used=9)["blocked"] is False
    assert mb.consult_status(m, used=10)["blocked"] is True
    assert mb.consult_status(m, used=99)["remaining"] == 0


def test_残りが僅かになったら昇格の合図が立つ():
    """★上限は止めるために置くんやのうて、上の層へ上がってもらう合図や。"""
    m = {"plan": "tsukiyomi"}
    assert mb.consult_status(m, used=7)["upsell"] is False
    assert mb.consult_status(m, used=9)["upsell"] is True   # 残り1
    assert mb.consult_status(m, used=10)["upsell"] is False  # 使い切り後はblocked側


def test_上の層は相談し放題のまま():
    st = mb.consult_status({"plan": "koyomi"}, used=50)
    assert st["limit"] == 0 and st["blocked"] is False
    assert st["weekly"] is True


def test_使い切る回には終わりを伝える一行が付く():
    m = {"plan": "tsukiyomi"}
    assert mb.limit_notice(mb.consult_status(m, used=5)) == ""
    assert "使い切り" in mb.limit_notice(mb.consult_status(m, used=9))
    # し放題の人には絶対に付かん
    assert mb.limit_notice(mb.consult_status({"plan": ""}, used=99)) == ""


def test_通知文に装飾記号が混ざってへん():
    """★顧客に出す文や。Markdownの ** や # や、椿の書き癖の ★ を混ぜたらあかん。"""
    t = mb.LIMIT_NOTICE
    assert "★" not in t
    assert "**" not in t
    assert not re.search(r"(^|\n)#", t)
    assert "・" not in t  # 箇条書きの記号も使わん


def test_画面の一行は三つの状態を出し分ける():
    assert "し放題" in mb.status_line(mb.consult_status({"plan": ""}, used=3))
    assert "残り" in mb.status_line(mb.consult_status({"plan": "tsukiyomi"}, used=3))
    assert "使い切り" in mb.status_line(mb.consult_status({"plan": "tsukiyomi"}, used=10))


def test_設定から層の中身が引ける():
    ps = mb.plans()
    assert "tsukiyomi" in ps and "koyomi" in ps and mb.GRANDFATHERED in ps
    assert mb.plan_info("tsukiyomi")["price"] == 5980
    assert mb.plan_info("tsukiyomi")["consult_limit"] == 10
    assert mb.plan_info(mb.GRANDFATHERED)["weekly"] is False


def test_sqliteの通数の数え方(tmp_path, monkeypatch):
    """★数えるんは month=="相談" の行だけ。鑑定書や暦の控えを数えたらあかん。"""
    from src import store_sqlite
    # ★DB_PATH は読み込み時に決まる定数や。env では差し替わらん（既存テストと同じ作法）
    monkeypatch.setattr(store_sqlite, "DB_PATH", tmp_path / "t.db")
    store_sqlite.init_db()
    mid = store_sqlite.add_member("テスト", "1990-01-01", "1991-02-02")
    for _ in range(3):
        store_sqlite.add_reading(mid, "相談", "w", "r")
    store_sqlite.add_reading(mid, "個別鑑定書", "w", "r")
    store_sqlite.add_reading(mid, "九十日の暦", "w", "r")
    ym = mb.this_month()
    assert store_sqlite.consult_counts(ym) == {str(mid): 3}
    # 別の月を聞いたら空
    assert store_sqlite.consult_counts("2020-01") == {}
    assert store_sqlite.set_member_plan(mid, "tsukiyomi") is True
    assert mb.plan_of(dict(store_sqlite.list_members()[0])) == "tsukiyomi"

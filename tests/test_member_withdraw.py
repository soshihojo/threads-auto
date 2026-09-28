"""会員の退会と完全削除（2026-09-28・店主の要望で作った画面の裏側）。"""
import re
import pytest
from src import store_sqlite as db


@pytest.fixture()
def member(tmp_path, monkeypatch):
    # ★DB_PATH を差し替える（DATA_DIR だけ差し替えても、読み込み時に決まった DB_PATH は変わらん）
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    mid = db.add_member("テスト会員", "1990-01-01", "1991-02-02", "元のメモ")
    db.add_reading(mid, "個別鑑定書", "（納品済み）", "本文" * 200)
    db.add_reading(mid, "9月", "悩み", "返信")
    return mid


def test_withdrawn_member_drops_out_of_the_list(member):
    assert any(str(m["id"]) == str(member) for m in db.list_members())
    assert db.set_member_withdrawn(member, True)
    assert not any(str(m["id"]) == str(member) for m in db.list_members())
    # 退会込みなら見える。控えも残っとる（戻した時に鑑定書を貼り直さんで済むように）
    withdrawn = [m for m in db.list_members(include_withdrawn=True) if str(m["id"]) == str(member)]
    assert withdrawn and db.member_is_withdrawn(withdrawn[0])
    assert len(db.list_readings(member)) == 2


def test_withdraw_keeps_the_original_note_and_can_be_undone(member):
    db.set_member_withdrawn(member, True)
    m = [x for x in db.list_members(include_withdrawn=True) if str(x["id"]) == str(member)][0]
    assert re.match(r"^【退会 \d{4}-\d{2}-\d{2}】 元のメモ$", str(m["note"]))
    db.set_member_withdrawn(member, False)
    m = [x for x in db.list_members() if str(x["id"]) == str(member)][0]
    assert str(m["note"]) == "元のメモ"


def test_purge_removes_the_member_and_the_readings(member):
    assert db.delete_readings_for_member(member) == 2
    db.delete_member(member)
    assert not any(str(m["id"]) == str(member) for m in db.list_members(include_withdrawn=True))
    assert db.list_readings(member) == []


def test_member_status_treats_a_withdrawn_member_as_free(member, monkeypatch):
    """★退会にしたら、公式LINEでも会員扱いをやめる（自動返信がまた効く）。"""
    from src import line_bot as b
    monkeypatch.setattr(b.store, "list_members", db.list_members)
    user = {"me_birth": "1990-01-01", "him_birth": "1991-02-02"}
    assert b._member_status(user) == "member"
    db.set_member_withdrawn(member, True)
    assert b._member_status(user) == "free"

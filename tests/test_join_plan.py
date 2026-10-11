"""入会登録（join）が plan 列を埋めることを確かめる（2026-10-11：空欄のまま据え置き組に入っとった）。"""
import argparse

import pytest

from src import kantei, main, store


@pytest.fixture
def fake_store(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(store, "list_line_users",
                        lambda: [{"user_id": "U1", "display_name": "S.Hiroko",
                                  "me_birth": "1974-06-27", "him_birth": "1994-02-10"}], raising=False)
    monkeypatch.setattr(store, "list_members", lambda: [{"nickname": "18-だれか", "plan": ""}])
    monkeypatch.setattr(store, "find_line_user_by_births", lambda me, him: {"user_id": "U1", "display_name": "S.Hiroko"})
    monkeypatch.setattr(store, "add_member", lambda *a, **k: calls.setdefault("add", (a, k)) and 21)
    monkeypatch.setattr(store, "set_member_plan", lambda mid, plan: calls.setdefault("plan", (mid, plan)) or True)
    monkeypatch.setattr(kantei, "OUT_DIR", tmp_path)
    return calls


def _args(**kw):
    base = dict(line_name="S.Hiroko", kantei_name=None, me=None, him=None, plan=None, tier="tsukiyomi")
    base.update(kw)
    return argparse.Namespace(**base)


def test_join_sets_tsukiyomi_by_default(fake_store):
    main.cmd_join(_args())
    assert fake_store["plan"] == (21, "tsukiyomi")


def test_join_can_set_koyomi(fake_store):
    main.cmd_join(_args(tier="koyomi"))
    assert fake_store["plan"] == (21, "koyomi")

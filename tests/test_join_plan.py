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


# ---- 入会して最初の一通（サービスの説明・十通に数えん）2026-10-11 ----
from src import membership
from src.diagnosis import planning_leak


@pytest.mark.parametrize("plan", ["tsukiyomi", "koyomi"])
def test_welcome_explains_service_and_how_to_stop(plan):
    t = membership.welcome_message("ヒロさん", plan)
    assert t.startswith("ヒロさん。")
    assert "週の一手" in t and "3営業日前" in t and "自動更新" in t
    assert "今回のは" not in t and "入らんでええ" not in t      # 決まりの禁句
    assert "**" not in t and "#" not in t and "★" not in t      # 客に出す文
    assert planning_leak(t) == ""


def test_welcome_says_not_counted_only_when_limited():
    assert "十通には入れへん" in membership.welcome_message("ヒロさん", "tsukiyomi")
    assert "相談の十通には入れへん" not in membership.welcome_message("ヒロさん", "koyomi")
    assert "暦" in membership.welcome_message("ヒロさん", "koyomi")


def test_join_writes_welcome_file(fake_store, tmp_path):
    main.cmd_join(_args(kantei_name="ヒロ"))
    f = tmp_path / "入会あいさつ_ヒロさん.txt"
    assert f.exists() and f.read_text(encoding="utf-8").startswith("ヒロさん。")

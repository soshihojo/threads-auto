"""「0」だけの吹き出しが届いた事故の再発防止（2026-10-23 阿部夏己さん「0とはなんですか？」）。

生成の末尾の「\n\n0」を、吹き出しの自動分割が一通まるごとに切り出しとった。
"""
import hashlib

from src import line_bot as bot

BODY = ("あんた、情で流されて決める人やのうて、自分で筋通してからしか動けん型や。"
        "「今年中」って出したんも、焦りで区切ったんやのうて、自分の中で納得できる形を探した末の答えやろ。"
        "感情より先に、筋道が立たな気が済まん性分やな。")


def test_trailing_zero_never_becomes_its_own_bubble():
    out = bot._split_bubbles(BODY + "\n\n0")
    assert out and all(p.strip() != "0" for p in out)
    assert "0" not in "\n".join(out).splitlines()


def test_zero_bubble_written_by_model_is_dropped():
    out = bot._split_bubbles(BODY + "\n---\n0")
    assert out == [BODY]


def test_split_that_would_isolate_zero_is_handled_for_any_hash(monkeypatch):
    # 3回に1回の自動分割が必ず起きる形にして確かめる
    monkeypatch.setattr(hashlib, "sha1", lambda b: type("H", (), {"digest": lambda self: b"\x00"})())
    out = bot._split_bubbles(BODY + "\n\n0")
    assert all(p.strip() != "0" for p in out)


def test_number_only_message_alone_is_still_sent():
    # 本文が数字だけの時は、空送りにせん（鑑定番号の再掲などの保険）
    assert bot._split_bubbles("3117") == ["3117"]

"""同じ人に返す回数の上限を見る（config.replies.max_per_username）。

★★★2026-09-28：コメントが常連に寄っとった。9月の実数——
  ・コメント者に占める新規の割合 … 97%(8/03週) → 46%(9/21週)
  ・上位10人が占めるコメント     … 17% → 40%
  ・累計5回超の常連が占める割合   … 9% → 66%
  ・生年月日の組の再診断率       … 13% → 48%（＝もう診断済みの人に出し続けとる）
★上限を入れて、枠を新しい人へ回す。
★★ただし鑑定を求める言葉が入っとる回は上限の外に置く。そこを捨てたら本末転倒や。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import replies  # noqa: E402


def _comment(rid, user, text="①"):
    return {"id": rid, "username": user, "text": text, "timestamp": "2026-09-28T10:00:00+0000"}


class _Client:
    """Threads APIの代わり。投稿1本と、そこに付いたコメントを返すだけ。"""

    def __init__(self, comments):
        self.comments = comments
        self.sent = []

    def me(self):
        return {"username": "tsubaki_honne"}

    def my_threads(self, limit=25):
        return [{"id": "p1", "text": "本文", "permalink": "http://x"}]

    def replies(self, post_id, top_level_only=True, max_pages=10):
        return list(self.comments)

    def reply_to(self, rid, text):
        self.sent.append((rid, text))
        return "r" + str(rid)


def _run(monkeypatch, comments, counts, cap=3):
    seen: set[str] = set()
    drafts: list[tuple[str, str]] = []

    monkeypatch.setattr(replies.store, "init_db", lambda: None)
    monkeypatch.setattr(replies.store, "is_reply_seen", lambda rid: rid in seen)
    monkeypatch.setattr(replies.store, "mark_reply_seen",
                        lambda rid, *a, **k: seen.add(rid))
    monkeypatch.setattr(replies.store, "unmark_reply_seen", lambda rid: seen.discard(rid))
    def _counts():
        if counts is None:          # ★数えられん時（シートが読めん等）を再現する
            raise RuntimeError("シートが読めん")
        return dict(counts)
    monkeypatch.setattr(replies.store, "sent_reply_counts", _counts)
    monkeypatch.setattr(replies.store, "add_draft",
                        lambda rid, pid, user, in_text, draft: drafts.append((user, draft)))
    monkeypatch.setattr(replies.store, "set_draft_status", lambda *a, **k: None)
    monkeypatch.setattr(replies.store, "add_lead", lambda *a, **k: False)
    monkeypatch.setattr(replies, "_recent_reply_tails", lambda limit=12: [])
    monkeypatch.setattr(replies, "_draft_reply", lambda *a, **k: "返信の本文")
    monkeypatch.setattr(replies, "_maybe_self_reply", lambda *a, **k: None)
    monkeypatch.setattr(replies.notify, "chatwork", lambda *a, **k: True)

    cfg = replies.load_config()
    cfg["replies"] = {**cfg["replies"], "mode": "draft", "max_per_username": cap}
    monkeypatch.setattr(replies, "load_config", lambda: cfg)

    client = _Client(comments)
    stats = replies.process_replies(client)
    return stats, [u for u, _ in drafts], seen


def test_a_regular_over_the_cap_gets_no_reply(monkeypatch):
    stats, replied, seen = _run(
        monkeypatch,
        [_comment("c1", "butan822"), _comment("c2", "newcomer")],
        {"butan822": 7},
    )
    assert replied == ["newcomer"]
    assert stats.get("over_limit") == 1
    # ★既読の印は付ける。付けんと毎回の巡回で同じコメントを拾い直す
    assert "c1" in seen


def test_the_cap_counts_up_to_the_limit(monkeypatch):
    """3回までは返す。3回に達した人から外す。"""
    _, replied, _ = _run(
        monkeypatch,
        [_comment("c1", "two_times"), _comment("c2", "three_times")],
        {"two_times": 2, "three_times": 3},
    )
    assert replied == ["two_times"]


def test_a_comment_asking_for_a_reading_is_exempt(monkeypatch):
    """上限に達しとっても、鑑定を求める言葉が入っとる回は返す。"""
    _, replied, _ = _run(
        monkeypatch,
        [_comment("c1", "butan822", "彼の本音、視てほしい")],
        {"butan822": 40},
    )
    assert replied == ["butan822"]


def test_zero_means_no_cap(monkeypatch):
    _, replied, _ = _run(
        monkeypatch,
        [_comment("c1", "butan822"), _comment("c2", "newcomer")],
        {"butan822": 99},
        cap=0,
    )
    assert sorted(replied) == ["butan822", "newcomer"]


def test_counting_failure_does_not_stop_replies(monkeypatch):
    """回数を数えられん時は、上限をかけずに返す（返信が止まる方が痛い）。"""
    _, replied, _ = _run(monkeypatch, [_comment("c1", "butan822")], None)
    assert replied == ["butan822"]

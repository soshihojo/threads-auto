"""週の一手の材料が「作る時点から過去七日」になっとることを確かめる（2026-10-19 の事故の再発防止）。

実害：月曜の朝8時に「その週の月曜以降のLINE」だけを渡しとったんで、ほぼ毎回0通やった。
S.Hirokoさんが10/16に彼へ返事して5分後に返信が来た週に「一通も動かんかった」と書いた。
"""
from datetime import datetime

from src import main, store


def test_material_covers_past_seven_days_and_consults(monkeypatch):
    chats = [
        {"role": "user", "text": "二週間前の話", "created_at": "2026-10-05T10:00:00"},
        {"role": "user", "text": "今日返事を送ったんです Take care で最後締めて", "created_at": "2026-10-16T22:26:00"},
        {"role": "assistant", "text": "返したんやな。", "created_at": "2026-10-16T22:34:00"},
    ]
    monkeypatch.setattr(store, "recent_line_chats", lambda uid, limit=12: chats)
    rows = [
        {"month": "相談", "worry": "絵文字の羅列送るのは良くない？", "reading": "絵文字の羅列、あれはな、ウチは止めとく。",
         "created_at": "2026-10-15T20:20:21"},
        {"month": "相談", "worry": "古い相談", "reading": "古い返事", "created_at": "2026-10-01T09:00:00"},
        {"month": "個別鑑定書", "worry": "", "reading": "鑑定書の全文", "created_at": "2026-10-11T19:17:14"},
    ]
    # 月曜の夜20時に作る
    out = main._weekly_material("U1", rows, datetime(2026, 10, 19, 20, 0, 0))
    assert "Take care" in out                    # 金曜のLINE（前は月曜以降しか見とらんかった）
    assert "絵文字の羅列、あれはな" in out           # 七日以内の会員相談への椿の返事
    assert "二週間前の話" not in out and "古い返事" not in out
    assert "鑑定書の全文" not in out               # 鑑定書は別枠で渡す
    assert out.index("絵文字") < out.index("Take care")   # 古い順（10/15の相談→10/16のLINE）


def test_monday_morning_no_longer_empty(monkeypatch):
    # 前の作りやと、月曜8時には「月曜0時以降」しか読めず空になっとった
    chats = [{"role": "user", "text": "金曜の話", "created_at": "2026-10-16T21:00:00"}]
    monkeypatch.setattr(store, "recent_line_chats", lambda uid, limit=12: chats)
    assert "金曜の話" in main._weekly_material("U1", [], datetime(2026, 10, 19, 8, 0, 0))

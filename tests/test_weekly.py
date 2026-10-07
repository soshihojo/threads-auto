"""「週の一手」（src/weekly.py）。

★守りたいこと
  ① 同じ週に二通作らん（ラベルはその週の月曜に寄る）
  ② 前の週の焼き直しを機械で止める。★ここが抜けると「毎週おなじ話」で解約に直結する
  ③ 保証・リンク・相談窓口・Markdown・時間帯の決めつけを機械で止める
  ④ 動きの無かった週は、動いたように見せかけず「無い」と書かせる
"""
from datetime import date

from src import weekly


# ---- ① 週のラベル ----
def test_ラベルはその週の月曜に寄る():
    """★水曜に走らせても、月曜と同じラベルになる。
    寄せんかったら already_done が効かんで、同じ週に二通届く。"""
    mon, wed, sun = date(2026, 10, 5), date(2026, 10, 7), date(2026, 10, 11)
    assert weekly.last_monday(mon) == mon
    assert weekly.last_monday(wed) == mon
    assert weekly.last_monday(sun) == mon
    assert weekly.month_label(mon) == weekly.month_label(wed) == weekly.month_label(sun)
    assert weekly.month_label(wed) == "週の一手 2026-10-05"


def test_その週ぶんが積んであるか見分ける():
    rows = [{"month": "週の一手 2026-10-05"}, {"month": "相談"}]
    assert weekly.already_done(rows, date(2026, 10, 7)) is True   # 同じ週
    assert weekly.already_done(rows, date(2026, 10, 14)) is False  # 次の週
    assert weekly.already_done([], date(2026, 10, 7)) is False


def test_過去の週の一手だけを新しい順で拾う():
    rows = [{"month": "週の一手 2026-09-21", "reading": "a"},
            {"month": "相談", "reading": "x"},
            {"month": "個別鑑定書", "reading": "y"},
            {"month": "週の一手 2026-09-28", "reading": "b"}]
    got = weekly.previous_weeklies(rows)
    assert [p["month"] for p in got] == ["週の一手 2026-09-28", "週の一手 2026-09-21"]


# ---- ② 焼き直しを止める ----
def _long(seed: str) -> str:
    """検品を通る長さの本文を作る（900字前後）。

    ★種の長さがまちまちなので、繰り返す回数は字数から逆算する。
      固定回数にすると、短い種のときだけ「短すぎる」で落ちる。
    """
    unit = seed + "。"
    return unit * max(1, -(-900 // len(unit)))


def test_前の週と被っとったら検品に掛かる():
    """★これが一番大事や。材料が動いてへん週は、生成の側が同じ結論に戻る。
    人の目だけに頼ったら、四週続けて同じ話を送ってまう。"""
    body = _long("この一週間は動きが無かった")
    prevs = [{"month": "週の一手 2026-09-28", "reading": body}]
    bad = weekly.inspect_weekly(body, prevs)
    assert any("被っとる" in b for b in bad)
    assert any("2026-09-28" in b for b in bad)


def test_中身がちがう週は通る():
    prevs = [{"month": "週の一手 2026-09-28", "reading": _long("先週は彼から電話が来た")}]
    body = _long("今週はあんたの誕生日の前に一通だけ置く段取りを組む")
    assert weekly.inspect_weekly(body, prevs) == []


# ---- ③ 顧客に出したらあかんもの ----
def test_保証の言い方を止める():
    for w in ("必ず連絡が来るで", "絶対に戻ってくる", "きっと連絡が来るわ"):
        bad = weekly.inspect_weekly(_long(w), [])
        assert any("保証" in b for b in bad), w


def test_リンクや商品の案内を止める():
    bad = weekly.inspect_weekly(_long("詳しくは https://example.com を見てな"), [])
    assert any("リンク" in b for b in bad)


def test_相談窓口の案内を止める():
    """★よそへ回す形にはせん（店主の方針）。"""
    bad = weekly.inspect_weekly(_long("しんどかったら相談窓口に行ってみ"), [])
    assert any("相談窓口" in b for b in bad)


def test_Markdownの記号を止める():
    bad = weekly.inspect_weekly("**今の盤面**\n" + _long("あんたは我慢しとる"), [])
    assert any("Markdown" in b for b in bad)


def test_時間帯を決めつけた言葉を止める():
    """★airiさんの事故（18時58分の相談に深夜2時44分の前提で返した）と同じ型や。
    週の一手は【月曜に作って月曜の夜に届く】んで、ここはもっと外れる。"""
    bad = weekly.inspect_weekly(_long("おはよう、今週もいこか"), [])
    assert any("時間帯" in b for b in bad)


def test_長さの外れを止める():
    assert any("短すぎる" in b for b in weekly.inspect_weekly("みじかい", []))
    assert any("長すぎる" in b for b in weekly.inspect_weekly("あ" * 2000, []))


def test_まともな本文は何も掛からん():
    body = _long("この一週間、彼からの連絡は無かった。動かんかった週や")
    assert weekly.inspect_weekly(body, []) == []


# ---- ④ 材料が無い週の扱い ----
def test_やりとりが無い週はプロンプトにそう書く(monkeypatch):
    """★動きが無かった週に、動いたように見せかける文を書かせたらあかん。
    プロンプトの側で「無い」と明示して渡す。"""
    seen = {}

    def _fake(system, user, **kw):
        seen["system"], seen["user"] = system, user
        return _long("この一週間は動きが無かった")

    monkeypatch.setattr(weekly, "complete", _fake)
    weekly.generate_weekly(me_birth="1990-01-01", him_birth="1991-02-02",
                           nickname="テスト", chats="", today=date(2026, 10, 5))
    assert "一通も無い" in seen["user"]
    assert "動きが無かった週" in seen["user"]
    assert "無い出来事を作って埋めたらあかん" in seen["user"]


def test_前の週の本文がプロンプトに入る(monkeypatch):
    def _fake(system, user, **kw):
        _fake.user = user
        return _long("今週は置く週や")

    monkeypatch.setattr(weekly, "complete", _fake)
    weekly.generate_weekly(me_birth="1990-01-01", him_birth="1991-02-02",
                           nickname="テスト",
                           prevs=[{"month": "週の一手 2026-09-28", "reading": "先週の中身"}],
                           today=date(2026, 10, 5))
    assert "先週の中身" in _fake.user
    assert "同じ中身を書いたらあかん" in _fake.user


def test_呼び名の決まりごとがプロンプトに入る(monkeypatch):
    def _fake(system, user, **kw):
        _fake.user = user
        return _long("今週は置く週や")

    monkeypatch.setattr(weekly, "complete", _fake)
    weekly.generate_weekly(me_birth="1990-01-01", him_birth="1991-02-02",
                           nickname="なつみ", note="必ず「なつみさん」。呼び捨て禁止",
                           today=date(2026, 10, 5))
    assert "なつみさん" in _fake.user
    assert "呼び捨て禁止" in _fake.user


def test_ラベルと警告が返る(monkeypatch):
    monkeypatch.setattr(weekly, "complete",
                        lambda s, u, **k: _long("この一週間は動きが無かった"))
    got = weekly.generate_weekly(me_birth="1990-01-01", him_birth="1991-02-02",
                                 nickname="テスト", today=date(2026, 10, 7))
    assert got["label"] == "週の一手 2026-10-05"
    assert got["warns"] == []
    assert len(got["body"]) > 700


def test_検品に掛かったら一度だけ作り直す(monkeypatch):
    calls = []

    def _fake(system, user, **kw):
        calls.append(system)
        return "みじかい" if len(calls) == 1 else _long("ちゃんと書き直した")

    monkeypatch.setattr(weekly, "complete", _fake)
    got = weekly.generate_weekly(me_birth="1990-01-01", him_birth="1991-02-02",
                                 nickname="テスト", today=date(2026, 10, 5))
    assert len(calls) == 2
    assert "【厳重注意】" in calls[1]
    assert got["warns"] == []


# ---- ⑤ 呼び捨てと署名（毎週届くんで、一回の漏れが目立つ） ----
def test_呼び捨ては機械で直す(monkeypatch):
    """★「相談者の呼び名は必ずさん付き」は店主の方針や。
    プロンプトで頼むだけでは3回に1回漏れる（実測：試作が「ゆきえ、椿や。」で始まった）。
    ★彼の呼び名には足さん。"""
    monkeypatch.setattr(weekly, "complete",
                        lambda s, u, **k: _long("ゆきえ、この一週間は動かんかった"))
    got = weekly.generate_weekly(me_birth="1990-01-01", him_birth="1991-02-02",
                                 nickname="ゆきえ", today=date(2026, 10, 5))
    assert "ゆきえさん" in got["body"]
    assert "ゆきえ、" not in got["body"]


def test_末尾の署名を止める():
    """★LINEで届く一通や。末尾に「椿」だけの行は要らん。"""
    bad = weekly.inspect_weekly(_long("今週は置く週や") + "\n\n椿", [])
    assert any("署名" in b for b in bad)
    # 本文の中に出てくる「椿」は止めん
    assert weekly.inspect_weekly(_long("椿が視たとこを言うで"), []) == []

"""書く前の下書き・考えた道筋が、相談者に届かんことを確かめる（2026-10-11 の事故の再発防止）。"""
import pytest

from src import line_bot as bot
from src.diagnosis import inspect_reply, planning_leak

# 実害①：10/11 大野桃佳さんに届いた本文（そのまま）
LEAK_1011 = (
    "考える必要がある。言い当ての回、頭から断定で入る。相談者の性質:ああいう性質→感受性豊か、人を楽しませる、"
    "見栄っ張りな面、実は情に厚く世話好きだが、プライドもある。性質と癖を「◯◯やのうて、△△な型」で断定。\n"
    "相談者の行動パターンから見える癖:今の発言は「考えすぎて動けない、最悪のシナリオを全部想定してしまう」。\n\n"
    "型:「ただ怖がって動けんのやのうて、最悪を先に全部並べて自分を守ろうとする型」のような。\n\n"
    "書き方:頭から言い切り、質問で終わらせない。170字以内。\n\n"
    "案:\n「迷っとるように見えて、実はもう答え決まっとる型やで。」\n\n"
    "Check length ~140字. Good. Avoid forbidden phrases. No question mark at end, ends with statement."
    "迷うてるように見えて、実はもうあんたの中で答えは出とる型やで。"
)
# 実害②：9/23 みさきさんに届いた本文（メモだけで、返事が一行も無かった）
LEAK_0923 = (
    "了解のトーンに短く応えつつ、まだ扉開けとく感じで。\n\n"
    "「わかりました、また連絡きたら連絡します」への返信を書く。相手は落ち着いた/一区切りついたトーン。\n\n"
    "短めに、40-80字くらいで。"
)
# 下書きを除いた、ほんまの本文（これは通らなあかん）
CLEAN = (
    "迷うてるように見えて、実はもうあんたの中で答えは出とる型やで。"
    "「連絡したら終わる」方を先に信じ込んで、動かん理由を自分で積み上げてく。\n"
    "怖がりやのうて、先回りして最悪を潰しとかな気が済まん性分や。"
)
# 普段の返信（誤検出したら返信が届かんようになる）
NORMAL = [
    "真面目な人が1ヶ月黙って抱え込んだ末に出す言葉が「しんどい」や。衝動的な一言やのうて、整理しきれんまま限界が来た感じがする。",
    "ここで期間の訂正な、別れて1週間、ちゃんと受け取った。\n\n今、あんたから連絡したい気持ちはどれくらいある?🌙",
    "このまま自分の勘で動くか、ウチがちゃんと彼の今の状態視てから動くか、どっちや。",
    "一言でええ。どこが刺さったか聞かせてな🌙",
]


@pytest.mark.parametrize("text", [LEAK_1011, LEAK_0923])
def test_leaks_are_detected(text):
    assert planning_leak(text)


@pytest.mark.parametrize("text", [CLEAN, *NORMAL])
def test_normal_replies_pass(text):
    assert planning_leak(text) == ""


@pytest.mark.parametrize("text", [LEAK_1011, LEAK_0923])
def test_final_guard_sends_nothing(text):
    # 送る直前の最後の砦：一文字も送らん（空＝送信失敗→未返信スイープが作り直す）
    assert bot._plain_text(text) == ""


def test_final_guard_keeps_clean_text():
    assert bot._plain_text(CLEAN).strip() != ""


def test_inspect_reply_asks_for_rewrite():
    assert any("下書き" in p for p in inspect_reply("どうしたらいい？", LEAK_1011))


def _user():
    return {"display_name": "テスト", "me_birth": "1999-10-04", "him_birth": "1998-01-01"}


def test_retry_output_is_inspected_again(monkeypatch):
    # 実害の再現：一回目も作り直しも下書き入り → 作り直しを素通しせず、例外で止める
    monkeypatch.setattr(bot, "complete", lambda *a, **k: LEAK_1011)
    with pytest.raises(RuntimeError):
        bot.generate_nurture(_user(), [], "どうしたらいい？", extra_system=bot._ASSERT_GUIDE.format(target="相談者本人"))


def test_retry_that_fixes_it_is_sent(monkeypatch):
    outs = iter([LEAK_1011, CLEAN])
    monkeypatch.setattr(bot, "complete", lambda *a, **k: next(outs))
    assert bot.generate_nurture(_user(), [], "どうしたらいい？") == CLEAN

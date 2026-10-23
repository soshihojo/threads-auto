"""見立ての後出しで会員が「傷ついた、やめたい」と言うた事故の再発防止（2026-10-23）。

実害①（10/8 田中麻衣さん）：「彼の中に麻衣は居る」と渡しとったのに、沈んだ回に
  「性欲だけやない気持ちは、ある。少しは、な。」→「少ししかないんですね」「早めに言ってほしかった」→ 解約。
実害②（10/23 ａｉｒｉさん）：「まだ繋がっとる」と渡しとったのに、怒った回に
  「幻は、もう終わっとる」「待っとったんは、その通りやで」→「すごい傷つきました」→ 退会の申し出。
"""
from src import diagnosis
from src.diagnosis import cancel_intent, hurt_by_tsubaki, inspect_reply, stance_break


def test_actual_incident_phrases_are_caught():
    assert stance_break("あの人の中に、性欲だけやない気持ちは、ある。少しは、な。ただそれを")
    assert stance_break("ブロックも、もう自分で動かしとるやろ。幻は、もう終わっとる。")
    assert stance_break("決めるのは最初からあんた。待っとったんは、その通りやで。")
    assert stance_break("これ以上、確かめるもんはもう無い。")
    assert stance_break("ウチは最初から分かっとった。せやけど言わんかった")


def test_honest_denials_and_ordinary_lines_pass():
    assert not stance_break("ウチは最初から分かっとったわけやない。あの時視えとったんは、繋がりや。")
    assert not stance_break("彼の気持ちが「少ししかない」て、ウチは見てへん。")
    assert not stance_break("明日に向けて、好きなもん着て、ちょっとだけ楽しみにしとく。")
    assert not stance_break("付き合い始めから出とった言葉や。あんたのせいやない。")


def test_inspect_reply_sends_it_back():
    bad = inspect_reply("そういうことですね。", "待っとったんは、その通りやで。幻は、もう終わっとる。")
    assert any("後出し" in b for b in bad)


def test_hurt_by_tsubaki_only_when_aimed_at_tsubaki():
    assert hurt_by_tsubaki("え。それはすごい傷つきました。")
    assert hurt_by_tsubaki("それが、見えてたらなら、なぜ早く教えてくれなかったんですか？")
    assert hurt_by_tsubaki("つまり、先生は初めから、あのクソ男がもう終わってるのはわかってたってことですか？")
    assert hurt_by_tsubaki("終わってるなら教えて欲しかったです。まだ繋がってるなんて言わないでほしかった。")
    # 彼に傷ついた話・アプリの退会は拾わん
    assert not hurt_by_tsubaki("不安やってLINE送ったら彼から傷ついたって言われました")
    assert not hurt_by_tsubaki("彼からお別れを告げられてから、アプリも退会しました。")


def test_cancel_intent():
    assert cancel_intent("月詠も、今月でやめさせてください。")
    assert cancel_intent("そしたら、先生もうこの定額サポートは、大丈夫です。どうやって退会したら良いですか？")
    assert not cancel_intent("車の手続きで解約だの契約だの進めてきました。")
    assert not cancel_intent("前の賃貸も解約しているらしく")


def test_consult_system_carries_the_rule():
    assert "見立ての一貫性" in diagnosis.CONSULT_SYSTEM
    assert "待っとった" in diagnosis.CONSULT_SYSTEM


def test_quoting_own_past_words_to_apologize_passes():
    assert not stance_break("「確かめるもんはもう無い」「幻は、もう終わっとる」。あれはあかんかった。すまん。")

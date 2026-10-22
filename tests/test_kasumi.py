"""かすみ（推し活占い）の診断・LINEボットのテスト。LINEと生成はすべて差し替える。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import datetime

import pytest

from src.kasumi import bot, diag, en, store, texts

if store._BACKEND == "sheets":   # 本物のスプレッドシートに書かないための安全装置
    pytest.skip("STORE_BACKEND=sheets では走らせない", allow_module_level=True)

JST = store.JST


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "kasumi.db")
    store.init()


@pytest.fixture
def line(monkeypatch):
    """LINEへの送信を記録するだけにする。"""
    sent: list[dict] = []
    monkeypatch.setenv("LINE_CHANNEL_SECRET_KASUMI", "s" * 32)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN_KASUMI", "t" * 40)
    monkeypatch.setattr(bot, "_post", lambda path, payload: sent.append({"path": path, **payload}) or True)
    monkeypatch.setattr(bot, "display_name", lambda uid: "ゆい")
    monkeypatch.setattr(bot, "_notify_owner", lambda m: None)
    return sent


def _text(sent: dict) -> str:
    return sent["messages"][0]["text"]


def _msg(uid: str, text: str) -> dict:
    return {"type": "message", "replyToken": "r", "source": {"userId": uid},
            "message": {"type": "text", "text": text}}


# ---------------- 縁タイプ ----------------
def test_relation_is_symmetric_and_maps_known_distances():
    for d in range(27):
        assert en.relation_from_distance(d) == en.relation_from_distance(27 - d)
    assert en.relation_from_distance(0) == "命"
    assert en.relation_from_distance(9) == en.relation_from_distance(18) == "業胎"
    assert en.relation_from_distance(1) == "栄親"
    assert en.relation_from_distance(2) == "友衰"
    assert en.relation_from_distance(3) == "安壊"
    assert en.relation_from_distance(4) == "危成"
    assert {en.relation_from_distance(d) for d in range(27)} == set(texts.EN_TYPES)


def test_relation_from_birthdates_is_one_of_six_and_symmetric():
    a, b = "1990-05-12", "1995-10-03"
    assert en.relation(a, b) in texts.EN_TYPES
    assert en.relation(a, b) == en.relation(b, a)


def test_user_facing_texts_have_no_jargon():
    from src.diagnosis import SHUKU_27
    banned = [*SHUKU_27, "宿曜", "本命宿", *texts.EN_TYPES.keys()]
    blobs = [texts.GREETING, texts.CODE_NOT_FOUND, texts.READING, texts.CLOSING, texts.DANGER,
             *texts.OSHI_MONTH.values(), *texts.ME_MONTH.values(), *texts.WORRIES.values(),
             *(v for t in texts.EN_TYPES.values() for k, v in t.items()),
             *(s["text"] for s in texts.STEPS)]
    for blob in blobs:
        for w in banned:
            assert w not in blob, (w, blob[:30])


def test_step_options_fit_line_quick_reply_limits():
    assert len(texts.STEPS) == 7
    for s in texts.STEPS:
        assert 1 <= len(s["options"]) <= 13
        assert all(len(o) <= 20 for o in s["options"])


# ---------------- 診断ページ ----------------
def test_page_has_all_choices_and_no_placeholders():
    html = diag.page_html()
    assert "__" not in re.sub(r"<script.*?</script>", "", html, flags=re.S) or "__FLOWER__" not in html
    assert "__STATES__" not in html and "__WORRIES__" not in html and "__FLOWER__" not in html
    for w in texts.WORRIES:
        assert w in html
    assert "/kasumi/privacy" in html and "/kasumi/shindan/api" in html


def test_submit_issues_code_and_redeem_once():
    r = diag.submit({"me": "1990-05-12", "oshi": "1995-10-03", "state": texts.STATES[1],
                     "worry": "推しの熱愛・結婚・変化への気持ち", "src": "c1", "vid": "v"})
    assert r["ok"] and r["name"] in {t["name"] for t in texts.EN_TYPES.values()}
    assert 3000 <= int(r["code"]) <= 9999
    assert "751dznkf" in r["line_oa_url"] and r["code"] in r["line_oa_url"]
    row = diag.redeem(r["code"])
    assert row and row["oshi_birth"] == "1995-10-03" and row["source"] == "c1"
    store.mark_diag_used(r["code"], "U1")
    assert diag.redeem(r["code"]) is None
    assert diag.redeem("9999") is None or diag.redeem("9999")["code"] == "9999"


def test_submit_rejects_unknown_choice_and_bad_date():
    with pytest.raises(ValueError):
        diag.submit({"me": "1990-05-12", "oshi": "1995-10-03", "state": "なにか", "worry": texts.NO_WORRY})
    with pytest.raises(ValueError):
        diag.submit({"me": "1990-02-30", "oshi": "1995-10-03", "state": texts.STATES[0], "worry": texts.NO_WORRY})


# ---------------- 署名 ----------------
def test_verify_signature(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET_KASUMI", "abc")
    body = b'{"events":[]}'
    sig = base64.b64encode(hmac.new(b"abc", body, hashlib.sha256).digest()).decode()
    assert bot.verify_signature(body, sig)
    assert not bot.verify_signature(body, "x")
    monkeypatch.delenv("LINE_CHANNEL_SECRET_KASUMI")
    assert not bot.verify_signature(body, sig)


# ---------------- 縁の読み ----------------
def test_build_reading_assembles_parts_with_honorific():
    t = bot.build_reading("ゆい", "1990-11-12", "1995-03-03", "友衰", "推しの熱愛・結婚・変化への気持ち")
    assert t.startswith("ゆいさん、お待たせしました。")
    assert texts.EN_TYPES["友衰"]["reading"] in t and texts.EN_TYPES["友衰"]["tired"] in t
    assert texts.OSHI_MONTH[3] in t and texts.ME_MONTH[11] in t
    assert t.rstrip().endswith("そのことを知ったとき、どんな気持ちになりましたか？")
    assert bot.build_reading("", "1990-11-12", "1995-03-03", "命", texts.NO_WORRY).startswith("お待たせしました。")


# ---------------- 会話の流れ ----------------
def test_full_flow_follow_code_hearing_closing_steps(line, monkeypatch):
    calls = []

    def fake_complete(system, prompt, **kw):
        calls.append(prompt)
        return "そう感じていたんですね。" + ("いつごろからですか？" if "次に聞くこと：なし" not in prompt else "")
    monkeypatch.setattr(bot, "complete", fake_complete)

    bot.handle_event({"type": "follow", "replyToken": "r", "source": {"userId": "U1"}})
    assert "ゆいさん、来てくれてありがとうございます" in _text(line[-1])

    r = diag.submit({"me": "1990-11-12", "oshi": "1995-03-03", "state": texts.STATES[1],
                     "worry": "ファン同士・界隈の人間関係"})
    bot.handle_event(_msg("U1", f"縁の番号{r['code']}"))
    reading = _text(line[-1])
    assert "推しとあなたの縁を読みました" in reading and "モヤっとした出来事" in reading
    assert store.get_user("U1")["stage"] == "hearing"
    assert diag.redeem(r["code"]) is None   # 使用済み

    for i in range(3):
        bot.handle_event(_msg("U1", f"答え{i}"))
        assert line[-1]["path"] == "message/reply"
    assert store.get_user("U1")["turns"] == "3"
    assert "もしかすみがもっと深く読むとしたら" in calls[2]   # 3問目は「知りたいこと」

    bot.handle_event(_msg("U1", "答え3"))
    u = store.get_user("U1")
    assert u["stage"] == "done" and u["step_day"] == "1"
    assert texts.CLOSING.splitlines()[0] in _text(line[-1])

    # 翌日20時を過ぎたら1日目の配信（ボタンつき）
    later = datetime.fromisoformat(u["step_next_at"])
    assert later.hour == 20
    assert bot.send_due_steps(later.replace(minute=5)) == 1
    pushed = line[-1]
    assert pushed["path"] == "message/push" and "quickReply" in pushed["messages"][0]
    assert bot.send_due_steps(later.replace(minute=6)) == 0   # 同じ日に二度送らない
    bot.handle_event(_msg("U1", "5〜10年"))
    ans = store._rows("kasumi_answers")
    assert ans[-1]["kind"] == "oshi_years" and ans[-1]["answer"] == "5〜10年"
    assert store.get_user("U1")["step_wait"] == ""


def test_steps_not_sent_outside_window_or_over_cap(line, monkeypatch):
    store.upsert_user("U2", stage="done", step_day="1", bot="on", step_next_at="2026-10-23T20:00:00")
    assert bot.send_due_steps(datetime(2026, 10, 23, 23, 0, tzinfo=JST)) == 0   # 22時以降は送らない
    monkeypatch.setattr(bot, "PUSH_MONTHLY_CAP", 0)
    assert bot.send_due_steps(datetime(2026, 10, 24, 20, 30, tzinfo=JST)) == 0


def test_unknown_code_and_birthdates_without_code(line):
    bot.handle_event(_msg("U3", "4321"))
    assert _text(line[-1]) == texts.CODE_NOT_FOUND
    bot.handle_event(_msg("U3", "わたし 1990/5/12　推し 1995/10/3"))
    assert "推しとあなたの縁を読みました" in _text(line[-1])
    assert store.get_user("U3")["stage"] == "hearing"


def test_money_amount_in_hearing_is_not_a_code(line, monkeypatch):
    monkeypatch.setattr(bot, "complete", lambda *a, **k: "そうなんですね。誰かに話せていますか？")
    store.upsert_user("U4", stage="hearing", turns="0", worry=texts.DEFAULT_WORRY, bot="on")
    bot.handle_event(_msg("U4", "月5000円くらいです"))
    assert _text(line[-1]) != texts.CODE_NOT_FOUND
    assert store.get_user("U4")["turns"] == "1"


def test_danger_words_hold_the_bot(line, monkeypatch):
    monkeypatch.setattr(bot, "complete", lambda *a, **k: pytest.fail("生成を呼んではいけない"))
    store.upsert_user("U5", stage="hearing", turns="1", bot="on")
    bot.handle_event(_msg("U5", "もう消えたいです"))
    assert _text(line[-1]) == texts.DANGER
    assert store.get_user("U5")["bot"] == "hold"
    n = len(line)
    bot.handle_event(_msg("U5", "まだいますか"))
    assert len(line) == n   # 店主が対応するまで自動では返さない


def test_generated_reply_is_cleaned(line, monkeypatch):
    monkeypatch.setattr(bot, "complete", lambda *a, **k: "**そうなんですね**。昴宿の縁ですね。誰かに話せていますか？")
    store.upsert_user("U6", stage="hearing", turns="0", worry=texts.DEFAULT_WORRY, bot="on")
    bot.handle_event(_msg("U6", "最近しんどくて"))
    out = _text(line[-1])
    assert "**" not in out and "昴宿" not in out


def test_llm_failure_falls_back_to_fixed_question(line, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(bot, "complete", boom)
    store.upsert_user("U7", stage="hearing", turns="0", worry=texts.DEFAULT_WORRY, bot="on")
    bot.handle_event(_msg("U7", "こんにちは"))
    assert _text(line[-1]) == texts.FALLBACK_QUESTIONS[0]


def test_webhook_disabled_without_keys(monkeypatch):
    monkeypatch.delenv("LINE_CHANNEL_SECRET_KASUMI", raising=False)
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN_KASUMI", raising=False)
    assert not bot.enabled()
    assert bot.send_due_steps() == 0

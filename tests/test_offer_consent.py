"""Synthetic regressions: no invented consent, clipped questions, or stale consultation counts."""
from datetime import datetime, timedelta
import pytest
from src import line_bot as b


def message(role, text, at):
    return dict(role=role, text=text, created_at=at)


def resumed_history(gap_days=40):
    old = datetime(2020, 1, 1, 10)
    current = old + timedelta(days=gap_days)
    history = [message('assistant', '以前の相談だけで話した内容', old.isoformat()) for _ in range(7)]
    history += [message('user', '鑑定番号4826', current.isoformat()),
                message('assistant', b.WEB_DIAG_INTRO + '今回の診断内容。' * 40,
                        (current + timedelta(seconds=20)).isoformat()),
                message('user', '最後は挨拶を送りました',
                        (current + timedelta(minutes=1)).isoformat())]
    return history


@pytest.mark.parametrize('incoming', ['自分の勘だと判断できません。お願いします',
                                     '料金を知りたいです', '少し考えたいです'])
def test_offer_cannot_invent_consent_even_if_model_would(monkeypatch, incoming):
    monkeypatch.setattr(b, 'complete', lambda *a, **k: pytest.fail('offer must not use a model'))
    text = b.generate_offer({}, [message('user', '前の相談の話', '2020-01-01')], incoming)
    assert text == b.OFFER_INTRO_FALLBACK + '\n\n' + b.OFFER_MENU
    assert '受け取った' not in text and '中身はもう組んである' not in text
    assert '理由を三つ' not in text and '前の相談の話' not in text
    assert b.URL_KANTEI in text and b.URL_SHIOMI in text
    assert '3,980円' in text and '9,800円' in text and '有料' in text


@pytest.mark.parametrize('days,expected', [(29, 10), (30, 3), (40, 3)])
def test_restart_requires_long_gap_and_delivered_diagnosis(days, expected):
    history = resumed_history(days)
    original = list(history)
    assert len(b._current_consultation(history)) == expected
    assert history == original


@pytest.mark.parametrize('change', ['no_code', 'not_delivered', 'unknown_date', 'invalid_date'])
def test_partial_or_unverifiable_restart_keeps_count(change):
    history = resumed_history()
    if change == 'no_code': history[7]['text'] = '相談に戻りました'
    if change == 'not_delivered': history[8]['text'] = b.CODE_NOT_FOUND
    if change == 'unknown_date': history[6].pop('created_at')
    if change == 'invalid_date': history[7]['created_at'] = 'invalid'
    assert b._current_consultation(history) == history


def test_resending_code_same_day_does_not_reset_again():
    history = resumed_history()
    history += [message('user', '鑑定番号5678', '2020-02-10T11:00:00'),
                message('assistant', b.WEB_DIAG_INTRO + '診断', '2020-02-10T11:00:20')]
    current = b._current_consultation(history)
    assert len(current) == 5 and current[0]['text'] == '鑑定番号4826'


def test_old_offer_still_prevents_duplicate_offer(monkeypatch):
    history = resumed_history()
    history[0]['text'] = b.OFFER_MENU
    monkeypatch.setattr(b.store, 'recent_line_chats', lambda *a, **k: history)
    monkeypatch.setattr(b.store, 'get_line_user', lambda *a: {'bot': 'on', 'note': ''})
    assert len(b._current_consultation(history)) == 3
    assert b._offer_already_sent('synthetic') is True


def test_entire_prior_question_and_dates_reach_question_generator(monkeypatch):
    question = '状況についての説明です。' * 12 + '自分の勘で動くか、見てから決めるか、どちらですか？'
    captured = []
    monkeypatch.setattr(b, 'complete', lambda system, prompt, **k: captured.append(prompt) or b.ASK_DEEPER)
    b.generate_ask_deeper({}, [message('assistant', question, '2020-03-01T10:00:00')], '勘では難しいです')
    assert question in captured[0] and '2020-03-01T10:00:00' in captured[0]


def test_old_turns_do_not_force_immediate_offer_or_question(monkeypatch):
    history = resumed_history()
    state = {'bot': 'on', 'note': ''}
    sent, contexts = [], []
    monkeypatch.setattr(b.store, 'get_line_user', lambda *a: dict(state))
    monkeypatch.setattr(b.store, 'recent_line_chats', lambda *a, **k: list(history))
    monkeypatch.setattr(b.store, 'upsert_line_user', lambda uid, **k: state.update(k))
    monkeypatch.setattr(b, '_handle_code', lambda *a: False)
    monkeypatch.setattr(b, '_send', lambda uid, token, text: sent.append(text) or True)
    monkeypatch.setattr(b, '_route_offer', lambda *a: pytest.fail('old replies cannot trigger offer'))
    monkeypatch.setattr(b, 'generate_ask_deeper', lambda *a: pytest.fail('old replies cannot trigger question'))
    monkeypatch.setattr(b, 'generate_nurture', lambda user, h, incoming: contexts.append(h) or '挨拶を送ったんやな。')
    b._auto_reply('synthetic', state, history[-1]['text'], live=False)
    assert sent == ['挨拶を送ったんやな。'] and state['bot'] == 'on'
    assert all('以前の相談' not in r['text'] for r in contexts[0])


def test_new_consultation_still_reaches_its_normal_limit(monkeypatch):
    history = resumed_history()
    history[-1:-1] = [message('assistant', '今回の返信', '2020-02-10T10:00:40') for _ in range(b.FREE_REPLY_LIMIT)]
    state, sent = {'bot': 'on', 'note': ''}, []
    monkeypatch.setattr(b.store, 'get_line_user', lambda *a: dict(state))
    monkeypatch.setattr(b.store, 'recent_line_chats', lambda *a, **k: list(history))
    monkeypatch.setattr(b, '_handle_code', lambda *a: False)
    monkeypatch.setattr(b, '_send', lambda uid, token, text: sent.append(text) or True)
    monkeypatch.setattr(b, 'generate_ask_deeper', lambda *a: b.ASK_DEEPER)
    b._auto_reply('synthetic', state, history[-1]['text'], live=False)
    assert sent == [b.ASK_DEEPER] and state['bot'] == 'on'


@pytest.mark.parametrize('text', ['「視てほしい」——ちゃんと受け取ったで。',
                                 '『見て欲しい』、受けとったで。'])
def test_old_consent_acknowledgement_is_blocked_before_transport(text):
    assert b._plain_text(text) == ''


@pytest.mark.parametrize('incoming', [
    'それで別の相手ができていました',
    'その後も連絡はありません',
    'はい、でもまだ決めていません',
    'それで彼から連絡がありました',
    'もう疲れました',
    '勘で動くのは怖いです',
    'ありがとうございます',
    '少し考えたいです',
])
def test_story_after_choice_is_not_a_request(incoming):
    history = [message('assistant', b.ASK_DEEPER, '2020-03-01T10:00:00'),
               message('user', incoming, '2020-03-01T10:01:00')]
    assert b._canned_ask_deeper_just_sent(history)
    assert b.detect_signal(incoming, history) is None


@pytest.mark.parametrize('incoming', ['みて', '後者です', 'お願いします',
                                     '自分の勘だと判断できません。お願いします',
                                     '料金を教えてください'])
def test_explicit_request_after_choice_still_gets_information(incoming):
    history = [message('assistant', b.ASK_DEEPER, '2020-03-01T10:00:00')]
    assert b.detect_signal(incoming, history) == 'purchase'


def test_story_after_choice_continues_conversation(monkeypatch):
    incoming = 'それで別の相手ができていました'
    history = [message('assistant', '以前の返事', '2020-03-01T09:00:00') for _ in range(7)]
    history += [message('assistant', b.ASK_DEEPER, '2020-03-01T10:00:00'),
                message('user', incoming, '2020-03-01T10:01:00')]
    state, sent = {'bot': 'on', 'note': ''}, []
    monkeypatch.setattr(b.store, 'get_line_user', lambda *a: dict(state))
    monkeypatch.setattr(b.store, 'recent_line_chats', lambda *a, **k: list(history))
    monkeypatch.setattr(b.store, 'upsert_line_user', lambda uid, **k: state.update(k))
    monkeypatch.setattr(b, '_handle_code', lambda *a: False)
    monkeypatch.setattr(b, '_send', lambda uid, token, text: sent.append(text) or True)
    monkeypatch.setattr(b, '_route_offer', lambda *a: pytest.fail('story is not consent'))
    monkeypatch.setattr(b, 'generate_ask_deeper', lambda *a: pytest.fail('do not immediately repeat choice'))
    monkeypatch.setattr(b, 'generate_nurture', lambda *a: 'その後の状況も話してくれたんやな。')
    b._auto_reply('synthetic', state, incoming, live=False)
    assert sent == ['その後の状況も話してくれたんやな。']
    assert state['bot'] == 'on'

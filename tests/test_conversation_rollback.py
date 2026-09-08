import pytest
from src import line_bot as b, conversation_quality as q, offer_routing as r

@pytest.fixture
def flow(monkeypatch):
    state={'bot':'on'};history=[];sent=[]
    monkeypatch.setattr(b.store,'get_line_user',lambda *a:dict(state))
    monkeypatch.setattr(b.store,'recent_line_chats',lambda *a,**k:list(history))
    monkeypatch.setattr(b.store,'upsert_line_user',lambda uid,**kw:state.update(kw))
    monkeypatch.setattr(b,'_handle_code',lambda *a:False)
    monkeypatch.setattr(b,'_diag_count',lambda *a:1)
    monkeypatch.setattr(b,'_is_minor',lambda *a:False)
    monkeypatch.setattr(b,'detect_signal',lambda *a:None)
    monkeypatch.setattr(b,'_offer_already_sent',lambda *a:False)
    monkeypatch.setattr(q,'classify',lambda *a:pytest.fail('observational classifier cannot control replies'))
    monkeypatch.setattr(b,'generate_nurture',lambda *a,**k:'今の話を踏まえて返すな。')
    monkeypatch.setattr(b,'_send',lambda uid,token,text:sent.append(text) or True)
    return state,history,sent

@pytest.mark.parametrize('incoming',['気持ちが楽になりました。ありがとう','AI文章やめてほしいです','彼にやめてと言われました','普通に相談したい'])
def test_new_classifier_cannot_send_canned_closure_or_stop(monkeypatch,flow,incoming):
    state,h,sent=flow
    h[:]=[{'role':'assistant','text':'相談への返信'}]*2+[{'role':'user','text':incoming}]
    monkeypatch.setattr(b,'_route_offer',lambda *a:pytest.fail('two replies cannot trigger new early offer'))
    b._auto_reply('test',state,incoming,live=False)
    assert state['bot']=='on' and sent==['今の話を踏まえて返すな。']


def test_added_ten_reply_stop_is_removed(monkeypatch,flow):
    state,h,sent=flow
    h[:]=[{'role':'assistant','text':'相談への返信'}]*10+[{'role':'user','text':'話を続けたい'}]
    monkeypatch.setattr(b,'generate_ask_deeper',lambda *a:'従来の意思確認')
    b._auto_reply('test',state,'話を続けたい',live=False)
    assert state['bot']=='on' and sent and '自動返信' not in sent[0]


def test_old_quality_marker_does_not_override_operator_restart(flow):
    state,h,sent=flow
    state['note']='[会話状態:error]'
    h[:]=[{'role':'user','text':'相談です'}]
    b._auto_reply('test',state,'相談です',live=False)
    assert sent and state['bot']=='on'


def test_direct_price_question_still_needs_no_model():
    result=r.route([], '鑑定料いくらですか？',lambda *a,**k:pytest.fail('no classifier needed'),'test')
    assert result.kind=='offer' and result.key=='compare'
    assert '3,980円' in result.text and '9,800円' in result.text


def test_removed_canned_copy_cannot_reach_transport():
    for text in ['希望に合う内容を確認するため、ここからは店主が対応します。',
                 '返し方で嫌な思いをさせてごめんな。こちらからの自動返信は止めとくな。',
                 '話してくれてありがとう。今日はここで終わりにしよな。']:
        assert b._plain_text(text)==''


def test_retired_handoff_is_absent_from_reply_and_push_payloads(monkeypatch):
    import json
    from types import SimpleNamespace

    payloads = []
    records = []
    monkeypatch.setattr(b, '_headers', lambda: {})
    monkeypatch.setattr(b, '_maybe_split_bubble', lambda text: text)
    monkeypatch.setattr(b.store, 'add_line_chat', lambda *a: records.append(a))

    def transport(url, **kwargs):
        payload = json.loads(kwargs['data'])
        payloads.append(payload)
        # LINE rejects empty text. No real transport is used in this test.
        return SimpleNamespace(ok=all(m['text'].strip() for m in payload['messages']))

    monkeypatch.setattr(b.requests, 'post', transport)
    text = '希望に合う内容を確認するため、ここからは店主が対応します。'
    assert b._send('synthetic-user', 'synthetic-token', text) is False
    assert len(payloads) == 2  # Both reply and push fallback are checked.
    assert all(text not in m['text'] for p in payloads for m in p['messages'])
    assert records == []

    assert b._send('synthetic-user', 'synthetic-token', '待ちたい気持ちは受け取ったで。') is True
    assert payloads[-1]['messages'][0]['text'] == '待ちたい気持ちは受け取ったで。'
    assert len(records) == 1

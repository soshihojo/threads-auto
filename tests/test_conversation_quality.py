import json
import pytest
from src import conversation_quality as q, line_bot as bot, offer_routing as r

@pytest.mark.parametrize('raw', ['{}','not json',json.dumps({'kind':'closed','evidence':'invented','ready':False})])
def test_bad_classification_is_rejected(raw):
    with pytest.raises((ValueError,TypeError)):
        q.classify([], 'ありがとう',lambda *a,**k:raw,'test')

@pytest.mark.parametrize('kind,text', [('closed','気持ちが楽になりました。ありがとう'),
    ('correction','疲れたのは彼じゃなくて私'),('ai','AIですか？'),('decline','今は買いません')])
def test_grounded_decisions(kind,text):
    d={'kind':kind,'evidence':text,'ready':False}
    assert q.classify([],text,lambda *a,**k:json.dumps(d),'test')==d

@pytest.mark.parametrize('state', ['', 'closed','decline','complaint','correction','answered','followed_up'])
def test_followup_only_for_new_explicit_waiting_state(state):
    assert q.followup({'note':f'[会話状態:{state}]'},[{'role':'assistant','text':'何を知りたい？'}]) is None

def test_followup_uses_actual_single_question():
    t=q.followup({'note':'[会話状態:awaiting]'},[{'role':'assistant','text':'最後に話したのはいつ？'}])
    assert '最後に話したのはいつ？' in t
    assert q.followup({'note':'[会話状態:awaiting]'},[{'role':'assistant','text':'いつ？何を話した？'}]) is None

@pytest.fixture
def flow(monkeypatch):
    state={'bot':'on'};history=[{'role':'assistant','text':'彼が疲れたんやな。'}, {'role':'user','text':'疲れたのは私です'}];sent=[];events=[]
    monkeypatch.setattr(bot.store,'get_line_user',lambda *a:dict(state))
    monkeypatch.setattr(bot.store,'recent_line_chats',lambda *a,**k:list(history))
    monkeypatch.setattr(bot.store,'upsert_line_user',lambda uid,**kw:state.update(kw))
    monkeypatch.setattr(bot.store,'append_ops_event',lambda e:events.append(e))
    def snd(t):sent.append(t);history.append({'role':'assistant','text':t});return True
    return state,history,sent,events,snd

@pytest.mark.parametrize('kind,stopped', [('closed',False),('decline',True),('complaint',True),('ai',True),('error',True)])
def test_exceptional_turn_never_sells(monkeypatch,flow,kind,stopped):
    state,h,sent,events,snd=flow
    monkeypatch.setattr(q,'classify',lambda *a:{'kind':kind,'ready':False})
    assert bot._quality_turn('test',dict(state),h,h[-1]['text'],snd) is None
    assert (state['bot']=='hold')==stopped
    assert all('stores.jp' not in t and '店主' not in t for t in sent)
    if kind=='error':assert not sent and any(e['kind']=='task.set' for e in events)
    if kind=='ai':assert not sent and q.state(state)=='ai' and any(e['kind']=='task.set' for e in events)


def test_correction_acknowledged_before_pending_offer(monkeypatch,flow):
    state,h,sent,events,snd=flow
    h[0]['text']=r.QUESTIONS['scope']
    monkeypatch.setattr(q,'classify',lambda *a:{'kind':'correction','ready':False})
    monkeypatch.setattr(bot,'generate_nurture',lambda *a,**k:'読み違えてごめんな。疲れたのは相談してくれた本人やね。')
    monkeypatch.setattr(bot,'_send',lambda uid,token,t:snd(t))
    monkeypatch.setattr(bot,'_handle_code',lambda *a:False)
    monkeypatch.setattr(bot,'_diag_count',lambda *a:1)
    monkeypatch.setattr(bot,'_member_status',lambda *a:'free')
    monkeypatch.setattr(bot,'_route_offer',lambda *a:pytest.fail('must not sell after correction'))
    bot._auto_reply('test',dict(state),h[-1]['text'],live=False)
    assert len(sent)==1 and '読み違え' in sent[0]


def test_new_message_during_triage_cancels_stale_send(monkeypatch,flow):
    state,h,sent,events,snd=flow
    def classify(*a):
        h.append({'role':'user','text':'まだ質問があります'})
        return {'kind':'closed','ready':False}
    monkeypatch.setattr(q,'classify',classify)
    bot._quality_turn('test',dict(state),h,'疲れたのは私です',snd)
    assert not sent and not events


def test_failed_send_does_not_close_conversation(monkeypatch,flow):
    state,h,sent,events,snd=flow
    monkeypatch.setattr(q,'classify',lambda *a:{'kind':'closed','ready':False})
    bot._quality_turn('test',dict(state),h,h[-1]['text'],lambda t:False)
    assert not q.state(state) and not events


def test_state_preserves_operator_notes_and_refollow(monkeypatch,flow):
    state,*_=flow;state['note']='再追加:2026-09-08T10:00:00 メモ [会話状態:closed]'
    q.set_state('test','awaiting')
    assert '再追加:2026-09-08T10:00:00 メモ' in state['note']
    assert state['note'].count('[会話状態:')==1


def test_ai_question_has_no_automatic_customer_copy():
    assert q.AI_REPLY == ''

def test_advice_question_is_never_ready_for_automatic_sales():
    raw=json.dumps({'kind':'normal','ready':True,'evidence':''})
    assert not q.classify([], 'どう接すればいいですか？',lambda *a,**k:raw,'test')['ready']


def test_price_question_bypasses_quality_model_failure(monkeypatch,flow):
    state,h,sent,events,snd=flow
    monkeypatch.setattr(q,'classify',lambda *a:pytest.fail('price needs no model'))
    assert bot._quality_turn('test',state,h,'鑑定料いくらですか？',snd)['ready']


def test_seven_day_outcomes_exclude_pending_and_unlinked_cash():
    from datetime import datetime,timezone
    from src.operations import new_event
    rows=[new_event('a','conversation.offer',{'product':'kantei'},created_at='2026-09-01T12:00:00+09:00'),
          new_event('a','conversation.offer',{'product':'kantei'},created_at='2026-09-02T12:00:00+09:00'),
          new_event('b','conversation.offer',{'product':'shiomi'},created_at='2026-09-08T12:00:00+09:00'),
          new_event('a','payment.manual',{'provider':'STORES','reference':'ref1','amount_yen':3980},created_at='2026-09-02T12:00:00+09:00'),
          new_event('other','payment.manual',{'provider':'STORES','reference':'ref2','amount_yen':9800},created_at='2026-09-02T12:00:00+09:00')]
    report=q.offer_outcomes(rows,datetime(2026,9,8,12,tzinfo=timezone.utc))
    assert len(report)==1 and report[0]['user_id']=='a' and report[0]['receipts']==3980
    rows.append(new_event('a','payment.void',{'event_id':rows[3]['id']}))
    assert not q.offer_outcomes(rows,datetime(2026,9,8,12,tzinfo=timezone.utc))[0]['purchased']


def test_followup_success_only_once_and_new_input_cancels(monkeypatch,flow):
    state,h,sent,events,snd=flow
    state['note']='[会話状態:awaiting]';h[:]=[{'role':'assistant','text':'今いちばん困ってることは何？','id':'5'}]
    monkeypatch.setattr(bot,'_member_status',lambda *a:'free')
    monkeypatch.setattr(bot,'_send',lambda uid,token,t:snd(t))
    observed=list(h)
    assert bot._quality_followup('test',observed)
    assert not bot._quality_followup('test',observed)
    state['note']='[会話状態:awaiting]';h.append({'role':'user','text':'ありがとう','id':'6'})
    assert not bot._quality_followup('test',observed)
    assert len(sent)==1


def test_multiple_questions_fail_closed_after_retry(monkeypatch):
    monkeypatch.setattr(bot,'complete',lambda *a,**k:'いつ会った？何を話した？')
    assert bot.generate_nurture({},[],'相談です') is None

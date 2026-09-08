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

def test_advice_question_is_never_ready_for_automatic_sales():
    raw=json.dumps({'kind':'normal','ready':True,'evidence':''})
    assert not q.classify([], 'どう接すればいいですか？',lambda *a,**k:raw,'test')['ready']

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

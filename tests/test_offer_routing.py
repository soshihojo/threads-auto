import json
import pytest
from src import offer_routing as r
from src import line_bot as bot


def data(plan='unknown', **kw):
    return dict(plan=plan, evidence='', situation='', goal='', logistics=False, **{}) | kw


def msg(role, text):
    return {'role':role, 'text':text}


@pytest.mark.parametrize('field,key', [({},'situation'),({'situation':'返事がない'},'goal'),
                                     ({'situation':'返事がない','goal':'復縁したい'},'scope')])
def test_only_missing_information_is_asked(field,key):
    result=r.decide([], '相談です', data(**field))
    assert result.kind=='question' and result.key==key
    assert 'stores.jp' not in result.text


def test_questions_are_bounded_and_ambiguous_answer_does_not_upsell():
    history=[msg('assistant',q) for q in r.QUESTIONS.values()]
    result=r.decide(history,'はい',data())
    assert result.key=='compare' and '薦める' not in result.text
    assert r.pending(history)=='scope'


def test_customer_echo_is_not_a_question_state():
    assert r.pending([msg('user',r.QUESTIONS['scope'])]) is None
    assert r.pending([msg('assistant','遅うなってごめんな。\n'+r.QUESTIONS['goal'])])=='goal'


@pytest.mark.parametrize('plan,price,url,absent',[
    ('kantei','3,980円',r.KANTEI_URL,r.SHIOMI_URL),
    ('shiomi','9,800円',r.SHIOMI_URL,r.KANTEI_URL)])
def test_offer_has_exact_product_terms_and_single_link(plan,price,url,absent):
    result=r.decide([], '',data(plan,evidence='希望'))
    assert result.kind=='offer' and price in result.text and url in result.text and absent not in result.text
    assert '2営業日以内' in result.text and '返事をもらってから' in result.text and '2回まで' in result.text
    assert '29,800' not in result.text and '9,980' not in result.text


def test_price_question_does_not_require_interview():
    assert r.decide([], 'いくら？',data(logistics=True)).key=='compare'


def test_decline_has_no_offer():
    result=r.decide([], '買いません',data('decline',evidence='買いません'))
    assert result.kind=='decline' and 'stores.jp' not in result.text


def test_classifier_only_accepts_customer_evidence():
    history=[msg('assistant','九十日の暦が必要'),msg('user','返事がない')]
    fake=lambda *a,**kw:json.dumps(data('shiomi',evidence='九十日の暦が必要'))
    assert r.route(history,'いつ動けば？',fake,'test').kind=='handoff'


@pytest.mark.parametrize('raw',['not-json','{}',json.dumps(data('shiomi')),json.dumps(data(logistics='yes'))])
def test_classifier_failure_does_not_offer(raw):
    result=r.route([], '相談です',lambda *a,**kw:raw,'test')
    assert result.kind=='handoff' and 'stores.jp' not in result.text


def test_plain_yes_cannot_pick_expensive_choice():
    history=[msg('assistant',r.QUESTIONS['scope'])]
    fake=lambda *a,**kw:json.dumps(data('shiomi',evidence='はい'))
    result=r.route(history,'はい',fake,'test')
    assert result.key=='compare'


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.setattr(bot, "_quality_turn", lambda *a: {"kind":"normal", "ready":True})
    monkeypatch.setattr(bot.quality, "record", lambda *a, **k: None)
    history=[msg('user','相談です')]
    state={'bot':'on'}
    calls=[]
    monkeypatch.setattr(bot,'_is_minor',lambda u:False)
    monkeypatch.setattr(bot,'_member_status',lambda u:'free')
    monkeypatch.setattr(bot,'_offer_already_sent',lambda uid:any(bot._is_offer_text(h['text']) for h in history if h['role']=='assistant'))
    monkeypatch.setattr(bot.store,'get_line_user',lambda uid:dict(state))
    monkeypatch.setattr(bot.store,'recent_line_chats',lambda *a,**k:list(history))
    monkeypatch.setattr(bot.store,'upsert_line_user',lambda uid,**kw:state.update(kw))
    monkeypatch.setattr(bot,'_send_offer_after',lambda *a:calls.append('after'))
    def send(text):
        history.append(msg('assistant',text));return True
    return history,state,calls,send


def test_question_reply_then_offer_and_hold(monkeypatch,flow):
    history,state,calls,send=flow
    decision=r.Decision('question','scope',r.QUESTIONS['scope'])
    monkeypatch.setattr(r,'route',lambda *a:decision)
    bot._route_offer('test',{},history,'相談です',send)
    assert state['bot']=='on' and not calls and r.pending(history)=='scope'
    history.append(msg('user','九十日の暦が欲しい'))
    decision=r.Decision('offer','shiomi',r.OFFERS['shiomi'])
    bot._route_offer('test',{},history,'九十日の暦が欲しい',send)
    assert state['bot']=='hold' and calls==['after']
    count=len(history)
    bot._route_offer('test',{},history,'九十日の暦が欲しい',send)
    assert len(history)==count


def test_failed_question_or_offer_send_keeps_on(monkeypatch,flow):
    history,state,calls,send=flow
    monkeypatch.setattr(r,'route',lambda *a:r.Decision('offer','kantei',r.OFFERS['kantei']))
    bot._route_offer('test',{},history,'相談です',lambda t:False)
    assert state['bot']=='on' and not calls


@pytest.mark.parametrize('change',['owner_hold','new_message'])
def test_no_stale_send_after_classification(monkeypatch,flow,change):
    history,state,calls,send=flow
    def classify(*a):
        if change=='owner_hold':state['bot']='hold'
        else:history.append(msg('user','やっぱり買わない'))
        return r.Decision('offer','shiomi',r.OFFERS['shiomi'])
    monkeypatch.setattr(r,'route',classify)
    bot._route_offer('test',{},history,'相談です',send)
    assert all(h['role']=='user' for h in history) and not calls


@pytest.mark.parametrize('member,minor',[('active',False),('unknown',False),('free',True)])
def test_members_and_minors_do_not_enter_sales(monkeypatch,flow,member,minor):
    history,state,calls,send=flow
    monkeypatch.setattr(bot,'_member_status',lambda u:member)
    monkeypatch.setattr(bot,'_is_minor',lambda u:minor)
    monkeypatch.setattr(r,'route',lambda *a:pytest.fail('must not classify'))
    bot._route_offer('test',{},history,'相談です',send)
    assert state['bot']=='hold' and len(history)==1


def test_pending_answer_bypasses_purchase_signal_and_free_diagnosis(monkeypatch,flow):
    history,state,calls,send=flow
    history[:]=[msg('assistant',r.QUESTIONS['scope']),msg('user','後者です')]
    monkeypatch.setattr(bot,'_handle_code',lambda *a:False)
    monkeypatch.setattr(bot,'_route_offer',lambda *a:calls.append('route'))
    monkeypatch.setattr(bot,'_try_free_diagnosis',lambda *a:pytest.fail('must not restart diagnosis'))
    monkeypatch.setattr(bot,'generate_nurture',lambda *a:pytest.fail('must consume scope answer'))
    bot._auto_reply('test',{},'後者です',live=False)
    assert calls==['route']


def test_duplicate_delivery_does_not_ask_again(monkeypatch,flow):
    history,state,calls,send=flow
    count=[]
    def classify(*a):
        count.append(1)
        return r.Decision('question','goal',r.QUESTIONS['goal'])
    monkeypatch.setattr(r,'route',classify)
    bot._route_offer('test',{},history,'相談です',send)
    bot._route_offer('test',{},history,'相談です',send)
    assert len(count)==1 and len(history)==2


def test_new_note_is_in_followup_and_questions_survive_bubble_split():
    assert 'https://note.com/tsubaki_honne/n/ne55eb9fcc57c' in bot.OFFER_AFTER
    assert 'nbc7be8398a19' not in bot.OFFER_AFTER
    for key,q in r.QUESTIONS.items():
        assert r.question_key('\n'.join(bot._split_bubbles(q)))==key


def test_json_fence_is_supported_without_accepting_surrounding_prose():
    raw='```json\n'+json.dumps(data('kantei',evidence='個別鑑定希望'))+'\n```'
    assert r.route([], '個別鑑定希望',lambda *a,**k:raw,'test').key=='kantei'
    assert r.route([], '個別鑑定希望',lambda *a,**k:'answer:'+raw,'test').kind=='handoff'


def test_general_price_question_cannot_become_a_recommendation():
    assert r.decide([], 'いくら', data('kantei',evidence='いくら',logistics=True)).key=='compare'


@pytest.mark.parametrize('path',['purchase','limit','promise'])
def test_all_offer_entry_paths_use_router(monkeypatch,flow,path):
    history,state,calls,send=flow
    history[:]=[msg('assistant','相談への返信') for _ in range(8)]+[msg('user','お願いします')]
    monkeypatch.setattr(bot,'_handle_code',lambda *a:False)
    monkeypatch.setattr(bot,'_diag_count',lambda *a:1)
    monkeypatch.setattr(bot,'_effective_limit',lambda *a:7 if path=='limit' else 99)
    monkeypatch.setattr(bot,'detect_signal',lambda *a:'purchase' if path=='purchase' else None)
    monkeypatch.setattr(bot,'_asked_in_own_words',lambda *a:True)
    monkeypatch.setattr(bot,'_asked_deeper',lambda *a:True)
    monkeypatch.setattr(bot,'_ask_deeper_count',lambda *a:bot.ASK_DEEPER_MAX)
    monkeypatch.setattr(bot,'_money_trouble',lambda *a:False)
    monkeypatch.setattr(bot,'_route_offer',lambda *a:calls.append('route'))
    monkeypatch.setattr(bot,'generate_nurture',lambda *a:'あとで案内する')
    monkeypatch.setattr(bot,'_PROMISE_LATER_RE',__import__('re').compile('あとで案内'))
    bot._auto_reply('test',{},'お願いします',live=False)
    assert calls==['route']


@pytest.mark.parametrize('answer,key',[('後者です','shiomi'),('2つ目でお願いします','shiomi'),
                                      ('前者がいいです','kantei'),('①','kantei'),('はい','compare')])
def test_positional_scope_answer_is_deterministic(answer,key):
    def no_model(*a,**k):pytest.fail('positional answer does not need classifier')
    assert r.route([msg('assistant',r.QUESTIONS['scope'])],answer,no_model,'test').key==key


def test_direct_price_question_is_answered_without_model():
    def failed_model(*a, **k):
        pytest.fail("direct price question must not depend on classifier")
    decision = r.route([], "鑑定料いくらですか？", failed_model, "test")
    assert decision.kind == "offer" and decision.key == "compare"
    assert "3,980円" in decision.text and "9,800円" in decision.text


def test_failed_classification_is_silent_and_creates_private_task(monkeypatch, flow):
    history,state,calls,send = flow
    tasks=[]
    monkeypatch.setattr(r,"route",lambda *a:r.Decision("handoff","error",r.HANDOFF))
    monkeypatch.setattr(bot.store,"append_ops_event",lambda e:tasks.append(e))
    bot._route_offer("test",{},history,"相談です",send)
    assert state["bot"] == "hold" and len(history)==1 and not calls
    assert len(tasks)==1 and tasks[0]["kind"]=="task.set"


@pytest.mark.parametrize("text", ["希望に合う内容を確認するため、ここからは店主が対応します。", "店主の確認に回すな。"])
def test_internal_handoff_copy_cannot_reach_transport(text):
    assert bot._plain_text(text)==""

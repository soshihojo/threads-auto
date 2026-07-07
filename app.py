"""Threads運用ダッシュボード（Streamlit）。

機能:
- ポスト候補を生成 → 編集 → 投稿日時を指定して予約（or 今すぐ投稿）
- 無料診断（椿姉の鑑定文を生成）

予約の自動投稿は別途 `python -m src.main run-due` を GitHub Actions が定期実行して行う。
（このダッシュボードを閉じていても予約時刻に投稿される）

起動: streamlit run app.py
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import streamlit as st

# Streamlit Cloud の Secrets を環境変数へ橋渡し（config.env() が一律で読めるように）
try:
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass

from src import content, diagnosis, store
from src.config import active_profile, env
from src.schedule import JST, now_jst

st.set_page_config(page_title="Threads運用ダッシュボード", page_icon="🧵", layout="wide")


# ---------------- 簡易パスワード認証 ----------------
def _auth_token(pw: str) -> str:
    """URL保持用のログイントークン（パスワード本体はURLに出さない）。"""
    import hashlib
    return hashlib.sha256(f"threads-auto:{pw}".encode()).hexdigest()[:20]


def _auth() -> bool:
    pw = env("APP_PASSWORD")
    if not pw:
        return True  # 未設定なら認証なし（ローカル用）
    if st.session_state.get("authed"):
        return True
    # URLの ?k=トークン で自動ログイン（リロード・ブックマークでも再入力不要）
    if st.query_params.get("k") == _auth_token(pw):
        st.session_state["authed"] = True
        return True
    st.title("🔒 ログイン")
    entered = st.text_input("パスワード", type="password")
    if st.button("入る"):
        if entered == pw:
            st.session_state["authed"] = True
            st.query_params["k"] = _auth_token(pw)  # URLに保持。このURLをブックマークすれば次回から入力不要
            st.rerun()
        else:
            st.error("パスワードが違います")
    return False


if not _auth():
    st.stop()

try:
    store.init_db()
except Exception as e:
    # データ接続が一時的に不安定でも、ページ全体を落とさない（中身が"消える"のを防ぐ）
    st.warning(f"データ接続が一時的に不安定です（{e}）。少し待ってページを再読み込みしてください。")
profile = active_profile()

st.title("🧵 Threads運用ダッシュボード")
st.caption(f"プロファイル: {profile['name']}　|　返信モード: 下書き承認制　|　現在(JST): {now_jst():%Y-%m-%d %H:%M}")

tab_gen, tab_diag, tab_consult, tab_members = st.tabs(
    ["✍️ 承認待ちの候補", "🔮 無料診断", "💬 会員相談", "👥 会員"])


def _post_now(text: str, region: str | None) -> None:
    from src.main import make_client
    client = make_client()
    loc_id = client.first_location_id(region) if (profile.get("tag_location") and region) else None
    mid = client.publish_text(text, location_id=loc_id)
    store.save_post(mid, text, profile["name"])


# ---------------- 承認待ちの候補 ----------------
with tab_gen:
    st.caption("6時間ごとに候補が自動生成され、ここに溜まります。承認したものだけが投稿されます。")
    n = st.number_input("今すぐ追加生成する数", min_value=1, max_value=8, value=3)
    if st.button("➕ 今すぐ候補を生成", type="primary"):
        try:
            try:
                base = len(store.list_scheduled(limit=100000))
            except Exception:
                base = 0
            with st.spinner("生成中…"):
                for i in range(int(n)):
                    force = ((base + i) % 3 == 0)  # 3本に1本は必ずDM/無料鑑定へ誘導
                    text, region = content.generate_candidate(force_cta=force)
                    store.add_candidate(text, region)
            st.rerun()
        except Exception as e:
            st.error(f"生成に失敗しました（{e}）。少し待って再度お試しください。")

    try:
        pending = store.list_scheduled(status="pending_review")
    except Exception as e:
        st.warning(f"候補の読み込みが一時的に失敗しました（{e}）。下のボタンで再読み込みしてください。")
        if st.button("🔄 再読み込み", key="reload_pending"):
            st.rerun()
        pending = []
    if not pending:
        st.info("承認待ちの候補はありません。自動生成を待つか、上のボタンで追加生成してください。")
    else:
        st.write(f"承認待ち {len(pending)} 件")
    for cand in pending:
        with st.container(border=True):
            st.markdown(f"**候補 #{cand['id']}**　地域: `{cand['region'] or 'なし'}`")
            text = st.text_area("本文（編集可）", value=cand["text"], key=f"text_{cand['id']}", height=150)
            c1, c2, c3, c4 = st.columns([1.2, 1, 1.1, 1])
            d = c1.date_input("投稿日", value=now_jst().date(), key=f"date_{cand['id']}")
            t = c2.time_input("時刻", value=(now_jst() + timedelta(hours=1)).time().replace(minute=0, second=0, microsecond=0),
                              step=3600, key=f"time_{cand['id']}")
            if c3.button("✅ 承認して予約", key=f"approve_{cand['id']}", use_container_width=True):
                sched_iso = datetime.combine(d, t).strftime("%Y-%m-%dT%H:%M:%S")
                store.approve_candidate(cand["id"], sched_iso, text=text)
                st.success(f"予約しました（{sched_iso}）")
                st.rerun()
            if c4.button("🗑 却下", key=f"reject_{cand['id']}", use_container_width=True):
                store.cancel_scheduled(cand["id"])
                st.rerun()
            if st.button("🚀 今すぐ投稿", key=f"now_{cand['id']}"):
                try:
                    _post_now(text, cand["region"])
                    store.mark_scheduled(cand["id"], "posted")
                    st.success("投稿しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"投稿失敗: {e}")


# ---------------- 無料診断 ----------------
def _jp_birthday(label: str, key: str, default_year: int):
    """年/月/日の日本語セレクトで生年月日を選ばせ 'YYYY-MM-DD' を返す。無効な日付ならNone。"""
    st.markdown(f"**{label}**")
    years = list(range(date.today().year, 1954, -1))
    cy, cm, cd = st.columns(3)
    y = cy.selectbox("年", years, index=years.index(default_year) if default_year in years else 0,
                     key=f"{key}_y", format_func=lambda v: f"{v}年")
    m = cm.selectbox("月", list(range(1, 13)), key=f"{key}_m", format_func=lambda v: f"{v}月")
    d = cd.selectbox("日", list(range(1, 32)), key=f"{key}_d", format_func=lambda v: f"{v}日")
    try:
        return date(y, m, d).strftime("%Y-%m-%d")
    except ValueError:
        st.warning(f"「{label}」の{y}年{m}月{d}日は存在しません。日を選び直してください。")
        return None


with tab_diag:
    st.caption("生年月日・状況・相談内容を入れて「鑑定する」。椿姉の鑑定文（彼の本音→LINE誘導）が出ます。")
    me_birth = _jp_birthday("相談者（あなた）の生年月日", "diag_me", 1995)
    him_birth = _jp_birthday("彼の生年月日", "diag_him", 1993)
    c3, c4 = st.columns(2)
    status = c3.selectbox("状況", ["音信不通", "既読スルー", "急に冷められた", "別れ話の後",
                                   "片思いで進展なし", "復縁したい"], key="diag_status")
    period = c4.selectbox("最後の連絡からの期間", ["〜3日", "〜2週間", "1ヶ月以上"], key="diag_period")
    details = st.text_area(
        "相談内容（自由記載・任意）",
        placeholder="例：未読無視が続いてる／既読はつくけど返信がない／彼に新しい女がいそう など。書くほど鑑定が具体的になります",
        key="diag_details", height=100,
    )
    if st.button("🔮 鑑定する", type="primary", key="diag_run"):
        if not me_birth or not him_birth:
            st.error("生年月日を正しく選んでください。")
        else:
            try:
                with st.spinner("椿姉が視てます…"):
                    res = diagnosis.generate_reading(me_birth, him_birth, status, period, details)
                st.markdown(f"**あなた: {res['me_shuku']}　/　彼: {res['him_shuku']}　/　縁の距離: {res['distance']}**")
                st.text_area("鑑定文（コピーして相談者に送れます）", value=res["reading"], height=320, key="diag_out")
            except Exception as e:
                st.error(f"鑑定の生成に失敗しました（{e}）。少し待って再度お試しください。")


# ---------------- 会員相談（相談し放題の返信生成＋履歴） ----------------
with tab_consult:
    st.caption("相談し放題の会員対応。会員を選び、届いた相談を貼ると、その子専用の返信を作ります。控えも残せます。")
    try:
        _cmembers = store.list_members()
    except Exception as e:
        st.warning(f"会員リストの読み込みに失敗（{e}）")
        _cmembers = []
    _cmap = {f"{m['nickname']}（{m['me_birth']} / {m['him_birth']}）": m for m in _cmembers}
    if not _cmap:
        st.info("会員がいません。👥会員タブで登録してください。")
    else:
        cpick = st.selectbox("会員を選ぶ", list(_cmap.keys()), key="con_pick")
        cmem = _cmap[cpick]
        chist = store.list_readings(cmem["id"], limit=5)
        if chist:
            with st.expander(f"📜 この子との直近のやりとり（{len(chist)}件）"):
                for h in chist:
                    if h["worry"]:
                        st.caption(f"相談: {h['worry']}")
                    st.text(h["reading"])
        incoming = st.text_area("会員から届いた相談を貼る", key="con_msg", height=120,
                                placeholder="例：昨日ひさびさに彼から連絡きた。なんて返したらいい？")
        if st.button("💬 この子専用の返信を作る", type="primary", key="con_run"):
            if not incoming.strip():
                st.error("相談内容を貼ってください。")
            else:
                try:
                    hist_str = "\n".join(
                        f"- 相談: {h['worry']} / 返信: {str(h['reading'])[:60]}…" for h in chist[:3]
                    )
                    with st.spinner("椿姉が視てます…"):
                        res = diagnosis.generate_consult(cmem["me_birth"], cmem["him_birth"], incoming, hist_str)
                    st.session_state["con_result"] = {"reply": res["reply"], "member_id": cmem["id"], "msg": incoming}
                except Exception as e:
                    st.error(f"生成に失敗しました（{e}）。少し待って再度お試しください。")
        cr = st.session_state.get("con_result")
        if cr and str(cr.get("member_id")) == str(cmem["id"]):
            st.text_area("返信（コピーして送れます）", value=cr["reply"], height=300, key="con_out")
            if st.button("✅ この相談と返信を控えに保存", key="con_save"):
                try:
                    store.add_reading(cmem["id"], "相談", cr["msg"], cr["reply"])
                    st.success("控えに保存しました（👥会員タブの履歴にも残ります）")
                except Exception as e:
                    st.error(f"保存に失敗しました（{e}）")


# ---------------- 会員リスト ----------------
with tab_members:
    st.caption("サブスク会員を登録（二人の生年月日を保存）。月次鑑定タブで選ぶだけで生成できます。")
    with st.expander("➕ 会員を登録する", expanded=False):
        nick = st.text_input("ニックネーム（LINE名など）", key="mem_nick")
        reg_me = _jp_birthday("会員（あなた）の生年月日", "mem_me", 1995)
        reg_him = _jp_birthday("彼の生年月日", "mem_him", 1993)
        memo = st.text_input("メモ（任意・状況など）", key="mem_note")
        if st.button("登録する", type="primary", key="mem_add"):
            if not nick:
                st.error("ニックネームを入れてください。")
            elif not reg_me or not reg_him:
                st.error("生年月日を正しく選んでください。")
            else:
                try:
                    store.add_member(nick, reg_me, reg_him, memo)
                    st.success(f"「{nick}」を登録しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"登録に失敗しました（{e}）")
    try:
        members = store.list_members()
    except Exception as e:
        st.warning(f"会員リストの読み込みに失敗（{e}）")
        members = []
    if not members:
        st.info("まだ会員がいません。上の「会員を登録する」から追加してください。")
    else:
        st.write(f"会員 {len(members)} 名")
        for m in members:
            with st.container(border=True):
                top = st.columns([4, 1])
                top[0].markdown(f"**{m['nickname']}**　あなた:{m['me_birth']} ／ 彼:{m['him_birth']}")
                if top[1].button("削除", key=f"mem_del_{m['id']}"):
                    store.delete_member(m["id"])
                    st.rerun()
                if m["note"]:
                    st.caption(f"メモ: {m['note']}")
                hist = store.list_readings(m["id"])
                if hist:
                    with st.expander(f"📜 鑑定の控え（{len(hist)}件）"):
                        for h in hist:
                            st.markdown(f"**{h['month']}**　{h['created_at']}")
                            if h["worry"]:
                                st.caption(f"悩み: {h['worry']}")
                            st.text(h["reading"])

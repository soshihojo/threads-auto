"""Threads運用ダッシュボード（Streamlit）。

機能:
- ポスト候補を生成 → 編集 → 投稿日時を指定して予約（or 今すぐ投稿）
- 予約一覧の確認・取り消し
- 手挙げリードの確認

予約の自動投稿は別途 `python -m src.main run-due` を GitHub Actions が定期実行して行う。
（このダッシュボードを閉じていても予約時刻に投稿される）

起動: streamlit run app.py
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import streamlit as st

# Streamlit Cloud の Secrets を環境変数へ橋渡し（config.env() が一律で読めるように）
try:
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass

from src import content, store
from src.config import active_profile, env
from src.schedule import JST, now_jst

st.set_page_config(page_title="Threads運用ダッシュボード", page_icon="🧵", layout="wide")


# ---------------- 簡易パスワード認証 ----------------
def _auth() -> bool:
    pw = env("APP_PASSWORD")
    if not pw:
        return True  # 未設定なら認証なし（ローカル用）
    if st.session_state.get("authed"):
        return True
    st.title("🔒 ログイン")
    entered = st.text_input("パスワード", type="password")
    if st.button("入る"):
        if entered == pw:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("パスワードが違います")
    return False


if not _auth():
    st.stop()

store.init_db()
profile = active_profile()

st.title("🧵 Threads運用ダッシュボード")
st.caption(f"プロファイル: {profile['name']}　|　返信モード: 下書き承認制　|　現在(JST): {now_jst():%Y-%m-%d %H:%M}")

tab_gen, tab_sched, tab_leads = st.tabs(["✍️ 承認待ちの候補", "🗓 予約・実績", "🔥 リード"])


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
        with st.spinner("生成中…"):
            for _ in range(int(n)):
                region = content.pick_region()
                store.add_candidate(content.generate_post(region=region), region)
        st.rerun()

    pending = store.list_scheduled(status="pending_review")
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
            t = c2.time_input("時刻", value=(now_jst() + timedelta(hours=1)).time().replace(second=0, microsecond=0), key=f"time_{cand['id']}")
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


# ---------------- 予約一覧 ----------------
with tab_sched:
    rows = [r for r in store.list_scheduled() if r["status"] != "pending_review"]
    if not rows:
        st.info("予約はまだありません。")
    else:
        st.write(f"全 {len(rows)} 件")
        for r in rows:
            badge = {"scheduled": "🟡 予約中", "posted": "🟢 投稿済", "failed": "🔴 失敗", "canceled": "⚪ 取消"}.get(r["status"], r["status"])
            with st.container(border=True):
                top = st.columns([2, 2, 1, 1])
                top[0].markdown(f"**{badge}**　id={r['id']}")
                top[1].markdown(f"🕒 {r['scheduled_at']}　地域:`{r['region'] or '-'}`")
                if r["status"] == "scheduled":
                    if top[3].button("取消", key=f"cancel_{r['id']}"):
                        store.cancel_scheduled(r["id"])
                        st.rerun()
                st.text(r["text"])
                if r["error"]:
                    st.caption(f"⚠️ {r['error']}")


# ---------------- リード ----------------
with tab_leads:
    leads = store.recent_leads()
    if not leads:
        st.info("まだ手挙げリードはありません。投稿への返信から自動で拾われます。")
    else:
        st.write(f"直近 {len(leads)} 件")
        for l in leads:
            with st.container(border=True):
                st.markdown(f"**@{l['username']}**　検知:`{l['keyword']}`　{l['created_at']}")
                st.text(l["text"])

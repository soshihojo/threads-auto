"""Threads運用ダッシュボード（Streamlit）。

機能:
- ポスト候補を生成 → 編集 → 投稿日時を指定して予約（or 今すぐ投稿）
- 無料診断（椿の鑑定文を生成）

予約の自動投稿は別途 `python -m src.main run-due` を GitHub Actions が定期実行して行う。
（このダッシュボードを閉じていても予約時刻に投稿される）

起動: streamlit run app.py
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

import streamlit as st

# Streamlit Cloud の Secrets を環境変数へ橋渡し（config.env() が一律で読めるように）
try:
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass

from src import diagnosis, store
from src.config import active_profile, env
from src.schedule import now_jst

st.set_page_config(page_title="Threads運用ダッシュボード", page_icon="🧵", layout="wide")


# ---------------- 簡易パスワード認証 ----------------
# 一度ログインすると、同じデバイス・同じブラウザなら30分間パスワード不要。
# 仕組み: 有効期限を埋め込んだ署名付きチケットをCookieに置き、画面を開くたびに検証＋延長する。
# サーバ側に保存を持たないので、Render/Streamlit Cloudの再起動でもログイン状態は保たれる。
LOGIN_TTL_SEC = 30 * 60      # ログイン保持時間（30分・最後の操作から延長される）
LOGIN_COOKIE = "ta_login"


def _auth_token(pw: str) -> str:
    """旧方式（URL ?k= 保持）のログイントークン。ブックマーク運用の互換のため残す。"""
    import hashlib
    return hashlib.sha256(f"threads-auto:{pw}".encode()).hexdigest()[:20]


def _sign(payload: str, pw: str) -> str:
    import hashlib
    import hmac
    return hmac.new(f"threads-auto:{pw}".encode(), payload.encode(), hashlib.sha256).hexdigest()[:24]


def _make_ticket(pw: str) -> str:
    """「期限.署名」形式のチケット。パスワード本体は含まない。"""
    import time
    exp = str(int(time.time()) + LOGIN_TTL_SEC)
    return f"{exp}.{_sign(exp, pw)}"


def _ticket_valid(ticket: str, pw: str) -> bool:
    import hmac
    import time
    try:
        exp, sig = str(ticket).split(".", 1)
        if not hmac.compare_digest(sig, _sign(exp, pw)):
            return False           # 署名が合わない＝偽造・パスワード変更済み
        return int(exp) > int(time.time())   # 期限切れ
    except Exception:
        return False


def _cookie_ticket() -> str:
    """ブラウザから送られてきたCookieのチケットを読む（古いStreamlitでは空を返す）。"""
    try:
        return (st.context.cookies or {}).get(LOGIN_COOKIE, "") or ""
    except Exception:
        return ""


def _write_cookie(ticket: str) -> None:
    """Cookieを書き込む（＝30分の期限を貼り直す）。描画のたびに呼んで期限を延長する。"""
    if not ticket:
        return
    from streamlit.components.v1 import html as _html
    _html(
        "<script>document.cookie="
        f'"{LOGIN_COOKIE}={ticket}; path=/; max-age={LOGIN_TTL_SEC}; SameSite=Lax";'
        "</script>",
        height=0,
    )


def _auth() -> bool:
    pw = env("APP_PASSWORD")
    if not pw:
        return True  # 未設定なら認証なし（ローカル用）
    if st.session_state.get("authed"):
        return True
    # ① Cookieのチケットで自動ログイン（同じデバイスなら30分間パスワード不要）
    if _ticket_valid(_cookie_ticket(), pw):
        st.session_state["authed"] = True
        st.session_state["login_ticket"] = _make_ticket(pw)
        return True
    # ② 旧方式：URLの ?k=トークン（ブックマーク運用の互換）
    if st.query_params.get("k") == _auth_token(pw):
        st.session_state["authed"] = True
        st.session_state["login_ticket"] = _make_ticket(pw)
        return True
    st.title("🔒 ログイン")
    entered = st.text_input("パスワード", type="password")
    if st.button("入る"):
        if entered == pw:
            st.session_state["authed"] = True
            st.session_state["login_ticket"] = _make_ticket(pw)
            st.query_params["k"] = _auth_token(pw)
            st.rerun()
        else:
            st.error("パスワードが違います")
    st.caption("一度入れば、同じ端末・同じブラウザなら30分はパスワード不要です（操作するたびに延長されます）。")
    return False


if not _auth():
    st.stop()

# 認証済みの描画ごとにCookieの期限を貼り直す（最後の操作から30分間有効）
_write_cookie(st.session_state.get("login_ticket", ""))

try:
    store.init_db()
except Exception as e:
    # データ接続が一時的に不安定でも、ページ全体を落とさない（中身が"消える"のを防ぐ）
    st.warning(f"データ接続が一時的に不安定です（{e}）。少し待ってページを再読み込みしてください。")
profile = active_profile()

st.title("🧵 Threads運用ダッシュボード")
st.caption(f"プロファイル: {profile['name']}　|　返信モード: 下書き承認制　|　現在(JST): {now_jst():%Y-%m-%d %H:%M}")

# st.tabsは全タブの中身を毎回描画する（遅い・操作後に先頭タブへ戻る）ため、
# 選んだ画面だけを描画する完全切り替え式にする。選択はセッションに保持される。
VIEW_REPLIES, VIEW_DIAG, VIEW_CONSULT, VIEW_MEMBERS = VIEWS = [
    "↩️ コメント返信", "🔮 無料診断", "💬 会員相談", "👥 会員"]
view = st.radio("画面", VIEWS, horizontal=True, key="view", label_visibility="collapsed")
st.divider()


# ---------------- コメント返信の承認 ----------------
if view == VIEW_REPLIES:
    st.caption("投稿へのコメントに、椿の声で個別に返信します（DMではなく公開リプライ＝BAN安全）。"
               "手挙げコメントへの招待返信がLINE集客の主エンジンです。上から順に確認して送ってください。")
    try:
        _drafts = store.pending_drafts()
    except Exception as e:
        st.warning(f"下書きの読み込みに失敗しました（{e}）")
        _drafts = []
    _drafts = sorted(_drafts, key=lambda d: str(d["created_at"]), reverse=True)
    if not _drafts:
        st.info("承認待ちの返信はありません。コメントが付くと自動で下書きが溜まります（3時間ごとに巡回）。")
    else:
        top = st.columns([2, 1])
        top[0].write(f"承認待ち {len(_drafts)} 件（新しい順）")
        if top[1].button(f"🚀 {min(len(_drafts), 40)}件まとめて送信", type="primary", use_container_width=True):
            from src.main import make_client
            _client = make_client()
            _bar = st.progress(0.0, text="送信中…")
            _ok = _ng = 0
            _targets = _drafts[:40]
            for _i, _d in enumerate(_targets):
                # 画面で編集済みの文面があればそれを使う
                _text = st.session_state.get(f"rep_{_d['reply_id']}", _d["draft_text"])
                try:
                    _client.reply_to(_d["reply_id"], _text)
                    store.set_draft_status(_d["reply_id"], "sent", sent=True)
                    _ok += 1
                except Exception as _e:
                    _ng += 1
                    st.warning(f"@{_d['username']} への返信失敗: {_e}")
                _bar.progress((_i + 1) / len(_targets),
                              text=f"送信中… {_i + 1}/{len(_targets)}（成功{_ok}）")
                if _i + 1 < len(_targets):
                    time.sleep(8)  # 連投とみなされない間隔（凍結対策）
            _bar.empty()
            st.success(f"送信 {_ok}件 / 失敗 {_ng}件")
            time.sleep(1.5)
            st.rerun()
        for d in _drafts[:40]:
            with st.container(border=True):
                st.markdown(f"**@{d['username']}**　<span style='color:gray'>{str(d['created_at'])[:16]}</span>",
                            unsafe_allow_html=True)
                st.caption(f"コメント: {d['in_text']}")
                rtext = st.text_area("返信（編集可）", value=d["draft_text"],
                                     key=f"rep_{d['reply_id']}", height=80)
                c1, c2 = st.columns(2)
                if c1.button("↩️ この内容で返信する", key=f"rep_send_{d['reply_id']}", use_container_width=True):
                    try:
                        from src.main import make_client
                        make_client().reply_to(d["reply_id"], rtext)
                        store.set_draft_status(d["reply_id"], "sent", sent=True)
                        st.success("返信しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"返信に失敗しました（{e}）")
                if c2.button("🗑 スキップ", key=f"rep_skip_{d['reply_id']}", use_container_width=True):
                    store.set_draft_status(d["reply_id"], "skipped")
                    st.rerun()


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


def _in_range(ts: str, lo: str, hi: str) -> bool:
    """作成日時文字列が期間内か（sqlite=スペース区切り/sheets=T区切りの揺れを吸収）。"""
    t = str(ts or "").replace(" ", "T")
    return bool(t) and lo <= t <= hi


def _uniq_people(events: list[dict], name: str, lo: str, hi: str) -> int:
    """期間内のイベントを「人数」で数える（vid=訪問者の匿名IDでユニーク化。
    vidが取れなかった端末は1イベント=1人として加算）。"""
    vids, anon = set(), 0
    for r in events:
        if r.get("event") != name or not _in_range(r.get("created_at"), lo, hi):
            continue
        v = str(r.get("vid") or "").strip()
        if v:
            vids.add(v)
        else:
            anon += 1
    return len(vids) + anon


if view == VIEW_DIAG:
    # Web診断「椿の縁視」のファネル計測（固定投稿・プロフのA/Bテストの判定材料）
    with st.expander("🌐 縁視ファネル計測（訪問した人 → 診断した人 → LINE追加した人）", expanded=True):
        fc1, fc2 = st.columns(2)
        _f_from = fc1.date_input("開始", value=now_jst().date() - timedelta(days=13), key="wf_from")
        _f_to = fc2.date_input("終了", value=now_jst().date(), key="wf_to")
        _lo, _hi = f"{_f_from}T00:00:00", f"{_f_to}T23:59:59"
        try:
            _ev = store.list_web_events()
            _wd = store.list_web_diag()
            visitors_n = _uniq_people(_ev, "view", _lo, _hi)
            diagnosed_n = _uniq_people(_ev, "submit", _lo, _hi)
            follows_n = sum(1 for r in _ev if r.get("event") == "line_follow"
                            and _in_range(r.get("created_at"), _lo, _hi))
            clicks_n = _uniq_people(_ev, "line_click", _lo, _hi)
            redeems_n = sum(1 for r in _wd if str(r.get("used") or "0").lower() in ("1", "true")
                            and _in_range(r.get("used_at"), _lo, _hi))

            def _pct(a: int, b: int) -> str:
                return f"{a * 100 // b}%" if b else "—"

            m1, m2, m3 = st.columns(3)
            m1.metric("診断ページを訪問した人", visitors_n)
            m2.metric("診断を実行した人", diagnosed_n,
                      _pct(diagnosed_n, visitors_n) + " ←訪問から", delta_color="off")
            m3.metric("LINEを追加した人", follows_n,
                      _pct(follows_n, diagnosed_n) + " ←診断から", delta_color="off")
            st.caption(
                f"補助指標：LINEボタン押下 {clicks_n}人 ／ LINEで番号使用 {redeems_n}件"
                "　※人数計測は2026-07-13夜の計測開始以降。LINE追加は友だち追加の実イベント"
                "（診断以外の経路の追加も含む）。"
            )
        except Exception as e:
            st.warning(f"計測データの読み込みに失敗しました（{e}）")
    st.caption("DMやLINEで届いた文章をそのまま貼るだけ。生年月日（1つ目=相談者、2つ目=彼）・状況・期間は自動で読み取ります。")
    raw = st.text_area(
        "届いた文章を貼り付け",
        key="diag_raw", height=240,
        placeholder=(
            "例：\n"
            "・1995年4月3日\n"
            "・1993.08.21\n"
            "①復縁したい\n"
            "②1ヶ月以上\n"
            "未読無視が続いてて、彼に新しい女がいそうで不安です…"
        ),
    )
    if st.button("🔮 鑑定する", type="primary", key="diag_run"):
        p = diagnosis.parse_free_input(raw)
        if not p["me"] or not p["him"]:
            st.error("生年月日が2つ見つかりませんでした。貼り付け文に「相談者→彼」の順で2つ入っているか確認してください。")
        else:
            try:
                with st.spinner("椿が視てます…"):
                    res = diagnosis.generate_reading(
                        p["me"], p["him"],
                        p["status"] or "（相談文から読み取る）",
                        p["period"] or "（相談文から読み取る）",
                        raw,  # 貼り付け全文を相談内容として渡す（取りこぼしゼロ）
                    )
                # 結果は「どの入力から作ったか」とセットで保存（入力が変われば表示しない）
                st.session_state["diag_result"] = {"input": (raw or "").strip(), "parsed": p, "res": res}
            except Exception as e:
                st.error(f"鑑定の生成に失敗しました（{e}）。少し待って再度お試しください。")

    _dr = st.session_state.get("diag_result")
    if _dr and _dr["input"] != (raw or "").strip():
        st.session_state.pop("diag_result", None)  # 入力が書き換わったら前の結果は破棄（誤送信防止）
        _dr = None
    if _dr:
        p, res = _dr["parsed"], _dr["res"]
        st.caption(
            f"読み取り結果：あなた {p['me']} ／ 彼 {p['him']} ／ "
            f"状況: {p['status'] or '本文から判断'} ／ 期間: {p['period'] or '本文から判断'}"
        )
        st.markdown(f"**あなた: {res['me_shuku']}　/　彼: {res['him_shuku']}　/　縁の距離: {res['distance']}**")
        # keyを鑑定文の内容から作る＝古い表示が新しい結果を上書きする事故を構造的に防ぐ
        st.text_area("鑑定文（コピーして相談者に送れます）", value=res["reading"], height=320,
                     key=f"diag_out_{abs(hash(res['reading'])) % 10**8}")


# ---------------- 会員相談（相談し放題の返信生成＋LINE送信） ----------------
# 未返信を「いま届いたまとまり」で区切る間隔。これ以上あいたら別の話とみなす
_BURST_GAP_H = 6


def _backend_attr(name: str):
    """store / line_bot に新しく足した関数を、確実に掴んで返す（無ければ None）。

    ★2026-08-10：store は `from .store_sheets import *` で名前を配っとる。
    Streamlitがapp.pyだけ再読み込みして src.store を古いまま使い回すと、
    後から足した関数が store に生えてこず AttributeError になる
    （実害：list_line_users が「no attribute」で会員相談の画面が丸ごと落ちた）。
    まず store を見て、無ければバックエンドの実体を直に見に行く。
    """
    fn = getattr(store, name, None)
    if fn is not None:
        return fn
    import importlib
    mod_name = ("src.store_sheets" if (env("STORE_BACKEND") or "sqlite").lower() == "sheets"
                else "src.store_sqlite")
    try:
        return getattr(importlib.import_module(mod_name), name, None)
    except Exception:
        return None


@st.cache_data(ttl=25, show_spinner=False)
def _consult_board() -> tuple[str, list[dict]]:
    """会員ごとの「LINEの今」を、シートの読み込み3回だけでまとめて作る。

    会員とLINEの紐付けは生年月日2つの一致で自動（紐付け作業は要らん）。
    会員ごとに find_line_user_by_births / recent_line_chats を呼ぶと
    人数ぶんシートを読んで待たされるので、全件を1回ずつ読んで突き合わせる。

    戻り値の先頭は「いつ取ってきたか」。古い内容を新しい顔で見せんために画面に出す。
    """
    members = store.list_members()
    by_births = {}
    _list_users = _backend_attr("list_line_users")
    if _list_users:
        for u in _list_users():
            key = (str(u.get("me_birth") or "").strip(), str(u.get("him_birth") or "").strip())
            if all(key):
                by_births[key] = u
    else:
        # 最後の逃げ道：会員ごとに引く（人数ぶんシートを読むので遅いが、画面は動く）
        for m in members:
            u = store.find_line_user_by_births(m["me_birth"], m["him_birth"])
            if u:
                by_births[(str(m["me_birth"]).strip(), str(m["him_birth"]).strip())] = u
    chats_by_uid: dict[str, list[dict]] = {}
    for r in store.all_line_chats(days=45):
        chats_by_uid.setdefault(str(r.get("user_id") or ""), []).append(r)

    board = []
    for m in members:
        u = by_births.get((str(m["me_birth"]).strip(), str(m["him_birth"]).strip()))
        uid = str(u["user_id"]) if u else ""
        chats = chats_by_uid.get(uid, []) if uid else []
        # 末尾から続く「会員の発言」＝まだこっちが返せてないぶん
        wrows = []
        for r in reversed(chats):
            if str(r.get("role")) != "user":
                break
            wrows.append(r)
        wrows.reverse()
        # ★2026-08-10：未返信を全部つないで相談欄に入れると、何日ぶんもの独り言が
        #   一塊になって「どれに答える話や」が分からんようになった（みのりさん・3日19件）。
        #   最後の発言から遡って、_BURST_GAP_H 時間以上あいたところで切る＝
        #   「いま届いたまとまり」だけを既定にする。全部欲しい時は画面で切り替える。
        recent = wrows[-1:] if wrows else []
        for i in range(len(wrows) - 1, 0, -1):
            try:
                gap = (datetime.fromisoformat(str(wrows[i].get("created_at")))
                       - datetime.fromisoformat(str(wrows[i - 1].get("created_at")))).total_seconds()
            except Exception:
                gap = 0.0
            if gap > _BURST_GAP_H * 3600:
                break
            recent.insert(0, wrows[i - 1])
        waiting = [str(r.get("text") or "") for r in wrows]
        board.append({
            "id": str(m["id"]), "nickname": str(m["nickname"]),
            "me_birth": str(m["me_birth"]), "him_birth": str(m["him_birth"]),
            "uid": uid, "line_name": str(u.get("display_name") or "") if u else "",
            "waiting": "\n".join(waiting),
            "waiting_recent": "\n".join(str(r.get("text") or "") for r in recent),
            "n_waiting": len(wrows), "n_recent": len(recent),
            "recent_from": str(recent[0].get("created_at") or "")[:16] if recent else "",
            "last_ts": str(chats[-1].get("created_at") or "")[:16] if chats else "",
            "last_text": str(chats[-1].get("text") or "") if chats else "",
            "n_all": len(chats),
            "chats": chats[-30:],
        })
    board.sort(key=lambda b: b["last_ts"], reverse=True)
    board.sort(key=lambda b: 0 if b["waiting"] else 1)   # 未返信を上に（安定ソート）
    return f"{now_jst():%H:%M:%S}", board


if view == VIEW_CONSULT:
    st.caption("会員から届いた相談に、その子専用の返信を作って、そのままLINEに送れます。"
               "LINEアプリとの行き来もコピペも要らんので、スマホからでも完結します。")
    _hc = st.columns([3, 1])
    if _hc[1].button("🔄 最新に更新", use_container_width=True, key="con_reload"):
        _consult_board.clear()
        # 画面に残っている入力も捨てる。捨てんと、新しく届いた相談が
        # 「前に開いた時の内容」に上書きされて見えん（widgetのkeyが値を持ち続けるため）
        for _k in [k for k in list(st.session_state)
                   if str(k).startswith(("con_msg_", "con_out_", "con_confirm_"))]:
            st.session_state.pop(_k, None)
        st.session_state.pop("con_result", None)
        st.rerun()
    try:
        _fetched_at, _board = _consult_board()
    except Exception as e:
        st.warning(f"会員リストの読み込みに失敗（{e}）")
        _fetched_at, _board = "", []
    if not _board:
        st.info("会員がいません。👥会員の画面で登録してください。")
    else:
        _pending = [b for b in _board if b["waiting"]]
        _hc[0].markdown(f"### 🔴 会員の発言で止まっとる {len(_pending)}人"
                        f"　<span style='color:gray'>／ 会員 {len(_board)}人"
                        f"　（LINE取得 {_fetched_at}）</span>",
                        unsafe_allow_html=True)
        st.caption("🔴は「記録上、最後に喋ったのが会員」という意味です。LINE公式アプリから手で返した分は"
                   "こちらに記録が残らないため、返信済みでも🔴が付いたままになります。"
                   "この画面から送った分は記録されるので、使うほど正確になります。")
        _labels = [f"{'🔴' if b['waiting'] else '✅'} {b['nickname']}"
                   f"{'' if b['uid'] else '（LINE未リンク）'}　{b['last_ts']}" for b in _board]
        cpick = st.selectbox("会員を選ぶ", _labels, key="con_pick", label_visibility="collapsed")
        cb = _board[_labels.index(cpick)]
        cmem = {"id": cb["id"], "nickname": cb["nickname"],
                "me_birth": cb["me_birth"], "him_birth": cb["him_birth"]}

        # ★2026-08-10：LINEに新しい発言が来ていたら、画面に残っとる前回の入力を捨てる。
        #   Streamlitのwidgetはkeyに値を持ち続けるため、value=に最新を渡しても
        #   「前に開いた時の内容」が表示され続ける（riyoの相談が古いまま出た実害）。
        _sig = f"{cb['last_ts']}|{cb['n_all']}"
        _stale_reply = False
        if st.session_state.get(f"con_sig_{cb['id']}") != _sig:
            st.session_state[f"con_sig_{cb['id']}"] = _sig
            # 相談欄は最新で入れ直す（keyに範囲が付くので前方一致で消す）
            for _k in [k for k in list(st.session_state)
                       if str(k).startswith(f"con_msg_{cb['id']}_")]:
                st.session_state.pop(_k, None)
            st.session_state.pop(f"con_confirm_{cb['id']}", None)  # 確認中なら取り消す
            # 作りかけの返信は消さん（書いたもんを勝手に捨てん）。ただし古い前提のまま
            # 送ってまわんように、下で警告を出す
            _stale_reply = str((st.session_state.get("con_result") or {}).get("member_id")) == cb["id"]

        if cb["uid"]:
            if cb["last_ts"]:
                _who_last = "会員" if cb["waiting"] else "椿"
                st.markdown(f"🆕 **いちばん新しい発言**（{_who_last}・{cb['last_ts']}）")
                st.info(cb["last_text"][:400] or "（本文なし）")
            with st.expander(f"📱 LINEのやりとり全部（{cb['line_name'] or cb['nickname']}・"
                             f"直近{len(cb['chats'])}件／全{cb['n_all']}件・下ほど新しい）",
                             expanded=False):
                for _r in cb["chats"]:
                    _who = "🙋 会員" if str(_r.get("role")) == "user" else "🌙 椿"
                    st.markdown(f"{_who}　<span style='color:gray;font-size:0.85em'>"
                                f"{str(_r.get('created_at'))[:16]}</span>", unsafe_allow_html=True)
                    st.text(str(_r.get("text") or ""))
            if cb["waiting"] and st.button("✓ これはもうLINEアプリで返した（対応済みにする）",
                                           key=f"con_done_{cb['id']}"):
                # LINE公式アプリから手で送った返信はWebhookに流れてこず記録が残らんので、
                # 印だけ残して🔴を消す。会員の発言やないので、返信生成の材料には入らん
                store.add_line_chat(cb["uid"], "assistant", "［店主がLINEアプリから手動で返信］")
                _consult_board.clear()
                st.rerun()
        else:
            st.warning("この会員のLINEが見つかりません（line_usersに登録された生年月日2つが"
                       "会員登録と一致していない）。返信文は作れますが、送信ボタンは出ません。")

        _all_hist = store.list_readings(cmem["id"], limit=50)
        # 納品済みの個別鑑定書（👥会員でPDF登録したもの）は、履歴とは別に返信生成の参照資料にする
        _kantei_rows = [h for h in _all_hist if h["month"] == "個別鑑定書"]
        kantei_text = str(_kantei_rows[0]["reading"]) if _kantei_rows else ""
        chist = [h for h in _all_hist if h["month"] != "個別鑑定書"][:5]
        if kantei_text:
            st.caption("📎 個別鑑定書 登録済み — 返信はこの鑑定の内容（性質の読み・時期・処方箋）と矛盾しない形で生成されます")
        # 会員からLINEに届いた直近のメッセージ（画像の読み取り内容含む）も返信生成が参照する。
        # 上でまとめて読んだ会話をそのまま使う＝ここでシートを読み直さない
        _lmsgs = [h for h in cb["chats"] if str(h.get("role")) == "user"][-8:]
        _line_recent = "\n".join(f"・{str(h.get('created_at'))[:16]} {str(h.get('text') or '')[:250]}"
                                 for h in _lmsgs)
        if _line_recent:
            st.caption("📱 LINEを自動リンク済み — 会員から最近LINEに届いた内容（画像の読み取り含む）も返信生成が参照します")
        if chist:
            with st.expander(f"📜 この子との直近のやりとり（{len(chist)}件）"):
                for h in chist:
                    if h["worry"]:
                        st.caption(f"相談: {h['worry']}")
                    st.text(h["reading"])
        # 未返信ぶんは自動で入れておく（LINEを開いてコピーしてくる手間をなくす）。
        # 既定は「いま届いたまとまり」だけ。何日ぶんも溜まっとる人は全部にも切り替えられる
        _scope_i = 0
        if cb["waiting"] and cb["n_recent"] < cb["n_waiting"]:
            _opts = [f"直近のまとまりだけ（{cb['n_recent']}件・{cb['recent_from']}〜）",
                     f"未返信ぜんぶ（{cb['n_waiting']}件）"]
            _scope_i = _opts.index(st.radio(
                "どこまで相談欄に入れる？", _opts, key=f"con_scope_{cb['id']}", horizontal=True))
        _fill = cb["waiting"] if _scope_i else (cb["waiting_recent"] or cb["waiting"])
        # keyに範囲を含める＝切り替えたら中身が入れ替わる（widgetが古い値を握るのを避ける）
        incoming = st.text_area(
            "会員から届いた相談" + ("（LINEから自動で入れました。編集できます）" if cb["waiting"] else ""),
            value=_fill, key=f"con_msg_{cb['id']}_{_scope_i}", height=160,
            placeholder="例：昨日ひさびさに彼から連絡きた。なんて返したらいい？")
        if st.button("💬 この子専用の返信を作る", type="primary", key="con_run"):
            if not incoming.strip():
                st.error("相談内容を貼ってください。")
            else:
                try:
                    # 直近5件を「古い順・フル文脈」で渡す（60字要約だと過去の指示が見えず
                    # 矛盾や事実の取り違えが起きた実害があった）
                    hist_str = "\n\n".join(
                        f"◆{str(h['created_at'])[:10]} 会員の相談: {str(h['worry'])[:300]}\n"
                        f"　椿の返信: {str(h['reading'])[:800]}"
                        for h in reversed(chist[:5])
                    )
                    if _line_recent:
                        hist_str += ("\n\n◆会員から最近LINEに届いたメッセージ（新しい順ではなく時系列。"
                                     "[画像を送付]は画像の自動読み取り内容）:\n" + _line_recent)
                    with st.spinner("椿が視てます…"):
                        res = diagnosis.generate_consult(cmem["me_birth"], cmem["him_birth"], incoming, hist_str,
                                                         kantei=kantei_text)
                    # 相談と返信を自動で控えに保存（次回の返信生成が「前回までのやりとり」として参照する）。
                    # 同じ相談文で作り直した場合は前の控えを上書き＝重複させない
                    reading_id = None
                    try:
                        _dup = next((h for h in _all_hist if h["month"] == "相談"
                                     and str(h["worry"]).strip() == incoming.strip()), None)
                        if _dup:
                            reading_id = _dup["id"]
                            store.update_reading(reading_id, res["reply"])
                        else:
                            reading_id = store.add_reading(cmem["id"], "相談", incoming.strip(), res["reply"])
                        saved = True
                    except Exception as e:
                        saved = False
                        st.warning(f"控えの自動保存に失敗しました（返信自体は下に表示されています）: {e}")
                    st.session_state["con_result"] = {"reply": res["reply"], "member_id": cmem["id"],
                                                      "msg": incoming, "saved": saved,
                                                      "reading_id": reading_id}
                    st.session_state.pop(f"con_confirm_{cmem['id']}", None)  # 前回の確認状態を持ち越さない
                except Exception as e:
                    st.error(f"生成に失敗しました（{e}）。少し待って再度お試しください。")
        cr = st.session_state.get("con_result")
        if cr and str(cr.get("member_id")) == str(cmem["id"]):
            if _stale_reply:
                st.warning("この返信を作ったあとに、LINEに新しい発言が届いています。"
                           "上の「いちばん新しい発言」を見て、必要なら作り直してください。")
            edited = st.text_area("返信（このまま送れます。直したい所は直してや）",
                                  value=cr["reply"], height=300, key=f"con_out_{cmem['id']}")
            if cr.get("saved"):
                st.caption("📝 この相談と返信は自動で控えに保存されました（同じ相談文で作り直すと上書きされます）。"
                           "送らなかった返信を消したい場合は、スプレッドシートの readings シートから該当行を削除してください。")

            # ---- LINEへ直接送る（コピペとアプリの行き来をなくす。誤爆防止に2段階） ----
            if not cb["uid"]:
                st.info("この会員はLINEが未リンクなので、上の文面をコピーしてLINEアプリから送ってください。")
            elif not env("LINE_CHANNEL_ACCESS_TOKEN"):
                st.info("この画面からLINEへ送るには、Streamlitのシークレットに "
                        "LINE_CHANNEL_ACCESS_TOKEN を追加してください（未設定のため送信ボタンは出ません）。")
            else:
                _ck = f"con_confirm_{cmem['id']}"
                if not st.session_state.get(_ck):
                    if st.button(f"📤 {cb['nickname']}さんのLINEに送る", type="primary",
                                 use_container_width=True, key=f"con_send_{cmem['id']}"):
                        st.session_state[_ck] = True
                        st.rerun()
                else:
                    st.warning(f"「{cb['line_name'] or cb['nickname']}」さんのLINEに、"
                               f"上の文面（{len(edited)}字）をこのまま送ります。ええか？")
                    _s1, _s2 = st.columns(2)
                    if _s1.button("✅ 送信する", type="primary", use_container_width=True,
                                  key=f"con_go_{cmem['id']}"):
                        try:
                            from src import line_bot as _lb
                            # push_long_text も後から足した関数なので、
                            # 古いモジュールが残っていても送れるように push_text へ落とす
                            _push = getattr(_lb, "push_long_text", None) or _lb.push_text
                            if _push(cb["uid"], edited):
                                # 会話ログに残す＝次の生成もダッシュボードの表示も、送った文面を前提にできる
                                store.add_line_chat(cb["uid"], "assistant", edited)
                                # 控えは「実際に送った文面」で上書きする（編集ぶんを取りこぼさない）
                                if cr.get("reading_id"):
                                    try:
                                        store.update_reading(cr["reading_id"], edited)
                                    except Exception as e:
                                        st.warning(f"控えの更新に失敗（送信は成功しています）: {e}")
                                for _k in ([_ck, "con_result", f"con_out_{cmem['id']}"]
                                           + [k for k in list(st.session_state)
                                              if str(k).startswith(f"con_msg_{cmem['id']}_")]):
                                    st.session_state.pop(_k, None)
                                _consult_board.clear()
                                st.success(f"{cb['nickname']}さんに送信しました")
                                time.sleep(1.2)
                                st.rerun()
                            else:
                                st.error("送信に失敗しました（LINE側がエラーを返しました）。"
                                         "文面はそのまま残っているので、少し待って再度お試しください。")
                        except Exception as e:
                            st.error(f"送信に失敗しました（{e}）")
                    if _s2.button("やめる", use_container_width=True, key=f"con_no_{cmem['id']}"):
                        st.session_state[_ck] = False
                        st.rerun()


# ---------------- 会員リスト ----------------
if view == VIEW_MEMBERS:
    st.caption("サブスク会員を登録（二人の生年月日を保存）。💬会員相談の画面で選ぶだけで返信を生成できます。")
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
                _kantei = [h for h in hist if h["month"] == "個別鑑定書"]
                _normal = [h for h in hist if h["month"] != "個別鑑定書"]
                # 個別鑑定PDFの登録（会員相談の返信生成が全文を参照する）
                if _kantei:
                    st.caption(f"📎 個別鑑定書 登録済み（{len(str(_kantei[0]['reading']))}字・{_kantei[0]['created_at']}）")
                up = st.file_uploader("個別鑑定書PDFを登録（💬会員相談の返信生成が内容を参照します）",
                                      type=["pdf"], key=f"mem_pdf_{m['id']}")
                if up is not None and st.button("📎 この鑑定書を登録する", key=f"mem_pdf_btn_{m['id']}"):
                    try:
                        from pypdf import PdfReader
                        text = "\n".join((pg.extract_text() or "") for pg in PdfReader(up).pages).strip()
                        if len(text) < 200:
                            st.error("PDFから本文を読み取れませんでした（画像化されたPDFの可能性）")
                        else:
                            store.add_reading(m["id"], "個別鑑定書", "（納品済み個別鑑定PDFの全文）", text[:15000])
                            st.success(f"鑑定書を登録しました（{len(text)}字）")
                            st.rerun()
                    except Exception as e:
                        st.error(f"登録に失敗しました（{e}）")
                if _normal:
                    with st.expander(f"📜 鑑定の控え（{len(_normal)}件）"):
                        for h in _normal:
                            st.markdown(f"**{h['month']}**　{h['created_at']}")
                            if h["worry"]:
                                st.caption(f"悩み: {h['worry']}")
                            st.text(h["reading"])

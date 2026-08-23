"""椿の会員ダッシュボード（Streamlit）。

★2026-08-21：画面を会員まわりの二つだけに絞った。
　　💬 会員相談 … 会員から届いた相談に、その子専用の返信を作って、そのままLINEへ送る
　　👥 会員　　 … 会員の登録と、納品済み鑑定書の控え

　コメント返信と無料診断の画面は、運用で使わんようになったんで外した。
　★消えたんは「人が見る窓」だけや。裏の仕組みは今までどおり動いとる：
　　・コメントの巡回と下書き作り → poll.yml（GitHub Actions）
　　・無料診断そのもの　　　　　 → line_app.py（Webのフォームから直に届く）
　　・予約投稿　　　　　　　　　 → scheduler.yml が run-due を叩く

起動: streamlit run app.py
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime

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
# ★2026-08-21：「↩️ コメント返信」と「🔮 無料診断」の画面をやめた。
#   どっちも運用で使わんようになったんで、置いといても画面が重いだけや。
#   ★消したんは画面だけ。コメント巡回も無料診断そのものも、裏では今までどおり動いとる
#     （poll.yml と line_app.py の側）。ここで消えたんは「人が見る窓」だけやからな。
VIEW_CONSULT, VIEW_MEMBERS = VIEWS = ["💬 会員相談", "👥 会員"]
view = st.radio("画面", VIEWS, horizontal=True, key="view", label_visibility="collapsed")
st.divider()


# ---------------- 会員相談（相談し放題の返信生成＋LINE送信） ----------------
# 未返信を「いま届いたまとまり」で区切る間隔。これ以上あいたら別の話とみなす
_BURST_GAP_H = 6
# ★2026-08-23：時間で切れん連投のための上限。件数と字数の両方で止める
_BURST_MAX_N = 12
_BURST_MAX_CHARS = 2500


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


# ★★★2026-08-23：PDFから抜いた本文には「康熙部首」いう別の字が混ざる。
#   見た目は同じでも中身が違う字や：本⾳（正しくは本音）、⼆⼈（二人）、⽉（月）。
#   ★実測で、会員8人の鑑定書に5,875個も入っとった（まいかさん928個、絵麻さん838個）。
#   ★★この鑑定書が、毎回の返信の土台になる。日付も名前も、崩れた字で椿に渡っとった。
#   ★★★登録する時に直す。ここで直さんかったら、また同じ字が入ってくる。
_RADICAL_EXTRA = {"⻑": "長", "⻤": "鬼", "⻭": "歯"}   # NFKCで戻らん字は手で持つ


def _norm_doc(text: str) -> str:
    """突き合わせ用に、空白と表記ゆれを落とす（PDF由来の崩れも吸う）。"""
    import unicodedata
    return re.sub(r"\s", "", unicodedata.normalize("NFKC", _unmangle(text)))


def _unmangle(text: str) -> str:
    import unicodedata
    out = []
    for c in text:
        o = ord(c)
        if 0x2E80 <= o <= 0x2FDF or o == 0xFE30:
            if c in _RADICAL_EXTRA:
                out.append(_RADICAL_EXTRA[c])
                continue
            n = unicodedata.normalize("NFKC", c)
            out.append(n if len(n) == 1 else c)
        else:
            out.append(c)
    return "".join(out)


@st.cache_data(ttl=25, show_spinner=False)
def _consult_board() -> tuple[str, list[dict], dict]:
    """会員ごとの「LINEの今」を、シートの読み込み3回だけでまとめて作る。

    会員とLINEの紐付けは生年月日2つの一致で自動（紐付け作業は要らん）。
    会員ごとに find_line_user_by_births / recent_line_chats を呼ぶと
    人数ぶんシートを読んで待たされるので、全件を1回ずつ読んで突き合わせる。

    戻り値の先頭は「いつ取ってきたか」。古い内容を新しい顔で見せんために画面に出す。
    """
    members = store.list_members()
    by_births = {}
    # ★★★2026-08-23：ここは【他人の会話が混ざる】いちばん危ない所や。
    #   前は by_births[key] = u と後勝ちで入れとった。
    #   ★もし二人の相談者の生年月日が二つとも同じやったら、後から読んだ方に上書きされて、
    #     会員は【別人のLINE会話】で返信を作られる。相手の彼の名前も、揉めた話も、全部混ざる。
    #   ★★今はまだ0組やけど、人が増えたら必ずぶつかる（誕生日は365通りしかない）。
    #     ほんで、ぶつかっても画面には何も出んから、こっちは気づけん。
    #   ★★★せやから、ぶつかったら【紐付けん】。黙って間違うより、繋がらん方が百倍ましや。
    _clash = {}
    _list_users = _backend_attr("list_line_users")
    if _list_users:
        _cand = {}
        for u in _list_users():
            key = (str(u.get("me_birth") or "").strip(), str(u.get("him_birth") or "").strip())
            if all(key):
                _cand.setdefault(key, []).append(u)
        for key, us in _cand.items():
            if len(us) == 1:
                by_births[key] = us[0]
            else:
                _clash[key] = [str(x.get("display_name") or x.get("user_id")) for x in us]
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
        # ★★★2026-08-23：時間だけやと切れん人がおる。
        #   絵麻さんは、六時間あけずに三十八件、四千七百字を投げてくる。
        #   ★六時間ルールを通しても、まるごと一塊のまま相談欄に入っとった。
        #   ★★その塊を一つの相談として渡すと、椿はどれに答えたらええか分からんようになる。
        #     ぜんぶに触ろうとして、話があちこち飛ぶ返信になる。それが「不自然」の正体や。
        #   ★★★せやから、時間に加えて【件数】と【字数】でも切る。
        #     直近の十二件、または二千五百字まで。それより前は、画面で「未返信ぜんぶ」に
        #     切り替えたら読める（捨てるんやのうて、既定を軽うするだけや）。
        recent = wrows[-1:] if wrows else []
        _ch = len(str(wrows[-1].get("text") or "")) if wrows else 0
        for i in range(len(wrows) - 1, 0, -1):
            try:
                gap = (datetime.fromisoformat(str(wrows[i].get("created_at")))
                       - datetime.fromisoformat(str(wrows[i - 1].get("created_at")))).total_seconds()
            except Exception:
                gap = 0.0
            if gap > _BURST_GAP_H * 3600:
                break
            if len(recent) >= _BURST_MAX_N:
                break
            _n = len(str(wrows[i - 1].get("text") or ""))
            if _ch + _n > _BURST_MAX_CHARS:
                break
            _ch += _n
            recent.insert(0, wrows[i - 1])
        waiting = [str(r.get("text") or "") for r in wrows]
        board.append({
            "id": str(m["id"]), "nickname": str(m["nickname"]),
            # ★2026-08-23：会員メモも持ってくる。呼び名の指定がここに入っとる
            #   （例：「必ず『なつみさん』。呼び捨て禁止」）。生成に渡さんかったら、
            #   ★実際に「なんで呼び捨てですか」と言われた事故が、また起きる。
            "note": str(m.get("note") or ""),
            "me_birth": str(m["me_birth"]), "him_birth": str(m["him_birth"]),
            "uid": uid, "line_name": str(u.get("display_name") or "") if u else "",
            "waiting": "\n".join(waiting),
            "waiting_recent": "\n".join(str(r.get("text") or "") for r in recent),
            # ★★★2026-08-23：日時つきの版も持っとく。
            #   相談欄は本文だけを入れとった＝椿には【いつ届いたか】が分からん。
            #   実測：未返信の塊874件のうち75件が丸一日以上またいどる。
            #   最長はMadokaさんの25件で19.7日ぶん。★これが一かたまりの「今の相談」に見える。
            #   ★★ほんで、十九日前の話を「さっき言うてたやん」と今の話みたいに扱う。
            #     逆に、いちばん新しい一行（＝ほんまに答えてほしい話）が埋もれる。
            #   ★★★せやから、何時間もまたいどる時だけ、頭に日時を足す。
            #     数分のうちの連投は、足しても読みにくうなるだけやから足さん。
            "waiting_ts": [(str(r.get("created_at") or ""), str(r.get("text") or "")) for r in wrows],
            "waiting_recent_ts": [(str(r.get("created_at") or ""), str(r.get("text") or "")) for r in recent],
            "n_waiting": len(wrows), "n_recent": len(recent),
            "recent_from": str(recent[0].get("created_at") or "")[:16] if recent else "",
            "last_ts": str(chats[-1].get("created_at") or "")[:16] if chats else "",
            "last_text": str(chats[-1].get("text") or "") if chats else "",
            "n_all": len(chats),
            "chats": chats[-30:],
        })
    board.sort(key=lambda b: b["last_ts"], reverse=True)
    board.sort(key=lambda b: 0 if b["waiting"] else 1)   # 未返信を上に（安定ソート）
    return f"{now_jst():%H:%M:%S}", board, _clash


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
        _fetched_at, _board, _clash_keys = _consult_board()
    except Exception as e:
        st.warning(f"会員リストの読み込みに失敗（{e}）")
        _fetched_at, _board, _clash_keys = "", [], {}
    if _clash_keys:
        # 紐付けを止めた組は、必ず目に見せる（黙って繋がらんのは、黙って間違うのの次に悪い）
        st.error("⚠️ 生年月日が二つとも同じ人が複数おるため、LINEの紐付けを止めました。"
                 "この人たちは返信生成にLINEの会話が渡りません。会員のニックネームか"
                 "生年月日を見直してください：\n"
                 + "\n".join(f"・{a}／{b} → {'、'.join(v)}" for (a, b), v in _clash_keys.items()))
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
        cmem = {"id": cb["id"], "nickname": cb["nickname"], "note": cb.get("note", ""),
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
            if cb["waiting"]:
                # ★★★2026-08-23：前は印だけ残しとった。
                #   せやけど、それやと【何を返したか】が記録に残らん。
                #   ★次に返信を作る時、椿は自分が何を言うたか分からんまま書くことになる。
                #     ほんで、前と逆のことを言うたり、同じ話をもう一回したりする。
                #   ★★実際、七件も「中身なし」が入っとった（田中麻衣さん2件ほか）。
                #   ★★★せやから、手で返した本文を貼れるようにした。
                #     貼らんでも印は押せる（急いどる時に手間で止まったら本末転倒やからな）。
                with st.expander("✓ これはもうLINEアプリで返した（対応済みにする）"):
                    _manual = st.text_area(
                        "送った文面を貼っといて（次の返信がここを踏まえる）",
                        key=f"con_manual_{cb['id']}", height=120,
                        placeholder="LINEアプリからコピーして貼るだけでええ。空でも印は押せるが、"
                                    "貼っといた方が次の返信がずれん")
                    if st.button("対応済みにする", key=f"con_done_{cb['id']}"):
                        _txt = (_manual or "").strip() or "［店主がLINEアプリから手動で返信（本文は未登録）］"
                        store.add_line_chat(cb["uid"], "assistant", _txt)
                        st.session_state.pop(f"con_manual_{cb['id']}", None)
                        _consult_board.clear()
                        st.rerun()
        else:
            st.warning("この会員のLINEが見つかりません（line_usersに登録された生年月日2つが"
                       "会員登録と一致していない）。返信文は作れますが、送信ボタンは出ません。")

        _all_hist = store.list_readings(cmem["id"], limit=50)
        # 納品済みの個別鑑定書（👥会員でPDF登録したもの）は、履歴とは別に返信生成の参照資料にする
        _kantei_rows = [h for h in _all_hist if h["month"] == "個別鑑定書"]
        kantei_text = str(_kantei_rows[0]["reading"]) if _kantei_rows else ""
        # ★★★2026-08-23：5件 → 12件に増やした。
        #   5件やと、よう相談してくれる人ほど記憶が短うなる。実測したらこうやった：
        #     田中麻衣さん … 控え113件。5件で遡れるんは【19時間】ぶんだけ
        #     絵麻さん　　 … 控え 33件。5件で【21時間】ぶん
        #     ゆきえさん　 … 控え 11件。5件で【8時間】ぶん
        #   ★一日に何回も来る人ほど、前の日に話したことが見えん。
        #     ほんで「前に言うたやろ」が通じん返信になる。矛盾や記憶違いの正体はここや。
        #   ★★12件にすると、上の三人でだいたい二〜四日ぶんを見渡せる。
        # ★★★2026-08-23：「相談」以外の資料は、古うなっても落とさん。
        #   readings には、相談の控えのほかに、こういう資料が入っとる：
        #     ・ヒアリング回答（本人が書いた事実の原典）
        #     ・鑑定書の感想と、判明した事実（★見立てが外れた回の訂正が入っとる）
        #     ・本人の最新の意向（★ここが最新、と書いてあるやつ）
        #     ・九十日の暦
        #   ★これらを「直近12件」の枠で数えたら、相談が溜まった瞬間に押し出されて消える。
        #   ★★消えたら何が起きるか。訂正した事実がまた元に戻る。暦に書いた日付を忘れる。
        #     ——【一度直したはずの間違いが、また出てくる】。これがいちばんタチが悪い。
        #   ★★★せやから、相談以外は別枠で全部渡す。数が少ないんで、量は膨らまん。
        _refs = [h for h in _all_hist
                 if h["month"] not in ("個別鑑定書", "相談")]
        chist = [h for h in _all_hist if h["month"] == "相談"][:12]
        if kantei_text:
            st.caption("📎 個別鑑定書 登録済み — 返信はこの鑑定の内容（性質の読み・時期・処方箋）と矛盾しない形で生成されます")
        else:
            # ★2026-08-23：田中麻衣さんは相談50件・やりとり382件の常連やのに、
            #   鑑定書が登録されとらんかった（PDFは手元にあった）。
            #   ★土台なしで返信を作っとった＝彼の性質も、動く時期も、処方も見えん。
            #   黙って足りんかったから、誰も気づかん。せやから、ここで言う。
            st.warning("📎 この会員の個別鑑定書が登録されていません。"
                       "返信は鑑定の見立て（彼の性質・時期・処方箋）を踏まえずに作られます。"
                       "👥会員の画面からPDFを登録してください。")
        # 会員からLINEに届いた直近のメッセージ（画像の読み取り内容含む）も返信生成が参照する。
        # 上でまとめて読んだ会話をそのまま使う＝ここでシートを読み直さない
        _lmsgs = [h for h in cb["chats"] if str(h.get("role")) == "user"][-8:]
        # ★2026-08-11：ここを250字で切っとったせいで、画像の書き起こしが尻切れになっとった
        #   （MAKIKOさんのスクショ4枚は376〜477字。毎回126〜227字が捨てられとった）。
        #   会員が送ってくるのは彼とのトーク画面で、いちばん読ませたい中身がそこにある。
        _shot = sum(1 for h in _lmsgs if "読み取り内容" in str(h.get("text") or ""))
        _line_recent = "\n".join(f"・{str(h.get('created_at'))[:16]} {str(h.get('text') or '')[:1500]}"
                                 for h in _lmsgs)
        if _line_recent:
            st.caption("📱 LINEを自動リンク済み — 会員から最近LINEに届いた内容も返信生成が参照します"
                       + (f"（うち画像の読み取り{_shot}件を全文で渡しています）" if _shot else ""))
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
        _rows_ts = cb.get("waiting_ts") if _scope_i else (cb.get("waiting_recent_ts") or cb.get("waiting_ts"))
        _fill = cb["waiting"] if _scope_i else (cb["waiting_recent"] or cb["waiting"])
        _span_h = 0.0
        if _rows_ts and len(_rows_ts) >= 2:
            try:
                _span_h = (datetime.fromisoformat(_rows_ts[-1][0])
                           - datetime.fromisoformat(_rows_ts[0][0])).total_seconds() / 3600
            except Exception:
                _span_h = 0.0
        if _span_h >= 3:
            # ［08/21 09:59］のかたちで頭に足す。本文はそのまま残る＝後の突き合わせも効く
            _fill = "\n".join(f"［{t[5:16].replace('T', ' ')}］{x}" for t, x in _rows_ts)
            st.caption(f"🕒 この相談は {_span_h/24:.1f}日ぶん（{len(_rows_ts)}件）にまたがるので、"
                       "各行の頭に届いた日時を入れました（椿が古い話を『今の話』と取り違えんように）")
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
                    # ★2026-08-23：切り詰めを緩めた。実測で、こんだけ捨てとった：
                    #   ・会員の相談文 300字 → 26%が尻切れ（中央値151字やが、長い回ほど大事な話が入る）
                    #   ・椿の返信 800字 → 10%が尻切れ。★前に自分が出した指示が、途中で消える
                    #   ★★過去の指示が見えんまま次を書くから、前と逆のことを言う。
                    #     日付も入れとく（★「いつ言うたか」が分からんと、順番を取り違える）
                    # ★2026-08-23：いま答えよとする相談そのものは、履歴から外す。
                    #   作り直した時、同じ相談文の控えが残っとる＝【前回のやりとり】に
                    #   自分の前の返信が並ぶ。★椿は「この話はもう答えた」と思て、
                    #   「さっき言うたとおりや」と、初回みたいに答えてくれん。
                    _past = [h for h in chist[:13]
                             if str(h.get('worry') or '').strip() != incoming.strip()][:12]
                    hist_str = "\n\n".join(
                        f"◆{str(h['created_at'])[:16]} 会員の相談: {str(h['worry'])[:800]}\n"
                        f"　椿の返信: {str(h['reading'])[:1600]}"
                        for h in reversed(_past)
                    )
                    # ★★★2026-08-23：ここで【いま答えよとする相談】を履歴から外す。
                    #   相談欄は未返信ぶんを自動で入れとる＝LINE直近と中身が丸かぶりや。
                    #   実測：ema shimotsumaさん8/8件、riyoさん5/5件、なつみさん4/4件が重複しとった。
                    #   ★重なったまま渡すと、椿には【今の相談】と【前にも来とった同じ話】の
                    #     二回に見える。ほんで「さっきも言うてたな」「その話は前に聞いた」と、
                    #     ★★初めて聞いた話を二度目みたいに扱う。これが不自然さの正体のひとつ。
                    #   ★★★せやから、相談欄に入っとる文はここから落とす。残りは「それより前の話」。
                    _cur = incoming.strip()
                    _before = [h for h in _lmsgs
                               if str(h.get('text') or '').strip() not in _cur]
                    _line_before = "\n".join(
                        f"・{str(h.get('created_at'))[:16]} {str(h.get('text') or '')[:1500]}"
                        for h in _before)
                    if _line_before:
                        hist_str += ("\n\n◆それより前に会員からLINEに届いとったメッセージ（時系列。"
                                     "[画像を送付]は画像の自動読み取り内容）。"
                                     "★これは【今の相談】より前の話や。今の相談と混同せんこと:\n"
                                     + _line_before)
                    # ★相談以外の資料（ヒアリング原文・訂正・最新の意向・暦）は、
                    #   古うても必ず渡す。ここが落ちると、直したはずの間違いがまた出る
                    if _refs:
                        hist_str += "\n\n◆この会員についての、消したらあかん資料（古うても必ず踏まえる）:\n" + \
                            "\n\n".join(f"【{str(h['month'])}】\n{str(h['reading'])[:4000]}" for h in _refs)
                    # ★★★呼び名の指定は、いちばん上に置く。ここを外すと事故る
                    if _span_h >= 3:
                        hist_str += ("\n\n◆★今の相談の各行の頭の［08/21 09:59］は、"
                                     "その一行が届いた日時や。"
                                     f"この相談は{_span_h/24:.1f}日ぶんが溜まったもんで、"
                                     "★下の行ほど新しい。"
                                     "古い行を『今さっきの話』として扱わんこと。"
                                     "★★いちばん答えてほしいんは、たいてい【いちばん下の行】や。")
                    _who = (cb.get("line_name") or cb["nickname"]).strip()
                    _memo = str(cmem.get("note") or "").strip()
                    hist_str = (
                        f"◆この会員の呼び名：{_who}"
                        + (f"\n◆この会員についての決まりごと：{_memo}" if _memo else "")
                        + "\n★呼び名は、ここに書いてあるとおりに書くこと。"
                          "『さん』を勝手に足したり外したりせん。\n\n"
                        + hist_str
                    )
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
                                # ★★★2026-08-23：ここで add_line_chat を呼ぶんは、やめた。
                                #   8/22に line_bot 側の push_long_text / push_text へ
                                #   「送ったら記録する」を入れた。★せやから、ここでも呼んだら二回入る。
                                #   ★★実害：会員の画面に同じ返信が二回並ぶ。8/22以降で53件出とった。
                                #     絵麻さんとなつみさんから「なんか変」と言われたんは、これや。
                                #   ★★★記録は push した側で一回だけ。ここは送信の後始末だけやる。
                                pass
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
                        text = _unmangle(text)
                        if len(text) < 200:
                            st.error("PDFから本文を読み取れませんでした（画像化されたPDFの可能性）")
                        else:
                            # ★★★2026-08-23：登録先を間違えても、今までは何も出んかった。
                            #   実害：ａｉｒｉさんの鑑定書と訂正メモ4件が、絵麻さんの控えに
                            #   入っとった（2026-08-16）。★訂正メモは「最優先で参照する」いう
                            #   札つきで、絵麻さんへの返信に毎回渡っとった。共同親権も、離婚届も、
                            #   3歳の娘も、ぜんぶ別の人の話や。★★「なんか噛み合わん」の正体はこれ。
                            #   ★★★せやから、鑑定書の中の名前と、この会員の名前を突き合わせる。
                            #     食い違うたら、登録する前に必ず止めて訊く。
                            # 宛名の字面で照合するんは当てにならん（ローマ字の会員は必ず食い違うし、
                            # 訂正を頭に足した版は宛名が後ろへずれる）。せやから【同じ文書が
                            # 他の会員にも入っとらんか】で見る。実際の事故がまさにこれやった。
                            _owner = ""
                            _probe = [_norm_doc(text)[i:i + 60]
                                      for i in range(0, min(len(text), 6000), 600)]
                            _probe = [x for x in _probe if len(x) == 60]
                            for _om in store.list_members():
                                if str(_om["id"]) == str(m["id"]):
                                    continue
                                for _oh in store.list_readings(_om["id"], limit=60):
                                    if str(_oh["month"]) != "個別鑑定書":
                                        continue
                                    _ob = _norm_doc(str(_oh["reading"]))
                                    if _probe and sum(1 for x in _probe if x in _ob) >= max(2, len(_probe) // 2):
                                        _owner = str(_om["nickname"])
                                        break
                                if _owner:
                                    break
                            _ck2 = f"mem_pdf_ok_{m['id']}"
                            if _owner and not st.session_state.get(_ck2):
                                st.session_state[_ck2] = True
                                st.error(f"⚠️ この鑑定書は、すでに「{_owner}」さんの控えに入っている"
                                         f"のと同じ中身です。登録先は「{m['nickname']}」で合っていますか？"
                                         "（別の人の鑑定書が混ざると、その人の話が"
                                         "毎回の返信に渡ってしまいます）"
                                         "合っている場合は、もう一度ボタンを押すと登録します。")
                            else:
                                st.session_state.pop(_ck2, None)
                                # ★2026-08-23：15,000字で切っとった。tomokoさんの鑑定書が
                                #   ちょうど15,000字＝【末尾が落ちとる】。鑑定書の後ろは
                                #   第七章（やったらあかんこと）と第八章や。★いちばん効く処方が消える。
                                store.add_reading(m["id"], "個別鑑定書", "（納品済み個別鑑定PDFの全文）", text)
                                st.success(f"鑑定書を登録しました（{len(text)}字"
                                           + (f"・宛名「{_to} 様へ」" if _to else "") + "）")
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

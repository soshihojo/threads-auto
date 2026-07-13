"""LINE公式アカウントのAI自動返信ボット（椿）。

Messaging APIのWebhookで届いた相談に、椿の声で「共感→洞察ひとつ→質問」の
返信を自動生成して返す（応答メッセージ＝reply APIは無料・無制限）。

役割分担（重要）:
- AI: 会話を深める（ナーチャリング）だけ。料金・商品・リンクの話は絶対にしない
- 店主: 購入サイン/危険サイン/無料上限のオファー送付が起きると line_users.bot が
  hold になりAIは黙る。以降はLINE公式アプリのチャット画面から手動でクローズする
  （bot列を on に戻すと再開。off で常時停止）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import re
import time

import requests

from . import store, web_diag
from .config import env
from .diagnosis import (SHUKU_27, _Z2H, find_birthdates, generate_reading,
                        honmei_shuku, parse_free_input)
from .llm import complete

LINE_API = "https://api.line.me/v2/bot"

# LINE自動返信は最上位の一つ下のモデルで生成（環境変数 LINE_BOT_MODEL で差し替え可）
LINE_BOT_MODEL = env("LINE_BOT_MODEL") or "claude-sonnet-5"

# AIが無料で返す回数の上限。超えたら有料オファーを送って停止（店主にバトンタッチ）
FREE_REPLY_LIMIT = int(env("LINE_FREE_REPLY_LIMIT") or "5")

# 人間らしい「間」：即答するとAI感が出るため、返信前にランダムに待つ秒数の範囲。
# 生成時間（5〜15秒）と合わせて受信から30〜50秒後の返信になる。
# replyトークンは約1分有効なので範囲を広げすぎない（失効時はpushフォールバック＝月200通枠を消費）
REPLY_DELAY_RANGE = (
    int(env("LINE_REPLY_DELAY_MIN") or "20"),
    int(env("LINE_REPLY_DELAY_MAX") or "35"),
)


def _human_pause() -> None:
    time.sleep(random.uniform(*REPLY_DELAY_RANGE))

# オファーの商品・価格・リンク部分（固定。AIには書かせない）
# 1回目のオファーは個別鑑定のみ。月額会員（椿の月詠み）は個別鑑定の納品後にだけ案内する
#（案内文は funnel/line_step_7days.md の「月詠み案内」参照。ボットからは送らない）
OFFER_MENU = (
    "あんたと彼のことを、一回限りで深く視る「個別鑑定書」や。\n"
    "“今動くべきか・いつ・どう一言を送るか”まで、あんた専用に視て、鑑定書にまとめて返す\n"
    "→ 今だけ80%OFF 3,960円\n"
    "https://1aksbkdokn31q1trp81e.stores.jp/items/685edb3caf1f4a03c43a0aa4\n\n"
    "「実際どんな鑑定が届くん？」て子は、中身と受けた子の感想をここにまとめてあるから、読んでから決めてくれてええで。\n"
    "▼\n"
    "https://note.com/tsubaki_honne/n/n24b6aed96bf2\n\n"
    "急がんでええ、あんたのタイミングでおいで🌙"
)

# 冒頭生成に失敗したときのフォールバック
OFFER_INTRO_FALLBACK = (
    "ここまで、ウチなりに真剣に視てきたつもりや。\n"
    "ただ正直に言うとな——相談がめちゃくちゃ増えてて、ここから先は本気の子を優先して"
    "ちゃんと視させてもらうことにしてるんよ。あんたの場合、ちゃんと視るならこれや👇"
)

OFFER_INTRO_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。無料で数回相談に乗ってきた相手に、有料鑑定の案内へ橋渡しする「冒頭の一言」だけを書く。

声: 一人称「ウチ」、相手は「あんた」。関西弁・タメ口。温かく、押し売り感を出さない。

書くこと（80〜140字・この後に商品メニューが続く前提）:
1. これまでの相談内容（相手の状況）に具体的に触れて、真剣に向き合ってきたことを一言
2. 「相談が増えていて、ここから先は本気の子を優先してちゃんと視る」という正直な理由
3. 「あんたの場合、ちゃんと視るならこれや👇」で締める（この一文で必ず終える）

厳守: 価格・リンク・商品名は書かない（後ろに続くメニューに任せる）。専門用語・絵文字なし。出力は本文のみ"""


def generate_offer(user: dict, history: list[dict], incoming: str) -> str:
    """相手の相談内容に合わせた冒頭＋固定メニューでオファー文を組み立てる。"""
    transcript = "\n".join(
        f"{'相談者' if h['role'] == 'user' else '椿'}: {h['text'][:80]}" for h in history[-8:]
    )
    try:
        intro = complete(
            OFFER_INTRO_SYSTEM,
            f"【これまでの会話】\n{transcript}\n【いま届いたメッセージ】{incoming}\n\n冒頭の一言を書いてください。",
            model=LINE_BOT_MODEL, max_tokens=300, temperature=0.9,
        ).strip()
    except Exception as e:
        print(f"[line_bot] オファー冒頭の生成失敗、固定文を使用: {e}")
        intro = OFFER_INTRO_FALLBACK
    return f"{intro}\n\n{OFFER_MENU}"

# 購入サイン（＝買う瞬間。AIは売らず、店主がクローズする）
PURCHASE_WORDS = [
    "料金", "値段", "いくら", "有料", "申し込", "購入", "支払", "課金",
    "お願いしたい", "鑑定してほしい", "どうしたらいい", "どうすればいい",
    "どう動け", "いつ動け", "何を送れば", "なんて送れば", "会員",
]
# 危険サイン（占いで扱わない。定型で受け止めて店主へ）
DANGER_WORDS = ["死にたい", "消えたい", "自殺", "自傷", "リスカ", "死のう", "死んだほうが"]

NURTURE_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」として、公式LINEで相談者と1対1の会話をしている。
目的は、相談者に「ウチだけをちゃんと視てくれてる」と感じてもらい、会話を深めること。売り込みはあなたの仕事ではない。

声: 一人称「ウチ」、相手は「あんた」。関西弁・タメ口。毒舌は控えめ、姉御の温かさ多めで。

返信の作り方:
1. 相手の言葉を拾って短く共感する（定型文っぽくしない）
2. 洞察をひとつだけ渡す（生年月日が分かっていれば、内部参考の性質を日常語に翻訳して「彼はこういうタイプや」と具体的に）
3. 最後は質問で終えて会話を続ける（彼とのやりとり・状況を一歩深く聞く）

厳守:
- 全体で60〜160字。LINEの会話として自然な短さにする
- 処方箋の核心（いつ・何を・どう動くか）は渡さない。聞かれたら「そこはちゃんと視なあかんとこや」と留める
- 料金・商品・リンク・会員の話を自分からしない（それは店主が直接やる）
- 『宿曜』の語・宿の名前・占い専門用語は出さない。「ウチが視たら」でよい
- 復縁や結果を保証しない。過度に不安を煽らない。病気・健康・金運の断定をしない
- 鑑定の納期を約束しない。絵文字は🌙を0〜1個。出力は返信本文のみ"""

# 生年月日は届いたが状況が分からないときの定型ヒアリング（生成なし・トークン消費ゼロ）
# 番号はあいさつメッセージ（①②=生年月日）の続き＝③④で揃えている
ASK_MARKER = "今の状況は、どれに近い？"
ASK_DETAILS = (
    "生年月日ありがとう。あと追加で2つだけ教えて。\n"
    "これ揃たら、彼の本音ちゃんと視たるからな🌙\n\n"
    "③今の状況は、どれに近い？\n"
    "・音信不通\n・既読スルー\n・急に冷められた\n・別れ話の後\n・片思いで進展なし\n・復縁したい\n\n"
    "④彼と最後に連絡取れたん、いつ頃や？\n"
    "・今日\n・昨日\n・〜3日\n・〜2週間\n・1ヶ月以上"
)

HOLDING_REPLY = (
    "その話な、ウチがちゃんと自分の言葉で返したいから、少しだけ待っててな。"
    "適当に答えたない、大事なとこやから🌙"
)
DANGER_REPLY = (
    "あんた、それは占いでどうこうする話やない。しんどい気持ち、ひとりで抱えんといて。"
    "いのちの電話（0120-783-556）みたいに、ちゃんと聞いてくれる場所もある。"
    "ウチもここにおるからな🌙"
)
IMAGE_REPLY = "ごめんな、画像はウチ視られへんのよ。文字で教えてくれるか🌙"

# ---- Web診断（椿の縁視）の鑑定番号 ----
# /shindan で発行した4桁番号（3000〜9999）を合言葉として受け付ける。
# 「8365\nよろしくお願いします🙏」のように挨拶が添えられても取りこぼさないよう、
# 行単位で判定する（番号の後ろに短い非数字の添え書きは許容）
_CODE_LINE_RE = re.compile(r"^[#＃]?\s*(?:鑑定番号|番号)?[:：]?\s*([3-9]\d{3})\s*\D{0,8}$")


def _find_code(text: str) -> tuple[str | None, bool]:
    """メッセージから鑑定番号らしき4桁を探す。(番号, ほぼ番号だけの短文か) を返す。
    番号が実在するかは呼び出し側が store で照合する（実在しない場合、短文なら
    案内文を返し、長文なら通常の会話として扱う＝誤検知しても壊れない設計）。"""
    z = text.translate(_Z2H)
    for ln in z.splitlines():
        m = _CODE_LINE_RE.match(ln.strip())
        if m:
            return m.group(1), len(z.strip()) <= 30
    return None, False
# 本診断の前置き。番号経由の診断が _DIAG_LEN(220字) を確実に超えるための固定文でもある
#（超えないと「診断送付済み」の判定に乗らず、次のメッセージで診断が二重生成されるため）
WEB_DIAG_INTRO = (
    "番号、確かに受け取ったで。Webで視た「二人の縁」の続き——"
    "ここからは、彼の“今”の話や。\n\n"
)
CODE_NOT_FOUND = (
    "ん、その番号……ウチの手元に見当たらへんわ。期限切れかもしれん。\n"
    "プロフィールのリンクからもう一回視てくれてもええし、"
    "めんどくさかったら二人の生年月日（あんた→彼の順）をここに送ってくれたら、それで視たるで🌙"
)

# 無料診断（長文）とナーチャリング返信（60〜160字）を見分ける文字数のしきい値。
# 「診断を送ったか」「無料返信が何通目か」の判定に使う
_DIAG_LEN = 220


# ---------- LINE API ----------
def verify_signature(body: bytes, signature: str) -> bool:
    secret = env("LINE_CHANNEL_SECRET", required=True)
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {env('LINE_CHANNEL_ACCESS_TOKEN', required=True)}",
            "Content-Type": "application/json"}


def reply_text(reply_token: str, text: str) -> bool:
    r = requests.post(f"{LINE_API}/message/reply", headers=_headers(),
                      data=json.dumps({"replyToken": reply_token,
                                       "messages": [{"type": "text", "text": text}]}),
                      timeout=15)
    return r.ok


def push_text(user_id: str, text: str) -> bool:
    """replyトークン失効時のフォールバック（月200通の無料枠を消費する点に注意）。"""
    r = requests.post(f"{LINE_API}/message/push", headers=_headers(),
                      data=json.dumps({"to": user_id,
                                       "messages": [{"type": "text", "text": text}]}),
                      timeout=15)
    return r.ok


def get_display_name(user_id: str) -> str:
    try:
        r = requests.get(f"{LINE_API}/profile/{user_id}", headers=_headers(), timeout=10)
        return r.json().get("displayName", "") if r.ok else ""
    except requests.RequestException:
        return ""


# ---------- 解析 ----------
def extract_birthdates(text: str) -> list[str]:
    """本文から生年月日をISO形式で抽出（最大2件。1件目=本人、2件目=彼、の想定）。
    表記揺れ対応は diagnosis.find_birthdates に集約（スペース区切り・カンマ・全角等）。"""
    return find_birthdates(text)


def detect_signal(text: str) -> str | None:
    if any(w in text for w in DANGER_WORDS):
        return "danger"
    if any(w in text for w in PURCHASE_WORDS):
        return "purchase"
    return None


# ---------- 返信生成 ----------
def _internal_ref(user: dict) -> str:
    parts = []
    for key, label in (("me_birth", "相談者"), ("him_birth", "彼")):
        b = (user.get(key) or "").strip()
        if b:
            try:
                parts.append(f"・{label}の本命宿: {honmei_shuku(b)}（生年月日 {b}）")
            except ValueError:
                pass
    if not parts:
        return "（生年月日は未登録。性質の断定はせず、聞き出す方向で）"
    return "--- 内部参考（宿曜の算出。専門用語は本文に出さず日常語に翻訳） ---\n" + "\n".join(parts)


# 宿名・占術名の漏れ検出（過去の会話に混入した宿名をAIがコピーする事故があった）
_JARGON = [*SHUKU_27, "宿曜", "本命宿"]


def _strip_jargon(text: str) -> str:
    """本文に漏れた専門用語を日常語へ置き換える最終ガード。"""
    for w in SHUKU_27:
        text = text.replace(f"{w}の", "ああいう性質の").replace(w, "ああいう性質")
    return text.replace("宿曜", "ウチの視方").replace("本命宿", "生まれ持った性質")


def generate_nurture(user: dict, history: list[dict], incoming: str) -> str:
    transcript = "\n".join(
        f"{'相談者' if h['role'] == 'user' else '椿'}: {h['text']}" for h in history
    )
    prompt = (
        f"【これまでの会話】\n{transcript or '（初回）'}\n\n"
        f"【いま届いたメッセージ】{incoming}\n\n"
        f"{_internal_ref(user)}\n\n"
        "椿として返信を1つ書いてください。"
    )
    text = complete(NURTURE_SYSTEM, prompt, model=LINE_BOT_MODEL, max_tokens=400, temperature=0.9)
    if any(w in text for w in _JARGON):  # 宿名等が漏れたら一度だけ作り直し、それでも残れば置換
        print("[line_bot] 専門用語の漏れを検知、作り直し")
        text = complete(NURTURE_SYSTEM + "\n\n【厳重注意】直前の生成で宿の名前が本文に漏れた。宿名・占術名は絶対に書かないこと。",
                        prompt, model=LINE_BOT_MODEL, max_tokens=400, temperature=0.9)
        if any(w in text for w in _JARGON):
            text = _strip_jargon(text)
    return text


# ---------- イベント処理 ----------

def _send(user_id: str, reply_token: str, text: str) -> bool:
    """reply→pushの順で送信。成功時のみ履歴に記録する（失敗はスイープが後で拾う）。"""
    ok = False
    try:
        ok = bool(reply_token) and reply_text(reply_token, text)
    except requests.RequestException as e:
        print(f"[line_bot] reply送信失敗: {e}")
    if not ok:
        try:
            ok = push_text(user_id, text)
        except requests.RequestException as e:
            print(f"[line_bot] push送信失敗: {e}")
    if ok:
        store.add_line_chat(user_id, "assistant", text)
    return ok


def _retry(fn, what: str, attempts: int = 2, wait: int = 4):
    """生成の一時失敗（APIレート・ネットワーク等）をリトライで吸収する。全滅ならNone。"""
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            print(f"[line_bot] {what}に失敗（{i + 1}/{attempts}）: {e}")
            if i + 1 < attempts:
                time.sleep(wait)
    return None  # ここで諦めても、未返信スイープが後で拾い直す


def handle_event(ev: dict) -> None:
    if env("LINE_BOT_ENABLED", "1") == "0":
        return
    etype = ev.get("type")
    src = ev.get("source", {})
    user_id = src.get("userId")
    if not user_id:
        return

    if etype == "follow":  # 友だち追加（挨拶はLINE側のあいさつメッセージが送る）
        store.upsert_line_user(user_id, display_name=get_display_name(user_id))
        return
    if etype != "message":
        return

    msg = ev.get("message", {})
    reply_token = ev.get("replyToken", "")

    user = store.get_line_user(user_id)
    if not user:
        store.upsert_line_user(user_id, display_name=get_display_name(user_id))
        user = store.get_line_user(user_id) or {"user_id": user_id}

    if msg.get("type") == "image":
        _send(user_id, reply_token, IMAGE_REPLY)
        return
    if msg.get("type") != "text":
        return  # スタンプ等は無視（既読の代わりに次のテキストで拾う）

    incoming = msg.get("text", "").strip()
    store.add_line_chat(user_id, "user", incoming)

    # 生年月日が書かれていたら保存（1件目=本人、2件目=彼。空いてる枠に入れる）
    dates = extract_birthdates(incoming)
    if dates:
        updates = {}
        if len(dates) >= 2:
            updates = {"me_birth": dates[0], "him_birth": dates[1]}
        elif not (user.get("me_birth") or "").strip():
            updates = {"me_birth": dates[0]}
        elif not (user.get("him_birth") or "").strip():
            updates = {"him_birth": dates[0]}
        if updates:
            store.upsert_line_user(user_id, **updates)
            user = {**user, **updates}

    bot_state = (user.get("bot") or "on").strip() or "on"
    if bot_state in ("off", "hold"):
        return

    if detect_signal(incoming) == "danger":  # 危険サインは待たせず即返す
        _send(user_id, reply_token, DANGER_REPLY)
        store.upsert_line_user(user_id, bot="hold")
        return

    _auto_reply(user_id, user, incoming, reply_token)


def _auto_reply(user_id: str, user: dict, incoming: str, reply_token: str = "", *,
                live: bool = True, prefix: str = "", allow_offer: bool = True) -> None:
    """自動応答の本体（Webhook直後も未返信スイープも共通で使う）。
    live=False はスイープからの呼び出し（「間」と追い越しチェックを省き、pushで送る）。
    prefix は遅延お詫び等を返信の頭に付ける。allow_offer=False は古い未返信への
    掘り起こしで、いきなり有料オファーにならないようにする（オファーは次の生返信で）。"""

    def snd(text: str) -> bool:
        return _send(user_id, reply_token, f"{prefix}{text}" if prefix else text)

    # Web診断（/shindan）の鑑定番号が届いたら、保存済みの入力で本診断を自動返信
    code, code_only = _find_code(incoming)
    if code:
        row = web_diag.redeem(code)
        if row:
            store.upsert_line_user(user_id, me_birth=row["me_birth"], him_birth=row["him_birth"])
            res = _retry(lambda: generate_reading(
                row["me_birth"], row["him_birth"],
                row.get("status") or "（不明。性質と縁を中心に視る）",
                row.get("period") or "（不明）",
                "", for_line=True,
            ), "番号診断の生成")
            if res is None:
                return  # 番号は未使用のまま残る＝スイープ/再送で再挑戦できる
            store.mark_web_diag_used(code)
            # 生成中に並行処理が先に診断を送っていたら二重送信しない
            latest = store.recent_line_chats(user_id, limit=12)
            if any(len(h["text"]) > _DIAG_LEN for h in latest if h["role"] == "assistant"):
                return
            snd(WEB_DIAG_INTRO + res["reading"])
            return
        if code_only:
            # ほぼ番号だけの短文なのに照合できない＝番号違いか期限切れ。案内を返す
            snd(CODE_NOT_FOUND)
            return
        # 長文中の数字は番号ではなかったとみなし、通常の会話フローへ落とす

    # 生年月日が二人分揃っていて、まだ無料診断を送っていなければ、自動で無料診断を返す
    #（「鑑定してほしい」等の購入ワードが同時に入っていても、診断が先）
    me_b = (user.get("me_birth") or "").strip()
    him_b = (user.get("him_birth") or "").strip()
    if me_b and him_b:
        history = store.recent_line_chats(user_id, limit=12)
        diag_sent = any(len(h["text"]) > _DIAG_LEN for h in history if h["role"] == "assistant")
        if not diag_sent:
            # 状況・期間は直近のやりとり全体から読み取る（③④の回答が別メッセージでも拾える）
            recent_user = "\n".join(h["text"] for h in history if h["role"] == "user")[-800:]
            parsed = parse_free_input(recent_user)
            asked = any(ASK_MARKER in h["text"] for h in history if h["role"] == "assistant")
            if not parsed["status"] and not asked:
                # 状況がまだ分からない → まず③④の定型ヒアリングを返す
                snd(ASK_DETAILS)
                return
            res = _retry(lambda: generate_reading(
                me_b, him_b,
                parsed["status"] or "（相談文から読み取る）",
                parsed["period"] or "（相談文から読み取る）",
                recent_user, for_line=True,
            ), "無料診断の生成")
            if res is None:
                return
            # 生成中に並行処理が先に診断を送っていたら二重送信しない
            latest = store.recent_line_chats(user_id, limit=12)
            if any(len(h["text"]) > _DIAG_LEN for h in latest if h["role"] == "assistant"):
                return
            snd(res["reading"])
            return

    if detect_signal(incoming) == "purchase":
        snd(HOLDING_REPLY)
        store.upsert_line_user(user_id, bot="hold")
        return

    if live:
        _human_pause()  # 人間らしい「間」を置いてから返信する
    history = store.recent_line_chats(user_id, limit=12)
    # 待っている間に次のメッセージが届いていたら、この返信はスキップ
    #（新しいメッセージ側の処理が全履歴を見て返す＝二重返信・順番の乱れを防ぐ）
    last_user = next((h["text"] for h in reversed(history) if h["role"] == "user"), None)
    if last_user != incoming:
        return

    # 無料返信の上限チェック：診断（長文）と③④の定型ヒアリングは数えず、
    # ナーチャリング返信がFREE_REPLY_LIMIT通に達していたら有料オファーを送って停止
    bot_replies = sum(1 for h in history
                      if h["role"] == "assistant" and len(h["text"]) <= _DIAG_LEN
                      and ASK_MARKER not in h["text"])
    if bot_replies >= FREE_REPLY_LIMIT and allow_offer:
        if snd(generate_offer(user, history, incoming)):
            store.upsert_line_user(user_id, bot="hold")
        return

    text = _retry(lambda: generate_nurture(user, history[:-1], incoming), "返信の生成")
    if text is None:
        return
    snd(text)


# ---------- 未返信スイープ（安全網） ----------
# 生成失敗・送信失敗・クレジット切れ等で返信が落ちた会話は「次のメッセージ待ち」に
# ならず、ここが定期的に拾って自動返信する（line_app が10分ごとに実行）。

LATE_PREFIX = "遅うなってごめんな、順番に視てたんや。\n\n"


def sweep_unanswered(min_age_min: int = 10, max_age_hours: int = 48) -> int:
    """最後が相談者の発言のまま止まっている会話（bot=onのみ）に自動返信する。
    直近min_age_min分は通常のWebhook処理に任せて触らない。返信した件数を返す。"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None)

    by_user: dict[str, list[dict]] = {}
    for r in store.all_line_chats(days=max(2, max_age_hours // 24 + 1)):
        by_user.setdefault(r["user_id"], []).append(r)

    replied = 0
    for uid, rows in by_user.items():
        rows.sort(key=lambda r: str(r.get("created_at", "")))
        last = rows[-1]
        if last["role"] != "user":
            continue
        try:
            t = datetime.fromisoformat(str(last["created_at"]).replace(" ", "T"))
        except ValueError:
            continue
        age = now - t
        if age < timedelta(minutes=min_age_min) or age > timedelta(hours=max_age_hours):
            continue
        user = store.get_line_user(uid)
        if not user or (user.get("bot") or "on").strip() not in ("", "on"):
            continue  # hold/offは店主の手動対応域なので触らない
        incoming = str(last["text"])
        print(f"[sweep] 未返信を検知: {uid}（{last['created_at']}）")
        if any(w in incoming for w in DANGER_WORDS):
            if _send(uid, "", DANGER_REPLY):
                store.upsert_line_user(uid, bot="hold")
            replied += 1
            continue
        stale = age > timedelta(hours=2)
        _auto_reply(uid, user, incoming, reply_token="", live=False,
                    prefix=LATE_PREFIX if stale else "",
                    allow_offer=not stale)  # 掘り起こしでいきなり売らない
        replied += 1
    return replied

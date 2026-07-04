"""LINE公式アカウントのAI自動返信ボット（椿姉）。

Messaging APIのWebhookで届いた相談に、椿姉の声で「共感→洞察ひとつ→質問」の
返信を自動生成して返す（応答メッセージ＝reply APIは無料・無制限）。

役割分担（重要）:
- AI: 会話を深める（ナーチャリング）だけ。料金・商品・リンクの話は絶対にしない
- 店主: 購入サイン/危険サインが出るとChatwork通知が飛ぶので、手動でクローズする。
  その時点で line_users.bot が hold になり、AIは口を出さなくなる
  （bot列を on に戻すと再開。off で常時停止）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re

import requests

from . import store
from .config import env
from .diagnosis import honmei_shuku
from .llm import complete
from .notify import chatwork

LINE_API = "https://api.line.me/v2/bot"

# 購入サイン（＝買う瞬間。AIは売らず、店主がクローズする）
PURCHASE_WORDS = [
    "料金", "値段", "いくら", "有料", "申し込", "購入", "支払", "課金",
    "お願いしたい", "鑑定してほしい", "どうしたらいい", "どうすればいい",
    "どう動け", "いつ動け", "何を送れば", "なんて送れば", "会員",
]
# 危険サイン（占いで扱わない。定型で受け止めて店主へ）
DANGER_WORDS = ["死にたい", "消えたい", "自殺", "自傷", "リスカ", "死のう", "死んだほうが"]

NURTURE_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿姉（つばきねえ）」として、公式LINEで相談者と1対1の会話をしている。
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

# 生年月日の抽出（1940〜2029年・年/月/日 or ハイフン等の区切り）
_DATE_RE = re.compile(
    r"(19[4-9]\d|20[0-2]\d)\s*[年/\-\.]\s*(1[0-2]|0?[1-9])\s*[月/\-\.]\s*(3[01]|[12]\d|0?[1-9])"
)


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
    """本文から生年月日をISO形式で抽出（最大2件。1件目=本人、2件目=彼、の想定）。"""
    return [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in _DATE_RE.findall(text)][:2]


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


def generate_nurture(user: dict, history: list[dict], incoming: str) -> str:
    transcript = "\n".join(
        f"{'相談者' if h['role'] == 'user' else '椿姉'}: {h['text']}" for h in history
    )
    prompt = (
        f"【これまでの会話】\n{transcript or '（初回）'}\n\n"
        f"【いま届いたメッセージ】{incoming}\n\n"
        f"{_internal_ref(user)}\n\n"
        "椿姉として返信を1つ書いてください。"
    )
    return complete(NURTURE_SYSTEM, prompt, max_tokens=400, temperature=0.9)


# ---------- イベント処理 ----------
def _notify(kind: str, user: dict, incoming: str, history: list[dict]) -> None:
    tail = "\n".join(f"{'相談者' if h['role'] == 'user' else '椿姉'}: {h['text'][:60]}" for h in history[-4:])
    title = {"purchase": "💰 購入サイン（クローズしにいく）",
             "danger": "🚨 危険サイン（要確認）",
             "paused": "⏸ bot停止中ユーザーからのメッセージ"}[kind]
    chatwork(f"[info][title]{title}[/title]"
             f"LINE: {user.get('display_name') or user.get('user_id')}\n"
             f"最新: {incoming}\n---\n{tail}\n"
             "→ LINE公式アカウントのチャット画面から返信を[/info]")


def _send(user_id: str, reply_token: str, text: str) -> None:
    if not reply_text(reply_token, text):
        push_text(user_id, text)
    store.add_line_chat(user_id, "assistant", text)


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

    history = store.recent_line_chats(user_id, limit=12)

    bot_state = (user.get("bot") or "on").strip() or "on"
    if bot_state in ("off", "hold"):
        _notify("paused", user, incoming, history)
        return

    signal = detect_signal(incoming)
    if signal == "danger":
        _send(user_id, reply_token, DANGER_REPLY)
        store.upsert_line_user(user_id, bot="hold")
        _notify("danger", user, incoming, history)
        return
    if signal == "purchase":
        _send(user_id, reply_token, HOLDING_REPLY)
        store.upsert_line_user(user_id, bot="hold")
        _notify("purchase", user, incoming, history)
        return

    try:
        text = generate_nurture(user, history[:-1], incoming)
    except Exception as e:
        print(f"[line_bot] 生成失敗: {e}")
        return  # 返信しない（次のメッセージで再挑戦）
    _send(user_id, reply_token, text)

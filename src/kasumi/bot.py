"""かすみのLINEボット（POST /webhook/kasumi）。

流れ（docs/14・15）：
  友だち追加 → あいさつ
  4桁の番号 or 生年月日2つ → 縁の詳しい読み（手書きの部品を組み合わせる。生成は使わない）＋最初の質問
  ヒアリング（生成で1通ずつ。3往復聞いて、4回目の答えで終わりのあいさつ）
  終わった人だけ、翌日から4日間の配信（send_due_steps。ボタンで答える質問つき）

椿の line_bot とは鍵・保存先・文面がすべて別。椿の関数は呼ばない（宿の計算と検品の関数だけ借りる）。
ボットは商品の案内をしない。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
from datetime import datetime, timedelta

import requests

from ..config import env
from ..diagnosis import find_birthdates, foreign_word, strip_ai_leak, strip_jargon, with_honorific
from ..llm import complete
from . import store, texts
from .diag import redeem
from .en import relation

LINE_API = "https://api.line.me/v2/bot"
MODEL = env("KASUMI_MODEL") or env("LINE_BOT_MODEL") or "claude-sonnet-5"
PUSH_MONTHLY_CAP = int(env("KASUMI_PUSH_MONTHLY_CAP") or "180")   # 無料プラン200通の内側で止める
STEP_HOUR = 20          # 7日間の配信は毎日20時（JST）
STEP_WINDOW = (9, 22)   # この時間帯の外では送らない

DANGER_WORDS = ["死にたい", "消えたい", "自殺", "自傷", "リスカ", "死のう", "死んだほうが", "いなくなりたい"]
DELETE_WORDS = ["データを削除", "データ削除", "情報を削除", "削除してほしい", "消してほしい"]
# 番号として扱うのは「番号だけ」か「縁の番号XXXX」の形だけ。
# ヒアリング中の「月5000円くらい」などを番号と取り違えないため
_CODE_RE = re.compile(r"^\s*(?:縁の番号|番号)?\s*[:：]?\s*([3-9]\d{3})\s*$")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(user_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(user_id, threading.Lock())


# ---------------- LINE API ----------------
def enabled() -> bool:
    return bool(env("LINE_CHANNEL_SECRET_KASUMI") and env("LINE_CHANNEL_ACCESS_TOKEN_KASUMI")
                and (env("KASUMI_BOT_ENABLED") or "1") not in ("0", "false"))


def verify_signature(body: bytes, signature: str) -> bool:
    secret = env("LINE_CHANNEL_SECRET_KASUMI")
    if not secret:
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {env('LINE_CHANNEL_ACCESS_TOKEN_KASUMI', required=True)}",
            "Content-Type": "application/json"}


def _clean(text: str) -> str:
    """Markdownの記号・モデル名・宿曜の専門用語を落とす。"""
    t = strip_jargon(strip_ai_leak(text or ""))
    t = re.sub(r"\*\*|__|`", "", t)
    t = re.sub(r"(?m)^\s*#+\s*", "", t)
    t = re.sub(r"(?m)^\s*[-*]\s+", "・", t)
    t = t.replace("🌙", "")          # 椿の印。かすみは使わない
    return t.strip()


def _message(text: str, options: list[str] | None = None) -> dict:
    msg: dict = {"type": "text", "text": text[:4900]}
    if options:
        msg["quickReply"] = {"items": [
            {"type": "action", "action": {"type": "message", "label": o[:20], "text": o}} for o in options[:13]]}
    return msg


def _post(path: str, payload: dict) -> bool:
    r = requests.post(f"{LINE_API}/{path}", headers=_headers(), data=json.dumps(payload), timeout=15)
    if not r.ok:
        print(f"[kasumi] {path} がLINEに弾かれた: {r.status_code} {r.text[:200]}")
    return r.ok


def reply(reply_token: str, user_id: str, text: str, options: list[str] | None = None) -> bool:
    """相手のメッセージへの返事（通数に数えない）。送れたら記録する。"""
    text = _clean(text)
    ok = _post("message/reply", {"replyToken": reply_token, "messages": [_message(text, options)]})
    if ok:
        store.add_chat(user_id, "assistant", text)
    return ok


def push(user_id: str, text: str, options: list[str] | None = None) -> bool:
    """こちらから送る（月の通数に数える）。送れたら記録する。"""
    text = _clean(text)
    ok = _post("message/push", {"to": user_id, "messages": [_message(text, options)]})
    if ok:
        store.add_chat(user_id, "assistant", text)
        store.add_event("push", user_id)
    return ok


def display_name(user_id: str) -> str:
    try:
        r = requests.get(f"{LINE_API}/profile/{user_id}", headers=_headers(), timeout=10)
        return str(r.json().get("displayName") or "") if r.ok else ""
    except Exception:
        return ""


def _notify_owner(message: str) -> None:
    try:
        from ..notify import chatwork
        chatwork(f"[かすみ] {message}")
    except Exception as e:
        print(f"[kasumi] 店主への通知に失敗: {e}")


# ---------------- 縁の読み ----------------
def _name_prefix(name: str) -> str:
    return f"{with_honorific(name)}、" if (name or "").strip() else ""


def build_reading(name: str, me_birth: str, oshi_birth: str, rel: str, worry: str) -> str:
    t = texts.EN_TYPES[rel]
    question = texts.WORRIES.get(worry) or texts.WORRIES[texts.DEFAULT_WORRY]
    reason = texts.FIRST_REASON_NO_WORRY if worry == texts.NO_WORRY else texts.FIRST_REASON
    return texts.READING.format(
        name_prefix=_name_prefix(name), en=t["reading"],
        oshi=texts.OSHI_MONTH[int(oshi_birth[5:7])], me=texts.ME_MONTH[int(me_birth[5:7])],
        tired=t["tired"], reason=reason, question=question)


def _send_reading(token: str, user: dict, me: str, oshi: str, state: str, worry: str, source: str) -> None:
    uid = user["user_id"]
    rel = relation(me, oshi)
    text = build_reading(user.get("display_name", ""), me, oshi, rel, worry)
    store.upsert_user(uid, me_birth=me, oshi_birth=oshi, en_type=rel, state=state, worry=worry,
                      source=source, stage="hearing", turns="0")
    reply(token, uid, text)
    store.add_event("reading", uid)


# ---------------- ヒアリング（生成） ----------------
# 生成を使うのはここだけ。1人あたり、ヒアリングの返事3通＋最後の「ひとこと」1通。
# 質問の前の「なぜ聞くか」と「（2/4）」は、生成させずにこちらで必ず足す。
_RULES = """口調：やわらかい丁寧語。一人称は「わたし」、相手は「あなた」。友だちに寄り添うような温度で。

守ること：
・プレーンテキストのみ（Markdownの記号、箇条書き、見出しは使わない）
・助言の押しつけ、商品やサービスの案内はしない
・相手が書いた推しやグループ、他のファンの名前（固有名詞）は繰り返さない
・推しや他のファンを悪く言う話には乗らない。相手の気持ちだけを受け止める
・宿曜、宿の名前などの占いの専門用語は使わない。絵文字は使っても1つまで
・自分がAIか人間かを聞かれても、肯定も否定もせず「わたしはかすみです」とだけ答えて、相手の話に戻る（人間だとは言わない）
・AI、モデル名、開発元の名前、署名は書かない
・英単語を使わず、日本語だけで書く（相手が英字で書いた言葉をそのまま使うときだけは可）"""

HEARING_SYSTEM = """あなたは推し活占い師「かすみ」。公式LINEで、推し活をしている人の話を聞いている。
目的は、その人が推し活で感じていることを、安心して言葉にしてもらうこと。

""" + _RULES + """

出力は次の形のJSONだけ。説明や前置きは書かない。
{"ack": "相手の言葉を一つ拾って受け止める文（40〜100字。質問はしない。毎回同じ決まり文句にしない）",
 "question": "「次に聞くこと」を聞く質問を1つだけ（40字以内。質問を2つ並べない）"}
「次に聞くこと」が「なし」のときは question を空文字にする。"""

FINAL_SYSTEM = """あなたは推し活占い師「かすみ」。公式LINEで4つの質問に答えてくれた人に、その人のためだけの「ひとこと」を書く。

""" + _RULES + """

書き方：
・150〜250字。本文だけを書く
・最初に、相手が話してくれたことを一つ受け止める
・次に、渡された縁タイプと生まれ月の読みの範囲で、その人の推し活の良さやくせを言葉にする
・最後に、その人が「一番知りたい」と言ったことに、占いとしての見立てをやさしく一つ渡す。小さな一歩の提案は一つまで
・絵文字は使わない
・結果を保証しない。不安をあおらない。続けるべき・やめるべきを断定しない（決めるのは本人）"""

AFTER_SYSTEM = HEARING_SYSTEM.split("出力は次の形")[0] + "今はヒアリングが終わったあと。相手のメッセージを受け止める返事を40〜100字で書く。質問はしない。JSONではなく本文だけを書く。"


def _history_text(user_id: str) -> str:
    lines = []
    for c in store.recent_chats(user_id, limit=12):
        who = "相手" if c.get("role") == "user" else "かすみ"
        lines.append(f"{who}：{str(c.get('text', ''))[:300]}")
    return "\n".join(lines)


def _context(user: dict, incoming: str) -> str:
    rel = texts.EN_TYPES.get(user.get("en_type", ""), {}).get("name", "")
    return (f"この人の縁タイプ：{rel or '未診断'}\n"
            f"診断で選んだ、いま一番気になっていること：{user.get('worry') or '未回答'}\n\n"
            f"これまでのやりとり（古い順）：\n{_history_text(user['user_id'])}\n\n"
            f"相手の最新のメッセージ：{incoming}\n")


_TRIES = 2   # 英単語が混ざったら、もう一度だけ作り直す（だめなら呼び出し元が予備の文面にする）


def _ensure_question_mark(q: str) -> str:
    q = q.rstrip("。 ")
    return q if not q or q[-1] in "？?" else q + "？"


def _no_foreign(prompt: str, text: str) -> None:
    """相手が書いていない英単語（例：relationship）が混ざっていたら作り直させる。"""
    w = foreign_word(prompt, text)
    if w:
        raise ValueError(f"英単語が混ざった: {w}")


def _generate(system: str, user: dict, incoming: str, focus: str | None) -> str:
    """ヒアリング後の返事（本文だけ）。"""
    prompt = _context(user, incoming)
    last: Exception | None = None
    for _ in range(_TRIES):
        try:
            out = _clean(complete(system, prompt, model=MODEL, max_tokens=600, temperature=0.8)).strip("「」\"' \n")
            if not out or len(out) > 260:
                raise ValueError(f"生成の長さが範囲外: {len(out)}")
            _no_foreign(prompt, out)
            return out
        except Exception as e:
            last = e
    raise last


def _generate_turn(user: dict, incoming: str, focus: str | None) -> tuple[str, str]:
    """ヒアリングの1往復ぶん：(受け止め, 質問)。"""
    prompt = _context(user, incoming) + f"\n次に聞くこと：{focus or 'なし'}"
    last: Exception | None = None
    for _ in range(_TRIES):
        try:
            raw = complete(HEARING_SYSTEM, prompt, model=MODEL, max_tokens=600, temperature=0.8)
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {}
            ack = _clean(str(data.get("ack", ""))).strip("「」 \n")
            q = _ensure_question_mark(_clean(str(data.get("question", ""))).strip("「」 \n"))
            if not ack or len(ack) > 160 or len(q) > 80 or (focus and not q):
                raise ValueError(f"生成の形が想定外: ack={len(ack)} q={len(q)}")
            _no_foreign(prompt, ack + q)
            if ack[-1] not in "。！？!?…）)":
                ack += "。"
            return ack, q
        except Exception as e:
            last = e
    raise last


def _final_reading(user: dict, incoming: str) -> str:
    t = texts.EN_TYPES.get(user.get("en_type", ""), texts.EN_TYPES["命"])
    me, oshi = str(user.get("me_birth") or ""), str(user.get("oshi_birth") or "")
    material = (f"縁タイプの読み：{t['reading']}\n疲れやすいところ：{t['tired']}\n"
                + (f"{texts.OSHI_MONTH[int(oshi[5:7])]}\n" if len(oshi) >= 7 else "")
                + (f"{texts.ME_MONTH[int(me[5:7])]}\n" if len(me) >= 7 else ""))
    prompt = material + "\n" + _context(user, incoming)
    last: Exception | None = None
    for _ in range(_TRIES):
        try:
            out = _clean(complete(FINAL_SYSTEM, prompt, model=MODEL, max_tokens=900, temperature=0.8)).strip("「」\"' \n")
            if not out or len(out) > 400:
                raise ValueError(f"ひとことの長さが範囲外: {len(out)}")
            _no_foreign(prompt, out)
            return out
        except Exception as e:
            last = e
    raise last


def _hearing(token: str, user: dict, text: str) -> None:
    uid = user["user_id"]
    turns = int(user.get("turns") or 0) + 1
    focus_list = texts.HEARING_FOCUS_NO_WORRY if user.get("worry") == texts.NO_WORRY else texts.HEARING_FOCUS
    if turns <= len(focus_list):
        f = focus_list[turns - 1]
        try:
            ack, q = _generate_turn(user, text, f["focus"])
        except Exception as e:
            print(f"[kasumi] ヒアリングの生成に失敗（予備の文面で返す）: {e}")
            ack, q = "教えてくれて、ありがとうございます。", f["fallback"]
        msg = f"{ack}\n\n（{turns + 1}/{texts.QUESTION_COUNT}）{f['reason']}\n{q}"
        store.upsert_user(uid, turns=str(turns))
        reply(token, uid, msg)
        return
    # 4つ目の答え → あなたへのひとこと＋終わりのあいさつ。翌日20時から7日間の配信
    try:
        final = _final_reading(user, text)
    except Exception as e:
        print(f"[kasumi] ひとことの生成に失敗（予備の文面で返す）: {e}")
        t = texts.EN_TYPES.get(user.get("en_type", ""), texts.EN_TYPES["命"])
        final = texts.FINAL_FALLBACK.format(tired=t["tired"])
    store.upsert_user(uid, turns=str(turns), stage="done", step_day="1",
                      step_next_at=next_step_time(datetime.now(store.JST)).isoformat(timespec="seconds"))
    reply(token, uid, f"{texts.FINAL_HEAD}\n\n{final}\n\n{texts.CLOSING}")
    store.add_event("closing", uid)


# ---------------- 4日間の配信 ----------------
def next_step_time(after: datetime) -> datetime:
    """after の翌日の STEP_HOUR 時（JST）。"""
    d = (after.astimezone(store.JST) + timedelta(days=1)).date()
    return datetime(d.year, d.month, d.day, STEP_HOUR, 0, tzinfo=store.JST)


def _step_by_key(key: str) -> dict | None:
    return next((s for s in texts.STEPS if s["key"] == key), None)


def send_due_steps(now: datetime | None = None) -> int:
    """配信の時刻が来た人に、その日のお便りを送る。送った件数を返す。"""
    if not enabled():
        return 0
    now = (now or datetime.now(store.JST)).astimezone(store.JST)
    if not (STEP_WINDOW[0] <= now.hour < STEP_WINDOW[1]):
        return 0
    used = store.count_events("push", now.strftime("%Y-%m"))
    sent = 0
    for u in store.list_users():
        day = int(u.get("step_day") or 0)
        if u.get("stage") != "done" or u.get("bot") != "on" or not (1 <= day <= len(texts.STEPS)):
            continue
        try:
            due = datetime.fromisoformat(str(u.get("step_next_at") or ""))
        except ValueError:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=store.JST)
        if due > now:
            continue
        if used + sent >= PUSH_MONTHLY_CAP:
            print(f"[kasumi] 今月の送信が上限（{PUSH_MONTHLY_CAP}通）に達したので、配信を止める")
            _notify_owner(f"今月の送信数が上限（{PUSH_MONTHLY_CAP}通）に達したので、7日間の配信を止めました。")
            break
        step = texts.STEPS[day - 1]
        if push(u["user_id"], step["text"], step["options"]):
            sent += 1
            last = day >= len(texts.STEPS)
            store.upsert_user(u["user_id"], step_day=str(day + 1), step_wait=step["key"],
                              step_next_at="" if last else next_step_time(now).isoformat(timespec="seconds"))
    return sent


# ---------------- 受信 ----------------
def handle_event(ev: dict) -> None:
    uid = (ev.get("source") or {}).get("userId")
    if not uid:
        return
    with _lock(uid):
        _handle(ev, uid)


def _handle(ev: dict, uid: str) -> None:
    kind = ev.get("type")
    token = ev.get("replyToken", "")
    if kind == "follow":
        name = display_name(uid)
        user = store.get_user(uid)
        store.upsert_user(uid, display_name=name, bot="on" if not user or user.get("bot") != "hold" else "hold")
        store.add_event("follow", uid)
        reply(token, uid, texts.GREETING.format(name_prefix=_name_prefix(name)))
        return
    if kind == "unfollow":
        if store.get_user(uid):
            store.upsert_user(uid, bot="off")
        store.add_event("unfollow", uid)
        return
    if kind != "message":
        return
    msg = ev.get("message") or {}
    user = store.get_user(uid) or store.upsert_user(uid, display_name=display_name(uid))
    if msg.get("type") != "text":
        store.add_chat(uid, "user", f"（{msg.get('type')}）")
        if user.get("bot") == "on":
            reply(token, uid, texts.NON_TEXT)
        return
    text = str(msg.get("text") or "").strip()
    store.add_chat(uid, "user", text)
    if user.get("bot") == "off":           # ブロック解除せずに届くことは基本ないが、来たら戻す
        user = store.upsert_user(uid, bot="on")
    if user.get("bot") == "hold":           # 店主が手で対応中
        return

    if any(w in text for w in DANGER_WORDS):
        store.upsert_user(uid, bot="hold")
        store.add_event("danger", uid)
        reply(token, uid, texts.DANGER)
        _notify_owner(f"つらさの強い言葉が届きました。自動の返事を止めています。user_id={uid}")
        return
    if any(w in text for w in DELETE_WORDS):
        store.upsert_user(uid, bot="hold")
        store.add_event("delete_request", uid)
        reply(token, uid, "承知しました。確認して、お預かりしている情報を削除しますね。")
        _notify_owner(f"データ削除の依頼が届きました。user_id={uid}")
        return

    # 4桁の番号
    m = _CODE_RE.match(text.translate(str.maketrans("０１２３４５６７８９", "0123456789")))
    if m:
        row = redeem(m.group(1))
        if not row:
            reply(token, uid, texts.CODE_NOT_FOUND)
            return
        store.mark_diag_used(m.group(1), uid)
        _send_reading(token, user, row["me_birth"], row["oshi_birth"], row.get("state", ""),
                      row.get("worry", ""), row.get("source", ""))
        return

    # 番号なしで、生年月日2つ
    births = find_birthdates(text)
    if len(births) >= 2 and user.get("stage") in ("", "new"):
        _send_reading(token, user, births[0], births[1], "", texts.DEFAULT_WORRY, "line_direct")
        return

    # 7日間の配信のボタンへの答え
    step = _step_by_key(user.get("step_wait", ""))
    if step and text in step["options"]:
        store.add_answer(uid, step["key"], text)
        store.upsert_user(uid, step_wait="")
        reply(token, uid, f"{texts.STEP_THANKS}\n{step['replies'][text]}")
        return

    stage = user.get("stage") or "new"
    if stage == "new":
        reply(token, uid, texts.ASK_BIRTHS)
    elif stage == "hearing":
        _hearing(token, user, text)
    else:
        # ヒアリングが終わったあとの返事は、1人1日1通まで（それ以上は返さない・通知もしない）
        today = datetime.now(store.JST).strftime("%Y-%m-%d")
        if user.get("after_date") == today:
            return
        try:
            if reply(token, uid, _generate(AFTER_SYSTEM, user, text, None)):
                store.upsert_user(uid, after_date=today)
        except Exception as e:
            print(f"[kasumi] 終わったあとの返事の生成に失敗（返さない）: {e}")

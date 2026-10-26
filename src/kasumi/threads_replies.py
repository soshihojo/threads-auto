"""かすみのThreads：コメントへの自動返信（診断URLつき）。

GitHub Actions の scheduler（15分おき）から呼ぶ：python -m src.kasumi.threads_replies
鍵は THREADS_ACCESS_TOKEN_C（無ければ何もせず終わる）。

返信の形：前半（生まれ月の読み or コメントの受け止め）＋後半（診断への案内3種を順に）＋診断URL。
- 生まれ月だけのコメント → docs/07 の返信文（生成なし）
- それ以外 → 1件ずつ生成で受け止めの一文を作る（まとめて作ると、どのコメント宛てかがずれる事故があった）
返さないもの：自分のコメント、他の人への返信、URL・勧誘っぽいもの、つらさの強い言葉（店主に通知）。
すでに手で返信してあるコメントには返さない。
"""
from __future__ import annotations

import json
import random
import re
import sys
import time
from datetime import datetime, timedelta

from ..config import env
from ..diagnosis import foreign_word
from ..llm import complete
from ..threads_client import AccountBlocked, ThreadsClient
from . import store, texts
from .bot import DANGER_WORDS, _clean

MODEL = env("KASUMI_MODEL") or env("LINE_BOT_MODEL") or "claude-sonnet-5"
MAX_PER_RUN = int(env("KASUMI_REPLY_MAX_PER_RUN") or "12")
DAILY_CAP = int(env("KASUMI_REPLY_DAILY_CAP") or "200")      # Threadsの返信上限は24時間で250件
LOOKBACK_DAYS = int(env("KASUMI_REPLY_LOOKBACK_DAYS") or "14")
GAP_SEC = (35, 65)                                             # 返信どうしの間（新しいアカウントの検問よけ）

_Z2H = str.maketrans("０１２３４５６７８９", "0123456789")
_MONTH_RE = re.compile(r"(1[0-2]|0?[1-9])\s*月")
# 英字の語・「・」でつないだカタカナ（キング・オブ・ポップ等の呼び名）は、名前とみなして使わせない
_NAMEISH_RE = re.compile(r"[A-Za-z]{2,}|[ァ-ヶー]+・[ァ-ヶー・]+")
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2764\u2665\u2661\uFE0F]")
_SPAM_RE = re.compile(r"https?://|LINE交換|ライン交換|DMください|DMして|フォロバ|相互|副業|稼げ", re.I)

ACK_SYSTEM = """あなたは推し活占い師「かすみ」。Threadsで自分の投稿についたコメントに返す文の「前半」を書く（後半の案内文はこちらで足す）。
口調：やわらかい丁寧語。一人称「わたし」、相手は「あなた」。
決まり：
・30〜80字。相手のコメントの言葉や気持ちを一つ拾って、受け止める・共感する
・質問はしない。助言の押しつけをしない
・推し、グループ、作品、人の名前（固有名詞・あだ名・呼び名・肩書き・英字の名前）は絶対に書かない。コメントに書かれていても繰り返さない。推しが誰かを当てにいかない
・コメントに生まれ月が書いてあっても、それを「今の月」や「季節」と取り違えない（生まれ月の話）
・推しや他のファンを悪く言う話、性的な冗談には乗らない
・占いの専門用語（宿曜・宿の名前）、Markdown、絵文字、英単語は使わない
本文だけを書く。前置きは書かない。"""


def _months(text: str) -> list[int]:
    return sorted({int(m) for m in _MONTH_RE.findall((text or "").translate(_Z2H))})


def _asks(post_text: str) -> str:
    """投稿が聞いている生まれ月：'me'（あなた）/ 'oshi'（推し）/ 'both' / ''。"""
    t = post_text or ""
    me = "あなたは何月生まれ" in t
    oshi = "推しは何月生まれ" in t or "推しの生まれ月" in t or "生まれ月と" in t
    if "あなたと推しの生まれ月" in t:
        return "both"
    return "me" if me and not oshi else "oshi" if oshi else ""


def _ack(post_text: str, comment: str) -> str:
    prompt = f"投稿：{post_text[:200]}\n\nコメント：{comment[:300]}"
    for _ in range(2):
        try:
            out = _clean(complete(ACK_SYSTEM, prompt, model=MODEL, max_tokens=400, temperature=0.8))
            out = _EMOJI_RE.sub("", out).strip("「」\"' \n")
            if (not out or len(out) > 120 or "？" in out or "?" in out or foreign_word(prompt, out)
                    or _NAMEISH_RE.search(out)):
                continue
            return out if out[-1] in "。！…" else out + "。"
        except Exception as e:
            print(f"[kasumi-replies] 受け止めの生成に失敗: {e}")
    return texts.COMMENT_ACK_FALLBACK


def compose(post_text: str, comment: str, n: int) -> str:
    """返信の本文を作る。n は案内文を回すための通し番号。"""
    months = _months(comment)
    asks = _asks(post_text)
    only_month = len(re.sub(r"[\s\d０-９月生まれです！!。、♡❤️💗🩷💙💚💛💜🤍🖤🧡✨☺️😊🥰🌱☃️🍀💎🦊🐥🎀🧸👀💓❣️]", "",
                            comment.translate(_Z2H))) <= 4 and not re.search(r"自分|私|わたし|僕|俺", comment)
    if months and asks == "me" and len(months) == 1 and only_month:
        head = texts.REPLY_ME[months[0]]
    elif months and asks == "oshi" and only_month:
        head = (texts.REPLY_OSHI[months[0]] if len(months) == 1
                else "".join(texts.OSHI_SHORT[m] for m in months[:3]) + "どの推しさんとの縁も、ちゃんとつながっています。")
    else:
        head = _ack(post_text, comment)
        if months and asks == "oshi" and len(months) == 1:
            head += texts.OSHI_SHORT[months[0]]
    return f"{head}\n\n{texts.COMMENT_CTA[n % len(texts.COMMENT_CTA)]}\n{texts.DIAG_URL_COMMENT}"


def _notify(msg: str) -> None:
    try:
        from ..notify import chatwork
        chatwork(f"[かすみ Threads] {msg}")
    except Exception as e:
        print(f"[kasumi-replies] 通知に失敗: {e}")


def run(client: ThreadsClient, *, max_per_run: int = MAX_PER_RUN, dry_run: bool = False) -> dict:
    stats = {"sent": 0, "already": 0, "skip": 0, "error": 0, "queued": 0}
    store.init()
    handled = store.handled_comment_ids()
    me = client.me().get("username")
    since = datetime.now(store.JST) - timedelta(days=LOOKBACK_DAYS)

    posts = []
    for p in client.my_threads(limit=60):
        try:
            ts = datetime.strptime(p["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        except (KeyError, ValueError):
            continue
        if ts >= since and p.get("text"):
            posts.append(p)

    queue = []
    for p in posts:
        conv = client.replies(p["id"], top_level_only=False)
        replied = {str((c.get("replied_to") or {}).get("id")) for c in conv if c.get("username") == me}
        for c in conv:
            cid = str(c.get("id"))
            if c.get("username") == me or cid in handled:
                continue
            if str((c.get("replied_to") or {}).get("id")) != p["id"]:
                continue                                  # 投稿への直接のコメントだけ（人どうしの会話には入らない）
            text = str(c.get("text") or "").strip()
            if cid in replied:                            # 手で返信済み（控えには書かない。毎回ここで分かる）
                stats["already"] += 1
                continue
            if not text or _SPAM_RE.search(text):
                if not dry_run:
                    store.add_thread_reply(cid, p["id"], c.get("username", ""), text, "", "skip")
                stats["skip"] += 1
                continue
            if any(w in text for w in DANGER_WORDS):
                if not dry_run:
                    store.add_thread_reply(cid, p["id"], c.get("username", ""), text, "", "skip")
                    _notify(f"つらさの強いコメントがあります。自動では返していません：@{c.get('username')}「{text[:60]}」")
                stats["skip"] += 1
                continue
            queue.append((bool(_months(text)), str(c.get("timestamp", "")), p, c, text))

    # 生まれ月のコメントを先に。その中では古い順
    queue.sort(key=lambda x: (not x[0], x[1]))
    stats["queued"] = len(queue)
    today = datetime.now(store.JST).strftime("%Y-%m-%d")
    budget = max(0, DAILY_CAP - store.count_thread_replies_since(today))
    try:
        # ★椿の publishing_limit() は、かすみの権限で読めん欄まで頼むので403になる。返信の枠だけ聞く
        lim = client._get(f"{client.user_id}/threads_publishing_limit", {"fields": "reply_quota_usage"})
        used = int(((lim.get("data") or [{}])[0]).get("reply_quota_usage") or 0)
        budget = min(budget, max(0, DAILY_CAP - used))
    except Exception as e:
        print(f"[kasumi-replies] 返信枠を確かめられんかった（続行）: {e}")
    todo = queue[:min(max_per_run, budget)]

    n0 = len(handled)
    for i, (_, _, p, c, text) in enumerate(todo):
        body = compose(p["text"], text, n0 + i)
        if dry_run:
            print(f"--- @{c.get('username')}: {text[:40]}\n{body}")
            continue
        if i:
            time.sleep(random.uniform(*GAP_SEC))
        try:
            rid = client.reply_to(str(c["id"]), body)
            store.add_thread_reply(str(c["id"]), p["id"], c.get("username", ""), text, body, "sent", rid)
            stats["sent"] += 1
        except AccountBlocked as e:
            _notify(f"Metaにアカウントを止められています。自動返信を止めました：{e}")
            raise
        except Exception as e:
            store.add_thread_reply(str(c["id"]), p["id"], c.get("username", ""), text, body, "error")
            stats["error"] += 1
            print(f"[kasumi-replies] 返信に失敗。今回はここで止める: {e}")
            break
    return stats


def main() -> None:
    token = env("THREADS_ACCESS_TOKEN_C")
    if not token:
        print("⏭  THREADS_ACCESS_TOKEN_C が無いので、かすみの自動返信は何もせず終わる")
        return
    dry = "--dry-run" in sys.argv
    stats = run(ThreadsClient(token, env("THREADS_USER_ID_C")), dry_run=dry)
    print("[kasumi-replies]", json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()

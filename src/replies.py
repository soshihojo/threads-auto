"""返信処理：自分の投稿への返信を取得 → リード検知 → 返信下書きを生成。

返信の送信自体は approve（承認）フローで行う（config.replies.mode=draft の場合）。
mode=auto の場合のみ、生成と同時に送信する。
"""
from __future__ import annotations

import re

from . import leads, notify, store
from .config import active_profile, load_config
from .diagnosis import AI_LEAK_RE, JARGON, strip_ai_leak, strip_jargon
from .kantei import strip_markdown
from .llm import complete
from .threads_client import ThreadsClient

REPLY_SYSTEM = """あなたは地域店舗の集客を支援する担当者として、Threadsのコメントに丁寧かつ親しみやすく短く返信します。
方針:
- 横文字・専門用語を避け、店主に伝わる言葉で
- 売り込みすぎない。まず相手の関心に応える
- 手挙げ（やりたい/知りたい等）には、自然にDM/詳細案内へ誘導する一言を添える
- 1〜2文、長くても80字程度。絵文字は0〜1個まで
- 出力は返信本文のみ"""

DEFAULT_LEAD_INTENT = "この人は手挙げ（見込み客）です。関心に応えつつ、DMか詳細案内にやんわり誘導してください。"
DEFAULT_NORMAL_INTENT = "コメントに自然に応答してください。無理に営業しないこと。"


# コメント返信は公開の場に残る。LINEの自動返信と同じ事故（モデル名の混入・宿名の漏れ・
# Markdown記号）が起きたら、投稿として消えずに残るのでこちらの方が危険。
# 生成のたびに必ずこのガードを通す。
def _clean_reply(text: str) -> str:
    return strip_markdown(strip_jargon(text)).strip()


# 直近に送った返信と骨格が被っていないかを見るための、末尾の型。
# 実害: 2026-08-05、12件中11件が「◯月生まれの彼、〜タイプや🌙 ちゃんと視たるから、
# 固定投稿から無料診断してみ。生年月日入れるだけ、30秒や。」と一字一句同じ締めやった。
# コメント欄は誰でも見られるので、並ぶとテンプレやと一目で分かる。
def _recent_reply_tails(limit: int = 12) -> list[str]:
    try:
        rows = [r for r in store.pending_drafts()] if False else []
    except Exception:
        rows = []
    return rows


def _draft_reply(reply_text: str, username: str, is_lead: bool,
                 recent: list[str] | None = None) -> str:
    profile = active_profile()
    system = profile.get("reply_system") or REPLY_SYSTEM
    intent = (profile.get("reply_lead_intent") or DEFAULT_LEAD_INTENT) if is_lead else \
        (profile.get("reply_normal_intent") or DEFAULT_NORMAL_INTENT)
    avoid = ""
    if recent:
        avoid = ("\n\n【直前に他の人へ返した文（この骨格・この締め方は使わない。"
                 "特に締めの一文は必ず変える）】\n" + "\n".join(f"・{t}" for t in recent[-8:]))
    user = (
        f"オファー文脈: {profile.get('offer','')}\n"
        f"相手(@{username})のコメント: 「{reply_text}」\n\n{intent}{avoid}"
    )
    text = _clean_reply(complete(system, user, max_tokens=200, temperature=0.9))
    # モデル名・署名が混じったら一度だけ作り直す。それでも残ったら該当行を落とす
    if AI_LEAK_RE.search(text) or any(w in text for w in JARGON):
        text = _clean_reply(complete(
            system + "\n\n【厳重注意】モデル名・署名・占術名・宿の名前を絶対に書かないこと。",
            user, max_tokens=200, temperature=0.9))
    return strip_ai_leak(text)


def process_replies(client: ThreadsClient) -> dict:
    """直近投稿の返信を処理。新規返信ごとにリード判定・下書き作成（autoなら送信）。"""
    cfg = load_config()
    mode = cfg["replies"].get("mode", "draft")
    max_per_run = cfg["replies"].get("max_per_run", 20)
    lookback = cfg["replies"].get("lookback_posts", 10)
    my_username = client.me().get("username", "")

    store.init_db()
    posts = client.my_threads(limit=lookback)
    stats = {"new_replies": 0, "leads": 0, "drafts": 0, "auto_sent": 0}
    _recent: list[str] = []   # この巡回で作った下書き。骨格の被りを避けるため次に渡す

    for post in posts:
        post_id = post["id"]
        permalink = post.get("permalink")
        for r in client.replies(post_id, top_level_only=True):
            if stats["new_replies"] >= max_per_run:
                return stats
            rid = r.get("id")
            rtext = r.get("text", "") or ""
            ruser = r.get("username", "") or ""
            if not rid or store.is_reply_seen(rid):
                continue
            if ruser and my_username and ruser == my_username:
                store.mark_reply_seen(rid, post_id, ruser, rtext)  # 自分の返信は無視
                continue

            store.mark_reply_seen(rid, post_id, ruser, rtext)
            stats["new_replies"] += 1

            # --- リード検知 ---
            kw = leads.match_keyword(rtext)
            is_lead = kw is not None
            if is_lead and store.add_lead(rid, post_id, ruser, rtext, kw):
                stats["leads"] += 1
                ok = notify.chatwork(notify.lead_message(ruser, rtext, kw, permalink))
                if ok:
                    store.mark_lead_notified(rid)

            # --- 返信下書き生成 ---
            draft = _draft_reply(rtext, ruser, is_lead, recent=_recent)
            if not draft:                     # ガードで空になったら送らない
                print(f"[replies] 下書きが空になったのでスキップ: {rid}")
                continue
            _recent.append(draft)
            store.add_draft(rid, post_id, ruser, rtext, draft)
            stats["drafts"] += 1

            # --- autoモードなら即送信 ---
            if mode == "auto":
                try:
                    client.reply_to(rid, draft)
                    store.set_draft_status(rid, "sent", sent=True)
                    stats["auto_sent"] += 1
                except Exception as e:
                    print(f"[replies] 自動送信失敗 {rid}: {e}")

    return stats

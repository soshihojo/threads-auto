"""返信処理：自分の投稿への返信を取得 → リード検知 → 返信下書きを生成。

返信の送信自体は approve（承認）フローで行う（config.replies.mode=draft の場合）。
mode=auto の場合のみ、生成と同時に送信する。
"""
from __future__ import annotations

import re
import time

from . import leads, notify, store
from .config import active_profile, load_config
from .diagnosis import AI_LEAK_RE, JARGON, strip_ai_leak, strip_jargon
from .kantei import strip_markdown
from .config import env as _env
from .llm import complete
from .threads_client import ThreadsClient

# コメント返信の生成モデル。
# 「生まれ月から彼の性質を一言」という短い定型の作業で、1回あたり入力700・出力200トークン程度。
# 最上位モデルを当てるほどの仕事やないので、一段下で回す（単価は入力2.5分の1・出力2.5分の1）。
# 指示文が668字とキャッシュの最低量（1,024トークン）に届かんので、こっちは値段で下げる。
REPLY_MODEL = _env("REPLY_MODEL") or "claude-sonnet-5"

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
    """直近に送った返信を持ってくる。次の下書きに「この型は使うな」と渡すため。

    ★2026-08-12：ここが `if False else []` のまま実装されてへんかった。
    そのせいで巡回のたびに記憶がゼロから始まり、毎回同じ骨格
    （「◯月生まれの彼、〜なタイプや。ただ〜は言わん。プロフの入り口から〜」）が
    並び続けとった。コメント欄は誰でも見られるので、並ぶと一目でテンプレと分かる。
    """
    try:
        return store.recent_sent_drafts(limit)
    except Exception as e:
        print(f"[replies] 直近の返信を読めんかった（重複回避なしで続行）: {e}")
        return []


def _draft_reply(reply_text: str, username: str, is_lead: bool,
                 recent: list[str] | None = None) -> str:
    profile = active_profile()
    system = profile.get("reply_system") or REPLY_SYSTEM
    intent = (profile.get("reply_lead_intent") or DEFAULT_LEAD_INTENT) if is_lead else \
        (profile.get("reply_normal_intent") or DEFAULT_NORMAL_INTENT)
    avoid = ""
    if recent:
        avoid = (
            "\n\n【直前に他の人へ返した文】\n" + "\n".join(f"・{t}" for t in recent[-8:])
            + "\n\n★上と同じ型を使わんこと。コメント欄は誰でも見えるから、"
              "同じ骨格が並んだ瞬間にテンプレやとバレる。次の三つを必ず変える：\n"
              "①書き出し（「◯月生まれの彼、」で始めるんは、上に既に有るなら使わん。"
              "読みから入る／相手の言葉を拾う／短い一言で刺す、など別の入り方にする）\n"
              "②引きの作り方（「ただ〜はまだ言わん」「その先は〜」の形が上に有るなら別の作りにする）\n"
              "③締めの一文（同じ言い回しは二度使わん）\n"
              "長さも変える。三行の時もあれば、一行で刺す時もあってええ。"
        )
    user = (
        f"オファー文脈: {profile.get('offer','')}\n"
        f"相手(@{username})のコメント: 「{reply_text}」\n\n{intent}{avoid}"
    )
    text = _clean_reply(complete(system, user, model=REPLY_MODEL,
                                 max_tokens=200, temperature=0.9))
    # モデル名・署名が混じったら一度だけ作り直す。それでも残ったら該当行を落とす
    if AI_LEAK_RE.search(text) or any(w in text for w in JARGON):
        text = _clean_reply(complete(
            system + "\n\n【厳重注意】モデル名・署名・占術名・宿の名前を絶対に書かないこと。",
            user, model=REPLY_MODEL, max_tokens=200, temperature=0.9))
    return strip_ai_leak(text)


# ── バズ回の自己リプライ（2026-08-08・討論の合意施策⑥） ──
# 返信がしきい値を超えた投稿にだけ、椿が1本だけ自己リプライして、
# コメント欄の上部で「まだ並んどる子」を回収する。
# 条件は3つで固定：しきい値超のみ・1投稿1回のみ・着地は本文CTAと同一（プロフィール直行）。
# この条件を緩めて常設化したら多段CTAになる（実データでリード0の最大要因）。緩めないこと。
SELF_REPLY_TEXT = (
    "ようけ来とるな。上から順に見とるで。\n"
    "自分の番まで待てん子は、ウチのプロフの入り口から二人の生年月日入れとき。"
    "そっちの方が深う視れるからな"
)


def _maybe_self_reply(client: ThreadsClient, post_id: str, reply_count: int,
                      threshold: int, stats: dict) -> None:
    """返信がしきい値を超えた投稿に、一度だけ自己リプライを送る。"""
    if threshold <= 0 or reply_count < threshold:
        return
    # 1回の巡回で送るのは最大2本。初回に過去のバズ投稿へ一斉に飛ぶと
    # 連投に見える（BAN対策）。残りは次の巡回（2時間後）が拾う
    if stats.get("self_replies", 0) >= 2:
        return
    marker = f"selfreply_{post_id}"
    if store.is_reply_seen(marker):
        return  # もう送っとる。二本目は絶対に送らん
    try:
        client.reply_to(post_id, SELF_REPLY_TEXT)
        store.mark_reply_seen(marker, post_id, "(self)", SELF_REPLY_TEXT)
        stats["self_replies"] = stats.get("self_replies", 0) + 1
        print(f"[replies] バズ回（返信{reply_count}件）に自己リプライを送った: {post_id}")
    except Exception as e:
        # 失敗しても巡回は止めない。マーカーを付けてへんので次回また試す
        print(f"[replies] 自己リプライの送信失敗（次回再試行）: {e}")


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
    # 骨格の被りを避けるため、前回までに送った返信を種にしてから始める
    _recent: list[str] = _recent_reply_tails()

    gap_sec = int(cfg.get("safety", {}).get("min_seconds_between_actions", 30))
    self_reply_threshold = int(cfg["replies"].get("self_reply_threshold", 30))
    for post in posts:
        post_id = post["id"]
        permalink = post.get("permalink")
        all_replies = list(client.replies(post_id, top_level_only=True))
        _maybe_self_reply(client, post_id, len(all_replies), self_reply_threshold, stats)
        for r in all_replies:
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
            # ★2026-08-14：ここで例外が出ると巡回が丸ごと止まっとった。
            #   実害：8/13 23時台、APIの過負荷（529）で一人分の生成が落ちた瞬間、
            #   その回の残りのコメントが全部処理されずに終わった。
            #   一人の失敗は一人分で済ませる。残りは続ける。
            try:
                draft = _draft_reply(rtext, ruser, is_lead, recent=_recent)
            except Exception as e:
                stats["errors"] = stats.get("errors", 0) + 1
                print(f"[replies] 下書きの生成に失敗（この人は次の巡回に回す）: @{ruser} {e}")
                store.unmark_reply_seen(rid)   # 既読の印を外して、次の回で拾い直す
                continue
            if not draft:                     # ガードで空になったら送らない
                print(f"[replies] 下書きが空になったのでスキップ: {rid}")
                continue
            _recent.append(draft)
            store.add_draft(rid, post_id, ruser, rtext, draft)
            stats["drafts"] += 1

            # --- autoモードなら即送信 ---
            # ★2026-08-12：自動送信に切り替えた。間を空けずに連投すると
            #   凍結の的になるので、必ず待ってから次を送る。
            #   config.yaml の safety.min_seconds_between_actions を実際に使う
            #   （今まで設定だけ書いてあって、コードでは一度も見てへんかった）。
            if mode == "auto":
                if stats["auto_sent"]:
                    time.sleep(gap_sec)
                try:
                    client.reply_to(rid, draft)
                    store.set_draft_status(rid, "sent", sent=True)
                    stats["auto_sent"] += 1
                    print(f"[replies] 自動送信 {stats['auto_sent']}件目: @{ruser}")
                except Exception as e:
                    print(f"[replies] 自動送信失敗 {rid}: {e}")

    return stats

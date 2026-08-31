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


# ★★★2026-08-31：番号の取り違えを、機械で止める。
#
#   投稿本文を渡すようにしたら精度は上がった。★せやけど「上がった」だけや。
#   モデルは読み違える。★実際に一回、「③」に「②選んだあんたやろ」と返して送信済みや。
#   ★★材料を渡すだけやと、また起きる。★せやから【出た文を検査して止める】。
#
#   何を見るか。★相手のコメントが【番号だけ】の時に限って、
#   　返信の中に【ちがう番号】が入っとったら、それは取り違えや。
#   ★★番号だけのコメントは、椿さんの①②③型で必ず出る。そこを狙い撃ちにする。
# ★★★返信の側で選択肢と見なすんは【丸数字だけ】や。裸の数字は見ん。
#   ★一回、裸の数字も拾う作りにして誤作動した。
#     選択肢が「②3ヶ月」やと、正しい返信「3ヶ月かぁ…」の【3】を③と読んでもうて、
#     ★★正しい返信を三回とも捨てた。★元のバグより悪い。
#   ★★返信本文の数字は、たいてい中身（3ヶ月・1ヶ月）や。選択肢の指し示しやない。
#     取り違えが表に出るんは「②選んだあんたやろ」のように【丸数字を書く】時だけや。
_CHOICE_RE = re.compile(r"[①②③④⑤]")
_CIRCLED = {"1": "①", "2": "②", "3": "③", "4": "④", "5": "⑤"}


def _only_choice(text: str) -> str | None:
    """コメントが【番号だけ】なら、その番号（①②③の形）を返す。ちがえば None。"""
    s = re.sub(r"[\s。、.,!！?？\-ー~〜（）()]", "", str(text or ""))
    if len(s) != 1:
        return None
    return _CIRCLED.get(s, s if s in "①②③④⑤" else None)


def _choice_mismatch(reply_text: str, draft: str) -> str | None:
    """返信がちがう番号を指しとったら、その番号を返す。問題なければ None。"""
    want = _only_choice(reply_text)
    if not want:
        return None
    for m in _CHOICE_RE.finditer(draft):
        got = _CIRCLED.get(m.group(0), m.group(0))
        if got in "①②③④⑤" and got != want:
            return got
    return None


def _draft_reply(reply_text: str, username: str, is_lead: bool,
                 recent: list[str] | None = None, post_text: str = "") -> str:
    """コメントへの返信を作る。

    ★★★2026-08-31：post_text（元の投稿の本文）を渡すようにした。理由を残す。
      椿さん（二本目）で「①②③のどれか番号だけコメントして」いう型を使い出した。
      ★そしたら相手のコメントが【「③」】の一文字だけになる。
      ★★元の投稿を渡してへんかったんで、モデルは①②③が何のことか分からんまま書いた。
      ★★★実害：「③」と答えた人に【「②選んだあんたやろ」】と返してもうた（8/31 22:57）。
        投稿の選択肢は ①1ヶ月 ②3ヶ月 ③半年超え。半年超えの人に三ヶ月の話をした。
      ★番号や絵文字だけのコメントは、元の投稿が無いと意味が取れん。必ず渡す。
    """
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
    # ★元の投稿を先に置く。★番号・絵文字だけのコメントは、これが無いと読めん。
    ctx = ""
    if post_text:
        ctx = (f"【このコメントが付いた、椿の投稿】\n{post_text.strip()}\n\n"
               "★相手のコメントは、この投稿への返事や。\n"
               "★★もし投稿に①②③のような選択肢があって、相手が番号だけ書いとるんやったら、"
               "【その番号が指す中身】を投稿から読み取って返すこと。"
               "★番号を取り違えたら、まるきり別の人の話を返すことになる。\n\n")
    user = (
        f"オファー文脈: {profile.get('offer','')}\n\n"
        f"{ctx}"
        f"相手(@{username})のコメント: 「{reply_text}」\n\n{intent}{avoid}"
    )
    text = _clean_reply(complete(system, user, model=REPLY_MODEL,
                                 max_tokens=200, temperature=0.9))
    # モデル名・署名が混じったら一度だけ作り直す。それでも残ったら該当行を落とす
    if AI_LEAK_RE.search(text) or any(w in text for w in JARGON):
        text = _clean_reply(complete(
            system + "\n\n【厳重注意】モデル名・署名・占術名・宿の名前を絶対に書かないこと。",
            user, model=REPLY_MODEL, max_tokens=200, temperature=0.9))

    # ★★★番号の取り違えを止める。★一度だけ作り直して、それでも直らんかったら空で返す。
    #   空で返したら呼び側が「下書きが空」で送信を見送る。★間違うた返信を送るよりましや。
    wrong = _choice_mismatch(reply_text, text)
    if wrong:
        want = _only_choice(reply_text)
        print(f"[replies] 番号の取り違え（相手は{want}やのに{wrong}と書いた）。作り直す")
        text = _clean_reply(complete(
            system + f"\n\n【厳重注意】相手が選んだんは【{want}】や。"
                     f"★★{want}が指す中身だけを読んで返すこと。"
                     f"★他の番号（{wrong}など）には一切触れんこと。"
                     "★★★返信の中に番号そのものを書かんでええ。中身だけ書き。",
            user, model=REPLY_MODEL, max_tokens=200, temperature=0.9))
        if _choice_mismatch(reply_text, text):
            print(f"[replies] 作り直しても取り違えが直らん。★この一件は送らん（次の巡回で拾い直す）")
            return ""
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

    # ★2026-08-14：ここは前まで「投稿を新しい順に見て、上限に達したら即return」やった。
    #   そのせいで、伸びとる最新投稿のコメントだけで毎回の枠を食い潰して、
    #   古い投稿に付いたコメントが永久に拾われんかった。
    #   実害：@soreshota1105 の「8月」は、最新投稿にコメントが付き続けとるせいで
    #   丸一日たっても順番が回ってこんかった（未処理が71件たまっとった）。
    #   まず全部の投稿から未処理を集めて、古い順に並べてから上限ぶんだけ処理する。
    #   待たされとる人から先に返す。これやと誰も置き去りにならん。
    pending: list[tuple[dict, str, str | None]] = []
    post_texts: dict[str, str] = {}      # ★post_id → 本文。返信を作る時に渡す
    for post in posts:
        post_id = post["id"]
        permalink = post.get("permalink")
        post_texts[str(post_id)] = str(post.get("text") or "")
        all_replies = list(client.replies(post_id, top_level_only=True))
        _maybe_self_reply(client, post_id, len(all_replies), self_reply_threshold, stats)
        for r in all_replies:
            rid = r.get("id")
            rtext = r.get("text", "") or ""
            ruser = r.get("username", "") or ""
            if not rid or store.is_reply_seen(rid):
                continue
            if ruser and my_username and ruser == my_username:
                store.mark_reply_seen(rid, post_id, ruser, rtext)  # 自分の返信は無視
                continue
            pending.append((r, post_id, permalink))

    pending.sort(key=lambda x: str(x[0].get("timestamp") or ""))

    # ★2026-08-14：同じ人へ、一回の巡回で二通以上返さんようにする。
    #   実害：@arale_gassie（「5月生まれ」「5月です」）と @elle_and_naomi（「1月です」×2）に、
    #   似た返信が二通ずつ並んだ。同じ人が同じ投稿に二回コメントすると、
    #   コメント単位で処理しとるせいで両方に返してまう。
    #   コメント欄は誰でも見られる。同じ相手に似た文が並んだ瞬間、
    #   「同じ形が二回来た＝機械や」と一目で分かる。返信の骨格を毎回変えとる意味が消える。
    #   二通目以降は既読の印を付けんまま残して、次の巡回に回す
    #   （※次の巡回でも同じ人が先頭に来たら、また一通だけ返る）。
    seen_users: set[str] = set()
    queue: list[tuple[dict, str, str | None]] = []
    skipped_dup = 0
    for r, post_id, permalink in pending:
        u = (r.get("username") or "").strip().lower()
        if u and u in seen_users:
            skipped_dup += 1
            continue
        if u:
            seen_users.add(u)
        queue.append((r, post_id, permalink))
        if len(queue) >= max_per_run:
            break

    rest = len(pending) - len(queue) - skipped_dup
    if rest > 0 or skipped_dup:
        print(f"[replies] 未処理 {len(pending)}件 → 今回 {len(queue)}件返す"
              f"（同じ人の二通目以降 {skipped_dup}件は次の巡回、残り {max(0, rest)}件）")

    for r, post_id, permalink in queue:
        rid = r.get("id")
        rtext = r.get("text", "") or ""
        ruser = r.get("username", "") or ""

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
            draft = _draft_reply(rtext, ruser, is_lead, recent=_recent,
                                 post_text=post_texts.get(str(post_id), ""))
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
                # ★★★2026-08-29：ここで既読の印を外す。
                #   ★前は、送信に失敗しても processed_replies の印が残ったままやった。
                #     ★★せやから次の巡回で拾い直されん。★永久に取り残される。
                #   ★実害：8/12・8/14×2・8/23 の四件が pending のまま半月放置されとった。
                #     下書きは出来とるのに、誰にも届いてへん。
                #   ★★下書きの生成に失敗した時は unmark しとる（上の except）。
                #     ★送信の失敗だけ、なんでか外してへんかった。★同じ扱いにする。
                print(f"[replies] 自動送信失敗（既読を外して次の巡回で拾い直す） {rid}: {e}")
                try:
                    store.unmark_reply_seen(rid)
                except Exception as e2:
                    print(f"[replies] 既読の解除にも失敗（この一件は手で見る）: {e2}")

    return stats

"""「週の一手」——月詠みの会員へ、毎週こっちから届ける一通。

★2026-10-07 新設。月詠みを「相談し放題」から【週の一手＋通数上限】へ改めるための本体。

なんでこれが要るか（実測）:
  ・会員の相談は中央値19通／月。10通を上限にすると【72%】が上限に当たり、
    その10通目が届く日は【中央値12日】。★月の前半で窓が閉まる人が4割強おる。
  ・★★上限だけ入れたら、残り三週間が空白になる。「7日目に見捨てられる商品」になる。
  ・退会した4人のうち、ema shimotsumaさん364通・なつみさん473通。
    ★一番たくさん使った人が辞めとる。★★相談の量は満足を作ってへん。
  ・riyoさんは90通やり取りして6週間黙り、そのまま解約。★使わんようになって消える型。
  ★★★せやから【聞かれてから答える】のをやめて、【こっちから届く】を柱にする。
     これが上の層との差の芯でもある（funnel/premium_products_spec.md）。

この紙がやらんこと:
  ・保証（連絡が来る／もう来ん、どっちも断定せん）
  ・宿曜の用語（strip_jargon で落とす）
  ・時間帯の挨拶・読む人の「今」の決めつけ（find_time_assumptions で止める）
  ・Markdownの装飾（顧客に出す文やから）
  ・相談窓口・専門機関の案内
  ・★★★前の週と同じ中身の焼き直し（_dup_warn で機械的に止める）
"""
from __future__ import annotations

import difflib
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .diagnosis import (
    add_honorific,
    find_time_assumptions,
    honmei_shuku,
    now_context,
    soften_rude,
    strip_jargon,
)
from .llm import complete

JST = ZoneInfo("Asia/Tokyo")

# 控えに積む時の月ラベル。consult_counts は month=="相談" だけ数えるんで、
# ★週の一手は相談の通数を食わん。ここは意図や。
MONTH_PREFIX = "週の一手"

MIN_CHARS, MAX_CHARS = 700, 1400
# 前の週との被り。★ここを緩めると「毎週おなじ話」になって解約に直結する
DUP_LIMIT = 0.55


WEEKLY_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。月額会員に、毎週月曜に届ける「週の一手」を書く。

これは相談の返信ではない。相手は何も聞いてきていない。こちらから届ける一通である。
だから「相談ありがとう」「よう話してくれたな」のような、問いに答える形の入りはしない。

声のルール:
- 一人称「ウチ」、相手は「あんた」。関西弁・タメ口（〜や／〜やで／〜してみ／〜しいや）
- 慰めの嘘は言わん。本音をズバッと。ただし突き放さず受け止める（厳しさ7・愛3）
- ダッシュ（——）は1通に1回まで
- 相談者の呼び名は、指定があればそれに従う。彼の呼び名には敬称を足さない

構成（この順で、全体800〜1,200字・プレーンテキスト）:
1. 今の盤面（300〜450字）
   この一週間で何が動いて、何が動かんかったか。材料に書いてあることだけで言う。
   動きが無かった週は「動かんかった」とはっきり書く。動いたように見せかけない。
2. 今週やる一手（250〜400字）
   今週の七日のあいだに、相手が実際にできることを一つだけ。二つ以上並べない。
   「待つ」も一手として渡してよい。ただし何をして待つのかまで書く。
3. 今週やったらあかん一手（200〜350字）
   その人が今いちばんやってしまいそうなことを一つ。理由も一緒に。

厳守:
- 結果の保証をしない。「必ず連絡が来る」も「もう来ん」も書かない。相手の気持ちを断定しない
- 材料に無い出来事を作らない。時期が書いてない出来事を「今週」「この数日」の中に並べない
- 相手が書き写した言葉だけを彼の発言として扱う。椿の読みを彼の言葉のように書かない
- 占いの専門用語を本文に出さない
- 「おはよう」「こんな時間に」「今夜は」のような、読む時刻を決めつけた言葉を書かない
- Markdownの記号（**、#、-、|）を使わない。箇条書きの記号も使わない
- 相談窓口や専門機関を案内しない
- 前の週に書いたことの焼き直しをしない。同じ見立てを繰り返すなら、この一週間で何が変わったかを必ず足す
- 商品名・価格・決済リンクを書かない
"""


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", str(t or ""))


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def last_monday(today: date | None = None) -> date:
    """その週の月曜。月曜に走らせたら、その日が返る。"""
    d = today or datetime.now(JST).date()
    return d - timedelta(days=d.weekday())


def month_label(d: date | None = None) -> str:
    """その週のラベル。★渡した日付は必ず【その週の月曜】に寄せる。

    ★ここを寄せんと、水曜に走らせた時だけラベルが変わって already_done が効かん。
      同じ週に二通届く事故になる。
    """
    return f"{MONTH_PREFIX} {last_monday(d).isoformat()}"


def already_done(rows: list[dict], d: date | None = None) -> bool:
    """その週ぶんが、もう控えに積んであるか。★二重に作って二通送らんため。"""
    want = month_label(d)
    return any(str(r.get("month") or "") == want for r in rows or [])


def previous_weeklies(rows: list[dict], limit: int = 3) -> list[dict]:
    """過去の週の一手を、新しい順で返す。"""
    out = [r for r in (rows or []) if str(r.get("month") or "").startswith(MONTH_PREFIX)]
    out.sort(key=lambda r: str(r.get("month") or ""), reverse=True)
    return out[:limit]


def _dup_warn(body: str, prevs: list[dict]) -> str | None:
    """前の週との被りを機械で見る。

    ★ここが無いと「毎週おなじ話」が静かに続く。
      生成の側は毎回ちゃんと書いたつもりでも、材料が動いてへん週は同じ結論に戻る。
    """
    worst = (0.0, "")
    for p in prevs:
        s = _sim(body, p.get("reading"))
        if s > worst[0]:
            worst = (s, str(p.get("month") or ""))
    if worst[0] >= DUP_LIMIT:
        return (f"前の週（{worst[1]}）と {worst[0]*100:.0f}% 被っとる。"
                "同じ見立てを繰り返すなら、この一週間で何が変わったかを必ず書き分けること")
    return None


def inspect_weekly(body: str, prevs: list[dict]) -> list[str]:
    """出来上がった一通を機械で検品する。直す指示の一覧を返す。"""
    bad: list[str] = []
    n = len(_norm(body))
    if n < MIN_CHARS:
        bad.append(f"短すぎる（{n}字）。800〜1,200字で書くこと")
    if n > MAX_CHARS:
        bad.append(f"長すぎる（{n}字）。800〜1,200字に収めること")
    if (t := find_time_assumptions(body)):
        bad.append(f"時間帯を決めつけた言葉（{'/'.join(t)}）を使うな。"
                   "書く時刻と読まれる時刻はちがう。時間に触れずに書き直すこと")
    if re.search(r"\*\*|(^|\n)#|(^|\n)[-*]\s|\|", body):
        bad.append("Markdownの記号が混ざっとる。プレーンテキストで書くこと")
    for w in ("必ず連絡", "絶対に戻", "必ず戻", "きっと連絡が来", "間違いなく戻"):
        if w in body:
            bad.append(f"保証の言い方（{w}）を書くな。結果は断定せん")
    for w in ("相談窓口", "専門機関", "カウンセリングを受け", "ホットライン"):
        if w in body:
            bad.append(f"{w}の案内を書くな。よそへ回す形にはせん")
    if re.search(r"https?://|stores\.jp|stripe\.com", body):
        bad.append("リンクや商品の案内を書くな。これは売り物の紹介やない")
    # ★LINEで届く一通や。差出人は見たら分かる。末尾に「椿」だけの行を置かん
    if re.search(r"(^|\n)\s*椿\s*$", body):
        bad.append("末尾に署名の行（椿）を置くな。LINEで届くんやから差出人は分かる")
    if (d := _dup_warn(body, prevs)):
        bad.append(d)
    return bad


def generate_weekly(*, me_birth: str, him_birth: str, nickname: str, note: str = "",
                    kantei: str = "", chats: str = "", prevs: list[dict] | None = None,
                    today: date | None = None) -> dict:
    """一人ぶんの「週の一手」を作る。

    chats は直近一週間のLINEのやりとり（日時つき・古い順）。
    prevs は過去の週の一手（新しい順）。被りを避けるために渡す。
    返り値: {body, label, warns}
    """
    prevs = prevs or []
    mon = last_monday(today)
    me_s = honmei_shuku(me_birth)
    him_s = honmei_shuku(him_birth)

    user = (
        f"月額会員「{nickname}」へ、今週ぶんの『週の一手』を書いてください。\n\n"
        f"【今の日時】{now_context()}\n"
        f"【この一通が届く週】{mon.isoformat()}（月曜）からの七日間\n"
        "★これは相談の返信やない。本人は何も聞いてきてへん。こっちから届ける一通や。\n\n"
        + (f"【この会員についての決まりごと】{note.strip()}\n\n" if note.strip() else "")
        + (
            "【この一週間のLINEのやりとり（古い順・各行の頭が日時）】\n"
            "★ここに動きが無かったら、無かったとはっきり書く。動いたように見せかけたらあかん。\n"
            "★出来事が【いつ】のことかは、書いてある時だけ言う。\n"
            "★★出来事は【どの連絡経路の話か】まで見て扱う。LINEとDMとアプリを混ぜたらあかん。\n"
            f"{chats.strip()}\n\n" if chats.strip() else
            "【この一週間のLINEのやりとり】\n"
            "★一通も無い。★★せやから『動きが無かった週』として書く。\n"
            "　この沈黙をどう過ごすかの一手を渡す。無い出来事を作って埋めたらあかん。\n\n"
        )
        + (
            "【この会員に納品済みの個別鑑定書（全文）】\n"
            "★ここで伝えた性質の読み・時期・処方箋と矛盾させん。続きとして一貫させること。\n"
            "★『あの長文』『あの時』のように会員が指す言い方をしたら、まずここから探して結び付ける。\n"
            f"{kantei.strip()}\n\n" if kantei.strip() else ""
        )
        + (
            "【前の週に、あんたが書いた『週の一手』（新しい順）】\n"
            "★★★ここと同じ中身を書いたらあかん。会員は毎週これを読む。\n"
            "　同じ見立てに戻るんやったら、この一週間で何が変わったかを必ず書き分ける。\n"
            + "\n\n".join(f"◆{p.get('month')}\n{str(p.get('reading') or '')[:1800]}"
                          for p in prevs) + "\n\n" if prevs else ""
        )
        + "--- 内部参考（宿曜の算出結果。専門用語なので本文に出さず、中身だけ日常語に翻訳） ---\n"
        f"・会員の本命宿: {me_s}\n"
        f"・彼の本命宿: {him_s}\n"
        "----------------------------------------------------------------\n\n"
        "今の盤面、今週やる一手、今週やったらあかん一手。この三つを、この順で書いてください。"
    )

    def _gen(extra: str = "") -> str:
        # ★★★呼び捨てはここで機械的に直す。プロンプトで頼んでも3回に1回漏れる。
        #   ★相談者の名前には必ず「さん」を付ける（店主の方針）。彼の呼び名には足さん。
        #   ★★実測：この検品を入れる前の試作は「ゆきえ、椿や。」で始まっとった。
        #   ★Markdownも落とす。週の一手は毎週届くんで、一回でも装飾が漏れたら目立つ。
        # strip_markdown は kantei にある。★ここで遅延輸入する
        #   （kantei はPDF周りを抱えとるんで、モジュールの頭で引っぱりたない）
        from .kantei import strip_markdown
        return add_honorific(soften_rude(strip_jargon(strip_markdown(
            complete(WEEKLY_SYSTEM + extra, user,
                     max_tokens=2200, temperature=0.9).strip()))), nickname)

    body = _gen()
    warns: list[str] = []
    if (bad := inspect_weekly(body, prevs)):
        print(f"[weekly] 検品に掛かって作り直し（{nickname}）: {[b[:30] for b in bad]}")
        body = _gen("\n\n【厳重注意】" + "。".join(bad) + "。")
        if (bad2 := inspect_weekly(body, prevs)):
            warns = bad2
            print(f"[weekly] 作り直しても残った（{nickname}）: {[b[:30] for b in bad2]}")
    return {"body": body, "label": month_label(mon), "warns": warns}

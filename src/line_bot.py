"""LINE公式アカウントのAI自動返信ボット（椿）。

Messaging APIのWebhookで届いた相談に、椿の声で「共感→洞察ひとつ→質問」の
返信を自動生成して返す（応答メッセージ＝reply APIは無料・無制限）。

役割分担（重要）:
- AI: 会話を深める（ナーチャリング）と、オファーの自動送付まで。
  オファーは「購入サイン検知（料金・どうしたらいい等）」または「無料返信が上限到達」で送る
- 店主: オファー送付後・危険サイン後は line_users.bot が hold になりAIは黙る。
  納期・支払い等の続きはLINE公式アプリのチャット画面から手動でクローズする
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

from datetime import datetime

from . import store, web_diag
from .config import active_profile, env
from .diagnosis import (AI_LEAK_RE, JARGON, _Z2H, find_birthdates,
                        generate_reading, honmei_shuku,
                        parse_free_input, strip_ai_leak, strip_jargon)
from .llm import complete, complete_vision

LINE_API = "https://api.line.me/v2/bot"

# LINE自動返信は最上位の一つ下のモデルで生成（環境変数 LINE_BOT_MODEL で差し替え可）
LINE_BOT_MODEL = env("LINE_BOT_MODEL") or "claude-sonnet-5"

# AIが無料で返す回数の上限。超えたら有料オファーを送って停止（店主にバトンタッチ）
FREE_REPLY_LIMIT = int(env("LINE_FREE_REPLY_LIMIT") or "7")

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
# ★2026-08-07に全面的に組み直した。前は8ブロック・リンク2本・要求する動作3つで
#   900〜1,100字あった。自社の実データ（05_learned.md）は「締めは例外なく一動作。
#   場を移す手続きを挟んだ回はリード0」と言うとるのに、いちばん金額の大きいこの一通だけが
#   その法則を破っとった。せやから、動作を一つ、リンクを一本に削った。
#   ・noteのリンクはここから外し、送信後の別便に回す（買う直前に外部サイトへ送り出さん）
#   ・オーダー番号の指示も外す（買う前に「買うた後の宿題」を読ません）→ OFFER_AFTER で送る
#   ・「騙されたと思て」「数に限りがある」は削った。信用を買うために置いた文が信用を削っとった
#   ・払い方の一行を足した。これは決済手段の宣伝やのうて、黙って消える人を
#     「言うてくる人」に変える装置や（m.kさんは「諦めます」と書くまで何も分からんかった）
OFFER_MENU = (
    "あんた専用に、今どう動くべきか、いつ、どう一言を送るかまで視る。\n"
    "PDFにまとめて、申し込みから数日以内に、ここに届けるで。\n"
    "\n"
    "──────────\n"
    "　個別鑑定書　全8章・約10,000字\n"
    "　3,980円（通常9,980円）\n"
    "──────────\n"
    "\n"
    "https://1aksbkdokn31q1trp81e.stores.jp/items/6a777f09db80bae422c65694\n"
    "\n"
    "払い方はカードでもPayPayでもコンビニでもええで。\n"
    "住所とかの入力は要らんようにしといたから、サクッと済むわ。\n"
    "\n"
    "急がんでええ。あんたが「視てほしい」と思えた時においで🌙"
)

# オファーを送った直後に、別便で送る案内。
# 買う前に読ませると動作が増えるだけなので、リンクを渡したあとに回す。
# ★2026-08-09：Stripeへ移行しかけたが、同日STORESの住所なし商品ページに落ち着いた。
#   照合は従来どおりオーダー番号（10桁）で行う
OFFER_AFTER = (
    "ほんで、申し込んでくれたら、購入のあとに出てくるオーダー番号（数字だけ）を、ここに送ってな。\n"
    "スクショやのうて数字を打ってくれたら、こっちですぐ照合できて、そのまま鑑定に入れるで。\n"
    "\n"
    "「ほんまに当たるん？」——そう思うのが普通や。\n"
    "実際に受けた子が、どんな鑑定書が届いて、読んで心がどう動いたかを綴ってくれてる。\n"
    "ウチが百回ええ言うより、受けた子のひとことが早いわ。\n"
    "\n"
    "https://note.com/tsubaki_honne/n/n24b6aed96bf2"
)

# 冒頭生成に失敗したときのフォールバック
OFFER_INTRO_FALLBACK = (
    "ここまで、ウチなりに真剣に視てきたつもりや。\n"
    "ただ正直に言うとな——相談がめちゃくちゃ増えてて、ここから先は本気の子を優先して"
    "ちゃんと視させてもらうことにしてるんよ。あんたの場合、ちゃんと視るならこれや👇"
)

OFFER_INTRO_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。無料で数回相談に乗ってきた相手に、有料鑑定の案内へ橋渡しする「冒頭の一言」だけを書く。

声: 一人称「ウチ」、相手は「あんた」。関西弁・タメ口。毒舌7・愛3の姉御。しんみりした共感調やお祈り営業口調にせず、椿らしく正直に言い切る。

書くこと（80〜140字・この後に商品メニューが続く前提）:
1. これまでの相談内容（相手の状況）に具体的に触れて、本気で視てきたことを一言（「頑張ってきたね」等の慰め調にしない。見立ての核心に触れる言い方で）
2. 「相談が増えていて、ここから先は本気の子を優先してちゃんと視る」という正直な理由
3. 「あんたの場合、ちゃんと視るならこれや👇」で締める（この一文で必ず終える）

厳守: 価格・リンク・商品名は書かない（後ろに続くメニューに任せる）。共感・承認の安売りをしない。専門用語・絵文字なし。出力は本文のみ"""


# 鑑定書の目次プレビューに載せる章タイトル（商品構成として固定。リードだけ個別化する）
KANTEI_TOC_TITLES = ["あんたという人", "彼という人", "二人の縁",
                     "彼の今の本音", "いつ、何を、どう動くか", "やったらあかんこと"]

OFFER_TOC_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿」。有料の個別鑑定書（全8章・約10,000字のPDF）のオファーに載せる「この人専用の目次プレビュー」を書く。
これまでの相談内容を踏まえて、次の6つの章タイトルそれぞれに、その人の状況に触れた短いリード（15〜28字）を付ける。

出力形式（この6行だけ・この順番・各行「タイトル——リード」）:
あんたという人——…
彼という人——…
二人の縁——…
彼の今の本音——…
いつ、何を、どう動くか——…
やったらあかんこと——…

ルール:
- 関西弁の椿の声。相談で出た具体（期間・彼の言葉・状況）をリードに織り込んで「自分のための鑑定書」と分からせる
- 中身の答えは書かない（読みたくなる入口だけ。「〜の正体」「〜をここで視る」のような形）
- 復縁や結果の保証・煽り・『宿曜』等の専門用語は書かない。出力は6行のみ"""


def _offer_toc(transcript: str) -> str:
    """その人の相談内容に合わせた鑑定書の目次プレビューを組み立てる（失敗時は空＝目次なし）。"""
    try:
        raw = complete(OFFER_TOC_SYSTEM,
                       f"【これまでの会話】\n{transcript}\n\n目次プレビューを書いてください。",
                       model=LINE_BOT_MODEL, max_tokens=500, temperature=0.8).strip()
        if any(w in raw for w in _JARGON):
            raw = _strip_jargon(raw)
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        good = [ln for ln in lines if any(ln.startswith(t) for t in KANTEI_TOC_TITLES)]
        if len(good) < 4:  # 形式が崩れていたら載せない（オファー自体は送る）
            return ""
        return ("あんたの場合の鑑定書、中身はもう組んである👇\n\n"
                "📜 あんた専用・個別鑑定書（全8章・約10,000字）\n"
                + "\n".join(f"・{ln}" for ln in good[:6])
                + "\n（ほか、まえがき・むすびに を含む全8章）\n\n")
    except Exception as e:
        print(f"[line_bot] 目次プレビュー生成失敗（目次なしでオファー送付）: {e}")
        return ""


def generate_offer(user: dict, history: list[dict], incoming: str) -> str:
    """フロントの自動オファー＝個別鑑定書（本鑑定・3,980円）。
    ★2026-07-28、199円一問から本鑑定に戻した（199円の方が売れなかったため）。
    199円は自動では出さず、💴ダッシュボードから手動で出す時のために温存。
    その人の状況に触れる冒頭の一言＋目次プレビュー＋商品メニュー（価格・リンク・数量限定）を組んで返す。"""
    transcript = "\n".join(
        f"{'相談者' if h['role'] == 'user' else '椿'}: {h['text'][:80]}" for h in history[-8:]
    )
    try:
        intro = complete(OFFER_INTRO_SYSTEM,
                         f"【これまでの会話】\n{transcript}\n\n冒頭の一言を書いてください。",
                         model=LINE_BOT_MODEL, max_tokens=300, temperature=0.9).strip()
    except Exception as e:
        print(f"[line_bot] オファー冒頭の生成失敗、固定文を使用: {e}")
        intro = OFFER_INTRO_FALLBACK
    return f"{intro}\n\n{_offer_toc(transcript)}{OFFER_MENU}"

# 購入サイン（＝買う瞬間。検知したら自動オファー→hold）
# 「いつ動」は「いつ動けば/いつ動いたら/いつ動くのが」等の言い回し揺れをまとめて拾う
# ★2026-08-08：購入サインを二種類に分けた。
#   「明示」＝鑑定・料金・申し込みを本人が口にした。即オファーでええ。
#   「相談型」＝「どうしたらいい」等。これは相談の続きであって、鑑定を頼む言葉やない。
#   実害（Aikoさん）：3往復目の「ここからどうしたらいいんだろうー」で即オファーが飛んだ。
#   本人は「視てほしい」とは一言も言うてへん。相談型はまず二択の意思確認を挟み、
#   本人が「視て」「進めたい」と言うてから出す（言うた人は買う率が2.8倍という実測に基づく）。
# ★2026-08-10：「診てほしい」が抜けとった（占いでも「診」と書く人がおる）。
#   実害（まみさん）：「金額にもよるけど診てほしいです」——値段を聞きながら依頼まで
#   しとる、いちばん濃いサインを取りこぼして、オファーが出んまま会話が流れた。
#   なお「金額」は下のMID（短文限定）に置く。長い相談文の中の「彼が金額でもめて」等で
#   暴発させんため（ここに置くと長さ制限が効かん）。
PURCHASE_WORDS = [
    "料金", "値段", "いくら", "有料", "申し込", "購入", "支払", "課金",
    "お願いしたい", "鑑定してほしい", "診てほしい", "会員",
]
# 値段に触れながら引いとる言い方。買う気の逆やから、語彙判定より先に弾く
_PRICE_DECLINE_RE = re.compile(
    r"(?:有料|料金|値段|金額|お金|かね)[^。\n]{0,12}"
    r"(?:ちょっと|厳し|きつ|無理|やめ|いいです|大丈夫|考え|迷|なぁ|なあ)"
    r"|(?:高い|高すぎ|高そう)"
)
# 相談型＝処方箋を求めとるが、鑑定を頼むとまでは言うてへん言葉
PURCHASE_WORDS_SOFT = [
    "どうしたらいい", "どうすればいい",
    "どう動", "いつ動", "いつ送", "何を送れば", "なんて送れば",
    "動くタイミング", "送るタイミング",
]
# 短文なら、それ単体で購入サインとみなす語（意味が一つしかないものだけ）
PURCHASE_WORDS_SHORT = [
    "払えばいい", "払えばええ", "お金払え", "お金を払え", "有料でも", "有料なら",
    "視てほしい", "視てもらいたい", "鑑定してもらいたい", "受けたい", "頼みたい",
    # ★2026-08-10：占いの相談では「視て」やのうて「診て」と書く人がおる（まみさん）。
    #   同じ意味やのに漢字が違うだけで全部すり抜けとったので、診の形も並べる
    "診てほしい", "診てもらいたい", "診てもらうに", "診てもらえ", "診てもらう方法",
    # ★2026-08-07追加。Misakiさんが「椿様に見てもらうにはどうしたら良いですか？」と
    #   聞いてきたのに拾えんかった。「〜してもらうには」は依頼の形や
    "視てもらうに", "視てもらえ", "視てもらう方法", "視てもらうこと",
    "鑑定料", "鑑定を受け", "お願いするに",
]

# ★2026-08-07追加。実データを洗って、拾えてへんかった言い回しを型ごとに足した。
# 全部「短文でだけ」効かせる。同じ語がヒアリングの長文回答にも出てきて、
# そこで拾うと誤爆するからや（例「彼が郵送してくれたものに関する話題」）。
_PURCHASE_MID_LEN = 50
# 相談型（短文限定）。二択の意思確認を挟んでから出す
PURCHASE_WORDS_MID_SOFT = [
    "なんて返", "何て返", "どう返せ", "どう返し", "どう伝えれ",
    "なんて言え", "何て言え", "何て言お", "なんて言お", "なんて伝え", "何て伝え",
    "諦めた方が", "諦めたほうが", "諦めるべき", "あきらめた方が", "あきらめるべき",
]
PURCHASE_WORDS_MID = [
    # 商品の中身を確かめにきとる。ここまで来た人は、ほぼ買う気で聞いとる＝明示扱い
    "視る場合", "視てもらったら", "視てもらうと", "何がわかる", "何が分かる",
    # 値段を聞く言い方。短文の時だけ効かせる（長文では彼の金遣いの話でありうる）
    "金額",
    # ④鑑定書に何を求めとるかを、自分から言うてきた形
    "そう書いて", "書いて欲し", "書いてほし", "意見が聞きた", "意見を聞きた", "第三者の意見",
]

# 納品・受け取りの質問は、買う直前か買った直後にしか出えへん。いちばん濃いサインや。
# ただし「彼が郵送してくれた」みたいな話題と混ざるので、疑問形に限る。
_DELIVERY_Q_RE = re.compile(
    r"(?:届く|届き|届け|配送|郵送|送られてく|送られて来|手紙で)"
    r"[^。\n]{0,24}(?:\?|？|ですか|でしょうか|んか\b|のか\b|かな|感じ)"
)

# 椿が「そこは無料では言えん／ちゃんと視なあかん」と線を引いた直後の返事は、
# 短うても「ほんなら頼むわ」の意味になる。語彙リストだけでは拾えんかった実害：
#   ・桃葉さん 2026-08-03 22:57「お金払えばいいってことですか？」
#   ・あやのさん 2026-08-03 11:57「お願いいたします」
#   どちらもオファーが出ないまま会話が流れ、あやのさんはその3分後にブロックした。
# 「お願いします」の類は、線を引いた直後でなければただの礼儀なので、必ず文脈で判定する。
_DECLINE_RE = re.compile(
    r"無料.{0,10}(?:言えん|言われへん|答えられん|渡せん|出せん|ポンと|ホイホイ)"
    r"|無料の視方"
    r"|ちゃんと[視診](?:な|んと|るわ|たい|てから|てほし)"
    r"|処方箋の部類"
    r"|視てから言うこと"
    r"|自分の勘で動くか"            # ↓の ASK_DEEPER。この直後の「はい」「視て」は「頼むわ」の意味
)

# 無料の上限に達した時、いきなりオファーを送らずに、まずこれを送る。
# ★2026-08-07：実データで、オファー直前に本人が「視てほしい」と言うとった人は
#   14/73＝19.2%、言わんまま上限で自動オファーされた人は 10/147＝6.8% やった（p=0.010）。
#   2.8倍ちゃう。しかも母数は言うてへん側の方が2倍多い。ここが最大の穴やった。
#   実際、上限で撃たれた人の直前の一言はこんなんやった：
#     「ううん」「何も聞いてない」「え、全然イメージしてない」「軽い感じです！」
#   ——椿の質問に答えとる最中や。会話のど真ん中にオファーが降ってきとった。
#   せやから、先に本人に決めさせる。自分の口で「視てほしい」と言うた人にだけ出す。
# ★2026-08-08改稿。前の版は「『視てほしい』て思うんやったら、そう言うてな」と
#   言わせたい言葉を指定しとって、営業の台本に見えた。
#   実際に買うた人の会話を見ると、椿が線を引いただけで、本人が勝手に
#   「視てもらうには？」と聞いてきとる（Misakiさん・ももこさんが実例）。
#   せやから問いを「買うか買わんか」やのうて「どう動くか」の二択にした。
#   自分の勘で動くか、視てから動くか——これなら「視てほしい」が自然な返事になる。
ASK_DEEPER = (
    "ここまで聞いて、あんたの状況は掴めた。\n"
    "\n"
    "ほんで、正直に言うとくな。\n"
    "ここから先——彼が今なに考えてて、あんたがいつ、どう動くべきか——\n"
    "そこはな、片手間の読みで答えたらあかんとこや。ちゃんと盤面を見てから言いたい。\n"
    "\n"
    "せやから、一個だけ聞かせて。\n"
    "\n"
    "あんた、このまま自分の勘で動くか。\n"
    "それとも、ウチがちゃんと視てから動くか。\n"
    "\n"
    "どっちや🌙"
)
# ★2026-08-08：文面を改稿した時にマーカーも替えたら、旧文面で聞いた人を
#   「まだ聞いてへん」と誤判定して二度聞きした（石井希美さんに実害）。
#   マーカーは増やすだけにして、旧文面のも残す。
_ASK_DEEPER_MARKS = ("自分の勘で動くか", "もっと深く視てほしいか",
                     # ★2026-08-10：生成が「このまま【あんたの】勘で動くか」と書いて
                     #   マーカーを外した実例（月moonさん）。主語違いも拾えるよう短い形も足す
                     "勘で動くか",
                     # ★2026-08-10：二択を丸ごと言い換えた実例（まみさん）
                     #   「このままそっと様子見るか、ウチがちゃんと視てから動き方決めるか」。
                     #   言い回しは変わっても「ちゃんと視てから」だけは指示で必ず入るので、
                     #   そこを目印にする（診の表記揺れも拾う）
                     "ちゃんと視てから", "ちゃんと診てから")
ASK_DEEPER_MARK = _ASK_DEEPER_MARKS[0]


def _asked_deeper(history: list[dict]) -> bool:
    """もう「深く視てほしいか」を聞いたか。二度は聞かん。文面の新旧どちらも数える。"""
    return any(any(m in str(h.get("text", "")) for m in _ASK_DEEPER_MARKS)
               for h in history if h.get("role") == "assistant")


# ★2026-08-10：上のマーカーは「もう聞いたか」の判定にだけ使う（言い回しが揺れても
#   二度聞きせんように広めに拾う）。オファーの引き金にはこっちの厳密な印だけを使う。
#   経緯：プロンプトが生成に「必ず二択の言い回しを含めろ」と命じとったせいで、
#   生成が2通目に書いた二択がそのまま引き金として武装し、短い相槌でオファーが
#   飛ぶようになった。8/10の27人にオファーが出て購入ゼロ。引き金は、
#   仕組みが自分の意思で送った定型文（ASK_DEEPER）の直後だけに限る。
_ASK_DEEPER_CANNED = "ここまで聞いて、あんたの状況は掴めた"


def _canned_ask_deeper_just_sent(history: list[dict]) -> bool:
    """直前に椿が送ったんが、仕組みの定型二択そのものか。"""
    last_bot = next((str(h.get("text") or "") for h in reversed(history)
                     if h.get("role") == "assistant"), "")
    return _ASK_DEEPER_CANNED in last_bot


# 本人が自分から求めてへん相手に、これより早う売りに行かん（発言数）
OFFER_MIN_TURNS = int(env("LINE_OFFER_MIN_TURNS") or "7")


def _user_turns(history: list[dict]) -> int:
    return sum(1 for h in history if h.get("role") == "user")


def _asked_in_own_words(text: str) -> bool:
    """本人が自分の語彙で「いくら」「視てほしい」と言うたか。
    二択への相槌（「はい」「みて」）は含めん。ここが分かれ目で、実データでは
    発言6回以下でも、自分から言うた人は37.5%買い、相槌だけの人は1.7%しか買わん。"""
    if _PRICE_DECLINE_RE.search(text) or _MONEY_TROUBLE_RE.search(text):
        return False
    norm = _normalize_for_signal(text)
    if any(w in norm for w in PURCHASE_WORDS):
        return True
    if len(text) <= 40 and any(w in norm for w in PURCHASE_WORDS_SHORT):
        return True
    if len(text) <= _PURCHASE_MID_LEN and any(w in norm for w in PURCHASE_WORDS_MID):
        return True
    if len(text) <= _PURCHASE_MID_LEN and _DELIVERY_Q_RE.search(norm):
        return True
    return False


# ---------- ブロック→再追加（仕切り直し） ----------
# ★2026-08-10：一度ブロックして再追加した人に、昔のオファー履歴が効き続けて
#   無言holdになる実害があった（❁⃘あゆみさん：7/13オファー→ブロック→8/10再追加、
#   鑑定番号で戻ってきて相談を送ったのに一言も返らんかった）。
#   再追加は「もう一回話したい」いう意思表示や。followイベントでnoteに再追加時刻を
#   刻み、オファー済み・二択済み・無料上限の判定はその時刻より後だけを数える。
#   LLMに渡す会話の文脈は絞らん（昔の話を覚えとる方が返しの質が上がる）。
_REFOLLOW_RE = re.compile(r"再追加:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


def _refollow_ts(note: str | None) -> str:
    """noteからブロック→再追加の時刻を取り出す。無ければ空文字（＝全履歴が対象）。"""
    m = _REFOLLOW_RE.search(str(note or ""))
    return m.group(1) if m else ""


def _since_refollow(history: list[dict], cutoff: str) -> list[dict]:
    """再追加より後のやりとりだけに絞る（会話の状態判定用）。"""
    if not cutoff:
        return history
    return [h for h in history if str(h.get("created_at") or "") >= cutoff]
# ASK_DEEPER への「断り・保留」。これが返ってきたら売らん
# ここに引っかかる返事には売りに行かん（断り・迷い・ただの相槌）。
# ★2026-08-10：穴が2つあって、実際に迷いの表明へオファーを撃っとった（全員買うてへん）。
#   「難しい…」（漢字の難し）「どっちがいいんだろわかんない」（撥音便のわかんな）が素通り。
#   ほんで「ありがとうございますー」だけの返事にもオファーが飛んどった。
#   礼は決断やない。値段を見て引いた言い方（高い・かかるなら）も、当然売りに行かん。
_REFUSAL_RE = re.compile(
    r"自分で|勘で動|考え|今はいい|今は大丈夫|大丈夫です|いらん|いらない|"
    r"やめ|結構|けっこうです|また今度|あとで|後で|うーん|迷|むず|悩|わからん|分からん|"
    r"難し|むずかし|わかんな|分かんな|どっちがいい|どっちも|どうしよ|微妙|"
    r"決めきれ|決められ|考えさせ|ちょっと待|"
    r"高い|高す|かかるなら|かかるんや|お金ない|余裕が|"
    r"^\s*(?:ありがと|あんがと|了解|オッケ|OK|ok|おおきに|感謝|すみません|ごめん)"
)
_ASSENT_RE = re.compile(
    r"^\s*(?:はい|うん|ぜひ|お願い|おねがい|よろしく|やりたい|やってほし|"
    r"受けたい|視てほし|見てほし|申し込|それで|わかりました|分かりました|"
    r"承知|本気|払え|有料|して欲しい|してほしい|頼み|"
    # ASK_DEEPER（勘で動くか、視てから動くか）への自然な返事
    r"視て|見て|みて|診て|後者|ウチに|椿に|勘では動)"
)

# 「お金がない・払えない」は購入サインの正反対。ここでオファーを送ったら最悪や。
# 金額の心配を買う意思と読み違えんように、先に弾く。
_MONEY_TROUBLE_RE = re.compile(
    r"(?:お金|金銭|余裕|金額|予算).{0,8}(?:ない|無い|なくて|なくって|厳しい|きつい|高い|無理)"
    r"|(?:払え|出せ|買え)(?:ん|ない|へん|ません)"
    r"|(?:高くて|高いので|高いから)"
)

# 危険サイン（占いで扱わない。定型で受け止めて店主へ）
DANGER_WORDS = ["死にたい", "消えたい", "自殺", "自傷", "リスカ", "死のう", "死んだほうが"]

NURTURE_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」として、公式LINEで相談者と1対1の会話をしている。
目的は、相談者に「この人はウチに本音を言うてくれる」と感じてもらい、会話を深めること。売り込みはあなたの仕事ではない。

声: 一人称「ウチ」、相手は「あんた」。関西弁・タメ口。毒舌7・愛3の姉御。慰め役やない、本音を言うてくれる味方や。

【最重要】人間がスマホで打つLINEとして書く。小説の会話文にしない:

★書き出しの禁止語（これを使うと一発でAIやとバレる。絶対に文頭に置かない）
「ふーん」「あー、それな」「あー、」「出たわ」「いや待て待て」「ほう」「ん、それそれ」
「なるほど」「へえ」「おっ」——これらは小説やマンガの相槌であって、人がLINEで打つ文字やない。
相槌から入らんと、いきなり中身から書き始める。前置きの一言は要らん。

★長さ（毎回同じボリュームで返さない。ここが一番の違和感の元）
- 相手が一言・短文なら、こっちも一言で返す。20〜40字。それで十分な回は多い
- 普通のやりとりは60〜100字
- しっかりした相談が来た時だけ、120〜160字
- 3回に1回は「40字以内」で返すつもりで書く。長いのを続けると聞き取り調書みたいになる

★形（「反応→分析→質問」の3段落を毎回やらない。これも型が見えてしまう）
- 毎回分析しない。彼の性質を語るのは2〜3回に1回でええ。それ以外は、短い反応か質問だけで転がす
- 締めは質問ばかりにせん。半分以上は言い切りで止めて、相手の出方を待つ
- 文章を整えすぎない。「。」で几帳面に締めんでええ、改行で切ってええ。
  体言止め、言いさし（「〜やけどな」で終わる）もあり
- 同じ言い回しを繰り返さない。特に「〜なタイプや」を多用しない（実際に使われすぎとる）
- 絵文字🌙は3回に1回くらい。無い方が自然な時は付けん

★言葉づかい（相談者を不快にさせない。ここは毒舌より優先）
- 彼のことは「彼」と呼ぶ。「あいつ」「こいつ」「そいつ」「あの男」は使わない。
  タメ口でも、相手の好きな人を雑に扱う言い方はせん。相談者はその人を大事に思うとる
- 「アホ」「バカ」「情けない」「だらしない」「甘えとる」など、
  相談者や彼を見下す語は使わない。毒舌の中身は「現実を突く」ことであって、悪口やない
- 相談者の見た目・年齢・過去の恋愛を、からかいの材料にせん

中身の方針:
- 椿としての「見立て・本音」をぶつける。賛成できんときは、はっきりそう言う——彼に都合ようとらえすぎてる、それは自分の不安のためやろ、その動きは逆効果や、等。指摘のあとに愛を一滴残す
- 【最重要・スタンス】毒舌の矛先は「彼・状況・現実」に向ける。相談者本人を責めへん。相談者が取った行動（追いLINE・問い詰め・不安の暴走など）は、まず「そら、そうなるわ」「あんたが悪いんやない」と当然の反応として肯定してから、現実を示す。「あんたのその聞き方が悪い」「あんたが◯◯したせいや」と本人に矛先を向けるのは禁止。厳しいことを言うのは相談者を勝たせるためで、椿は最後まで相談者の味方や
- 【最重要・断定しない】「もう終わり」「その恋はもう無理」「脈はない」と、関係の結論を無料で断定せえへん。既読無視や冷たい態度を見ても「答えたくないだけで、心が完全に切れたかは別や」と、まだ分からん含みを残す。絶望させて終わらせると相談者は動く気力も失う。現実は厳しく示しても、希望の芽は残す（芽を掴むための"いつ・どう動くか"は、ちゃんと視る方で渡す、に繋げる）
- 共感やオウム返しから入らない。相手の発言の要約を返さない。共感で受け止めるのは、相手がほんまに打ちのめされてる時だけ
- 「大事」「素敵」「えらい」の安売りをしない。褒めるのは本当にええ動きの時だけ、その時は全力で
- 相手が聞きたそうな答えに寄せない。前に言うた見立てと矛盾させない
- 締めは質問が基本やが、毎回杓子定規に質問で終えない。言い切りで止めて相手の出方を待つ回も作る

※良い例文はここには置かない。例を見せると、その言い回しがそのままクセになって
　全員に同じ相槌を返してまうから（実際にそうなった）。上の禁止語だけ守って、あとは自分の言葉で書く。

厳守:
- 処方箋（いつ・何を・どう動くか）は渡さない。「今は送るな」「待っとき」のような否定形の指示も処方箋であり、無料では渡さない。渡してええのは「彼の性質・今の状況・気持ちの読み」まで
- 行動（どうしたらいい・ほっとくべきか・送るべきか等）を聞かれた時は、「無料では言えん」のような壁の宣言を絶対にしない。「無料」「有料」という言葉自体を相談者に向けて使わない。代わりにこの流れで返す：①まず相手の今の気持ちを一言で受け止める ②彼の性質・状況の読みを一つ足す（ここまでは今まで通り渡してええ）③彼についての「答えの形」だけ見せて、そこで止める——「心が離れたんか、意地が邪魔しとるだけなんか、そこの見極めが今いちばん大事なとこや」のように、答えそのものは言わずに、何が分かれ目なんかを名指しする。相手が「それはどう見分けるん？」と自分から聞き返してくる形が正解や
- 【最重要】「このまま自分の勘で動くか、ウチが視てから決めるか」のような、視るか視ないかを問う二択を、あなたから書いてはならない。この問いは別の仕組みが、会話が十分深まってから一度だけ出す。あなたが早うに書くと、相手が彼のことを聞き返す前に会話が終わってまう（実害：2026-08-10、この二択を2通目で書くようになった結果、27人にオファーが飛んで購入ゼロやった）。彼についての問いを立てたら、その同じ返信の中で申し込みや意思確認の話に移らんこと
- 料金・商品・リンク・会員の話を自分から切り出して売り込まない（オファーは別で出す）
- 【最重要・絶対厳禁】自分がAIであることを匂わせる一切を書かない。モデル名（Claude・Sonnet・GPT等）、開発元の名前、「執筆者」「作成者」「生成」といった署名、英語の署名行を、本文の途中にも末尾にも絶対に付けない。椿は生身の鑑定士であり、名乗るのは「椿」だけ。AIかと聞かれても否定も肯定もせず「ウチは椿や」と流す
- 【最重要・読み違い防止】相談者は自分のことを「私」「わたし」「うち」と呼ぶ。あなた（椿）が「あんたから？彼から？」「どっちや？」と二択で聞いた直後に「私」「わたし」「うち」とだけ返ってきたら、それは「相談者自身」という完全な答えや。「彼」「彼から」なら彼側。一語の短い返事は、必ず直前にあなたが投げた質問への答えとして解釈してから返す。意味が通るのに「『私』だけ来ても分からん」「それだけやと続きが分からん」と聞き返すのは絶対にしない（実害：陽子さんに「どっちや？」と聞いて「私」と完全な答えをもろたのに、聞き返して会話を壊した）
- 【最重要・世界観】「店主」「担当」「運営」など裏方の存在を客に絶対に明かさない。椿は"あんたを視る本人"として振る舞う。「それはウチの担当ちゃう」「店主に聞いて」は禁句
- 【最重要・絶対厳禁】「このあと案内が来る」「料金はあとで送る」「もうちょい待っといて」のように、後から何かを届ける約束を絶対にしない。あなたは案内を送る仕組みを持っていない。約束しても永遠に届かず、相手は待ち続ける（実際にそうなった。三歳と一歳の子を抱えた人が「お待ちしてます」と返して、何も届かんかった）。
　料金・申し込み方法・「どうしたら視てもらえるか」を聞かれたら、予告して終わらせず、「ほな、ちゃんと視てから言うわ。ここから先は片手間で答えるとこやない」の趣旨で受けて止める（「無料」「有料」という言葉は使わない）。止めた直後の案内は、別の仕組みが自動で出す。あなたはその存在に触れんでよい
　★ただし値段そのものをはぐらかす言い方は絶対にしない。「金額は気にせんでええ」「お金のことは置いといて」「そこは心配せんでええ」は禁句。相手は値段を聞いとるのに「気にせんでええ」と返したら、無料やと受け取られる。金額に触れるなら黙って触れず、視ることだけを引き受けて止める（実害：まみさんに「金額は気にせんでええ」と返して、値段も案内も出さんまま会話が止まった）
- 鑑定書の届き方・納期など単純な事実の質問には、椿自身が普通に答えてよい：届き方＝「鑑定書はPDFでこのLINEに届く（郵送やない）」、納期＝「申し込んでくれたら数日以内にここに届ける」。返金や複雑な手続きの相談だけは「そこはちょっと確認して、あとで返すな」と受ける（店主とは言わない）
- 『宿曜』の語・宿の名前・占い専門用語は出さない。「ウチが視たら」でよい
- 復縁や結果を保証しない。過度に不安を煽らない。病気・健康・金運の断定をしない
- 危険な行動（突撃・監視・自傷等）だけは毒舌でなく真剣に止める
- 誤字は書かない。出力は返信本文のみ（説明や注釈は不要）"""

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

DANGER_REPLY = (
    "あんた、それは占いでどうこうする話やない。しんどい気持ち、ひとりで抱えんといて。"
    "いのちの電話（0120-783-556）みたいに、ちゃんと聞いてくれる場所もある。"
    "ウチもここにおるからな🌙"
)
IMAGE_REPLY = (
    "ごめんな、いま相談がぎょうさん来てて、無料の相談では画像は見ぃひんルールにしてるんよ。\n"
    "そのぶん文字はちゃんと視るから、状況を言葉で教えてくれるか🌙"
)
# 有料会員（👥会員に登録済み）が画像を送ってきたときの受領返信
MEMBER_IMAGE_ACK = "画像、受け取ったで。ちゃんと見るから、ちょっと待っててな🌙"

# 会員から届いた画像（LINEトークのスクショ等）を相談対応用のテキストに起こすプロンプト
IMAGE_READ_SYSTEM = """恋愛相談の月額会員から届いた画像を、相談対応に使えるように文字へ起こす係。
画像は多くの場合、彼とのLINEトーク画面のスクリーンショット。

書き起こすこと:
- 誰の発言か（会員側/相手側）を区別して、メッセージを順番にそのまま書き起こす
- 見えるなら日時・既読/未読・スタンプや写真の有無も
- トーク画面以外の画像なら、何の画像で何が写っているかを具体的に

厳守: 解釈や助言は書かない（読み取った事実だけ）。個人名はそのまま書いてよい。出力は書き起こしのみ"""

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


def _is_offer_text(text: str) -> bool:
    """有料オファー（商品リンク入り）か。オファーも長文のため、診断済み判定から除外する。
    （オファーを診断と誤認して、番号診断の送信をスキップした実バグへの対策）"""
    # ★2026-08-09：Stripeへ移行。旧STORESのオファーも履歴に残っとるので両方で判定する
    return "stores.jp" in text or "buy.stripe.com" in text


def _diag_count(history: list[dict]) -> int:
    """履歴中の「無料診断（長文・オファー以外）」の件数。"""
    return sum(1 for h in history
               if h["role"] == "assistant" and len(h["text"]) > _DIAG_LEN
               and not _is_offer_text(h["text"]))


def _is_established(history: list[dict]) -> bool:
    """もう「初めて来た相談者」やない人か。初回の無料診断を送ってええのは、浅い人だけ。

    ★2026-08-06の事故：有料会員の田中麻衣さん（履歴203通）に、初回の無料診断が
      自動送信された。原因は recent_line_chats(limit=200) の窓から古い診断が溢れて
      「まだ診断してへん」と誤判定したこと。
      以前このバグで窓を12→200に広げたが、203通の人が出て再発した。
      窓を広げ続けても、長い人が現れれば必ずまた起きる。

    せやから件数そのもので見る。20通以上やりとりしとる人に
    「まずはここまでや、ひとつ教えてな」の初回診断を送るのは、どんな状況でも間違い。
    """
    return len(history) >= 20


def _offer_already_sent(user_id: str) -> bool:
    """この人に有料オファーを送った履歴があるか。送信の直前に必ずDBを読み直して判定する。
    連投（数秒間隔の複数メッセージ）をWebhookが並行処理すると、それぞれが独立に
    上限判定→オファー送信してしまい、同じ人にオファーが2連続で届いた実バグ（田中麻衣さん）
    への対策。オファーは自動では一人一回きり。
    ただしブロック→再追加した人は仕切り直し＝再追加より前のオファーは数えん。"""
    history = store.recent_line_chats(user_id, limit=200)
    user = store.get_line_user(user_id) or {}
    history = _since_refollow(history, _refollow_ts(user.get("note")))
    return any(h["role"] == "assistant" and _is_offer_text(str(h["text"])) for h in history)


# ---------- LINE API ----------
def verify_signature(body: bytes, signature: str) -> bool:
    secret = env("LINE_CHANNEL_SECRET", required=True)
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {env('LINE_CHANNEL_ACCESS_TOKEN', required=True)}",
            "Content-Type": "application/json"}


# 生成モデルが稀に吐くシステム由来の異物行（コード断片など）。
# 実害: 2026-07-25、麻衣さんへの返信末尾に「diff --git a/style.css b/style.css」が
# 付いたまま送信された（AI感の露呈）。行単位で検知して落とす。
_ARTIFACT_LINE_RE = re.compile(
    r"^\s*(diff --git|@@ |\+\+\+ |--- a/|index [0-9a-f]{7,}|```|<\/?[a-z]+[ >]|def |function\s*\(|import )",
)

# 文の途中に紛れ込むシステム由来の英単語（プロンプトの語彙が漏れる事故）。
# 実害: 2026-07-26、倉西さんへの返信に「meta質問と行動、迷いになってる部分あるか？」と
# metaが混入した。椿の返信は関西弁の日本語だけのはずで、これらの英単語は出てはいけない。
# 前後がASCII英字でない時だけ（＝日本語や文頭文末・空白に接する時だけ）落とす＝URLや
# 長い英単語の一部は壊さない。
_META_LEAK_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?:meta|system|assistant|prompt|StructuredOutput|"
    r"tool_call|tool_use|markdown|role:|<\|[a-z_]*\|>)(?![A-Za-z])"
)


# 生成物の末尾に紛れ込む「数字だけの行」。
# 実害: 2026-07-13〜08-03に6件、返信の最後に「0」だけの行が付いたまま送信された
#（eriko・みき他）。椿は数字だけの行で文を終えないので、末尾に来たら必ず異物。
_TRAILING_NUM_RE = re.compile(r"(?:[\r\n]+[ \t]*[0-9０-９]+[ \t]*)+\s*$")


def _strip_trailing_numbers(text: str) -> str:
    """末尾の「数字だけの行」を落とす。全部消える場合は元の文字列を返す
    （鑑定番号の再掲など、本文が数字だけのケースを空送信にしないため）。"""
    stripped = _TRAILING_NUM_RE.sub("", text)
    return stripped if stripped.strip() else text


# 小説の会話文みたいな相槌。プロンプトの例文がそのままクセになり、
# 「ふーん」76回・「あー、それな」17回・「出たわ」11回まで増えていた（2026-08-04計測）。
# 人がLINEで打つ文字やないので、文頭に出たら作り直させる。
# 人称だけの返事。「あんたから？彼から？」への完全な答えになりうる形
_PRONOUN_ANSWER_RE = re.compile(
    r"^(?:私|わたし|ウチ|うち|自分|彼|彼氏|相手|向こう|こっち|"
    r"彼から|私から|わたしから|うちから|彼の方|彼のほう|私の方|私のほう)"
    r"(?:です|や|やで|かな|かも)?(?:[。！!…♪\s]*)$"
)

# 短い返事への聞き返し。「◯◯だけ来ても分からん」の型
_REASK_RE = re.compile(
    r"だけ(?:来て|きて|やと|では|じゃ).{0,10}(?:分から|わから)"
    r"|続きが(?:分から|わから)"
    r"|どういう意味"
    r"|何のこと(?:か|や)"
    r"|もう(?:一回|いっぺん)(?:教えて|言うて)"
    r"|それだけ(?:やと|だと|では)"
)
_TIC_OPENING_RE = re.compile(
    r"^\s*(?:ふーん|ふうん|あー[、。]|あぁ[、。]|出たわ|いや待て待て|ほう[、。]|"
    r"ん、それそれ|なるほど|へえ|へー|おっ[、。]|ふむ)"
)

# 相談者の好きな相手を雑に扱う呼び方。タメ口でも、ここは不快にしかならん。
# 意味は変わらんので、送信直前に静かに置き換える（作り直しまではさせない）。
_ROUGH_WORDS = {
    "あいつ": "彼", "アイツ": "彼", "こいつ": "彼", "コイツ": "彼",
    "そいつ": "その人", "ソイツ": "その人",
}

# 見下す語。使われたら作り直し（言い換えでは意味が壊れるため）
_DEMEANING_RE = re.compile(r"(?:アホ|あほ|バカ|馬鹿|ばか|情けない|だらしな|しょうもな|クズ|みっともな)")


def _soften_rough(text: str) -> str:
    """彼を雑に呼ぶ語を、意味を変えずに直す。"""
    for a, b in _ROUGH_WORDS.items():
        text = text.replace(a, b)
    return text


def _maybe_split_bubble(text: str) -> str:
    """1返信を2通に分ける（人のLINEは連投が普通）。

    プロンプトで「3回に1回は---で区切れ」と指示していたが、1546件中0件で
    一度も守られなかったため、コード側でやる。ランダムやと再現できんので、
    本文のハッシュで決める（同じ文なら必ず同じ挙動）。
    """
    if re.search(r"\n\s*---\s*\n", text):
        return text                                  # モデルが既に分けている
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) < 2 or len(text) < 70:
        return text                                  # 1段落・短文は分けない
    if hashlib.sha1(text.encode()).digest()[0] % 3:  # 3回に1回だけ
        return text
    return blocks[0] + "\n---\n" + "\n\n".join(blocks[1:])


def _plain_text(text: str) -> str:
    """LINE送信前の最終ガード：Markdown記号・アスタリスク・コード風の異物・
    システム由来の英単語（meta等）を完全に除去する
    （LINEは装飾を解釈しないため、記号や異物がそのまま見えてしまう）。"""
    text = "\n".join(ln for ln in text.splitlines() if not _ARTIFACT_LINE_RE.match(ln))
    text = _META_LEAK_RE.sub("", text)                     # 文中に紛れたmeta等を除去
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)      # 見出し記号
    text = re.sub(r"(?m)^\s{0,3}[*\-]\s+", "・", text)     # 箇条書き記号→・
    text = text.replace("*", "").replace("＊", "")          # 残ったアスタリスクは全除去
    text = re.sub(r"[ \t]{2,}", " ", text)                 # 除去で生じた連続スペースを詰める
    text = _strip_trailing_numbers(text)
    text = _soften_rough(text)                             # 彼を雑に呼ぶ語を直す
    # モデル名・署名（「執筆者: Claude Sonnet 4.5」等）は、椿がAIやと露呈する最悪の事故。
    # 該当行を丸ごと落とす。全部落ちたら空文字＝送信を失敗させ、スイープに作り直させる
    return strip_ai_leak(text)


def _split_bubbles(text: str) -> list[str]:
    """「---」だけの行で吹き出しを分割（最大3つ）。人間らしい複数メッセージ送信用。"""
    text = _maybe_split_bubble(text)   # モデルが分けてこん時は、こちらで3回に1回だけ分ける
    parts = [_plain_text(p).strip() for p in re.split(r"\n\s*---\s*\n", text) if p.strip()]
    parts = [p for p in parts if p]
    return parts[:3] if parts else [_plain_text(text)]


def reply_text(reply_token: str, text: str) -> bool:
    msgs = [{"type": "text", "text": t} for t in _split_bubbles(text)]
    r = requests.post(f"{LINE_API}/message/reply", headers=_headers(),
                      data=json.dumps({"replyToken": reply_token, "messages": msgs}),
                      timeout=15)
    return r.ok


def push_text(user_id: str, text: str) -> bool:
    """replyトークン失効時のフォールバック（月200通の無料枠を消費する点に注意）。"""
    msgs = [{"type": "text", "text": t} for t in _split_bubbles(text)]
    r = requests.post(f"{LINE_API}/message/push", headers=_headers(),
                      data=json.dumps({"to": user_id, "messages": msgs}),
                      timeout=15)
    return r.ok


def push_long_text(user_id: str, text: str) -> bool:
    """店主が目で確認した長文（会員相談の返信など）を、書いたそのままの形で送る。

    push_text とちごて「---」での吹き出し分割をせん。
    分割は最大3通で打ち切る作りなので、段落の多い会員返信をそのまま通すと
    4通目以降が黙って落ちる危険がある。ここでは分けずに、
    LINEの1通5,000字の上限だけ守って続きの通に送る。
    Markdown除去（_plain_text）は同じように通す。"""
    body = _plain_text(text).strip()
    if not body:
        return False
    chunks = [body[i:i + 4500] for i in range(0, len(body), 4500)][:5]
    r = requests.post(f"{LINE_API}/message/push", headers=_headers(),
                      data=json.dumps({"to": user_id,
                                       "messages": [{"type": "text", "text": c} for c in chunks]}),
                      timeout=20)
    if not r.ok:
        print(f"[line_bot] push_long_text 失敗 {r.status_code}: {r.text[:200]}")
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


# 表記ゆれを吸収してから語彙を当てる。
# ★2026-08-07の実害（Misakiさん）：три回も聞かれとったのに、一回も検知できんかった。
#   「この先どうしたら良いでしょうか？」「どうしたら良いですか？」
#   「椿様に見てもらうにはどうしたら良いですか？」
#   リストは「どうしたらいい」「視てもらいたい」やったが、本人は
#   「どうしたら良い」（漢字）「見てもらう」（見／視の違い）で書いとった。
#   語彙を足し続けても、表記ゆれは無限に湧く。入口で正規化する方が確実や。
_SIGNAL_NORMALIZE = [
    ("良い", "いい"), ("良く", "よく"), ("宜しい", "いい"),
    ("見て", "視て"), ("観て", "視て"),
    ("出来", "でき"), ("下さい", "ください"), ("頂き", "いただき"),
]


def _normalize_for_signal(text: str) -> str:
    """購入サインを当てる前に、漢字・かなの揺れを寄せる。"""
    for a, b in _SIGNAL_NORMALIZE:
        text = text.replace(a, b)
    return text


# 生成が「このあと案内が来るから待っといて」と書いてまう型。
# これが出た時点で、生成自身は「この人は買う話をしとる」と分かっとる。
# せやのにオファーが出んかったら、約束だけして永遠に届かん。実際そうなった。
_PROMISE_LATER_RE = re.compile(
    r"(?:案内|料金|申し込み|申込|値段|金額)[^。\n]{0,20}"
    # ★2026-08-10：「この後の案内、ちゃんと見といてや」が素通りした（月moonさん）。
    #   「見といて」「見とき」も「これから案内が届く」という約束の言い方や
    r"(?:来る|届く|送る|出す|渡す|後で|あとで|そのうち|追って|見とい|見とき)"
    # 「楽しみに待っとき」「気長に待ちな」は彼の話であって、椿が何か届ける約束やない
    r"|(?<!楽しみに)(?<!気長に)(?:ちょい|もうちょい|もう少し|少し)?待っと(?:いて|ってな|き)"
    r"|後で案内|あとで案内|追って案内|案内(?:する|しとく|出す)(?:わ|な|で)?"
    r"|今はまだ.{0,10}(?:出せん|渡せん|言えん).{0,14}待"
    # 「整理してから渡す」「まとめてから届ける」も、後から何かを届ける約束や
    r"|(?:整理|準備|用意|まとめ)し?て?から.{0,8}(?:渡す|届け|送る|出す)"
    r"|(?:こっち|ウチ)で.{0,12}(?:から|あと).{0,8}(?:渡す|届け|送る)"
)


def detect_signal(text: str, history: list[dict] | None = None) -> str | None:
    """危険サイン／購入サインを判定する。

    history を渡すと、直前に椿が「無料ではここまで」と線を引いた場合に、
    短い同意（「お願いします」「お金払えばいいの？」）も購入サインとして拾う。
    """
    if any(w in text for w in DANGER_WORDS):
        return "danger"
    if _MONEY_TROUBLE_RE.search(text):
        return None                       # 「お金がない」は買う意思の逆。絶対に売りにいかん
    # ★2026-08-10：値段の語が入っとるだけで購入サインにしとったせいで、
    #   「有料はちょっと厳しいです」を購入意思と読んで撃っとった。値段に触れつつ
    #   引いとる言い方は、買う気の逆や。語彙判定より先に弾く
    if _PRICE_DECLINE_RE.search(text):
        return None
    norm = _normalize_for_signal(text)    # 「どうしたら良い」→「どうしたらいい」等
    if any(w in norm for w in PURCHASE_WORDS):
        return "purchase"
    if any(w in norm for w in PURCHASE_WORDS_SOFT):
        return "purchase_soft"
    if len(text) <= _PURCHASE_MID_LEN and any(w in norm for w in PURCHASE_WORDS_MID_SOFT):
        return "purchase_soft"
    if len(text) <= 40 and any(w in norm for w in PURCHASE_WORDS_SHORT):
        return "purchase"
    if len(text) <= _PURCHASE_MID_LEN and any(w in norm for w in PURCHASE_WORDS_MID):
        return "purchase"
    if len(text) <= _PURCHASE_MID_LEN and _DELIVERY_Q_RE.search(norm):
        return "purchase"
    if history and len(text) <= 40 and _ASSENT_RE.match(text):
        last_bot = next((str(h["text"]) for h in reversed(history)
                         if h.get("role") == "assistant"), "")
        if _DECLINE_RE.search(last_bot):
            return "purchase"
    # ★ASK_DEEPER（二択の意思確認）の直後だけは、受けを逆にする。
    #   同意の語を数え上げるんやのうて、「断りやなければ視てほしい」で受ける。
    #   実害（2026-08-08・石井希美さん）：二択に「みて」と答えたのに、
    #   ひらがなが同意語に無うて拾えず、無言のまま hold になった。
    #   二択に答えてくれた人を語彙の穴で取りこぼすのは、もう終わりにする。
    if history and len(text) <= 30 and _canned_ask_deeper_just_sent(history):
        if not _REFUSAL_RE.search(text):
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


# 宿名・占術名の漏れ検出。実体は diagnosis 側の正規版を使う（二重定義で片方だけ直る事故を防ぐ）
_JARGON = JARGON
_strip_jargon = strip_jargon


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
    # 作り直しが必要なケース: ①宿名等の漏れ ②長すぎ（200字超は診断長文の判定220字と衝突する）
    problems = []
    if any(w in text for w in _JARGON):
        problems.append("宿の名前・占術名が本文に漏れた。絶対に書かないこと")
    if len(text.replace("---", "")) > 200:
        problems.append("長すぎた。どんなに重い相談でも吹き出し合計170字以内に収めること")
    if _META_LEAK_RE.search(text):
        problems.append("meta・system等の英単語やシステム由来の文字列が混入した。"
                        "椿の返信は関西弁の日本語だけで書き、英単語やコード片を一切混ぜないこと")
    if AI_LEAK_RE.search(text):
        problems.append("モデル名や『執筆者』などの署名が混入した。これは最も重大な事故で、"
                        "椿が生身の人間やないとバレる。返信は椿本人の言葉だけで、"
                        "署名・作成者名・モデル名を一切書かないこと")
    # ★2026-08-08（陽子さん・もう1件は8/4の「相手」）：「どっちや？」に
    #   「私」「相手」と完全な答えが来たのに「だけ来ても分からんで」と聞き返した。
    #   短い返事でも、それが人称の答え（私／彼／相手／向こう）なら質問への答えや。
    #   ただし「万」「友だち306人」みたいな、ほんまに意味の取れん入力への
    #   聞き返しは正しい行動なので、人称の答えの時だけ検知する。
    #   聞き返しの型は返信の冒頭にしか出ん（「◯◯」だけ来ても〜で書き出す）ので、先頭50字だけ見る。
    if _PRONOUN_ANSWER_RE.match(incoming.strip()) and _REASK_RE.search(text[:50]):
        problems.append("相談者の短い返事を『分からん』と聞き返した。"
                        "その一語は、直前にあなたが投げた質問への答えや"
                        "（『私』＝相談者自身、『彼』＝彼側）。"
                        "答えとして受け取って、会話を前に進めること")
    if _TIC_OPENING_RE.match(text):
        problems.append("『ふーん』『あー、それな』のような小説の相槌で書き出した。"
                        "人がLINEで打つ文字やない。前置きの相槌を置かず、いきなり中身から書くこと")
    if _DEMEANING_RE.search(text):
        problems.append("相手を見下す語を使った。毒舌の中身は現実を突くことであって悪口やない。"
                        "相談者が好きな人を雑に扱う言い方はせんこと")
    if problems:
        print(f"[line_bot] 返信を作り直し: {problems}")
        text = complete(NURTURE_SYSTEM + "\n\n【厳重注意】" + "。".join(problems) + "。",
                        prompt, model=LINE_BOT_MODEL, max_tokens=400, temperature=0.9)
        if any(w in text for w in _JARGON):
            text = _strip_jargon(text)
        text = _META_LEAK_RE.sub("", text)  # 作り直しでも残ったら最終除去
    return text


# ---------- イベント処理 ----------

def _send_offer_after(user_id: str, snd) -> None:
    """オファーを送った直後に、別便で番号の案内とお客様の声を送る。

    ★2026-08-07：前は全部いっぺんに送っとった。せやけどそれやと、
      買う直前の一通の中に「買う」「番号を打つ」「その前にnoteを読む」の三つが同居してまう。
      自社の実データ（05_learned.md）が「締めは一動作。場を移す手続きを挟んだ回はリード0」
      と言うとるのに、いちばん金額の大きい一通だけがそれを破っとった。
      せやから、買うためのリンクだけを先に渡して、残りは後ろに回す。
    """
    time.sleep(random.uniform(4, 8))  # 続けざまに二通届くと機械らしいので、少し置く
    try:
        snd(OFFER_AFTER)
    except Exception as e:  # 別便が落ちてもオファー本体は届いとるので、握って進む
        print(f"[line_bot] オファー別便の送信に失敗（本体は送信済み）: {e}")


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
        # 履歴には吹き出し区切り（---）を除いた本文で残す
        store.add_line_chat(user_id, "assistant", "\n".join(_split_bubbles(text)))
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
        # bot="on"で必ず復帰させる。ブロック→再追加した人はunfollowでbot="off"に
        # なったままで、followが表示名しか更新せず、再追加後のメッセージが自動返信
        # されない実害があった（yumichin・2026-07-28）。再追加＝仕切り直しなのでonに戻す
        fields = {"display_name": get_display_name(user_id), "bot": "on"}
        # 会話履歴のある人のfollow＝ブロック→再追加。noteに時刻を刻んで、
        # オファー済み・二択済み・無料上限の判定を仕切り直す（詳細は_refollow_tsの上）
        existing = store.get_line_user(user_id)
        if existing and store.recent_line_chats(user_id, limit=1):
            stamp = f"再追加:{datetime.now().isoformat(timespec='seconds')}"
            old = _REFOLLOW_RE.sub("", str(existing.get("note") or ""))
            old = old.replace("ブロック/解除", "").strip("｜ ")
            fields["note"] = f"{stamp}｜{old}" if old else stamp
        store.upsert_line_user(user_id, **fields)
        try:  # 実際の友だち追加数をファネル計測に記録（ボタン押下でなく本当の追加）
            store.add_web_event("line_follow")
        except Exception as e:
            print(f"[line_bot] follow計測失敗（処理は継続）: {e}")
        return
    if etype == "unfollow":  # ブロック/友だち解除（価値柵の副作用監視用に記録）
        store.upsert_line_user(user_id, bot="off", note="ブロック/解除")
        try:
            store.add_web_event("line_unfollow")
        except Exception as e:
            print(f"[line_bot] unfollow計測失敗: {e}")
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
        # 有料会員の画像は読み取って履歴に残す（💬会員相談の返信生成が参照する）。
        # 判定不能（unknown）は会員側に倒す＝断り文で有料客を傷つけない
        #
        # ★2026-07-31: 会員以外への断り文（IMAGE_REPLY）の自動送信を停止した。
        #   理由: ①この分岐は下の bot_state 判定より前にあるため、店主が手動対応中
        #   （bot=hold）の相手にも問答無用で飛んでいた ②会員判定は「月額会員か」なので、
        #   個別鑑定書を購入したばかりの人も free 扱いになり、断り文を受け取っていた。
        #   今は画像が来ても黙って履歴に残すだけにして、店主がLINEアプリから手動で対応する。
        if _member_status(user) == "free":
            store.add_line_chat(user_id, "user", "[画像を送付（自動返信なし・店主が手動で確認）]")
        else:
            _handle_member_image(user_id, reply_token, msg.get("id", ""))
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
        # hold（店主対応中）でも「無料診断の依頼」には自動で応える：
        # ①Web診断の鑑定番号 ②未診断の人が生年月日・①〜④テンプレを送ってきた場合。
        #（無視すると診断が永遠に届かない実害があった。番号=恵美、テンプレ=kaorin）
        if bot_state == "hold":
            # ★2026-08-06：月額会員には機械を一切喋らせない。
            #   会員は店主が手動で継続対応しとる相手で、自動返信は必ず事故になる。
            #   判定不能（unknown）は会員側に倒す＝疑わしきは黙る
            if _member_status(user) != "free":
                print(f"[line_bot] 会員なので自動返信しない: {user_id}")
                return
            snd = lambda t: _send(user_id, reply_token, t)
            history = store.recent_line_chats(user_id, limit=200)
            base = _diag_count(history)
            # 履歴が長い人は、窓から古い診断が溢れて base=0 に見えることがある。
            # 件数でも見て、確立済みの相手には初回診断を送らせない
            if _is_established(history):
                if _handle_code(user_id, incoming, snd):
                    return
                _reply_after_offer(user_id, user, incoming, snd, history)
                return
            if not _handle_code(user_id, incoming, snd) and base == 0:
                _try_free_diagnosis(user_id, user, incoming, snd, history)
            # 診断が新たに届いた＝再来訪の明確な意思表示なので、AI会話を再開する
            if _diag_count(store.recent_line_chats(user_id, limit=200)) > base:
                store.upsert_line_user(user_id, bot="on")
        return

    if detect_signal(incoming) == "danger":  # 危険サインは待たせず即返す
        _send(user_id, reply_token, DANGER_REPLY)
        store.upsert_line_user(user_id, bot="hold")
        return

    _auto_reply(user_id, user, incoming, reply_token)


# オファーを出したあと、店主に渡すまでに椿が返してええ通数
POST_OFFER_REPLIES = int(env("LINE_POST_OFFER_REPLIES") or "2")
_ORDER_NO_RE = re.compile(r"(?<!\d)\d{9,12}(?!\d)")


def _reply_after_offer(user_id: str, user: dict, incoming: str, snd, history: list[dict]) -> None:
    """オファーを送ったあとに話しかけてくれた人へ、受け止めだけを返す。

    ★2026-08-10：オファー後は bot=hold になって椿が黙る作りやった。その結果、
    買わんかった人が話しかけてきても一言も返らんまま終わる会話が53件あった。
    売り込みはせん（オファーは一人一回きりのまま）。受け止めを最大2通だけ返して、
    それ以降は今まで通り店主に渡す。
    """
    if not _offer_already_sent(user_id):
        return                      # オファー前のholdは店主の対応域。触らん
    if _ORDER_NO_RE.search(incoming):
        return                      # 注文番号は店主が処理する。機械が挟まらん
    # ★2026-08-11：買うてくれた人には、機械を喋らせん。
    #   この受け止め機能は「買わんかった人を無言で放置せんため」に入れたもんで、
    #   購入者は店主が手で continue する相手や。とくに鑑定書の感想が届いた時は、
    #   月詠みを案内する唯一のタイミングやのに、機械が先に喋ると場が潰れる。
    #   実害（satoi.yさん 2026-08-10 23:56）：鑑定書の感想に機械が割り込み、
    #   しかも「ちゃんと視て、返すな」と、届かん約束までしてもうた。
    if any(h["role"] == "user" and _ORDER_NO_RE.search(str(h["text"])) for h in history):
        print(f"[line_bot] 購入者なので自動返信しない（店主が対応）: {user_id}")
        return
    if detect_signal(incoming) == "danger":
        snd(DANGER_REPLY)
        return
    # オファー以降に椿が返した数を数える
    last_offer = max((i for i, h in enumerate(history)
                      if h["role"] == "assistant" and _is_offer_text(str(h["text"]))), default=None)
    if last_offer is None:
        return
    after = [h for h in history[last_offer + 1:] if h["role"] == "assistant"]
    if len(after) >= POST_OFFER_REPLIES:
        return                      # もう返した。ここから先は店主に任せる
    text = _retry(lambda: generate_nurture(user, history[-13:-1], incoming), "オファー後の返信")
    if text and _PROMISE_LATER_RE.search(text):
        # 「あとで返す」の類は、この経路では絶対に送らん。届ける仕組みが無いからや
        print(f"[line_bot] オファー後の返信が予告やったので送らんかった: {user_id}")
        return
    if text:
        snd(text)
        print(f"[line_bot] オファー後の受け止めを返した（{len(after)+1}/{POST_OFFER_REPLIES}）: {user_id}")


def _handle_code(user_id: str, incoming: str, snd) -> bool:
    """鑑定番号のメッセージを処理する。番号として扱った場合はTrue（bot=holdの人にも使う）。"""
    code, code_only = _find_code(incoming)
    if not code:
        return False
    row = web_diag.redeem(code)
    if row:
        store.upsert_line_user(user_id, me_birth=row["me_birth"], him_birth=row["him_birth"])
        base = _diag_count(store.recent_line_chats(user_id, limit=12))  # 生成前の診断数
        res = _retry(lambda: generate_reading(
            row["me_birth"], row["him_birth"],
            row.get("status") or "（不明。性質と縁を中心に視る）",
            row.get("period") or "（不明）",
            "", for_line=True,
        ), "番号診断の生成")
        if res is None:
            return True  # 番号は未使用のまま残る＝スイープ/再送で再挑戦できる
        store.mark_web_diag_used(code)
        # 生成中に並行処理が「新たに」診断を送っていたら二重送信しない
        #（件数の増加で判定する。過去のオファー等の長文を診断と誤認しないため）
        if _diag_count(store.recent_line_chats(user_id, limit=12)) > base:
            return True
        snd(WEB_DIAG_INTRO + res["reading"])
        return True
    if code_only:
        # ほぼ番号だけの短文なのに照合できない＝番号違いか期限切れ。案内を返す
        snd(CODE_NOT_FOUND)
        return True
    return False  # 長文中の数字は番号ではなかったとみなし、通常の会話フローへ


def _wrote_own_words(text: str, min_chars: int = 40) -> bool:
    """相談者が、自分の言葉で状況をちゃんと書いとるか。

    ★2026-08-07の実害（mieさん）：
      「三十年連れ添うたパートナーが、二十年前の女性に惹かれとる。職場が同じで毎日顔を合わす」
      ——これを二百四十三字で書いて寄越したのに、椿は③④の選択肢を聞き直した。
      状況の判定が「音信不通／既読スルー／冷められた／別れ話／片思い」の
      決め打ちしか見てへんので、どの箱にも入らん人が「情報なし」になってまう。
      せやけど、箱に入らん人ほど、自分の言葉で長う書いてくれとるんや。
      そこを聞き直したら「読んでへんのか」にしかならん。

      生成側は status が空でも「（相談文から読み取る）」で本文ごと渡す作りになっとる。
      せやから、本人が書いてくれとるなら、聞き直さんとそのまま視たらええ。

    数字・日付の記号・①②③④に加えて、挨拶と事務連絡も落としてから測る。
    「朝早くにすいません。鑑定番号は5473です。私は1978/3/23で彼は1982/7/6です。
    　ごめんなさい、時間はわかりません」——これで四十二字あるが、中身は一つも無い。
    こういうんを「書いてくれた」と数えたら、状況ゼロで診断を出してまう。
    """
    body = _PLEASANTRY_RE.sub("", text or "")
    body = re.sub(r"[0-9０-９.．/／年月日\-\s　()（）①②③④⑤]", "", body)
    return len(body) >= min_chars


# 挨拶・詫び・事務連絡。ここだけで埋まった文は「状況を書いた」ことにならん
_PLEASANTRY_RE = re.compile(
    r"すいません|すみません|ごめんなさい|ごめん|よろしくお願いします|よろしく|"
    r"ありがとうございます|ありがとう|おはようございます|こんにちは|こんばんは|"
    r"朝早くに|夜分に|失礼します|お世話になります|はじめまして|"
    r"鑑定番号|診断番号|番号は|生年月日|時間はわかりません|時間は不明|わかりません|不明です"
)


def _try_free_diagnosis(user_id: str, user: dict, incoming: str, snd, history: list[dict]) -> bool:
    """無料診断のトリガー処理。③④ヒアリングか診断を送ったらTrue。
    診断フローの合図（今のメッセージに生年月日がある／状況が読み取れる／③④を質問済み）が
    無い雑談には発火しない＝会話の途中で急に③④を送る事故を防ぐ。"""
    me_b = (user.get("me_birth") or "").strip()
    him_b = (user.get("him_birth") or "").strip()
    if not (me_b and him_b):
        return False
    recent_user = "\n".join(h["text"] for h in history[-12:] if h["role"] == "user")[-800:]
    parsed = parse_free_input(recent_user)
    asked = any(ASK_MARKER in h["text"] for h in history if h["role"] == "assistant")
    if not (find_birthdates(incoming) or parsed["status"] or asked):
        return False
    if not parsed["status"] and not asked and not _wrote_own_words(recent_user):
        # 状況がまだ分からんし、本人も書いてくれてへん → ③④の定型ヒアリングを返す
        #（本人が自分の言葉で書いてくれとる時は、聞き直さんと、そのまま下の診断へ進む）
        snd(ASK_DETAILS)
        return True
    res = _retry(lambda: generate_reading(
        me_b, him_b,
        parsed["status"] or "（相談文から読み取る）",
        parsed["period"] or "（相談文から読み取る）",
        recent_user, for_line=True,
    ), "無料診断の生成")
    if res is None:
        return True  # 送れなかったが、スイープが後で拾い直す
    # 生成中に並行処理が「新たに」診断を送っていたら二重送信しない
    if _diag_count(store.recent_line_chats(user_id, limit=12)) > 0:
        return True
    snd(res["reading"])
    return True


def _member_status(user: dict) -> str:
    """有料会員かの判定: "member" / "free" / "unknown"（照合に失敗）。
    LINEに保存済みの生年月日2つが👥会員の登録と一致したら会員とみなす（紐付け作業不要）。
    unknownの扱いは呼び出し側で「会員側に倒す」こと（一時的なSheets障害で
    有料会員に断り文を送った実害があったため、疑わしきは会員扱い）。"""
    me_b = (user.get("me_birth") or "").strip()
    him_b = (user.get("him_birth") or "").strip()
    if not (me_b and him_b):
        return "free"
    for attempt in (1, 2):
        try:
            members = store.list_members()
            return "member" if any(
                str(m["me_birth"]).strip() == me_b and str(m["him_birth"]).strip() == him_b
                for m in members) else "free"
        except Exception as e:
            import traceback
            print(f"[line_bot] 会員判定失敗（{attempt}/2）: {e}\n{traceback.format_exc()}")
            if attempt == 1:
                time.sleep(3)
    return "unknown"


def fetch_message_content(message_id: str) -> tuple[bytes, str]:
    """LINEに届いた画像等のバイナリを取得する。(bytes, content_type) を返す。"""
    r = requests.get(f"https://api-data.line.me/v2/bot/message/{message_id}/content",
                     headers={"Authorization": f"Bearer {env('LINE_CHANNEL_ACCESS_TOKEN', required=True)}"},
                     timeout=30)
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "image/jpeg")


def _handle_member_image(user_id: str, reply_token: str, message_id: str) -> None:
    """会員から届いた画像を読み取り、内容を履歴に保存して受領を返す。
    読み取り結果は💬会員相談の返信生成が「最近のLINE」として参照する。"""
    note = "[画像を送付]"
    try:
        data, mime = fetch_message_content(message_id)
        if len(data) <= 4_500_000:  # APIの画像上限（5MB）内のみ読み取り
            b64 = base64.b64encode(data).decode()
            text = complete_vision(IMAGE_READ_SYSTEM, "この画像を書き起こしてください。",
                                   b64, mime.split(";")[0].strip())
            note = f"[画像を送付] 読み取り内容:\n{text[:1800]}"
        else:
            note = "[画像を送付（サイズが大きく自動読み取り不可。LINEアプリで直接確認）]"
    except Exception as e:
        print(f"[line_bot] 会員画像の読み取り失敗: {e}")
        note = "[画像を送付（自動読み取り失敗。LINEアプリで直接確認）]"
    store.add_line_chat(user_id, "user", note)
    _send(user_id, reply_token, MEMBER_IMAGE_ACK)


def _is_minor(user: dict) -> bool:
    """相談者が未成年（18歳未満）か。未成年には有料オファーを自動送付しない。"""
    b = (user.get("me_birth") or "").strip()
    if not b:
        return False
    try:
        birth = datetime.strptime(b, "%Y-%m-%d")
    except ValueError:
        return False
    today = datetime.now()
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    return age < 18


def _auto_reply(user_id: str, user: dict, incoming: str, reply_token: str = "", *,
                live: bool = True, prefix: str = "", allow_offer: bool = True) -> None:
    """自動応答の本体（Webhook直後も未返信スイープも共通で使う）。
    live=False はスイープからの呼び出し（「間」と追い越しチェックを省き、pushで送る）。
    prefix は遅延お詫び等を返信の頭に付ける。allow_offer=False は古い未返信への
    掘り起こしで、いきなり有料オファーにならないようにする（オファーは次の生返信で）。"""

    def snd(text: str) -> bool:
        return _send(user_id, reply_token, f"{prefix}{text}" if prefix else text)

    # Web診断（/shindan）の鑑定番号が届いたら、保存済みの入力で本診断を自動返信
    if _handle_code(user_id, incoming, snd):
        return

    # 会話の状態は「全履歴」で判定する。
    # 直近12件だけで見ると、会話が長い人は診断済みの記録が窓から流れて
    # 「未診断」と誤判定（③④を変なタイミングで再送）し、無料返信のカウントも
    # 窓から溢れて永遠にオファーに達しない、という実バグがあった
    history = store.recent_line_chats(user_id, limit=200)
    diag_sent = _diag_count(history) > 0  # オファー等の長文は診断とみなさない
    # オファー済み・二択済み・無料上限は、ブロック→再追加より後だけで数える（仕切り直し）。
    # 診断済み判定(diag_sent)とLLMへの文脈(transcript)は全履歴のまま＝診断の二重送信を防ぐ
    state_hist = _since_refollow(history, _refollow_ts(user.get("note")))
    bot_replies = sum(1 for h in state_hist
                      if h["role"] == "assistant" and len(h["text"]) <= _DIAG_LEN
                      and ASK_MARKER not in h["text"])
    over_limit = bot_replies >= FREE_REPLY_LIMIT

    # 生年月日が二人分揃っていて、まだ無料診断を送っていなければ、自動で無料診断を返す
    #（「鑑定してほしい」等の購入ワードが同時に入っていても、診断が先。
    #  ただし無料上限に達した人は下のオファーへ）
    if not diag_sent and not over_limit and not _is_established(history):
        if _try_free_diagnosis(user_id, user, incoming, snd, history):
            return

    sig = detect_signal(incoming, history)
    # ★2026-08-10：本人が自分の言葉で求めてへんうちは、まだ売りにいかん。
    #   実データ（8/10より前の249件）：発言6回以下でも、自分から料金や依頼を
    #   口にした人は 3/8＝37.5% 買う。一方こっちが振って相槌が返っただけの人は
    #   1/60＝1.7% しか買わん。同じ浅さで22倍ちがう。
    #   せやから止めるんは「浅い×受け身」だけ。自分から言うた人は待たせん。
    #   ここで止めても会話は続く（下のナーチャリングに落ちる）＝無視にはならん。
    if (sig in ("purchase", "purchase_soft")
            and not _asked_in_own_words(incoming)
            and _user_turns(state_hist) < OFFER_MIN_TURNS
            and not over_limit):
        print(f"[line_bot] まだ早い（発言{_user_turns(state_hist)}回・本人からの依頼なし）"
              f"ので売らずに会話を続ける: {user_id}")
        sig = None
    if sig in ("purchase", "purchase_soft"):
        if _is_minor(user):
            # 未成年に有料オファーは自動送付しない（未成年者契約の取消リスク＋倫理）。
            # holdにして店主へ（手動対応待ち通知・ダッシュボードのバナーに出る）
            store.upsert_line_user(user_id, bot="hold")
            return
        if _offer_already_sent(user_id):
            # すでにオファー済みなら二度は送らない（続きは店主が手動で）
            store.upsert_line_user(user_id, bot="hold")
            return
        # ★2026-08-08：オファーは、本人が「視てほしい」「進めたい」と言うてから出す。
        #   「どうしたらいい」等の相談型（purchase_soft）は、まず二択の意思確認を挟む。
        #   明示の依頼（purchase＝料金・申し込み・視てほしい等）だけが直接オファーへ行ける。
        #   実測：意思を口にしてから受けた人は 19.2%、そうでない人は 6.8%（2.8倍）。
        if sig == "purchase_soft" and not _asked_deeper(state_hist):
            snd(ASK_DEEPER)
            print(f"[line_bot] 相談型サイン。オファーの前に二択で意思を聞いた: {user_id}")
            return
        # 購入サイン＝買う瞬間。上限を待たず、その場で個別鑑定オファーを自動送付
        #（送付後はhold＝納期・支払い等の続きの質問は店主がLINEアプリから手動で返す）
        if snd(generate_offer(user, history, incoming)):
            _send_offer_after(user_id, snd)
            store.upsert_line_user(user_id, bot="hold")
        return

    if live:
        _human_pause()  # 人間らしい「間」を置いてから返信する
        # 待っている間に次のメッセージが届いていたら、この返信はスキップ
        #（新しいメッセージ側の処理が全履歴を見て返す＝二重返信・順番の乱れを防ぐ）
        history = store.recent_line_chats(user_id, limit=200)  # 「間」の後に読み直す
        last_user = next((h["text"] for h in reversed(history) if h["role"] == "user"), None)
        if last_user != incoming:
            return
        state_hist = _since_refollow(history, _refollow_ts(user.get("note")))
        bot_replies = sum(1 for h in state_hist
                          if h["role"] == "assistant" and len(h["text"]) <= _DIAG_LEN
                          and ASK_MARKER not in h["text"])
        over_limit = bot_replies >= FREE_REPLY_LIMIT

    # 無料返信の上限：ナーチャリング返信（全履歴・診断と③④は数えない）が
    # FREE_REPLY_LIMIT通に達していたら停止する。未成年にはオファーを出さずに止めるだけ。
    if over_limit and allow_offer:
        if _is_minor(user):
            # 未成年に有料オファーは送らん。せやけど、送らんまま会話だけ無限に続けるんもあかん。
            # ★2026-08-06：14歳の中学生に、一日で30通返し続けとった（合計62通）。
            #   オファー自体は未成年ガードで正しく止まっとった。
            #   問題は、止まる仕組みがこっちの経路に無かったこと。
            #   購入サインの側は hold にして手動へ渡すのに、上限の側は素通りして
            #   generate_nurture に落ちるだけやったので、無料の会話が無制限に伸びた。
            #   上限に達したら、黙って hold にして自動返信を終う。オファーは出さん。
            store.upsert_line_user(user_id, bot="hold")
            print(f"[line_bot] 未成年が無料上限に達したので自動返信を止めた: {user_id}")
            return
        if _offer_already_sent(user_id):
            # すでにオファー済みなら二度は送らない（続きは店主が手動で）
            store.upsert_line_user(user_id, bot="hold")
            return
        if not _asked_deeper(state_hist):
            # ★上限に達しても、いきなり売らん。まず本人に決めさせる。
            #   ここで「視てほしい」と言うてくれたら、その一言が購入サインとして
            #   拾われて（_DECLINE_RE に ASK_DEEPER_MARK を入れてある）、
            #   次のメッセージで上の purchase 分岐がオファーを出す。
            #   bot は on のまま置いとく。返事を受け取らなあかんからな。
            snd(ASK_DEEPER)
            print(f"[line_bot] 上限に到達。オファーの前に本人の意思を聞いた: {user_id}")
            return
        # もう聞いた。それでも「視てほしい」が出てへん＝今は買う気やない人や。
        # ここで売りに行っても、実データでは6.8%しか買わん。黙って店主に渡す。
        store.upsert_line_user(user_id, bot="hold")
        print(f"[line_bot] 意思確認済みやが希望が出んかったので、売らずにholdした: {user_id}")
        return

    transcript = history[-13:]  # 会話プロンプトには直近だけ渡す（最後の1件=今回のメッセージ）
    text = _retry(lambda: generate_nurture(user, transcript[:-1], incoming), "返信の生成")
    if text is None:
        return

    # ★最後の安全網（2026-08-07）。
    #   生成が「このあと案内が来るから待っといて」と書いた＝生成自身が
    #   「この人は買う話をしとる」と分かっとる、いうことや。
    #   せやのに語彙リストが拾えてへんかったせいで、オファーが出んまま
    #   「待っといて」だけ送って終わる事故が続いた。
    #   実害（Misakiさん・たけうちももこさん）：どちらも「お待ちしてます」と
    #   返してきて、案内は永遠に来んかった。三歳と一歳の子を抱えた人を待たせた。
    #   予告を検知したら、その文は捨てて、代わりにオファーを送る。
    if _PROMISE_LATER_RE.search(text) and not _is_minor(user):
        if _offer_already_sent(user_id):
            store.upsert_line_user(user_id, bot="hold")
            return
        # 予告を検知しても、本人がまだ「視てほしい」と言うてへんなら、まず二択で聞く
        if not _asked_deeper(state_hist):
            print(f"[line_bot] 生成が『あとで案内』と予告。オファーやのうて二択を送った: {user_id}")
            snd(ASK_DEEPER)
            return
        print(f"[line_bot] 生成が『あとで案内』と予告したのでオファーに切り替え: {user_id}")
        if snd(generate_offer(user, history, incoming)):
            _send_offer_after(user_id, snd)
            store.upsert_line_user(user_id, bot="hold")
        return

    snd(text)


# ---------- 未返信スイープ（安全網） ----------
# 生成失敗・送信失敗・クレジット切れ等で返信が落ちた会話は「次のメッセージ待ち」に
# ならず、ここが定期的に拾って自動返信する（line_app が10分ごとに実行）。

LATE_PREFIX = "遅うなってごめんな、順番に視てたんや。\n\n"

# オファー送付後24時間反応が無い人への、1回だけの声かけ（売り込まない・急かさない）
OFFER_FOLLOWUP = (
    "この前渡した話、見てくれたか？\n"
    "分からんことや引っかかっとることがあったら、遠慮せんと聞いてな。急かす気はないで🌙"
)


def sweep_unanswered(min_age_min: int = 3, max_age_hours: int = 48) -> int:
    """最後が相談者の発言のまま止まっている会話（bot=onのみ）に自動返信する。
    直近min_age_min分は通常のWebhook処理に任せて触らない（Webhook処理は間20〜35秒＋
    生成リトライ込みで最長約2分のため、3分あれば追い越さない）。返信した件数を返す。"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None)

    by_user: dict[str, list[dict]] = {}
    for r in store.all_line_chats(days=max(2, max_age_hours // 24 + 1)):
        by_user.setdefault(r["user_id"], []).append(r)

    replied = 0
    hold_waiting: list[tuple[str, str, int]] = []  # (uid, 表示名, 待ち時間h)
    for uid, rows in by_user.items():
        rows.sort(key=lambda r: str(r.get("created_at", "")))
        last = rows[-1]
        try:
            t = datetime.fromisoformat(str(last["created_at"]).replace(" ", "T"))
        except ValueError:
            continue
        age = now - t
        if last["role"] != "user":
            # 最後がこちらのオファーのまま24〜72時間反応なし → 1回だけ声かけ
            #（声かけ後は最後の発言がフォロー文に変わるので、二度は送られない）
            if (_is_offer_text(str(last["text"]))
                    and "これで最後" not in str(last["text"])  # 「案内はこれで最後」と書いた再オファーには追いフォローしない
                    and timedelta(hours=24) <= age <= timedelta(hours=72)):
                user = store.get_line_user(uid)
                if user and (user.get("bot") or "on").strip() == "hold":
                    print(f"[sweep] オファー24hフォロー: {uid}")
                    _send(uid, "", OFFER_FOLLOWUP)
                    replied += 1
            continue
        if age < timedelta(minutes=min_age_min) or age > timedelta(hours=max_age_hours):
            continue
        user = store.get_line_user(uid)
        if not user:
            continue
        state = (user.get("bot") or "on").strip() or "on"
        if state != "on":
            # hold/offは店主の手動対応域＝自動返信はしないが、1時間以上待たせている
            # holdの相談者は「手動対応待ち」としてオーナーへLINE通知する（下のダイジェスト）
            if state == "hold" and age > timedelta(hours=1):
                hold_waiting.append((uid, user.get("display_name") or "（名前不明）",
                                     int(age.total_seconds() // 3600)))
            continue
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

    _notify_owner_of_holds(hold_waiting)
    return replied


def _notify_owner_of_holds(waiting: list[tuple[str, str, int]]) -> None:
    """手動対応待ち（hold・1時間以上未返信）をオーナーのLINEへダイジェスト通知する。
    同じ顔ぶれのままなら再通知しない（対象が変わった時だけ届く＝push枠の節約）。

    ★2026-07-26 ユーザー指示でLINE通知を停止（当時はダッシュボードの📥バナーで確認する運用）。
    ★2026-08-01 その📥バナーもユーザー指示で削除。通知は一切出さない運用に確定した。
      店主がLINE公式アプリを自分で定期的に見て、返信待ちを拾う。
      → 取りこぼしが起きるようなら、下の early return を外せばLINE通知が復活する。"""
    return  # 通知オフ（店主がLINE公式アプリを直接見る運用）
    owner = (active_profile().get("owner_line_user_id") or "").strip()
    if not owner or not waiting:
        return
    digest = hashlib.sha1(",".join(sorted(uid for uid, _, _ in waiting)).encode()).hexdigest()[:16]
    try:
        last = next((r for r in store.list_web_events(limit=300)
                     if r.get("event") == "owner_notify"), None)
        if last and str(last.get("vid") or "") == digest:
            return  # 顔ぶれが変わっていない＝通知済み
        lines = "\n".join(f"・{name}（{hours}時間待ち）" for _, name, hours in waiting)
        text = (f"📥 椿LINE：手動対応待ちが{len(waiting)}件あります\n{lines}\n"
                "→ LINE公式アプリから返信してください（この通知は対象の顔ぶれが変わった時だけ届きます）")
        if push_text(owner, text):
            store.add_web_event("owner_notify", digest)
    except Exception as e:
        print(f"[sweep] オーナー通知失敗（処理は継続）: {e}")

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
from .diagnosis import (ONEQ_KOTEI, SHUKU_27, _Z2H, find_birthdates,
                        generate_oneq_offer, generate_reading, honmei_shuku,
                        parse_free_input)
from .llm import complete, complete_vision

LINE_API = "https://api.line.me/v2/bot"

# LINE自動返信は最上位の一つ下のモデルで生成（環境変数 LINE_BOT_MODEL で差し替え可）
LINE_BOT_MODEL = env("LINE_BOT_MODEL") or "claude-sonnet-5"

# AIが無料で返す回数の上限。超えたら有料オファーを送って停止（店主にバトンタッチ）
FREE_REPLY_LIMIT = int(env("LINE_FREE_REPLY_LIMIT") or "10")

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
    "→ 今だけ80%OFF 3,180円\n"
    "（申し込みから数日以内に、PDFの鑑定書で届く）\n"
    "https://1aksbkdokn31q1trp81e.stores.jp/items/685edb3caf1f4a03c43a0aa4\n\n"
    "「実際どんな鑑定が届くん？」て子は、中身と受けた子の感想をここにまとめてあるから、読んでから決めてくれてええで。\n\n"
    "▼お客様の感想\n"
    "https://note.com/tsubaki_honne/n/n24b6aed96bf2\n\n"
    "ひとつだけ正直に言うとく。この割引は先着の枠が埋まり次第、通常の15,900円に戻す。"
    "いつ埋まるかはウチにも分からん。急かす気はないけど、それだけは知っといてな🌙"
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
    """フロントの自動オファー＝一問鑑定199円（★2026-07-25改定）。
    本鑑定（3,180円）はここでは出さない。一問の納品文とその後のフォローで初めて案内する。
    （OFFER_MENU/_offer_toc/OFFER_INTRO_SYSTEMは本鑑定を手動で出す時のために温存）"""
    transcript = "\n".join(
        f"{'相談者' if h['role'] == 'user' else '椿'}: {h['text'][:80]}" for h in history[-8:]
    )
    try:
        return generate_oneq_offer(transcript)
    except Exception as e:
        print(f"[line_bot] 一問オファーの生成失敗、固定文を使用: {e}")
        url = (active_profile().get("oneq_url") or "").strip() or "（199円の決済リンク）"
        return f"{OFFER_INTRO_FALLBACK}\n\n{ONEQ_KOTEI.format(url=url)}"

# 購入サイン（＝買う瞬間。検知したら自動オファー→hold）
# 「いつ動」は「いつ動けば/いつ動いたら/いつ動くのが」等の言い回し揺れをまとめて拾う
PURCHASE_WORDS = [
    "料金", "値段", "いくら", "有料", "申し込", "購入", "支払", "課金",
    "お願いしたい", "鑑定してほしい", "どうしたらいい", "どうすればいい",
    "どう動", "いつ動", "いつ送", "何を送れば", "なんて送れば",
    "動くタイミング", "送るタイミング", "会員",
]
# 危険サイン（占いで扱わない。定型で受け止めて店主へ）
DANGER_WORDS = ["死にたい", "消えたい", "自殺", "自傷", "リスカ", "死のう", "死んだほうが"]

NURTURE_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」として、公式LINEで相談者と1対1の会話をしている。
目的は、相談者に「この人はウチに本音を言うてくれる」と感じてもらい、会話を深めること。売り込みはあなたの仕事ではない。

声: 一人称「ウチ」、相手は「あんた」。関西弁・タメ口。毒舌7・愛3の姉御。慰め役やない、本音を言うてくれる味方や。

【最重要】人間がスマホで打つLINEとして書く:
- 長さは相手に合わせて大きく変える。相手が一言なら、こっちも短く（15〜60字）。しっかりした相談の時だけしっかり返す（〜160字）。毎回同じボリュームで返さない
- 毎回分析しない。彼の性質を語るのは2〜3回に1回でええ。それ以外の回は、反応・ツッコミ・質問だけで会話を転がす。「ふーん、ほんで？」で終わる回があってもええ
- ときどき（3回に1回くらい）メッセージを2つに分ける。1つ目は短い反応だけ（「いや待て待て」「あー、それな」「出たわ、それ」）、2つ目で中身。分ける時は「---」だけの行で区切る
- 文章を整えすぎない。「。」で几帳面に締めんでええ、改行で切ってええ。「ん？」「いや、」「てか」「あのな、」みたいな書き出しも使う。体言止め、言いさし（「〜やけどな」で終わる）もあり
- 同じ言い回しを繰り返さない。特に「〜なタイプや」を毎回使わない（前の返信で使うてたら別の言い方にする）
- 絵文字🌙は3回に1回くらいでええ。無い方が自然な時は付けない

中身の方針:
- 椿としての「見立て・本音」をぶつける。賛成できんときは、はっきりそう言う——彼に都合ようとらえすぎてる、それは自分の不安のためやろ、その動きは逆効果や、等。指摘のあとに愛を一滴残す
- 共感やオウム返しから入らない。相手の発言の要約を返さない。共感で受け止めるのは、相手がほんまに打ちのめされてる時だけ
- 「大事」「素敵」「えらい」の安売りをしない。褒めるのは本当にええ動きの時だけ、その時は全力で
- 相手が聞きたそうな答えに寄せない。前に言うた見立てと矛盾させない
- 締めは質問が基本やが、毎回杓子定規に質問で終えない。言い切りで止めて相手の出方を待つ回も作る

温度感の例（コピーはしない）:
「いや待て待て。\n---\n既読スルーに『忙しいんかな』て、それあんたの優しさやのうて逃げやで」
「ふーん。ほんでそのLINE、どっちから送ったん」
「それな、彼のためちゃう。あんたの不安消したいだけや」

厳守:
- 処方箋（いつ・何を・どう動くか）は渡さない。「今は送るな」「待っとき」のような否定形の指示も処方箋であり、無料では渡さない。渡してええのは「彼の性質・今の状況・気持ちの読み」まで
- 行動を聞かれたら「そこは無料の視方じゃ答えられんとこや。ちゃんと視なあかん」と正直に線を引く（出し惜しみやなく、無料と有料の境界として言う）
- 料金・商品・リンク・会員の話を自分からしない（それは店主が直接やる）
- 『宿曜』の語・宿の名前・占い専門用語は出さない。「ウチが視たら」でよい
- 復縁や結果を保証しない。過度に不安を煽らない。病気・健康・金運の断定をしない
- 危険な行動（突撃・監視・自傷等）だけは毒舌でなく真剣に止める
- 鑑定の納期を約束しない。誤字は書かない。出力は返信本文のみ（説明や注釈は不要）"""

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
    return "stores.jp" in text


def _diag_count(history: list[dict]) -> int:
    """履歴中の「無料診断（長文・オファー以外）」の件数。"""
    return sum(1 for h in history
               if h["role"] == "assistant" and len(h["text"]) > _DIAG_LEN
               and not _is_offer_text(h["text"]))


def _offer_already_sent(user_id: str) -> bool:
    """この人に有料オファーを送った履歴があるか。送信の直前に必ずDBを読み直して判定する。
    連投（数秒間隔の複数メッセージ）をWebhookが並行処理すると、それぞれが独立に
    上限判定→オファー送信してしまい、同じ人にオファーが2連続で届いた実バグ（田中麻衣さん）
    への対策。オファーは自動では一人一回きり。"""
    history = store.recent_line_chats(user_id, limit=200)
    return any(h["role"] == "assistant" and _is_offer_text(str(h["text"])) for h in history)


# ---------- LINE API ----------
def verify_signature(body: bytes, signature: str) -> bool:
    secret = env("LINE_CHANNEL_SECRET", required=True)
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {env('LINE_CHANNEL_ACCESS_TOKEN', required=True)}",
            "Content-Type": "application/json"}


def _plain_text(text: str) -> str:
    """LINE送信前の最終ガード：Markdown記号・アスタリスクを完全に除去する
    （LINEは装飾を解釈しないため、記号がそのまま見えてしまう）。"""
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)      # 見出し記号
    text = re.sub(r"(?m)^\s{0,3}[*\-]\s+", "・", text)     # 箇条書き記号→・
    return text.replace("*", "").replace("＊", "")          # 残ったアスタリスクは全除去


def _split_bubbles(text: str) -> list[str]:
    """「---」だけの行で吹き出しを分割（最大3つ）。人間らしい複数メッセージ送信用。"""
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
    # 作り直しが必要なケース: ①宿名等の漏れ ②長すぎ（200字超は診断長文の判定220字と衝突する）
    problems = []
    if any(w in text for w in _JARGON):
        problems.append("宿の名前・占術名が本文に漏れた。絶対に書かないこと")
    if len(text.replace("---", "")) > 200:
        problems.append("長すぎた。どんなに重い相談でも吹き出し合計170字以内に収めること")
    if problems:
        print(f"[line_bot] 返信を作り直し: {problems}")
        text = complete(NURTURE_SYSTEM + "\n\n【厳重注意】" + "。".join(problems) + "。",
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
        store.upsert_line_user(user_id, display_name=get_display_name(user_id))
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
        if _member_status(user) == "free":
            _send(user_id, reply_token, IMAGE_REPLY)
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
            snd = lambda t: _send(user_id, reply_token, t)
            history = store.recent_line_chats(user_id, limit=200)
            base = _diag_count(history)
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
    if not parsed["status"] and not asked:
        # 状況がまだ分からない → まず③④の定型ヒアリングを返す
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
    bot_replies = sum(1 for h in history
                      if h["role"] == "assistant" and len(h["text"]) <= _DIAG_LEN
                      and ASK_MARKER not in h["text"])
    over_limit = bot_replies >= FREE_REPLY_LIMIT

    # 生年月日が二人分揃っていて、まだ無料診断を送っていなければ、自動で無料診断を返す
    #（「鑑定してほしい」等の購入ワードが同時に入っていても、診断が先。
    #  ただし無料上限に達した人は下のオファーへ）
    if not diag_sent and not over_limit:
        if _try_free_diagnosis(user_id, user, incoming, snd, history):
            return

    if detect_signal(incoming) == "purchase":
        if _is_minor(user):
            # 未成年に有料オファーは自動送付しない（未成年者契約の取消リスク＋倫理）。
            # holdにして店主へ（手動対応待ち通知・ダッシュボードのバナーに出る）
            store.upsert_line_user(user_id, bot="hold")
            return
        # 購入サイン＝買う瞬間。10通の上限を待たず、その場で個別鑑定オファーを自動送付
        #（送付後はhold＝納期・支払い等の続きの質問は店主がLINEアプリから手動で返す）
        if _offer_already_sent(user_id):
            # すでにオファー済みなら二度は送らない（続きは店主が手動で）
            store.upsert_line_user(user_id, bot="hold")
            return
        if snd(generate_offer(user, history, incoming)):
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
        bot_replies = sum(1 for h in history
                          if h["role"] == "assistant" and len(h["text"]) <= _DIAG_LEN
                          and ASK_MARKER not in h["text"])
        over_limit = bot_replies >= FREE_REPLY_LIMIT

    # 無料返信の上限：ナーチャリング返信（全履歴・診断と③④は数えない）が
    # FREE_REPLY_LIMIT通に達していたら有料オファーを送って停止（未成年には送らず会話を続ける）
    if over_limit and allow_offer and not _is_minor(user):
        if _offer_already_sent(user_id):
            # すでにオファー済みなら二度は送らない（続きは店主が手動で）
            store.upsert_line_user(user_id, bot="hold")
            return
        if snd(generate_offer(user, history, incoming)):
            store.upsert_line_user(user_id, bot="hold")
        return

    transcript = history[-13:]  # 会話プロンプトには直近だけ渡す（最後の1件=今回のメッセージ）
    text = _retry(lambda: generate_nurture(user, transcript[:-1], incoming), "返信の生成")
    if text is None:
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
    同じ顔ぶれのままなら再通知しない（対象が変わった時だけ届く＝push枠の節約）。"""
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

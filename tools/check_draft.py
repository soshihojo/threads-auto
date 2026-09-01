"""顧客に送る文面を、送る前にまとめて検査する。

★★★2026-08-24 新設。作った理由をはっきり書いとく。
  呼び名の「さん」が抜ける事故を、店主から【二回】指摘された（まなかさん・のどかさん）。
  一回目のあと、funnel のルール文書に「名前だけ返ってきたら、さん付けを既定にする」と書いた。
  ★書いた直後の一通で、また抜けた。
  ★★つまり【文書に書く】では止まらん。読み返す保証が無いからや。
  ★★★せやから、機械で止める。文面を作るたび、必ずこれを通す。

使い方:
    .venv/bin/python tools/check_draft.py kantei_out/返信_◯◯.txt --name のどかさん
    （--name は、その人の呼び名を「敬称ごと」渡す。呼び捨てで通しとる人は「絵麻」でええ）
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.diagnosis import soften_rude, strip_ai_leak, strip_jargon  # noqa: E402
from src.line_bot import _split_bubbles  # noqa: E402

# 顧客向けの文面に出たらあかんもん
_NG_WORDS = ("ホットライン", "いのちの電話", "よりそい", "相談窓口", "支援センター")
_MD_RE = re.compile(r"\*\*|^#{1,6}\s|^[-*]\s", re.M)


def check(text: str, name: str = "", allow_plain: bool = False) -> list[str]:
    """見つかった問題を並べて返す（空なら綺麗）。"""
    bad: list[str] = []

    if _MD_RE.search(text):
        bad.append("Markdown記号が混ざっとる（**／#／- ）。LINEもPDFも解釈せんから、そのまま出てまう")

    for stage, fn in (("宿曜用語", strip_jargon), ("雑な呼び方", soften_rude), ("AI臭・署名", strip_ai_leak)):
        if fn(text).strip() != text.strip():
            bad.append(f"{stage}のガードに引っかかった。通したあとの文と食い違う")

    hit = [w for w in _NG_WORDS if w in text]
    if hit:
        bad.append(f"外部の相談窓口を出しとる: {'、'.join(hit)}（ルールで禁止）")

    for ln in text.splitlines():
        if ln.count("【") != ln.count("】"):
            bad.append(f"括弧が閉じてへん: {ln.strip()[:40]}")

    # ★★★2026-08-25：連絡経路の混線を、機械でも見張る。
    #   ゆきえさんの回で「アプリで8月10日が最後」を「LINEで8月10日が最後」として使うた。
    #   ★プロンプトに書くだけでは止まらん（この一ヶ月で何回も証明された）。
    #   ★★完全な検出はできん。せやから【日付＋「最後」の断定】が出た文だけを拾って、
    #     そこに経路の名前が入っとるかを見る。入ってなかったら、目で確かめろと言う。
    _CH = ("LINE", "ライン", "DM", "インスタ", "Instagram", "TikTok", "Twitter",
           "アプリ", "メール", "電話", "YouTube", "ツイキャス", "Facebook")
    _DATE = r"(?:\d{1,2}[月/]\d{1,2}日?|[一二三四五六七八九十]+月[一二三四五六七八九十]+日)"
    # ★引用（カギカッコの中）は原文や。前に自分が書いた間違いを引いて訂正する時もある。
    #   そこを直させたら訂正でけへんようになるんで、引用は外して見る。
    _outside_q = re.sub(r"「[^」]*」", "", text)
    for sent in re.split(r"[。\n]", _outside_q):
        if not re.search(_DATE, sent):
            continue
        # ★誤検知を減らす。ただの「開いて」「十月十四日に画面を開く」までは拾わん。
        #   狙いは【その日を境に、それ以降ずっと〜してへん】いう断定だけや。
        if not re.search(r"(?:を最後に|が最後|最後に[^。]{0,10}(?:開|触|送|来|返))"
                         r"|(?:以降|から)[^。]{0,14}(?:一度も|ずっと|まったく|全く)"
                         r"|(?:以降|から)[^。]{0,10}(?:触って|開いて|来て|返って)[^。]{0,4}(?:へん|ない|ません)", sent):
            continue
        if any(c in sent for c in _CH):
            continue
        bad.append(
            "★日付つきで『最後に〜』と断定しとるのに、どの連絡経路の話か書いてへん: "
            f"「{sent.strip()[:46]}」——LINEか、DMか、アプリか。混ざっとらんか目で確かめること"
        )

    n = len(_split_bubbles(text))
    if n > 3:
        bad.append(f"LINEで{n}通に分かれる。長すぎるかもしれん（3通までが目安）")

    # ★★★ここが本題。呼び名に「さん」が付いてへんかったら、無条件でアウト。
    #   2026-08-24（三回目の指摘）：店主から【呼び捨ては一切なし】に統一する指示が出た。
    #   ★相手が「さやかで呼んで」と呼び捨てで送ってきても、こっちは必ず「さん」を付ける。
    #     本人の書き方を見て判断する、いう余地を残しとったから、三回とも同じことが起きた。
    #     判断が入る限り、また起きる。せやから、判断せん形にする。
    #   （「ちゃん」付けを本人が望んどる時だけは、--name に「◯◯ちゃん」と入れて渡す）
    name = (name or "").strip()
    if name:
        suf = next((s for s in ("さん", "ちゃん", "くん", "様", "君") if name.endswith(s)), "")
        if not suf:
            if not allow_plain:
                bad.append(
                    f"★呼び名「{name}」に敬称が付いてへん。"
                    f"呼び捨ては使わん決まりや。--name は「{name}さん」で渡すこと"
                    "（前から呼び捨てで通してきた人だけは --already-plain を付けて通す）"
                )
            else:
                mixed = re.findall(rf"{re.escape(name)}(?=さん|ちゃん)", text)
                if mixed:
                    bad.append(
                        f"呼び捨てで通す指定やのに「{name}さん」が {len(mixed)}件混ざっとる。どっちかに揃える"
                    )
        else:
            base = name[: -len(suf)]
            # ★2026-08-25：カギカッコの中は、彼や本人が実際に言うた言葉の引用や。
            #   そこは原文どおりが正しい。呼び捨てで引用されとっても直したらあかん
            #   （実例：彼が言うた「のどかが嫌なわけではない」を「のどかさんが」に
            #     直してもうたら、それはもう彼の言葉やのうなる）。
            #   せやから、引用の中だけ外して数える。
            _outside = re.sub(r"「[^」]*」", "", text)
            bare = re.findall(rf"{re.escape(base)}(?!さん|ちゃん|くん|様|君)", _outside)
            if bare:
                bad.append(
                    f"★呼び名が呼び捨てになっとる箇所が {len(bare)}件。"
                    f"「{base}」→「{name}」に直すこと"
                )
    bad += _check_hearing_escapes(text)
    bad += _check_time_greeting(text)
    bad += _check_foreign_junk(text)
    bad += _check_star(text)
    return bad


# ★2026-08-26：ヒアリングの締めに付けとった「逃げ道の一式」を機械で止める。
#   親切のつもりで書いとったが、中身は全部こっちの希望と逆のことを許可しとった。
#   ・まとめて送ってほしいのに「三つだけ先に」と言うたら、三つで止まる
#   ・順番と番号を守ってほしいのに「気にせんでええ」と言うたら、崩れて返ってくる
#     （番号が崩れると、どの答えがどの問いのもんか照合でけへん＝取り違えの元）
#   ・納期はこっちから言わん方針やのに、締めでわざわざ言うとった
_ESCAPES = (
    (r"だけでも、?\s*先に(?:送|書)", "「①②③だけでも先に送って」は書かん。まとめて返してもらう形にする"),
    (r"だけ(?:でも)?先に(?:送|書)", "「〜だけ先に」は書かん。まとめて返してもらう形にする"),
    (r"順番も番号も気にせん", "「順番も番号も気にせんでええ」は書かん。番号が崩れたら照合でけへん"),
    (r"番号は気にせん|順番は気にせん", "順番・番号を崩す許可は出さん"),
    (r"思い出した順に", "「思い出した順に」は書かん。番号順で返してもらう"),
    (r"だらだら書いて", "「だらだら書いてええ」は書かん"),
    (r"答えられるとこだけ|書けるとこだけ|埋まるとこだけ", "虫食いを先に許可せん。相手が「書けん」と言うてきた時だけ緩める"),
    (r"[2２]営業日", "納期はこっちから言わん。聞かれた時だけ答える（特商法とオファー文には残す）"),
)


# ★★★2026-08-28：夜の22時に「おはようさん」で始まる文を三通も書いた。
#   ★なんでか。時刻を一回も見んと、「昨日の話の続きやから朝やろ」で決めつけたからや。
#   ★★ほんで、時刻を合わせるだけでは足りん。
#     ★こっちが書く時刻と、店主が実際にLINEで送る時刻は【ちがう】。
#       夜に書いて、朝に送ることもある。その逆もある。
#     ★★せやから、時間帯に寄りかかった挨拶は【そもそも書かん】。
#       相手の呼び名から入るか、用件から入る。それで一つも困らん。
_TIME_GREETINGS = ("おはよう", "こんばんは", "こんにちわ", "こんにちは",
                   "朝から", "今朝", "今晩")


# ★★★2026-08-29：生成文の末尾に、よその著作権表記が付いとった。
#   `Copyright © 2025 Ken Kawamura. All Rights Reserved. GACHACOPI.COM`
#   ★コードにもルールにもチャット記録にも無い文字列や。
#     スクショの書き起こしツールか、貼り付けの経路で混ざったもんやと思われる。
#   ★★他人の著作権表記を付けたまま顧客に送ったら、洒落にならん。ここで止める。
# ★★★2026-09-01：★印は【こっちの手元の記号】や。顧客に渡す文面に出したらあかん。
#   実測：システムが作る納品文86件には★が0個。
#   　　　手で書くヒアリング39件に813個、返信50件に368個、連絡4件に25個。
#   ＝完全にこっちの書き癖が漏れとるだけや。読む側には意味が無い記号が並ぶ。
#   店主から「★はいらん、全部削れ」。★機械で止める。
_STAR_RE = re.compile(r"[★☆]")


def _check_star(text):
    n = len(_STAR_RE.findall(text))
    if n:
        return [f"★印が{n}個ある。顧客に渡す文面から全部消すこと"
                "（こっちの手元の記号や。読む側には意味が無い）"]
    return []


_JUNK = (
    ("Copyright", "著作権表記"), ("copyright", "著作権表記"),
    ("All Rights Reserved", "著作権表記"), ("All rights reserved", "著作権表記"),
    ("©", "コピーライト記号"),
    (".COM", "よそのURL/社名"), (".com/", "よそのURL"),
    ("http://", "URL"),
    ("読み取り内容", "スクショの書き起こしが本文に混ざっとる"),
    ("※話者の左右は", "書き起こしの但し書きが本文に混ざっとる"),
    ("[画像を送付", "システムの印が本文に混ざっとる"),
    ("生成AI", "AIの話"), ("プロンプト", "AIの話"),
)


def _check_foreign_junk(text: str):
    """よそから紛れ込んだ異物が本文に残ってへんか見る。"""
    out = []
    for w, why in _JUNK:
        if w in text:
            # ★椿が自分で出す商品リンクは通す（STORES／Stripe／note）
            #   ★2026-08-29：stores.jp しか見てへんかったんで、
            #     月詠みの案内（buy.stripe.com）だけの文が弾かれた。
            _OURS = ("stores.jp", "buy.stripe.com", "note.com/tsubaki_honne")
            if w in (".com/", "http://", ".COM") and any(o in text for o in _OURS):
                continue
            out.append(f"★異物が混ざっとる（{w} ＝ {why}）。顧客に出す文から必ず消すこと")
    return sorted(set(out))


def _check_time_greeting(text: str):
    """時間帯に寄りかかった挨拶が入ってへんか見る（引用の中は除く）。"""
    head = "\n".join(text.splitlines()[:6])          # 挨拶は頭にしか出えへん
    head = re.sub(r"「[^」]*」", "", head)             # 相手の言葉の引用は見逃す
    hit = [w for w in _TIME_GREETINGS if w in head]
    if hit:
        return [f"★時間帯の挨拶が入っとる（{'/'.join(hit)}）。"
                f"書く時刻と送る時刻はちがうんやから、time-of-day に寄りかからんこと。"
                f"呼び名か用件から入る"]
    return []


def _check_hearing_escapes(text: str):
    """ヒアリング文の締めに逃げ道を書いてへんか見る。"""
    out = []
    for pat, why in _ESCAPES:
        if re.search(pat, text):
            out.append(f"★{why}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--name", default="", help="その人の呼び名（敬称ごと。例: のどかさん）")
    ap.add_argument("--already-plain", action="store_true",
                    help="★前から呼び捨てで通してきた人だけ。途中で「さん」を付けると他人行儀に"
                         "なるんで、その人らだけ現状維持を許す（絵麻・麻衣・ゆきえ・なぎ 等）。"
                         "新しい人には絶対に使わん")
    a = ap.parse_args()
    text = Path(a.file).read_text(encoding="utf-8")
    bad = check(text, a.name, a.already_plain)
    print(f"📄 {a.file}（{len(text)}字）")
    if not bad:
        print("✅ 問題なし。そのまま送ってええ")
        return 0
    for b in bad:
        print(f"❌ {b}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

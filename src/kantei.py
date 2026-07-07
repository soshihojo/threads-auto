"""個別鑑定（有料）のPDF納品物ジェネレータ。

購入者の生年月日×2＋悩みの詳細文から、椿の声で章立ての鑑定文（約10,000字）を生成し、
和風デザインのHTMLに流し込んでPDF（A4）を出力する。

使い方:
  python -m src.main kantei --name Madoka --me 1988-06-13 --him 1998-05-30 \
      --details-file kantei_out/input.txt

出力は kantei_out/（gitignore済み・顧客情報のためコミットしない）。
PDF化はローカルのGoogle Chrome（headless）を使う。
"""
from __future__ import annotations

import html
import subprocess
from datetime import datetime
from pathlib import Path

from .config import ROOT
from .diagnosis import _shuku_distance, honmei_shuku
from .llm import complete

OUT_DIR = ROOT / "kantei_out"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 内部参考にする27宿の性質メモ（本文には翻訳して出す。宿名は絶対に出さない）
SHUKU_TRAITS = {
    "井宿": "頭の回転が速い分析家。情が深く尽くすが、考えすぎて空回りしやすい。好きな相手ほど本音を言えず「察してほしい」が募る。白黒つけたい性分なのに、肝心なところで踏み込めない",
    "張宿": "太陽のように振る舞う自信家。人の輪の中心にいたい華やかさとプライドの高さ。弱みは絶対に見せない。自分のペース・自分の段取りが最優先で、他人に予定を握られるのを嫌う。サービス精神はあるが気分屋で、追われると引く。根は寂しがり",
}
_TRAIT_FALLBACK = "（性質メモ未登録。生まれ持った気質は椿の視立てで補う）"

KANTEI_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。購入者に納品する有料の個別鑑定書の本文を、章ごとに書く。

これは約4,000円の有料納品物。無料鑑定と違い、出し惜しみは一切しない。処方箋（いつ・何を・どう動くか）も時期も、具体的に渡しきる。読んだ相談者が「買ってよかった。もう占いを渡り歩かなくていい」と思える深さで書く。

声と文体:
- 一人称「ウチ」、相手は「あんた」。関西弁。ただし話し言葉すぎない「手紙の文体」で、じっくり読ませる
- 毒舌と愛は半々。慰めの嘘は書かないが、突き放さない。相談者の味方として書く
- 相談文の言葉・固有のエピソード（日付・出来事）を具体的に引用して、この人だけの鑑定にする

厳守:
- 『宿曜』という占術名、宿の名前（井宿・張宿など）、「距離」「命・業・胎」などの専門用語は一切書かない。内部参考の性質は、誰でも分かる日常の言葉に翻訳して「ウチが視たあんた（彼）はこういう人や」と語る
- 結果の保証はしない（「必ず戻る」「絶対うまくいく」は書かない）。ただし曖昧に逃げず、椿としての見立ては言い切る
- 病気・健康・金運の断定はしない。過度に不安を煽らない
- マークダウン記号（#や*や-）は使わない。プレーンな段落文で書く。段落の区切りは空行
- 絵文字は使わない（最終章の締めの一文にだけ🌙を1つ）
- 指定された章の内容だけを書く。他の章で扱う内容を先取りしない。章タイトルや見出しは書かず、本文だけを出力する"""

CHAPTERS = [
    ("maegaki", "まえがき", 550,
     "鑑定書の冒頭。詳細に書いてくれたことへの労い、この鑑定書の読み方（一回で全部飲み込まんでええ、迷ったら何度でも開き）、"
     "椿の姿勢（保証はせん。そのかわり嘘も書かん。本気で視た）を、手紙の書き出しとして書く。"),
    ("anata", "あんたという人", 1100,
     "相談者本人の生まれ持った性質を深く言い当てる。恋愛でのクセ（尽くし方・我慢の仕方・本音を言えないところ）、"
     "その長所と、今回の恋でそれがどう裏目に出てきたか。相談文の「ずっと合わせてきました」「追いLINEはほぼしない」という行動と性質を結びつける。"),
    ("kare", "彼という人", 1400,
     "彼の生まれ持った性質を深く描く。10歳年下であること、自分のペースと段取りが最優先の性分、プライドが高く外の世界では弱みを見せないこと——"
     "そのくせ相談者の前では弱音や弱みを出せること（本人からの追加情報）。この二面性を軸に、「彼にとって相談者は鎧を外せる数少ない場所」であることを描く。"
     "「忙しい」の言葉の裏にある彼の生き方、友達との時間を優先して見える理由、気分屋に見えて約束は守る一面（誕生日のLINE・「連絡しないで」の後にちゃんと説明しに来たこと）まで、矛盾ごと一人の男として立体的に。"),
    ("en", "二人の縁", 1100,
     "二人の縁の質。なぜ出会ってすぐ体の関係になり、なぜ2年間「セフレのまま」で止まっているのかを、二人の性質の噛み合わせから説明する。"
     "この縁の強みと危うさの両方。年の差10歳がこの縁でどう働いているか。"),
    ("honne", "彼の今の本音", 1800,
     "この鑑定書の核。相談者の一番の質問「彼は私のことをどう思っていて、これから先のことをどう考えているのか」に真正面から答える。"
     "彼が相談者にだけ弱みを見せていること（＝彼にとっての安全地帯である事実）と、「彼女にする覚悟」はまだ別の場所にあること——"
     "この2つの距離が今の彼の現在地やと読み解く。安全地帯やからこそ安心して後回しにできてしまう、という残酷な仕組みも逃げずに書く。"
     "会う時間が短くなっていること、リアクションボタンで終わった最後のやりとり、「帰ってきたら予定確認するわ」の言葉、"
     "トラブルの時に事情をわざわざ説明しに来たこと——それぞれが彼の中で何を意味するかを読み解き、"
     "甘やかさず、でも希望の芽（弱みを見せられる相手は彼の人生で貴重であること）も含めて正直に言い切る。"),
    ("shohousen", "いつ、何を、どう動くか", 1900,
     "処方箋の章。今日は2026年7月7日、彼はグアムでの訓練中で帰国後に「予定確認するわ」と言っている前提。"
     "ここから2027年までの時期の波を、月の単位で具体的に示す（動く時期・待つ時期・勝負の時期）。"
     "帰国後にあんたから送る最初の一言（実際の文面例をひとつ）、誘い方、会えた時にやること・言うこと、"
     "「彼女になる」への関係の切り替え方（体の関係の扱い方も逃げずに）、彼がプライド高い男やからこその伝え方の作法まで、迷いようがないほど具体的に。"
     "ただし「この通りやれば必ず結婚できる」とは書かず、波に乗るための最善手として渡す。"),
    ("kinki", "やったらあかんこと", 800,
     "この恋でやってはいけないことを具体的に。追いLINE・「私たちって何？」と正面から聞き詰めること・彼の予定を先回りして押さえようとすること・"
     "訓練中に重い連絡を送ること、など彼の性質から導かれる地雷。すでに我慢できているところは褒めて、続けさせる。"),
    ("musubi", "むすびに", 750,
     "締めの章。「占いジプシーを抜け出せない」「借金してまで占いに注ぎ込むのは終わりにしたい」という悩みに正面から答える——"
     "お金を借りてまで安心を買いに行くのは、占いやのうて依存や、とはっきり言い、それでも責めずに「不安の正体は彼やなく答えのない宙ぶらりんやった。答えはもうこの鑑定書に書いた」と支える。"
     "占いは渡り歩いて安心を買うものやなく、自分の足で立つための杖やという椿の考え。この鑑定書を持って、もう占いを探さんでええ、これで最後にしてええと背中を押す。"
     "困ったらまた椿に相談できること（何度でも相談できる月額の会員があること）にひとことだけ触れ、"
     "最後は椿らしい愛のある一言で結ぶ。締めの一文に🌙を1つ。"),
]


def _internal_brief(name: str, me_birth: str, him_birth: str, today: str) -> str:
    me_s, him_s = honmei_shuku(me_birth), honmei_shuku(him_birth)
    dist = _shuku_distance(me_s, him_s)
    me_age = _age(me_birth, today)
    him_age = _age(him_birth, today)
    return (
        f"・相談者: {name}（{me_birth}生まれ・{me_age}歳）。性質の内部参考: {SHUKU_TRAITS.get(me_s, _TRAIT_FALLBACK)}\n"
        f"・彼: {him_birth}生まれ・{him_age}歳（{me_age - him_age}歳年下）。性質の内部参考: {SHUKU_TRAITS.get(him_s, _TRAIT_FALLBACK)}\n"
        f"・縁の内部参考: 27分類の巡りで距離{dist}（0が最も近い、13が最も遠い）。近いほど似た者同士、遠いほど互いに無いものを持つ縁\n"
        f"・今日の日付: {today}"
    )


def _age(birth: str, today: str) -> int:
    b = datetime.strptime(birth, "%Y-%m-%d")
    t = datetime.strptime(today, "%Y-%m-%d")
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def generate_chapters(name: str, me_birth: str, him_birth: str, details: str,
                      today: str | None = None) -> list[dict]:
    """章ごとに鑑定文を生成して [{key,title,body}, ...] を返す。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    brief = _internal_brief(name, me_birth, him_birth, today)
    toc = "\n".join(f"・{t}" for _, t, _, _ in CHAPTERS)
    done: list[dict] = []
    for key, title, chars, instruction in CHAPTERS:
        prev = "\n".join(f"【{d['title']}】{d['body'][:150]}…" for d in done) or "（まだ無い。これが最初の章）"
        user = (
            f"=== 内部参考（本文には翻訳して出す。用語・数字は出さない） ===\n{brief}\n\n"
            f"=== 相談者から届いた詳細（全文） ===\n{details}\n\n"
            f"=== 鑑定書の全体構成 ===\n{toc}\n\n"
            f"=== ここまでに書いた章の冒頭（重複を避ける参考） ===\n{prev}\n\n"
            f"=== 今回書く章 ===\n章タイトル: {title}\n目安の分量: {chars}字（±2割）\n"
            f"この章で書くこと: {instruction}\n\n本文だけを出力してください。"
        )
        body = complete(KANTEI_SYSTEM, user, max_tokens=3000, temperature=0.7).strip()
        done.append({"key": key, "title": title, "body": body})
        print(f"  ✓ {title}（{len(body)}字）")
    return done


# ---------------- HTML / PDF ----------------

_CAMELLIA = """<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
<g>
<circle cx="50" cy="34" r="17" fill="#b3364b"/>
<circle cx="35" cy="45" r="17" fill="#a52e44"/>
<circle cx="65" cy="45" r="17" fill="#c04057"/>
<circle cx="41" cy="60" r="17" fill="#b3364b"/>
<circle cx="59" cy="60" r="17" fill="#a52e44"/>
<circle cx="50" cy="48" r="10" fill="#d9a441"/>
<circle cx="46" cy="45" r="1.8" fill="#f3e3b8"/><circle cx="54" cy="45" r="1.8" fill="#f3e3b8"/>
<circle cx="50" cy="52" r="1.8" fill="#f3e3b8"/><circle cx="45" cy="51" r="1.5" fill="#f3e3b8"/>
<circle cx="55" cy="51" r="1.5" fill="#f3e3b8"/>
<path d="M62 72 Q78 70 84 84 Q68 88 62 76 Z" fill="#3f6b4f"/>
</g></svg>"""

_KANJI_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]

_CSS = """
@page { size: A4; margin: 0; }
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { font-family: "Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", serif;
  color: #2b2621; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.page { width: 210mm; min-height: 297mm; padding: 24mm 22mm; page-break-after: always; position: relative; }
.cover { display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center; background: #f7f2e9;
  background-image: radial-gradient(circle at 85% 12%, rgba(179,54,75,.07) 0 90px, transparent 90px),
                    radial-gradient(circle at 12% 88%, rgba(176,141,62,.10) 0 120px, transparent 120px); }
.cover .flower { width: 84px; margin-bottom: 10mm; }
.cover .sub { font-size: 10.5pt; letter-spacing: .55em; color: #8a6d3b; margin-bottom: 6mm; }
.cover h1 { font-size: 33pt; letter-spacing: .35em; font-weight: 600; margin-bottom: 12mm; }
.cover .for { font-size: 14pt; letter-spacing: .2em; margin-bottom: 2.5mm; }
.cover .line { width: 42mm; height: 1px; background: #b08d3e; margin: 8mm auto; }
.cover .meta { font-size: 10pt; color: #6d6257; line-height: 2; }
.cover .sig { margin-top: 14mm; font-size: 12pt; letter-spacing: .3em; color: #2b2621; }
.toc { background: #fff; }
.toc h2, .chap h2 { font-size: 16pt; letter-spacing: .25em; font-weight: 600; margin-bottom: 10mm; }
.toc ol { list-style: none; }
.toc li { font-size: 11.5pt; letter-spacing: .12em; padding: 4.2mm 0; border-bottom: 1px dashed #d8cbb2;
  display: flex; align-items: baseline; }
.toc li .no { color: #b08d3e; font-size: 9.5pt; width: 22mm; letter-spacing: .2em; }
.chap { background: #fff; }
.chap .chapno { font-size: 9.5pt; color: #b08d3e; letter-spacing: .45em; margin-bottom: 2.5mm; }
.chap h2 { padding-bottom: 4mm; border-bottom: 1px solid #b08d3e; display: flex; align-items: center; gap: 4mm; }
.chap h2 .mark { width: 17px; height: 17px; flex: none; }
.chap .body { margin-top: 8mm; font-size: 10.5pt; line-height: 2.05; text-align: justify; }
.chap .body p { margin-bottom: 4.5mm; text-indent: 1em; }
.chap.rx .body { border: 1px solid #d9c894; background: #fbf7ec; padding: 7mm 8mm; }
.foot { position: absolute; bottom: 12mm; left: 0; right: 0; text-align: center;
  font-size: 8pt; color: #a3968a; letter-spacing: .3em; }
"""


def build_html(name: str, chapters: list[dict], today: str) -> str:
    d = datetime.strptime(today, "%Y-%m-%d")
    date_jp = f"{d.year}年{d.month}月{d.day}日"
    toc_items = "".join(
        f'<li><span class="no">第{_KANJI_NUM[i]}章</span>{html.escape(c["title"])}</li>'
        for i, c in enumerate(chapters)
    )
    chap_html = ""
    for i, c in enumerate(chapters):
        paras = "".join(f"<p>{html.escape(p.strip())}</p>" for p in c["body"].split("\n") if p.strip())
        rx = " rx" if c["key"] == "shohousen" else ""
        chap_html += (
            f'<div class="page chap{rx}">'
            f'<div class="chapno">第{_KANJI_NUM[i]}章</div>'
            f'<h2><span class="mark">{_CAMELLIA}</span>{html.escape(c["title"])}</h2>'
            f'<div class="body">{paras}</div>'
            f'<div class="foot">椿｜彼の本音しか視ん</div>'
            f"</div>"
        )
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>鑑定書</title><style>{_CSS}</style></head><body>
<div class="page cover">
  <div class="flower">{_CAMELLIA}</div>
  <div class="sub">個別鑑定書</div>
  <h1>彼の本音</h1>
  <div class="for">{html.escape(name)} 様へ</div>
  <div class="line"></div>
  <div class="meta">鑑定日　{date_jp}<br>この鑑定書は、あなたひとりのために視て、書いたものです。</div>
  <div class="sig">鑑定士　椿</div>
</div>
<div class="page toc">
  <h2>目次</h2>
  <ol>{toc_items}</ol>
  <div class="foot">椿｜彼の本音しか視ん</div>
</div>
{chap_html}
</body></html>"""


def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf_path}", f"file://{html_path}"],
        check=True, capture_output=True, timeout=120,
    )


def make_kantei(name: str, me_birth: str, him_birth: str, details: str,
                today: str | None = None) -> dict:
    """鑑定書を生成してPDFまで出力。{html, pdf, chars} を返す。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    OUT_DIR.mkdir(exist_ok=True)
    print(f"🖋 鑑定文を生成中（{len(CHAPTERS)}章）…")
    chapters = generate_chapters(name, me_birth, him_birth, details, today=today)
    total = sum(len(c["body"]) for c in chapters)
    stem = f"kantei_{name}_{today}"
    html_path = OUT_DIR / f"{stem}.html"
    pdf_path = OUT_DIR / f"{stem}.pdf"
    html_path.write_text(build_html(name, chapters, today), encoding="utf-8")
    html_to_pdf(html_path, pdf_path)
    print(f"📜 完成: {pdf_path}（本文{total}字）")
    return {"html": str(html_path), "pdf": str(pdf_path), "chars": total}

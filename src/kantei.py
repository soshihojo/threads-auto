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
import shutil
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
    "箕宿": "裏表のない姉御肌。情が深く面倒見がよく、人に好かれる華がある。自由を何より愛し、束縛や湿っぽさを嫌う。豪快に見えて実は繊細で寂しがり。惚れたら一途で、相手のために動くのを厭わないが、本当に欲しい言葉ほど自分からは言えない。プライドがあるので「追う恋」が苦手",
    "星宿": "野心家で一匹狼。自分の決めた道・仕事への集中を何より優先し、恋愛はその次に置く。感情を表に出さず、弱みは見せない。誠実で嘘がつけない分、中途半端な関係を続けられない潔癖さがある。愛情表現は不器用で、好きでも「好き」の形で出せず、からかいや世話焼きに化ける。一度決めたら曲げない頑固さと、内に秘めた熱",
    "胃宿": "一途で情熱的、決めたら一直線の頑張り屋。負けず嫌いで芯が強く、辛くても弱音を吐かず自分を磨き続けられる。正義感が強く白黒はっきりさせたい性分で、曖昧なまま流されるのが苦手。惚れ込むと相手に全部注ぎ込んで尽くすが、その分裏切られたときの傷が深い。「自分に足りないものがあったんや」と自分を責める方向に行きやすい",
    "鬼宿": "天真爛漫で人懐こく、誰からも可愛がられる無邪気さを持つ。その場その場の空気で生きる自由人で、つかみどころがない。誰にでも優しい分、恋愛では移り気・八方美人が出やすく、悪気なく嘘をつく子供っぽさがある。根は極端な寂しがりで、一人でいられない。責任・重い話・修羅場から逃げる癖があり、都合が悪くなると曖昧にしたまま距離を置く。ただし懐いた相手のことは、離れても完全には手放せない",
}
_TRAIT_FALLBACK = "（性質メモ未登録。生まれ持った気質は椿の視立てで補う）"

KANTEI_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。購入者に納品する有料の個別鑑定書の本文を、章ごとに書く。

これは有料の納品物。無料鑑定と違い、出し惜しみは一切しない。処方箋（いつ・何を・どう動くか）も時期も、具体的に渡しきる。読んだ相談者が「買ってよかった。もう占いを渡り歩かなくていい」と思える深さで書く。

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
     "鑑定書の冒頭。しんどい経緯（浮気・別れ際の言葉・お金のこと）まで包み隠さず、生まれた時間21時05分まで添えて詳細に書いてくれたことへの労い。"
     "この鑑定書の読み方（一回で全部飲み込まんでええ、迷ったら何度でも開き）、"
     "椿の姿勢（保証はせん。そのかわり嘘も書かん。本気で視た）を、手紙の書き出しとして書く。"),
    ("anata", "あんたという人", 1100,
     "相談者本人（もえ・25歳・消防士）の生まれ持った性質を深く言い当てる。一途で決めたら一直線、負けず嫌いで芯が強く、辛くても弱音を吐かずに自分を磨き続けられること"
     "（別れた後もジム・韓国語・ファッション・美容と自分磨きを徹底している行動と結びつける）。白黒はっきりさせたい正義感、惚れ込むと全部注ぎ込んで尽くす性分"
     "（95万円を貸したのもこの尽くし方の延長やと触れる）。その長所と、裏切られたとき「私に足りないものがあったんや」と自分を責める方向に行きやすい危うさ——"
     "今まさに「私に足りないことは何か」を考え続けている彼女に、それは性質の癖であって事実やないと先に釘を刺す。"),
    ("kare", "彼という人", 1400,
     "彼（24歳・消防士・スポーツカー好き）の生まれ持った性質を深く描く。人懐こく無邪気で誰からも可愛がられる、その場の空気で生きる自由人。"
     "誰にでも優しい分、恋愛では移り気と八方美人が出る。悪気なく嘘をつく子供っぽさ（複数回の浮気とその都度の嘘）。根は極端な寂しがりで一人でいられない"
     "（別れた直後から「久しぶりに話したい」「明日当直？」と連絡してくる行動と結びつける）。責任・重い話・修羅場から逃げる癖（不安に向き合わなかったこと・"
     "彼女ができたことを自分からは言わないこと・95万円の返済を曖昧にしてきたこと）。"
     "別れ際の「元から好きじゃなかった」「刺激がなかった」という言葉は、この男の場合、真実の告白やなく「別れ話という修羅場を一気に終わらせるための最も安易で残酷な逃げ方」やと読み解く。"
     "ただし懐いた相手を完全には手放せない性質でもある——矛盾ごと一人の男として立体的に。甘やかさず、けなすだけでもなく。"),
    ("en", "二人の縁", 1100,
     "二人の縁の質。消防の同期という出会い——同じ制服を着て、同じ現場を知る者同士の縁の強さ（友人期間を経て育った縁であること）。"
     "似たところと違うところが半々の縁で、まっすぐ突き進むもえと、風のように流れる彼——もえが彼の帰る場所・支える側になりやすく、"
     "彼がもえの一途さに甘えて寄りかかる構図が生まれやすい縁やったと説明する。楽しかった時間が嘘やなかったことと、"
     "この構図が浮気と嘘を許す土壌にもなってしまった危うさの両方を書く。"),
    ("honne", "彼の今の本音", 1800,
     "この鑑定書の核。③の問いに真正面から答える——「穴埋めやったんか」「本当に好きやったんか」「戻ってくることはあるんか」「彼女がいても可能性はあるんか」「今の彼女とどうなりそうか」。"
     "別れ際の残酷な言葉と、別れ後の行動（金の相談・電話の申し出・誕生日連絡・当直の他愛ないLINE・ディズニーのストーリーへの反応・「寂しかったけん」）の矛盾を、"
     "彼の性質から読み解く：本気で切った相手にこの男は連絡せん。もえは彼にとって「無条件で受け止めてくれた安全地帯」であり、手放してから効いてくるタイプの存在やということ。"
     "「好きやったんか」には正直に：彼なりに好きやった、ただしもえの一途さと同じ重さの好きやなかった——ここを誤魔化さない。"
     "新しい彼女（5月15日から・同じく年下・彼と似た者同士の近い縁）は、寂しがりの彼が空白を埋めるために最短で作った関係で、"
     "居心地は楽やが、もえに求めた「無条件に支えてくれる深さ」とは別物やという見立て。ただし「すぐ別れる」とは断定しない。"
     "希望の芽と、彼の性質が変わらん限り戻っても同じことが起きる危うさの両方を、逃げずに言い切る。"),
    ("shohousen", "いつ、何を、どう動くか", 1900,
     "処方箋の章。今日は2026年7月26日。彼には5月15日からの彼女がおり、10月に95万円の返済予定を決める約束、彼は福岡の消防を受け直している（内定はまだ）という前提。"
     "ここから2027年までの時期の波を、月の単位で具体的に示す。夏（今）＝動かん時期：ミュートは大正解、続けること。自分磨きの継続。"
     "彼からの連絡への返し方（当直LINEレベルには短く軽く・こちらから広げない）。"
     "10月＝正式な接点：95万円の返済確認は「堂々と・事務的に・恋愛と完全に切り離して」行うこと。ここでの毅然とした態度こそが、"
     "彼の中の「もえは俺に甘い」という認識を壊し、逆に価値を上げる一手やと具体的に（言う文面の実例をひとつ・標準語で）。"
     "福岡の内定が出た場合／出ない場合の分岐にも触れる。復縁の目があるとすれば彼の今の関係が揺らぐ時期であって、もえから仕掛けて作れるものやないこと——"
     "「待つ間に何をするか」（金の回収・自分の生活・同期としての自然な距離）を軸に、迷いようがないほど具体的に。保証はしない。"),
    ("kinki", "やったらあかんこと", 800,
     "この恋でやってはいけないことを具体的に。①お金の話を恋愛の道具にすること（返済を口実に会おうとする・逆に復縁のために返済をうやむやにする——95万円は何があっても回収する一線やと明言）"
     "②彼女のSNSや「515」のノートを見に行くこと（ミュートを解くこと）③「私に足りないものは何やったん」と彼に聞くこと・自分を責め続けること"
     "④彼の「寂しかったけん」に応えて都合のいい癒し係に戻ること（二人で会わないと断れた強さを褒めて、続けさせる）"
     "⑤焦って福岡の件で答えを迫ること。すでにできている我慢（金を貸さなかった・電話を断った・ミュート）は具体的に褒める。"),
    ("musubi", "むすびに", 750,
     "締めの章。頭から離れない2つの声——「もう戻ってこないのかな」「私に足りないことは何なのか」に正面から答える。"
     "足りなかったものなど無い、足りなかったのは彼の側の器やと言い切り、95万円と溺愛の願い（わがままやなく、次の恋で当たり前に求めてええ基準やということ）に触れる。"
     "復縁を望む気持ちは否定せず、ただし「彼が戻るかどうか」より「戻ってきた彼を受け入れるに値するか、あんたが審査する側や」と立場をひっくり返して締める。"
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


def build_html(name: str, chapters: list[dict], today: str, *,
               sub: str = "個別鑑定書", title: str = "彼の本音",
               meta_note: str = "この鑑定書は、あなたひとりのために視て、書いたものです。") -> str:
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
  <div class="sub">{html.escape(sub)}</div>
  <h1>{html.escape(title)}</h1>
  <div class="for">{html.escape(name)} 様へ</div>
  <div class="line"></div>
  <div class="meta">鑑定日　{date_jp}<br>{html.escape(meta_note)}</div>
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
    stem = f"個別鑑定_{name}"
    html_path = OUT_DIR / f"{stem}.html"
    pdf_path = OUT_DIR / f"{stem}.pdf"
    html_path.write_text(build_html(name, chapters, today), encoding="utf-8")
    html_to_pdf(html_path, pdf_path)
    # 納品用にダウンロードフォルダへも必ず置く（LINE公式アプリから添付しやすいように）
    dl_path = Path.home() / "Downloads" / pdf_path.name
    shutil.copy2(pdf_path, dl_path)
    print(f"📜 完成: {pdf_path}（本文{total}字）")
    print(f"⬇️ ダウンロードにも配置: {dl_path}")
    return {"html": str(html_path), "pdf": str(pdf_path), "download": str(dl_path),
            "chars": total}


# ---------------- 月詠み（月額会員向けの月次ミニ鑑定書） ----------------
# 月額会員「椿の月詠み」（月2,980円）の毎月の納品物。個別鑑定書のミニ版（2,000〜3,000字・A4数枚）。
# 個別鑑定書が納品済みの会員は、その内容と一貫した「続き」として書く。

TSUKIYOMI_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。月額会員に毎月納品する「月詠み鑑定書」の本文を、章ごとに書く。

これは月2,980円の有料納品物。会員はすでにお金を払ってくれてる常連やから、出し惜しみは一切しない。今月の時期の読みも、送る一言の実文面も、具体的に渡しきる。

声と文体:
- 一人称「ウチ」、相手は「あんた」。関西弁。ただし話し言葉すぎない「手紙の文体」で、じっくり読ませる
- 毒舌は控えめ、姉御の温かさ多め。慰めの嘘は書かないが、突き放さない
- 会員の近況・悩みの言葉を具体的に引用して、この人の今月だけの鑑定にする

厳守:
- 『宿曜』という占術名、宿の名前、「距離」「命・業・胎」などの専門用語は一切書かない。内部参考は日常語に翻訳する
- 個別鑑定書（あれば）で伝えた性質の読み・時期・処方箋と矛盾させない。「鑑定書にも書いたけどな」と自然に参照してよい
- 結果の保証はしない。過度に不安を煽らない。病気・健康・金運の断定はしない
- 「続きはLINEで」のような引っ張りはしない（会員には渡しきる）
- マークダウン記号は使わない。プレーンな段落文（段落の区切りは空行）。絵文字は最終章の締めの一文にだけ🌙を1つ
- 指定された章の内容だけを書く。章タイトルや見出しは書かず、本文だけを出力する"""

TSUKIYOMI_CHAPTERS = [
    ("nagare", "今月の二人", 700,
     "今月の彼の心の流れと、二人のあいだの空気を日常語で読む。会員の近況・悩み（与えられていれば）に正面から触れ、"
     "「今こういう位置におる」と現在地をはっきりさせる。"),
    ("jiki", "動いてええ時、待つ時", 700,
     "今月を上旬・中旬・下旬の感覚で分けて、連絡・誘い・大事な話をするなら「動いてええ時期」と「待った方がええ時期」の目安を具体的に示す。"
     "なぜその時期なのか、彼の状態と結びつけて理由も書く。"),
    ("shohousen", "今月の処方箋", 800,
     "今月やること・言うことを具体的に。送る一言の実文面をひとつ、避けるべき行動（追いLINE等その人の状況に応じた地雷）、"
     "会えた時・連絡が来た時の受け方まで。頑張らせすぎない、楽に実行できる範囲で。"),
    ("musubi", "むすびに", 300,
     "今月のあんたへの寄り添いの一言。来月もまた視ること（続きを見届けること）の安心で締める。締めの一文に🌙を1つ。"),
]


def generate_tsukiyomi_chapters(name: str, me_birth: str, him_birth: str, worry: str,
                                kantei_text: str = "", month_label: str = "今月",
                                today: str | None = None) -> list[dict]:
    """月詠みの章を生成して [{key,title,body}, ...] を返す。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    brief = _internal_brief(name, me_birth, him_birth, today)
    toc = "\n".join(f"・{t}" for _, t, _, _ in TSUKIYOMI_CHAPTERS)
    done: list[dict] = []
    for key, title, chars, instruction in TSUKIYOMI_CHAPTERS:
        prev = "\n".join(f"【{d['title']}】{d['body'][:120]}…" for d in done) or "（まだ無い。これが最初の章）"
        user = (
            f"=== 内部参考（本文には翻訳して出す。用語・数字は出さない） ===\n{brief}\n\n"
            f"=== 対象月 ===\n{month_label}の月詠み鑑定書\n\n"
            f"=== 会員の近況・今の悩み ===\n{worry.strip() or '（特に届いていない。二人の全体の流れで視る）'}\n\n"
            + (f"=== この会員に納品済みの個別鑑定書（抜粋。読みと処方箋を一貫させる） ===\n{kantei_text.strip()[:6000]}\n\n"
               if kantei_text.strip() else "")
            + f"=== 月詠みの全体構成 ===\n{toc}\n\n"
            f"=== ここまでに書いた章の冒頭 ===\n{prev}\n\n"
            f"=== 今回書く章 ===\n章タイトル: {title}\n目安の分量: {chars}字（±2割）\n"
            f"この章で書くこと: {instruction}\n\n本文だけを出力してください。"
        )
        body = complete(TSUKIYOMI_SYSTEM, user, max_tokens=1500, temperature=0.8).strip()
        done.append({"key": key, "title": title, "body": body})
        print(f"  ✓ {title}（{len(body)}字）")
    return done


def make_tsukiyomi(name: str, me_birth: str, him_birth: str, worry: str = "",
                   kantei_text: str = "", month_label: str | None = None,
                   today: str | None = None) -> dict:
    """月詠み鑑定書（月次ミニPDF）を生成して出力。{html, pdf, chars, month_label, body} を返す。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    d = datetime.strptime(today, "%Y-%m-%d")
    month_label = month_label or f"{d.year}年{d.month}月"
    OUT_DIR.mkdir(exist_ok=True)
    print(f"🖋 {month_label}の月詠みを生成中（{len(TSUKIYOMI_CHAPTERS)}章）…")
    chapters = generate_tsukiyomi_chapters(name, me_birth, him_birth, worry,
                                           kantei_text=kantei_text,
                                           month_label=month_label, today=today)
    total = sum(len(c["body"]) for c in chapters)
    stem = f"tsukiyomi_{name}_{d.year}-{d.month:02d}"
    html_path = OUT_DIR / f"{stem}.html"
    pdf_path = OUT_DIR / f"{stem}.pdf"
    html_path.write_text(
        build_html(name, chapters, today, sub="月詠み鑑定書", title=month_label,
                   meta_note="この月詠みは、あなたと彼の今月のために視て、書いたものです。"),
        encoding="utf-8",
    )
    html_to_pdf(html_path, pdf_path)
    body = "\n\n".join(f"【{c['title']}】\n{c['body']}" for c in chapters)
    print(f"📜 完成: {pdf_path}（本文{total}字）")
    return {"html": str(html_path), "pdf": str(pdf_path), "chars": total,
            "month_label": month_label, "body": body}

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
    "斗宿": "品と芯の強さを併せ持つ努力家。責任感が強く、自分の役割（家庭・仕事）を投げ出さずに守り抜く。感情を理性で抑えて表に出さないが、内側には人一倍激しい情熱を秘めている。プライドが高く、弱音や「寂しい」を素直に言えない。我慢を重ねた不安が限界を超えると、抑えていた分だけ鋭い言葉や皮肉になって口から漏れてしまう。愛されている実感を言葉で確かめたい人",
    "軫宿": "器用で世渡り上手、外面は柔らかく如才ない社交家。根はロマンチストで、惚れた相手には情熱的な言葉を惜しみなく注ぐ。ただしプライドが高く内面は繊細で、傷つけられた（と感じた）瞬間に殻に閉じこもって黙る。自分から折れる・謝るのが極端に苦手。機嫌の回復には時間がかかるが、一度切り替わると何事もなかったかのように戻る。連絡無精で、返事を後回しにしても平気",
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
     "鑑定書の冒頭。LINEのやり取りを一語一句・絵文字まで違わず書き起こしてくれた誠実さ、「本当は書きたくないけど書きます」と一番怖いこと（もし縁がないなら）まで書いてくれた勇気への労い。"
     "この鑑定書の読み方（一回で全部飲み込まんでええ、迷ったら何度でも開き）、"
     "椿の姿勢（保証はせん。そのかわり嘘も書かん。本気で視た）を、手紙の書き出しとして書く。"),
    ("anata", "あんたという人", 1100,
     "相談者本人（麻衣・44歳・小学生の男女の母・家庭は壊さないことが絶対条件）の生まれ持った性質を深く言い当てる。"
     "品と芯の強さ、責任感で家庭も彼との時間も両立させてきた器用さと踏ん張り（会う時間を自分の都合の21時からに整えてきたこと）。"
     "感情を理性で抑えて、彼にはほぼ敬語のLINE——その折り目正しさの内側に、人一倍激しい情熱と「愛されている実感を言葉で確かめたい」渇きがあること。"
     "我慢を重ねた不安が限界を超えた瞬間、抑えていた分だけ鋭い言葉になって漏れる癖——7月9日の「帰ろうかと思った」「着拒しとるやろ」は、意地悪やなく"
     "前日眠れないほど楽しみにしていた気持ちの裏返し・不安の暴発やったと言い当てる。責めずに、構造として理解させる。"),
    ("kare", "彼という人", 1400,
     "彼（48歳・4歳上・バツイチ独身・大学生と高校生の息子には今も会い続ける情のある父親）の生まれ持った性質を深く描く。"
     "外面は柔らかく如才ない社交家で、根はロマンチスト——「麻衣の全部が好きだ」「麻衣ロスなんだよ」「俺は彼氏だから」と、48歳が照れずに言葉を注ぐ熱。"
     "4月から休みを合わせようと画策し続けた執心。その一方で、プライドが高く内面は繊細で、傷つけられたと感じた瞬間に殻に閉じこもって黙る性質"
     "（7月9日の一日中の沈黙・携帯に逃げる態度は、怒鳴る代わりの彼なりの傷の抱え方）。自分から折れる・謝るのが極端に苦手。"
     "連絡無精・既読のみ・返事の後回しは23年前から変わらない彼の平常運転であること（今回の既読スルーを「冷めた証拠」と混同させない）。"
     "夕方には自分から話しかけ、帰り際は穏やかやった——切り替わりの兆しまで含めて、一人の男として立体的に。"),
    ("en", "二人の縁", 1100,
     "二人の縁の質。23年前に同じ会社で始まり、立場を入れ替えて（当時は彼が既婚・今は麻衣が既婚）二度結ばれた縁の稀さ。"
     "10年会わなかった空白のあいだも彼から誕生日の連絡が絶えず、3年前も彼から手繰り寄せてきた——この縁は麻衣が追ってできた縁やなく、彼が二度も引き戻した縁やという事実。"
     "互いに無いものを持ち合う縁（責任で自分を律する麻衣と、言葉で愛を注ぐロマンチストの彼）。"
     "「家庭を壊さない」という二人で決めた枠——その枠があるからこそ23年続いた強みと、枠の中では不安を確かめ合う時間が足りなくなる危うさの両方を書く。"),
    ("honne", "彼の今の本音", 1800,
     "この鑑定書の核。麻衣の問い——「彼の気持ち」「まだ好き？」「終わりじゃないよね？」「インスタに5日ログインがないのはどうして？」——に真正面から答える。"
     "7月9日の沈黙の正体を読み解く：4月から画策して熱烈に楽しみにしていた彼が、待ち合わせ直後に「帰ろうかと思った」「着拒しとるやろ」と疑われた——"
     "プライドの高いロマンチストにとってあれは「俺の気持ちを疑われた」一撃で、怒りより先に拗ねと傷。冷めたのではなく、傷ついた自尊心の回復に時間がかかっているだけやと言い切る。"
     "証拠を並べる：夕方から自分は話しかけてきた・帰り際は穏やかやった・ゴルフの返信には💦を付けた（完全拒否の男は絵文字を付けん）・既読のみは元々の彼の平常運転。"
     "7月10日の「昨日ごめんね」はもう届いており、謝罪は既に成立していること。"
     "インスタのログイン5日なしは、意味を断定せず「詮索しても答えの出ない領域。彼の生活の都合と、気持ちの引きこもりが半々」と正直に扱う。"
     "結論：終わりやない。ただし「元に戻る」には麻衣が動きすぎないことが条件——ここを甘やかさず言い切る。"),
    ("shohousen", "いつ、何を、どう動くか", 1900,
     "処方箋の章。今日は2026年7月26日。7月22日の「土曜日にゴルフだから厳しいかな💦」→「分かった」で止まっている前提。"
     "まず今〜7月末＝完全に待つ時期：「分かった」で止めたのは満点や、追撃したら台無しになると明言。"
     "8月上旬〜中旬＝再打診の時期：いつも通りの日時指定の打診を、何事もなかったかのようないつもの敬語のトーンで送る（実文面をひとつ・麻衣の普段の文体=敬語・絵文字なしで）。"
     "蒸し返さない・重ねて謝らないことが最重要（謝罪は7月10日に済んでいる。二度目の謝罪は彼に7月9日を思い出させるだけ）。"
     "会えた時の振る舞い：いつも通り・楽しい麻衣でいること（彼が惚れてるのは楽しい時間そのもの）。デートの話題は彼から出ない限り出さない。"
     "「もっと連絡を取りたい・早く既読してほしい」への正直な答え：彼の連絡の性質は23年変わっていない、そこを変えようとすると今の均衡ごと壊れる——"
     "求める場所を変える（会っている時間の濃さで受け取る）という発想の転換を渡す。月の単位で秋〜年末の波（会う頻度が戻る時期の目安）も示す。保証はしない。"),
    ("kinki", "やったらあかんこと", 800,
     "この恋でやってはいけないことを具体的に。①7月9日を蒸し返すこと・重ねて謝ること②「私たちどうなるの」「終わりなの」と詰めること"
     "③「お返事ほしいです」の催促を繰り返すこと（今回は効いたが、繰り返すと彼の逃げ癖を強める）④インスタのログイン監視・意味の詮索"
     "⑤「着拒しとるやろ」型の、不安を疑いの言葉に変換して口に出す癖（不安になった時の代わりの逃し方を渡す）。"
     "すでにできていること——「分かった」で引けたこと・普段の節度ある距離感——は具体的に褒めて、続けさせる。"),
    ("musubi", "むすびに", 750,
     "締めの章。③追加の問い「安心して穏やかに過ごす心を持つには」に正面から答える——"
     "23年もんの縁が、たった一日の失言で切れるほど薄いもんやないこと。7月9日から苦しいのは、彼を失う恐怖やなく「自分が壊したかもしれん」という自責やと言い当て、"
     "その自責はもう手放してええと伝える。④の「もし縁がないならハッキリ言ってほしい」には誠実に：ウチが視た限り、この縁はまだ終わってへん——だから乗り越え方やなく続け方を書いた、と。"
     "不安が湧いた時の心の置き場（彼の言葉やなく、彼が二度この縁を引き戻してきた事実を思い出すこと）を渡す。"
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
    # 納品用にダウンロードフォルダへも必ず置く（LINE公式アプリから添付しやすいように）。
    # ダウンロード側のファイル名は「個別鑑定_名前さん.pdf」（相談者に見える名前なので敬称付き）
    dl_path = Path.home() / "Downloads" / f"個別鑑定_{name}さん.pdf"
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

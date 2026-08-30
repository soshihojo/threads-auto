"""潮見（29,800円→9,800円）と潮見・構え（16,800円）——90日の暦を売る商品。

★2026-08-13 新設。

3,980円の鑑定書は「彼が分かる」商品や。処方箋の章に「いつ・何を」は書いてあるが、
90日を一望する暦は無い。相談の大半が「別れて待つ」「音信不通」——時間の悩みやのに、
待ち時間そのものには形が与えられてへん。そこを埋めるのが潮見や。

【この商品の生命線】
暦の各行は必ず「あんたが◯◯する週」の文法で書く。「彼から連絡が来る週」は禁止。
3人の設計者が独立に同じ弱点を白状した——「日付を売れば、外れが記録される」。
彼の行動を予言したら外れる。あんたの行動を指定したら、動いた事実が成果になって外れようがない。
同じ情報を売っとるのに、片方だけが崩れる。せやから src/lint.py の予言文法検査を
strict で通すまで、この商品は一枚も出さん。

  潮見   29,800円→9,800円 … 鑑定書＋九十日の暦＋解説
  構え    16,800円 … 潮見＋視直しの札2枚＋受けの型3〜5枚＋お守り札
"""
from __future__ import annotations

import html as _html
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import lint
from .diagnosis import with_honorific as _with_hon
from .kantei import (CHROME, OUT_DIR, _internal_brief,
                     add_honorific, soften_rude, strip_ai_leak,
                     strip_instruction_leak, strip_jargon, strip_markdown)
from .llm import complete

# ★★★2026-08-21：潮見を買うた人の月詠みは、本鑑定の人とは別のプランや。
#   本鑑定（3,980円）を買うた人 → 月詠み 3,980円
#   潮見（9,800円）を買うた人   → 月詠み 5,980円  ←★こっち
#   ★感想が返ってくるんは納品の何日もあとや。その頃には、何を買うた人か忘れとる。
#     LINEのやりとりを遡っても、オーダー番号しか残ってへんから商品は分からん。
#   ★★せやから【kantei_out に九十日の暦があるか】が、いちばん確実な見分け方になる。
#     暦がある＝潮見の人＝5,980円。★ここを間違えて安い方を送ったら、あとから値上げは言えん。
URL_TSUKIYOMI_SHIOMI = "https://buy.stripe.com/dRmdR88gCghlbf682e53O09"
TSUKIYOMI_SHIOMI_PRICE = "月5,980円"

SHIOMI_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。個別鑑定書に添える「九十日の暦（潮見表）」を組む。

これは9,800円（通常29,800円）の納品物の芯になる部分。相談者は、いつ動いていつ待つかが分からんまま毎日を過ごしとる。その待ち時間に形を与えるのがこの暦や。

【最重要・絶対厳守】
暦に書く行は、すべて主語を相談者にする。「彼から連絡が来る週」「彼が動く時期」のような、彼を主語にした未来の記述は一語も書いてはいけない。
★★ただし、毎行を「あんたは」で始めないこと。十三行が同じ言葉で始まったら読みにくい。主語は省いて、動詞から書き出す。相談者がやることだけを書けば、それで主語は伝わる。
★★★主語を省いてよいのは【相談者の行動】だけ。「連絡が来る週」「返事が来る」のような到来の表現は、主語を省いても彼の未来を書いたことになる。一語も書かない。書くのは「あんたが動いてええ週」「あんたが手を出さん週」「あんたが自分のことをする週」や。彼については「こういう男は、こういう状態の時に動きやすい」という性質の話までに留める。
これは表現の好みやのうて、商品の設計や。彼の行動を予言したら外れる。相談者の行動を指定したら、動いた事実そのものが成果になる。

声と文体:
- 一人称「ウチ」、相手は「あんた」。関西弁。暦の一言は短く、言い切る
- 慰めの嘘は書かない。ただし突き放さない
- 相談者の実際の事情（別れた日、彼の予定、記念日、本人の仕事）を暦に反映させる

厳守:
- 『宿曜』という占術名、宿の名前、専門用語は一切書かない
- 結果の保証はしない。過度に不安を煽らない。病気・健康・金運の断定はしない
- マークダウン記号は使わない
- 自分がAIであることを匂わせる一切を書かない。名乗るのは「椿」だけ
- 指定された形式を厳密に守って出力する"""

_FMT = """次の形式で、余計な前置きも後書きも付けずに出力してください。

=== 週 ===
（13行。1行ずつ「週番号|開始日|終了日|潮の名前|一言」を半角の縦棒で区切る。
　潮の名前は「静」「仕込み」「動」「凪」「守り」から選ぶ。
　一言は30〜45字。
　★★★主語の「あんたは」を毎行つけないこと。十三行ぜんぶ同じ言葉で始まったら読みにくい。
　　主語を省いて、いきなり動詞から書く。相談者がやることだけを書けば、それで主語は伝わる。
　　○「こっちからは何も送らん。手が届かん場所におるんが守りになる週や」
　　×「あんたは今週、こっちから何も送らん。手が届かん場所におるんが守りになる」
　★ただし主語を省いてええんは【相談者の行動】だけや。
　　「連絡が来る週」「返事が来る」のような到来の書き方は、主語を省いても予言になる。一語も書かない）
1|{d0}|{d6}|静|（一言）
…13行目まで

=== 動いてええ日 ===
（8〜10行。「日付|その日にやること」。日付は{start}から{end}の範囲内。
　★★★ここがこの暦でいちばん使われるとこや。読んだ人がその場で手を動かせるように書く。
　　「答えを求めん一通を置く」だけでは、何を書いたらええか分からんまま終わる。
　　★送る日には、送る文の見本を「」で必ず添える。相談者の事情に合う、短い一文にする。
　　★動く日には、何をどこまでやるかを具体的に書く。
　　○「平日の21時台に短い一通だけ置く。『暑いね、体だけ気ぃつけてな』——この温度で。送ったら追わん」
　　○「アプリをログアウトするか消す。通知も切る。開ける道を物理的に塞いでまう」
　　×「答えを求めん一通を送る」
　　×「自分の時間を大事にする」
　　一行は45〜75字。主語の「あんたは」は付けん）
2026-08-22|（やること。送る日なら見本の文も「」で入れる）

=== 手を出さん日 ===
（3〜5行。「日付|なぜその日は動かんのか」。相談者の事情・記念日・彼の予定から選ぶ。
　★なぜ動かんのかの理由を書く。「動かん」だけで終わらせん。
　★主語の「あんたは」は付けん。一行は35〜60字）

★★★日付の書き方（両方の欄に共通。ここを外したら暦の絵が壊れる）
・必ず 2026-08-24 の形で、一日ずつ書く。
・「8月18日以降の週末」「毎週土日」のような範囲や繰り返しは書かない。絵のマス目に置けんくなる。
　週の話をしたいときは、=== 週 === の一言の方に書く。
・{start} から {end} の範囲の外の日付を書かない。過ぎた日も書かない。
・★★同じ日付を「動いてええ日」と「手を出さん日」の両方に入れない。読む人 が矛盾で止まる。
　その日に何かをするなら「動いてええ日」に、何もせんのなら「手を出さん日」に、どちらか一方だけ置く。

=== 解説 ===
（900〜1200字。この暦をどう使うか。なぜこの並びになっとるか。
　最初の窓が来るまでに何を仕込むか。段落の区切りは空行。
　★ここでも、初めて動く日に送る一通は、そのまま送れる見本の文を「」で入れる。
　　何を書いたらあかんかも、一緒に書く）"""


@dataclass
class Shiomi:
    weeks: list[tuple[int, str, str, str, str]]   # 週番号, 開始, 終了, 潮, 一言
    go: list[tuple[str, str]]                     # 日付, やること
    stay: list[tuple[str, str]]                   # 日付, 理由
    note: str                                     # 解説


def _clean(text: str) -> str:
    return strip_ai_leak(strip_instruction_leak(strip_markdown(strip_jargon(text))))


def _parse(raw: str) -> Shiomi:
    def block(name: str) -> str:
        m = re.search(rf"===\s*{name}\s*===\s*(.*?)(?====|\Z)", raw, re.S)
        return m.group(1).strip() if m else ""

    weeks = []
    for ln in block("週").splitlines():
        p = [x.strip() for x in ln.split("|")]
        if len(p) >= 5 and p[0].isdigit():
            weeks.append((int(p[0]), p[1], p[2], p[3], p[4]))

    def pairs(name: str) -> list[tuple[str, str]]:
        out = []
        for ln in block(name).splitlines():
            p = [x.strip() for x in ln.split("|")]
            if len(p) >= 2 and re.search(r"\d", p[0]):
                out.append((p[0], p[1]))
        return out

    return Shiomi(weeks, pairs("動いてええ日"), pairs("手を出さん日"), block("解説"))


def generate_shiomi(name: str, me_birth: str, him_birth: str, details: str,
                    today: str | None = None) -> Shiomi:
    """九十日の暦を生成する。予言文法の検査を通らんかったら一度だけ書き直させる。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    d0 = datetime.strptime(today, "%Y-%m-%d").date()
    end = d0 + timedelta(days=89)
    fmt = _FMT.format(d0=today, d6=(d0 + timedelta(days=6)).isoformat(),
                      start=today, end=end.isoformat())
    user = (
        f"=== 内部参考（本文には翻訳して出す。用語・数字は出さない） ===\n"
        f"{_internal_brief(name, me_birth, him_birth, today)}\n\n"
        f"=== 相談者から届いた詳細（全文） ===\n{details}\n\n"
        f"=== 暦が見る期間 ===\n{today} から {end} までの90日\n\n{fmt}"
    )
    for attempt in (1, 2):
        raw = _clean(complete(SHIOMI_SYSTEM, user, max_tokens=4000, temperature=0.7).strip())
        s = _parse(raw)
        body = "\n".join(w[4] for w in s.weeks) + "\n" + "\n".join(g[1] for g in s.go) \
            + "\n" + "\n".join(t[1] for t in s.stay) + "\n" + s.note
        bad = lint.check_prophecy(body, strict=True)
        if not bad:
            return s
        print(f"  ⚠ 予言文法が{len(bad)}件（{attempt}回目）: {bad[0].text[:40]}")
        if attempt == 1:
            user += ("\n\n【書き直しの指示】前回の出力に、彼を主語にした未来の記述が混ざっとった。"
                     "例：" + bad[0].text[:50] + "。すべての行の主語を相談者にして書き直すこと。")
    return s


# ---------------- 暦の画像 ----------------

_SHIO_COLOR = {"動": "#b3364b", "仕込み": "#c98a3c", "守り": "#5b7c8d",
               "静": "#8a8f7a", "凪": "#9a9a9a"}


def build_calendar_html(name: str, s: Shiomi, today: str) -> str:
    d0 = datetime.strptime(today, "%Y-%m-%d").date()
    end = d0 + timedelta(days=89)
    # ★2026-08-13：ここで「09-05」を lstrip("0") しとったせいで "9/05" になり、
    #   マス目側の "9/5" と一致せんかった。1桁の日だけ暦に色が付かん事故や
    #   （生成は正しいのに、絵にだけ出えへん。刷ってから気づく類のやつ）。
    #   数字に直してから組み直す。
    def norm(v: str) -> str:
        m = re.search(r"(\d{1,2})\s*[-/月]\s*(\d{1,2})", v[-6:] if len(v) > 6 else v)
        return f"{int(m.group(1))}/{int(m.group(2))}" if m else v

    go = {norm(g[0]): g[1] for g in s.go}
    stay = {norm(t[0]): t[1] for t in s.stay}

    def key(d: date) -> str:
        return f"{d.month}/{d.day}"

    # 月ごとのマス目
    months, cur = [], date(d0.year, d0.month, 1)
    while cur <= end:
        cells = ""
        lead = (cur.weekday() + 1) % 7          # 日曜始まり
        cells += '<i class="pad"></i>' * lead
        d = cur
        while d.month == cur.month:
            k = key(d)
            cls = "day"
            if not (d0 <= d <= end):
                cls += " out"
            elif k in stay:
                cls += " stay"
            elif k in go:
                cls += " go"
            cells += f'<i class="{cls}">{d.day}</i>'
            d += timedelta(days=1)
        months.append(f'<div class="mo"><h4>{cur.month}月</h4>'
                      f'<div class="grid"><b>日</b><b>月</b><b>火</b><b>水</b>'
                      f'<b>木</b><b>金</b><b>土</b>{cells}</div></div>')
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)

    rows = "".join(
        f'<tr><td class="wk">{w[0]}</td>'
        f'<td class="dt">{w[1][5:].replace("-", "/")}〜{w[2][5:].replace("-", "/")}</td>'
        f'<td><span class="shio" style="background:{_SHIO_COLOR.get(w[3], "#8a8f7a")}">'
        f'{_html.escape(w[3])}</span></td>'
        f'<td class="cm">{_html.escape(w[4])}</td></tr>'
        for w in s.weeks)

    golist = "".join(f'<li><b>{_html.escape(g[0][5:].replace("-", "/"))}</b>'
                     f'{_html.escape(g[1])}</li>' for g in s.go)
    staylist = "".join(f'<li><b>{_html.escape(t[0][5:].replace("-", "/"))}</b>'
                       f'{_html.escape(t[1])}</li>' for t in s.stay)
    note = "".join(f"<p>{_html.escape(p.strip())}</p>"
                   for p in s.note.split("\n") if p.strip())

    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<title>九十日の暦 {_html.escape(name)}</title><style>
/* ★★2026-08-20：余白の直し。
   @page の margin は【印刷（PDF）にしか効かん】。
   スクショで出しとるPNGは screen メディアやから、body の margin:0 のまんまで
   端まで文字が行っとった。★LINEで送るんはPNGの方が多いのに、そっちだけ詰まっとった。
   せやから body に padding を持たせて、印刷のときだけ @page に任せて外す。 */
@page {{ size: A4; margin: 16mm; }}
body {{ font-family:"Hiragino Mincho ProN","Yu Mincho",serif; color:#241b1d;
  background:#fdfbf9; margin:0; padding:26px 30px 30px; box-sizing:border-box;
  font-size:10.5px; line-height:1.8; }}
@media print {{ body {{ padding:0; }} }}
.head {{ text-align:center; padding:6px 0 16px; border-bottom:2px solid #a52e44; }}
.head h1 {{ font-size:23px; margin:0 0 4px; letter-spacing:.14em; }}
.head p {{ margin:0; font-size:10px; color:#7a6a6d; letter-spacing:.06em; }}
.months {{ display:flex; gap:12px; justify-content:center; margin:16px 0 8px; }}
.mo h4 {{ text-align:center; font-size:11px; margin:0 0 5px; color:#7a6a6d; font-weight:normal; }}
.grid {{ display:grid; grid-template-columns:repeat(7,17px); gap:2px; }}
.grid b {{ font-size:7.5px; color:#a99; text-align:center; font-weight:normal; }}
.grid i, .grid .pad {{ display:grid; place-items:center; height:17px; font-style:normal;
  font-size:9px; border-radius:2px; }}
.day {{ background:#f0ebe6; color:#5c5052; }}
.day.out {{ background:transparent; color:#d8d0cc; }}
.day.go {{ background:#a52e44; color:#fff; font-weight:600; }}
.day.stay {{ background:#fff; color:#a52e44; box-shadow:inset 0 0 0 1.4px #a52e44; }}
.legend {{ text-align:center; font-size:9px; color:#7a6a6d; margin-bottom:14px; }}
.legend span {{ display:inline-block; margin:0 7px; }}
.dot {{ display:inline-block; width:9px; height:9px; border-radius:2px;
  vertical-align:-1px; margin-right:3px; }}
table {{ width:100%; border-collapse:collapse; margin-bottom:14px; }}
td {{ border-bottom:1px solid #e8e0da; padding:4.5px 5px; vertical-align:middle; }}
.wk {{ width:20px; text-align:center; color:#b0a3a5; font-size:9px; }}
.dt {{ width:78px; font-size:9.5px; color:#7a6a6d; white-space:nowrap; }}
.shio {{ display:inline-block; color:#fff; font-size:9px; padding:1.5px 7px;
  border-radius:2px; white-space:nowrap; }}
.cm {{ font-size:10px; }}
/* ★2026-08-20：ここは二段組をやめた。
   行に送る文の見本を入れる作りにしたんで、一行が45〜75字になる。
   細い二段に流し込んだら折り返しだらけで読めん。縦に並べる方が、手が動く。 */
.two {{ display:block; margin-bottom:14px; }}
.two > div {{ margin-bottom:12px; }}
h3 {{ font-size:11px; margin:0 0 5px; padding-bottom:3px; border-bottom:1px solid #a52e44;
  letter-spacing:.1em; }}
ul {{ list-style:none; padding:0; margin:0; font-size:10px; }}
li {{ padding:4px 0 4px 44px; border-bottom:1px dotted #e8e0da; line-height:1.75;
  text-indent:-44px; }}
li b {{ display:inline-block; min-width:40px; color:#a52e44; text-indent:0; }}
.note {{ border-top:2px solid #a52e44; padding-top:10px; }}
.note p {{ margin:0 0 7px; text-align:justify; font-size:10px; }}
.foot {{ text-align:center; font-size:8.5px; color:#a99; margin:14px 0 4px; }}
</style></head><body>
<div class="head"><h1>九十日の暦</h1>
<p>{_html.escape(_with_hon(name))}のために　{d0.year}年{d0.month}月{d0.day}日 — {end.year}年{end.month}月{end.day}日</p></div>
<div class="months">{''.join(months)}</div>
<div class="legend">
<span><i class="dot" style="background:#a52e44"></i>動いてええ日</span>
<span><i class="dot" style="background:#fff;box-shadow:inset 0 0 0 1.4px #a52e44"></i>手を出さん日</span>
<span><i class="dot" style="background:#f0ebe6"></i>ふだんの日</span></div>
<table>{rows}</table>
<div class="two">
<div><h3>動いてええ日</h3><ul>{golist}</ul></div>
<div><h3>手を出さん日</h3><ul>{staylist}</ul></div>
</div>
<div class="note"><h3>この暦の使い方</h3>{note}</div>
<div class="foot">椿</div>
</body></html>"""


def check_daylists(s: "Shiomi", today: str, horizon: int = 89) -> list[str]:
    """動いてええ日／手を出さん日の突き合わせ。

    ★2026-08-20 新設。生成に「同じ日を両方に入れるな」と書いても、実際に入ってきた。
      八月二十四日が「動いてええ日」と「手を出さん日」の両方に載って、
      読む人がどっちを信じたらええか分からん紙になっとった。
    ★★もう一つ、「8月18日以降の週末」みたいな範囲で書かれる事故もあった。
      build_calendar_html の norm() は一日ぶんの日付しか読めんので、
      こう書かれるとマス目に色が付かん。★文章には出るのに、絵には出えへん。
      刷ってから気づく類やから、ここで止める。
    """
    d0 = datetime.strptime(today, "%Y-%m-%d").date()
    end = d0 + timedelta(days=horizon)
    out: list[str] = []

    def parse(v: str) -> date | None:
        m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*", v)
        if not m:
            return None
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    seen: dict[date, str] = {}
    for label, rows in (("動いてええ日", s.go), ("手を出さん日", s.stay)):
        for raw, _ in rows:
            d = parse(raw)
            if d is None:
                out.append(f"{label}の日付が一日ぶんの形になっとらん（絵に出えへん）: {raw!r}")
                continue
            if not (d0 <= d <= end):
                out.append(f"{label}の日付が九十日の外や: {d.isoformat()}")
                continue
            if d in seen and seen[d] != label:
                out.append(f"{d.isoformat()} が「動いてええ日」と「手を出さん日」の両方に入っとる")
            seen[d] = label
    return out



# ★★★2026-08-21：潮見を買うた人の名簿。
#   月詠みの案内は、感想が返ってきてからやから、納品の何日もあとになる。
#   その頃には、この人が何を買うたか忘れとる。★LINEにはオーダー番号しか残ってへん。
#   ★★暦のファイルが有るかどうかで見分けよう、と最初は考えた。せやけどそれは危ない。
#     試しに作った暦が混ざるからや。実際、8月13日に「あや」「ゆき」で試作したもんが残っとって、
#     ファイル名だけ見たら、買うてもない人が潮見の人に見えとった。
#   ★★★せやから、名簿を別に持つ。作った時に、ここへ一行足す。
#     試作の時は、この行を手で消す。それだけの手間で、取り違えが消える。
#   ★★名簿は kantei_out の中に置く。つまり .gitignore の対象で、git には上がらん。
#     客の名前が入るからや。これは意図してそうしとる。バックアップは手元だけ。
#     ★別のパソコンで作業する時は、名簿がそっちに無い。その時は kantei_out ごと持っていくこと。
SHIOMI_LEDGER = OUT_DIR / "_潮見を買うた人.txt"


def record_shiomi_buyer(name: str, today: str) -> None:
    """潮見の名簿に一行足す。★同じ名前が二回来ても、重ねて書かん。"""
    SHIOMI_LEDGER.parent.mkdir(exist_ok=True)
    line = f"{today}\t{name}\t月詠みは5,980円"
    old = SHIOMI_LEDGER.read_text(encoding="utf-8") if SHIOMI_LEDGER.exists() else ""
    if any(l.split("\t")[1:2] == [name] for l in old.splitlines() if l.strip()):
        return
    head = ("# 潮見（9,800円）を買うた人の名簿。\n"
            "# ★この人らの月詠みは【月5,980円】や。本鑑定だけの人（3,980円）とちゃう。\n"
            "# ★★試しに作っただけの分は、この行を手で消しといて。\n"
            "# 日付\t名前\t月詠みの値段\n") if not old else ""
    SHIOMI_LEDGER.write_text(old + head + line + "\n", encoding="utf-8")


# ---------------- 暦の納品文 ----------------
#
# ★2026-08-20 新設。潮見を二人に納品して、二回とも同じ穴が開いた。
#   kantei 側の納品文は【鑑定書一枚】を前提に書かれとるんで、暦のことを一行も書かん。
#   そのまま送ったら、9,800円の半分を占める暦が「おまけの画像」として流される。
#   ★★ほんで暦は、使い方を知らんかったら機能せん紙や。
#     「行の主語が全部あんたやから外れようがない」いう設計も、
#     動く日と手を止める日の見どころも、説明せんかったら伝わらん。
#   ★★★せやから、暦には暦の納品文を別便で付ける。鑑定書の便とは分けて送る。
#     一通にまとめたら長すぎて、後半（＝暦の説明）から確実に読み飛ばされるからや。

CAL_NOTE_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。
九十日の暦（潮見表）を納品するときに、LINEで送る案内文の【見どころ】の部分だけを書く。

声と文体:
- 一人称「ウチ」、相手は「あんた」。関西弁。毒舌7：愛3の姉御肌
- 標準語のビジネス敬語にせん。テンプレの一斉配信調にせん

書くこと:
渡された暦の中から、その人にとって特に大事な日を【三つ】選んで、一つずつ短い段落で説明する。
各段落は「一つ。」「二つ。」「三つ。」で始める。

選ぶ基準:
- 一つ目は、いちばん近い山場か、本人の事情で特別な意味を持つ日
- 二つ目は、この九十日で初めてこっちから動く日（暦の「動」の週にある日）
- 三つ目は、いちばん先にある着地点の日
それぞれ、なんでその日なのかを、その人の事情に触れて書く。

【絶対厳守】
- 主語は必ず相談者にする。「彼から連絡が来る」「彼が動く」のような、彼を主語にした
  未来の記述は一語も書かない。書くのは相談者が何をするかだけ
- 「必ず戻る」「きっと会える」の類の保証を書かない。日付に「会える」と書かない
- 商品名・価格・決済リンクを一切書かない
- Markdown記号（**、#、- 、`）を一文字も使わない
- 『宿曜』という占術名、宿の名前、専門用語を書かない
- 見出しや前置きを書かない。三つの段落だけを出す
"""

# ★★この固定文の中で、予言の例を挙げるときは【彼を主語にせん】こと。
#   「彼から連絡が来る週」と書いてもうたら、否定文脈（＝一行も書いてへん）やのに
#   lint.check_prophecy が拾う。lint は文脈を見んし、見んでええ。ここは商品の生命線やから
#   検査は厳しいままにしといて、こっちの言い回しの方を避ける。
_CAL_NOTE_HEAD = """ほんで、九十日の暦の方や。ここは読み方があるから、先に言うとく。

まず、この紙は「待て」と言う紙やない。待つ時間に、形をつける紙や。

線が無いまま毎日を過ごすと、通知を開いては確かめて、そのたびに削れる。
それを九十日続けたら、向こうがどうこう言う前に、あんたの方が保たん。
せやから、先に線を引いとく。今日は動く日か、手を止める日か。
それが決まってたら、画面を見ても意味が変わる。

ほんで、この暦のいちばん大事なとこを言うとく。

書いてある行は、ぜんぶ主語が【あんた】や。

「◯月の◯週に連絡が来る」みたいな、彼を主語にした先の話は、一行も書いてへん。
ウチは彼の動きを予言せん。外れるからや。
書いたんは「この週は手を出さん」「この日は動いてええ」——
全部、あんたが決めて、あんたが動く話や。せやからこの暦は、外れようがない。
あんたが動いた事実が、そのまま結果になる。

見どころを三つだけ、先に言うとく。
"""

# ★★★2026-08-28：ここに「感想を聞かせてな」と月詠みの線引きを入れとったが、外した。
#   ★鑑定書の納品文（＝一通目）に、まったく同じ文が入っとる。
#     二通続けて送るんやから、読む側には同じ締めが二回来る形になっとった。
#     （実際、千佳さんの回で「感想依頼」と「線引き」が一字一句おんなじで二回出た）
#   ★★二通目は、一通目の続きや。締めは一通目で済んどる。
#     二通目は【暦にしか無い話】だけで終わらせる。
#   ★★★感想依頼と月詠みの線引きは、必ず【一通目にだけ】置く。
_CAL_NOTE_TAIL = """この暦は、今日から手元に置いといたらええ。
今日が動く日か、手を止める日か——迷た時は、それだけ見てくれたらええからな🌙"""


def generate_calendar_note(name: str, s: "Shiomi", details: str) -> str:
    """九十日の暦に添える納品文。★鑑定書の納品文とは別便で送るための一通。"""
    weeks = "\n".join(f"{w[1]}〜{w[2]} [{w[3]}] {w[4]}" for w in s.weeks)
    go = "\n".join(f"{g[0]} {g[1]}" for g in s.go)
    stay = "\n".join(f"{t[0]} {t[1]}" for t in s.stay)
    user = (
        f"=== 相談者から届いた詳細（全文） ===\n{details}\n\n"
        f"=== 組んだ暦・週ごと ===\n{weeks}\n\n"
        f"=== 動いてええ日 ===\n{go}\n\n"
        f"=== 手を出さん日 ===\n{stay}\n\n"
        "この暦の見どころを三つ、書いてください。"
    )
    for attempt in (1, 2):
        raw = complete(CAL_NOTE_SYSTEM, user, max_tokens=1200, temperature=0.8).strip()
        mid = add_honorific(soften_rude(strip_jargon(strip_markdown(
            strip_ai_leak(strip_instruction_leak(raw))))), name).strip()
        bad = lint.check_prophecy(mid, strict=True)
        if not bad:
            break
        print(f"  ⚠ 納品文に予言文法が{len(bad)}件（{attempt}回目）: {bad[0].text[:40]}")
        if attempt == 1:
            user += ("\n\n【書き直しの指示】前回の出力に、彼を主語にした未来の記述が混ざっとった。"
                     "例：" + bad[0].text[:50] + "。すべての行の主語を相談者にして書き直すこと。")
    # ★DELIVERY_CLOSING（月詠みの線引き）は付けん。一通目に入っとる。上のコメント参照
    return f"{_CAL_NOTE_HEAD}\n{mid}\n\n{_CAL_NOTE_TAIL}"


def make_shiomi(name: str, me_birth: str, him_birth: str, details: str,
                today: str | None = None) -> dict:
    """九十日の暦を生成してPDFとPNGを出す。鑑定書とセットで潮見（29,800円→9,800円）になる。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    OUT_DIR.mkdir(exist_ok=True)
    print("🌊 九十日の暦を組んどる…")
    s = generate_shiomi(name, me_birth, him_birth, details, today)
    print(f"  ✓ {len(s.weeks)}週 / 動いてええ日{len(s.go)} / 手を出さん日{len(s.stay)} "
          f"/ 解説{len(s.note)}字")

    body = "\n".join(w[4] for w in s.weeks) + "\n" + s.note
    problems = lint.check_prophecy(body, strict=True)
    problems += lint.check_dates("\n".join(f"{g[0]} {g[1]}" for g in s.go + s.stay),
                                 today=today, horizon_days=95, scope="all")
    problems += check_daylists(s, today)
    print("  ✅ 自動検査 問題なし" if not problems else f"  ⚠ 自動検査 {len(problems)}件")
    for p in problems:
        print(f"   ・{p}")

    stem = f"九十日の暦_{name}"
    html_path = OUT_DIR / f"{stem}.html"
    pdf_path = OUT_DIR / f"{stem}.pdf"
    html_path.write_text(build_calendar_html(name, s, today), encoding="utf-8")
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                   check=True, capture_output=True, timeout=120)
    png_path = OUT_DIR / f"{stem}.png"
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--window-size=1000,1414",
                    f"--screenshot={png_path}", html_path.resolve().as_uri()],
                   check=True, capture_output=True, timeout=120)
    for p in (pdf_path, png_path):
        shutil.copy2(p, Path.home() / "Downloads" / f"九十日の暦_{_with_hon(name)}{p.suffix}")
    print(f"  📜 {pdf_path}\n  🖼 {png_path}")

    # ★暦の納品文は、鑑定書の納品文とは別便や。ここで必ず出す（2026-08-20ルール化）。
    #   「あとで書く」にしたら、暦がおまけの画像として流れる。二回それをやった。
    print("✍️ 暦の納品文を生成中…")
    cal_note = generate_calendar_note(name, s, details)
    note_path = OUT_DIR / f"納品文_{name}_暦.txt"
    note_path.write_text(cal_note, encoding="utf-8")
    print(f"  💬 暦の納品文: {note_path}")
    # ★★2026-08-21：潮見を買うた人の月詠みは【5,980円】や。本鑑定の人（3,980円）とちゃう。
    #   感想が返ってきた時に案内するんやが、その頃には何を買うた人か忘れとる。
    #   ★せやから、作った時点でここに出しとく。あとで kantei_out を見返す時の証拠にもなる。
    record_shiomi_buyer(name, today)
    print(f"  🌙 この人の月詠みは【{TSUKIYOMI_SHIOMI_PRICE}】や（本鑑定の人とはちゃう）")
    print(f"     名簿に足しといた: {SHIOMI_LEDGER}")
    print(f"     {URL_TSUKIYOMI_SHIOMI}")
    return {"shiomi": s, "pdf": str(pdf_path), "png": str(png_path),
            "note": cal_note, "note_path": str(note_path),
            "tsukiyomi_url": URL_TSUKIYOMI_SHIOMI, "problems": problems}

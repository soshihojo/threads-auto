"""潮見・構え（16,800円）の上乗せ分——札と型。

潮見（9,800円）は「いつ動くか」が分かる商品や。
構えはそこに、その日が来た時に手が止まらんための道具を足す。

  視直しの札 二枚 … 45日以内に盤面が動いたら「視直し」とLINEで送るだけで、
                    二日のうちに四段の視直し文書が返る。権利を画像の札にして渡す
  受けの型        … その人の盤面から要る場面だけを選んで、そのまま送れる文面を作る。
                    場面の数は揃えん。音信不通の子に「会う当日の締め」の札は死に札やからな
  お守り札 一枚   … 鑑定書の中でいちばん効いた一行を一枚にする。
                    不安な夜にそれだけ開いたらええ

★権利を「札」という物にするんは演出やない。数えられる権利は使う重みが生まれるし、
　使い切った瞬間が次への橋になる。抽象の「アフターフォロー付き」とは別物や。
"""
from __future__ import annotations

import html as _html
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import lint
from .kantei import (CHROME, OUT_DIR, strip_ai_leak, strip_instruction_leak,
                     strip_jargon, strip_markdown)
from .llm import complete

KAMAE_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。16,800円の「潮見・構え」に入れる、相談者が実際に使う道具を作る。

これは読み物やない。相談者が場面に出くわした時に開いて、そのまま使う道具や。だから飾らん。短く、具体的に、そのまま送れる形で書く。

声と文体:
- 一人称「ウチ」、相手は「あんた」。関西弁
- 送る文面そのものは、相談者が自分で打ったように見える自然な日本語にする（関西弁にせんでええ。相談者の普段の言葉づかいに寄せる）
- 条件と禁じ手は一行で言い切る

厳守:
- 場面の数を揃えようとせん。その人の盤面で起こりうる場面だけを選ぶ。起こらん場面の札は死に札や
- 彼を主語にした未来の断定は書かない（「彼から連絡が来る」は禁止）。書くのは「こうなった時」という条件と、相談者の動きだけ
- 『宿曜』という占術名、宿の名前、専門用語は一切書かない
- 結果の保証はしない。過度に不安を煽らない
- マークダウン記号は使わない
- 自分がAIであることを匂わせる一切を書かない。名乗るのは「椿」だけ
- 指定された形式を厳密に守る"""

_KATA_FMT = """次の形式で、前置きも後書きも付けずに出力してください。

=== 型 ===
（3〜5行。この相談者の盤面で実際に起こりうる場面だけを選ぶ。
　1行につき「場面|そのまま送れる文面|出す条件|やったらあかんこと」を半角の縦棒で区切る。
　文面は相談者本人が打つ言葉として自然に、40〜90字。
　条件と禁じ手はそれぞれ25〜40字）
彼から短い一通が来た時|（文面）|（出す条件）|（やったらあかんこと）

=== お守り ===
（1行。鑑定書の中で、この人がいちばん救われる一行を選ぶか、
　同じ意味を25〜45字に凝縮して書く。不安な夜に開く一枚に載せる言葉や）"""


@dataclass
class Kamae:
    kata: list[tuple[str, str, str, str]]   # 場面, 文面, 条件, 禁じ手
    omamori: str


def _clean(t: str) -> str:
    return strip_ai_leak(strip_instruction_leak(strip_markdown(strip_jargon(t))))


def generate_kamae(name: str, details: str, kantei_body: str = "") -> Kamae:
    """受けの型とお守りの一行を作る。"""
    user = (
        f"=== 相談者から届いた詳細 ===\n{details}\n\n"
        f"=== この人に渡した鑑定書の中身（お守りの一行はここから選ぶ） ===\n"
        f"{kantei_body[:4000] if kantei_body else '（無し）'}\n\n{_KATA_FMT}"
    )
    kata: list[tuple[str, str, str, str]] = []
    omamori = ""
    for attempt in (1, 2):
        raw = _clean(complete(KAMAE_SYSTEM, user, max_tokens=2500, temperature=0.7).strip())

        # ★2026-08-13：見出し（=== 型 ===）を省いて本文だけ返してくることがある。
        #   見出し前提で切っとったせいで、中身は正しいのに0枚になった。
        #   お守りの塊だけ先に切り離して、残りから縦棒の行を拾う。
        m2 = re.search(r"===\s*お守り\s*===\s*(.+)", raw, re.S)
        omamori = m2.group(1).strip().splitlines()[0].strip() if m2 else ""
        head = raw[:m2.start()] if m2 else raw
        head = re.sub(r"===\s*型\s*===", "", head)
        kata = []
        for ln in head.splitlines():
            p = [x.strip() for x in ln.split("|")]
            if len(p) >= 4 and len(p[1]) > 10:
                kata.append((p[0], p[1], p[2], p[3]))
        if not omamori:                      # お守りの見出しも無い時は最終行を拾う
            tail = [l.strip() for l in raw.splitlines() if l.strip() and "|" not in l]
            omamori = tail[-1] if tail else ""

        body = "\n".join(k[0] + k[2] + k[3] for k in kata) + "\n" + omamori
        bad = lint.check_prophecy(body, strict=True)
        if 3 <= len(kata) <= 5 and omamori and not bad:
            return Kamae(kata, omamori)
        if bad:
            print(f"  ⚠ 予言文法{len(bad)}件（{attempt}回目）: {bad[0].text[:40]}")
            user += f"\n\n【書き直し】彼を主語にした未来の記述が混ざっとった：{bad[0].text[:50]}。直すこと。"
        elif not (3 <= len(kata) <= 5):
            print(f"  ⚠ 型が{len(kata)}枚（{attempt}回目）。3〜5枚で作り直す")
            user += "\n\n【書き直し】型は3〜5行、必ず縦棒で4つに区切って出すこと。"
    return Kamae(kata, omamori)


# ---------------- 札の絵 ----------------

_CSS = """
@page { size: 108mm 135mm; margin: 0; }
* { box-sizing: border-box; }
body { margin:0; width:1080px; height:1350px; display:flex; align-items:center;
  justify-content:center; font-family:"Hiragino Mincho ProN","Yu Mincho",serif;
  background:#f7f3ef; }
.card { width:960px; height:1230px; background:#fffdfb; border:2px solid #a52e44;
  padding:64px 60px; display:flex; flex-direction:column; position:relative; }
.fill { flex:1; display:flex; flex-direction:column; justify-content:center; min-height:0; }
.card::after { content:""; position:absolute; inset:14px; border:1px solid #e7d9d2; }
.kind { font-size:24px; letter-spacing:.5em; color:#a8853f; margin-bottom:8px; }
h1 { font-size:52px; margin:0 0 34px; color:#241b1d; line-height:1.35; letter-spacing:.06em; }
.rule { height:3px; background:#a52e44; width:96px; margin-bottom:38px; }
.lead { font-size:28px; color:#6f6165; line-height:1.9; margin:0 0 34px; }
.msg { font-size:33px; line-height:2.05; color:#241b1d; background:#f6f0ea;
  border-left:6px solid #a52e44; padding:34px 32px; margin:0; white-space:pre-wrap; }
.meta { font-size:25px; line-height:1.85; padding-top:26px; }
.meta div { display:flex; gap:18px; padding:16px 0; border-top:1px solid #e7d9d2; }
.meta b { color:#a8853f; white-space:nowrap; font-weight:normal; letter-spacing:.14em; }
.meta span { color:#4c4143; }
.big { font-size:44px; line-height:2.0; color:#241b1d; margin:0; text-align:center; }
.sig { position:absolute; right:56px; bottom:44px; font-size:24px; color:#a52e44;
  letter-spacing:.3em; }
.note { font-size:24px; color:#6f6165; line-height:1.85; margin:0; }
"""


def _card(kind: str, title: str, inner: str) -> str:
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>{_CSS}</style>'
            f'</head><body><div class="card"><div class="kind">{_html.escape(kind)}</div>'
            f'<h1>{_html.escape(title)}</h1><div class="rule"></div>{inner}'
            f'<div class="sig">椿</div></div></body></html>')


def _split(inner: str) -> str:
    """本文は余白の真ん中に置き、条件と禁じ手は下に敷く。"""
    i = inner.find('<div class="meta">')
    return (f'<div class="fill">{inner}</div>' if i < 0
            else f'<div class="fill">{inner[:i]}</div>{inner[i:]}')


def _shoot(html: str, out: Path) -> None:
    tmp = out.with_suffix(".html")
    tmp.write_text(html, encoding="utf-8")
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--window-size=1080,1350",
                    "--hide-scrollbars", f"--screenshot={out}", tmp.resolve().as_uri()],
                   check=True, capture_output=True, timeout=120)
    tmp.unlink(missing_ok=True)


def make_kamae(name: str, details: str, kantei_body: str = "",
               today: str | None = None) -> dict:
    """構えの上乗せ分（札と型）を作って画像で出す。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    d0 = datetime.strptime(today, "%Y-%m-%d").date()
    expire = d0 + timedelta(days=45)
    OUT_DIR.mkdir(exist_ok=True)
    print("🎴 構えの札を作っとる…")
    k = generate_kamae(name, details, kantei_body)
    print(f"  ✓ 受けの型{len(k.kata)}枚 / お守り{len(k.omamori)}字")

    made: list[str] = []
    dl = Path.home() / "Downloads"

    # 視直しの札 二枚
    for i in (1, 2):
        inner = (
            f'<p class="lead">盤面が動いた時、この札を切り。<br>'
            f'LINEに「視直し」と送ってくれたらええ。</p>'
            f'<div class="msg">二日のうちに、こう返す。\n\n'
            f'一、何が起きたと、ウチが視るか\n二、彼の側から見た意味\n'
            f'三、次の一手。いつ、何を\n四、今はやらんこと</div>'
            f'<div class="meta"><div><b>つかえる期限</b>'
            f'<span>{expire.year}年{expire.month}月{expire.day}日まで</span></div>'
            f'<div><b>のこり</b><span>この札で {i} 枚目（ぜんぶで二枚）</span></div></div>')
        p = OUT_DIR / f"札_視直し{i}_{name}.png"
        _shoot(_card("みなおしの札", "視直しの札", _split(inner)), p)
        made.append(str(p)); shutil.copy2(p, dl / f"札_視直し{i}_{name}さん.png")

    # 受けの型
    for i, (scene, msg, cond, ng) in enumerate(k.kata, 1):
        inner = (f'<div class="msg">{_html.escape(msg)}</div>'
                 f'<div class="meta">'
                 f'<div><b>出す時</b><span>{_html.escape(cond)}</span></div>'
                 f'<div><b>やらんこと</b><span>{_html.escape(ng)}</span></div></div>')
        p = OUT_DIR / f"型{i}_{name}.png"
        _shoot(_card("うけのかた", scene, _split(inner)), p)
        made.append(str(p)); shutil.copy2(p, dl / f"型{i}_{name}さん.png")

    # お守り札
    p = OUT_DIR / f"札_お守り_{name}.png"
    _shoot(_card("おまもり", "不安になった夜に",
                 _split(f'<p class="big">{_html.escape(k.omamori)}</p>')), p)
    made.append(str(p)); shutil.copy2(p, dl / f"札_お守り_{name}さん.png")

    print(f"  🎴 {len(made)}枚（視直し2・型{len(k.kata)}・お守り1）")
    return {"kamae": k, "cards": made, "expire": expire.isoformat()}

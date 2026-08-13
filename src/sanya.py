"""椿の三夜（14,800円）——盤面が動いた、その一点だけを視る鑑定。

★2026-08-13 新設。個別鑑定書（3,980円）との違いは「厚み」やのうて「仕事」や。

  個別鑑定書 … あの日のあんたと彼を視た地図。買うた時点の一枚絵
  三夜       … その地図の外で起きたことを、新しい素材から視直す

せやから三夜には、必ず新しい素材（報告フォーム5問の答え）を入れる。
入力が前と同じなら、出てくるもんも前の焼き直しにしかならん。
討論での指摘そのままや——「入力が初回の11問と同じなら、生成は同じ素材の再蒸留しかできん」。

三晩に分けるのは演出やのうて、仕事の順番や。
  一夜目 読み解き   何が起きたか。彼がその一手を打った時の温度。今の彼の本音
  二夜目 潮の引き直し これからの見通し。動いてええ時期と、手を出さん方がええ時期
  三夜目 手の書     次の三手と、そのまま送れる文面。返しが早い時・遅い時・返らん時
  ＋判             ウチならどの道を取るか。理由も書く。ただし決めるのはあんた

出す前に必ず lint を通す（src/lint.py）。
  ・予言文法   二夜目は時期を扱う。彼を主語にした未来の断定が一語でも入ったら止める
  ・日付の整合 過ぎた日・期間外・存在せん日
  ・焼き直し   前に渡した鑑定書との一致。18%を超えたら書き直し
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from . import lint
from .kantei import (OUT_DIR, _internal_brief, build_html, html_to_pdf,
                     strip_ai_leak, strip_instruction_leak, strip_jargon,
                     strip_markdown)
from .llm import complete

SANYA_SYSTEM = """あなたは恋愛・復縁専門の占い師「椿（つばき）」。すでに個別鑑定書を渡した相談者に、そのあと盤面が動いたときだけ渡す「三夜」の本文を書く。

これは14,800円の納品物。相手は前の鑑定書を手元に持っとって、読み込んどる。だから「前に書いたことの言い換え」は一行たりとも許されん。今回届いた新しい話（相談者が報告してくれた出来事と、その時のやりとりの原文）を素材にして、前の鑑定書には物理的に書けんかったことだけを書く。

声と文体:
- 一人称「ウチ」、相手は「あんた」。関西弁。話し言葉すぎない「手紙の文体」で、じっくり読ませる
- ダッシュ（——）は1章に1〜2回まで。基本は読点か句点で切る
- 毒舌と愛は半々。慰めの嘘は書かないが、突き放さない
- 相談者が書いてくれた言葉、彼が言うた言葉を、そのまま引用して扱う。引用こそがこの商品の芯や

厳守:
- 前の鑑定書で書いた見立ての要約や言い換えを書かない。参照するなら一言だけ（「鑑定書で書いた通り」）に留め、そこから先の新しい話に進む
- 『宿曜』という占術名、宿の名前、「距離」「命・業・胎」などの専門用語は一切書かない。内部参考は日常の言葉に翻訳する
- 【最重要】彼を主語にした未来の断定を書かない。「彼から連絡が来る」「彼が動く」は予言であって鑑定やない。時期を書くときは必ず主語をあんた（相談者）にして、あんたが何をする時期かを書く。彼については「こういう時に動きやすい男や」という性質の話までに留める
- 結果の保証はしない。過度に不安を煽らない。病気・健康・金運の断定はしない
- マークダウン記号（#や*や-）は使わない。プレーンな段落文。段落の区切りは空行
- 絵文字は使わない（最後の夜の締めの一文にだけ🌙を1つ）
- 【絶対厳禁】自分がAIであることを匂わせる一切を書かない。モデル名、開発元の名前、「執筆者」「作成者」「生成」といった署名を、本文の途中にも末尾にも絶対に付けない。名乗るのは「椿」だけ
- 指定された夜の内容だけを書く。他の夜で扱う内容を先取りしない。見出しは書かず、本文だけを出力する"""

NIGHTS = [
    ("yomitoki", "一夜目　読み解き", 2500,
     "今回届いた報告——何が起きて、彼がどんな言葉を使うたか——を、一語ずつ解く夜や。"
     "相談者が書き写してくれた彼の言葉を、実際に引用しながら扱う。"
     "その言葉を彼が選んだ時、彼の中で何が起きとったか。言葉の表面と、その裏にある動きを分ける。"
     "前の鑑定書で視た彼の性質を土台にするが、性質の説明そのものを繰り返してはいけない。"
     "『あの性質の男が、この場面でこの言葉を選んだ』という一点だけを深く視る。"
     "相談者がその言葉をどう受け取って、今どこが一番しんどいかにも、正面から触れる。"),
    ("shio", "二夜目　潮の引き直し", 2000,
     "前の鑑定書で描いた見通しを、今回の出来事を入れて引き直す夜や。"
     "何が変わって、何が変わってへんかを、はっきり分ける。"
     "そのうえで、これから相談者が『動いてええ時期』と『手を出さん方がええ時期』を書く。"
     "【絶対厳守】主語は必ず相談者にする。『彼から連絡が来る時期』のような予言は一語も書かない。"
     "書くのは『あんたが動いてええ時期』『あんたが静かにしとく時期』や。"
     "彼については『こういう男は、こういう状態の時に動きやすい』という性質の話までに留める。"
     "期間は今日から90日以内で書く。具体的な日付を出す時は、その日に相談者が何をするかまで書く。"),
    ("te", "三夜目　手の書", 1800,
     "次にやる三つの手を、順番に書く夜や。"
     "一つめは今すぐやること。二つめは待ってからやること。三つめは、ある出来事が起きた時にだけやること。"
     "そのうえで、実際に送る文面を一本、そのまま使える形で書く。"
     "さらに、その文面を送った後の分岐を三つ書く——彼の返しが早い時、遅い時、返ってこん時。"
     "それぞれ、どう受けるかと、やったらあかんことを一つずつ。"
     "最後に、やったらあかん一手を名指しで一つ挙げて締める。"),
]

HAN = ("判（はん）", 400,
       "三夜の最後に渡す一枚や。ここまで視てきたうえで、"
       "『ウチならこの道を取る』を一つだけ選んで言い切る。理由を三つ挙げる。"
       "そのうえで最後に必ず『ただし、決めるのはあんたや』の意味の一文を置く。"
       "断定するのは椿の見立てであって、結果の保証やない。"
       "短く、強く、繰り返しなしで書く。")


def _gen(system: str, user: str) -> str:
    """1本生成して、納品物に入ったらあかんもんを全部落とす。"""
    body = strip_jargon(complete(system, user, max_tokens=3500, temperature=0.7).strip())
    body = strip_markdown(body)
    trimmed = strip_instruction_leak(body)
    if trimmed != body:
        print("  ⚠ 指示文の混入を検知し、その段落以降を切り落とした")
        body = trimmed
    cleaned = strip_ai_leak(body)
    if cleaned != body:
        print("  ⚠ モデル名・署名の混入を除去した")
    return cleaned


def generate_sanya(name: str, me_birth: str, him_birth: str, report: str,
                   previous: str = "", today: str | None = None) -> list[dict]:
    """三夜の本文を3晩分＋判を生成して返す。

    report … 報告フォーム5問の答え（何が起きた・いつ・原文・どう返した・いまどう感じてる）
    previous … 前に渡した鑑定書の本文。焼き直しの照合と、重複回避の指示に使う
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    brief = _internal_brief(name, me_birth, him_birth, today)
    prev_digest = (previous[:3000] + "…") if previous else "（前の鑑定書は無し）"
    toc = "\n".join(f"・{t}" for _, t, _, _ in NIGHTS)

    done: list[dict] = []
    for key, title, chars, instruction in NIGHTS:
        already = "\n".join(f"【{d['title']}】{d['body'][:200]}…" for d in done) \
            or "（まだ無い。これが最初の夜）"
        user = (
            f"=== 内部参考（本文には翻訳して出す。用語・数字は出さない） ===\n{brief}\n\n"
            f"=== 前に渡した鑑定書の中身（ここに書いてあることは繰り返さない） ===\n{prev_digest}\n\n"
            f"=== 今回届いた報告（これが今回の素材や。ここから視る） ===\n{report}\n\n"
            f"=== 三夜の全体構成 ===\n{toc}\n\n"
            f"=== ここまでに書いた夜（重複を避ける参考） ===\n{already}\n\n"
            f"=== 今回書く夜 ===\n{title}\n目安の分量: {chars}字（±2割）\n"
            f"この夜で書くこと: {instruction}\n\n本文だけを出力してください。"
        )
        body = _gen(SANYA_SYSTEM, user)
        done.append({"key": key, "title": title, "body": body})
        print(f"  ✓ {title}（{len(body)}字）")

    # 判は三夜の中身を全部見てから書く
    han_title, han_chars, han_inst = HAN
    user = (
        f"=== 内部参考 ===\n{brief}\n\n"
        f"=== 今回届いた報告 ===\n{report}\n\n"
        f"=== 三夜で書いたこと ===\n"
        + "\n".join(f"【{d['title']}】{d['body'][:800]}…" for d in done)
        + f"\n\n=== 今回書くもの ===\n{han_title}\n目安の分量: {han_chars}字\n"
          f"書くこと: {han_inst}\n\n本文だけを出力してください。"
    )
    han = _gen(SANYA_SYSTEM, user)
    done.append({"key": "han", "title": han_title, "body": han})
    print(f"  ✓ {han_title}（{len(han)}字）")
    return done


def check(nights: list[dict], previous: str = "", today: str | None = None) -> list:
    """出す前の検査。二夜目は時期を扱うので予言文法を厳しく見る。"""
    problems = []
    for n in nights:
        strict = n["key"] == "shio"          # 潮の夜＝日付が主役
        for p in lint.check_prophecy(n["body"], strict=strict):
            p.kind = f"{p.kind}／{n['title']}"
            problems.append(p)
        for p in lint.check_dates(n["body"], today=today):
            p.kind = f"{p.kind}／{n['title']}"
            problems.append(p)
        if previous:
            for p in lint.check_rehash(n["body"], previous):
                p.kind = f"{p.kind}／{n['title']}"
                problems.append(p)
    return problems


def make_sanya(name: str, me_birth: str, him_birth: str, report: str,
               previous: str = "", today: str | None = None) -> dict:
    """三夜を生成して、夜ごとにPDFまで出す。予約配信はここではやらん（人が送る）。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    OUT_DIR.mkdir(exist_ok=True)
    print(f"🌙 三夜を生成中（{len(NIGHTS)}夜＋判）…")
    nights = generate_sanya(name, me_birth, him_birth, report, previous, today)

    print("🔎 自動検査…")
    problems = check(nights, previous=previous, today=today)
    if problems:
        print(f"  ⚠ {len(problems)}件見つかった（下に出す。直してから渡すこと）")
        for p in problems:
            print(f"   ・{p}")
    else:
        print("  ✅ 問題なし")

    paths = []
    for i, n in enumerate(nights, 1):
        stem = f"三夜{i}_{name}_{n['key']}"
        html_path = OUT_DIR / f"{stem}.html"
        pdf_path = OUT_DIR / f"{stem}.pdf"
        html_path.write_text(
            build_html(name, [n], today, sub="椿の三夜", title=n["title"],
                       meta_note="盤面が動いたあとの、あなたひとりのための視立てです。"),
            encoding="utf-8")
        html_to_pdf(html_path, pdf_path)
        dl = Path.home() / "Downloads" / f"三夜{i}_{name}さん.pdf"
        shutil.copy2(pdf_path, dl)
        paths.append(str(pdf_path))
        print(f"  📜 {n['title']}: {pdf_path}")

    total = sum(len(n["body"]) for n in nights)
    return {"nights": nights, "pdfs": paths, "chars": total, "problems": problems}

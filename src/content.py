"""投稿文の生成。ルール集（ペルソナ/型/必勝要素/NG）をClaudeに渡して生成する。"""
from __future__ import annotations

import random
import re
from pathlib import Path

from .config import ROOT, active_profile, load_config
from .llm import complete

# 型ファイルから抽出する型の見出し（02_templates.md の "## 型①〜" を想定）
SYSTEM = """あなたは地域店舗の集客に詳しいSNS運用のプロです。
渡されたルール（ペルソナ・型・必勝要素・NG）に厳密に従い、Threads向けの日本語投稿を1つだけ作成します。

絶対条件:
- 1行目（冒頭フック）で必ず指を止めさせる
- 必勝要素を最低3つ含める
- NG表現は使わない
- マーケ横文字を避け、店主の現場の言葉で書く
- 売り込み臭を出しすぎない（価値提供を優先）
- 出力は投稿本文のみ。説明・前置き・引用符・ハッシュタグの羅列は不要
- Markdownの装飾記号を一切使わない（**太字**、見出し#、箇条書きの記号*など禁止）。Threadsはそのまま文字として表示されるため。プレーンテキストで書く
- 文字数は概ね80〜400字"""


def sanitize(text: str) -> str:
    """Threadsはmarkdownを解釈せず記号がそのまま出るため、装飾記号を除去する。
    特に `**`（太字）は絶対に残さない。"""
    text = text.replace("**", "")          # 太字記号は必ず除去
    text = text.replace("__", "")          # 下線太字
    # 行頭の見出し記号（#, ##…）と箇条書き記号（- , * , ・の直後空白）を除去
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s{0,3}[*\-]\s+", "", text)
    return text.strip()


def _read_rules(rules_dir: str) -> dict[str, str]:
    base = ROOT / rules_dir
    out: dict[str, str] = {}
    for p in sorted(base.glob("*.md")):
        out[p.stem] = p.read_text(encoding="utf-8")
    return out


def pick_region() -> str | None:
    """アクティブプロファイルの region_words から1つをランダムに選ぶ。"""
    words = active_profile().get("region_words") or []
    return random.choice(words) if words else None


def generate_post(type_hint: str | None = None, region: str | None = None) -> str:
    """1投稿の本文を生成して返す。type_hint で型を指定（無ければ自動選択）。
    region を渡すとその地域名を使う（None なら内部でランダム選択）。"""
    profile = active_profile()
    rules = _read_rules(profile["rules_dir"])
    if region is None:
        region = pick_region()

    rules_block = "\n\n".join(f"# {name}\n{body}" for name, body in rules.items())
    instructions = [
        f"アクティブプロファイル: {profile['name']}",
        f"オファー（必要時のみ自然に）: {profile.get('offer','')}",
    ]
    if region:
        instructions.append(f"この地域名を自然に本文へ入れる: {region}")
    if type_hint:
        instructions.append(f"使う型: {type_hint}")
    else:
        instructions.append("型カタログから状況に合う型を1つ選んで使う（オファー直球型は頻度を抑える）")

    user = (
        "以下のルールに従って、Threads投稿を1つ作ってください。\n\n"
        f"=== ルール集 ===\n{rules_block}\n\n"
        f"=== 今回の指示 ===\n" + "\n".join(f"- {x}" for x in instructions)
    )
    return sanitize(complete(SYSTEM, user, max_tokens=800, temperature=1.0))


def generate_best(candidates: int | None = None, region: str | None = None) -> str:
    """複数候補を生成し、最良の1本を選んで返す。region を渡すと全候補で同じ地域を使う。"""
    cfg = load_config()
    n = candidates or cfg["posting"].get("candidates", 1)
    posts = [generate_post(region=region) for _ in range(max(1, n))]
    if len(posts) == 1:
        return posts[0]
    return _judge(posts)


def _judge(posts: list[str]) -> str:
    """候補をClaudeに採点させ、最も手挙げ（コメント）を取れそうな1本を選ぶ。"""
    listed = "\n\n".join(f"[{i}]\n{p}" for i, p in enumerate(posts))
    sys = "あなたはThreadsのバズ投稿を見抜く編集者です。冒頭フックの強さと手挙げ（コメント）誘発力で評価します。"
    user = (
        "次の候補から、店舗オーナーの手挙げを最も取れそうな投稿を1つ選び、"
        "その番号だけを半角数字で1文字返してください。\n\n" + listed
    )
    try:
        ans = complete(sys, user, max_tokens=8, temperature=0.0)
        idx = int("".join(ch for ch in ans if ch.isdigit())[:2])
        if 0 <= idx < len(posts):
            return posts[idx]
    except Exception:
        pass
    return posts[0]

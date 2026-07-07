"""LINE相談ログから「お客様の声」を学習し、投稿生成へ反映する。

相談者がLINEで送ってきた生の言葉（悩み・言い回し・感情の動き）をClaudeで分析し、
rules_dir/06_customer_voice.md に書き出す。content.py は rules_dir の全mdを読み込むため、
このファイルを更新するだけで以後の投稿生成に自動で効く。

プライバシー: 名前・生年月日など個人が特定できる情報は学習ファイルに残さない
（入力段階で生年月日をマスクし、相談者は匿名ラベル化。プロンプトでも禁止を明示）。
"""
from __future__ import annotations

import re
from datetime import datetime

from . import store
from .config import ROOT, active_profile
from .llm import complete

VOICE_FILE = "06_customer_voice.md"

# 入力段階でマスクする生年月日パターン（1995年4月3日 / 1995/4/3 / 1995-04-03 等）
_BIRTH_RE = re.compile(r"(?:19|20)\d{2}\s*[年/.\-]\s*\d{1,2}\s*[月/.\-]\s*\d{1,2}\s*日?")

# 会話ログの肥大化対策（データが増えたら直近を優先して切り詰める）
_MAX_LINES = 600
_MAX_CHARS_PER_MSG = 200

_SYS = """あなたは恋愛・復縁相談アカウント「椿姉」のマーケ分析担当です。
LINEに届いた相談者との生のやりとりから、SNS投稿（Threads/Instagram）の質を上げる
「お客様の声データ」を抽出・言語化します。ゴールは、読んだ女性が「私のことだ…」と
感じる解像度の投稿を作れるようにすることです。

抽出するもの:
1. 悩みテーマの分布（音信不通/既読スルー/急に冷めた/復縁したい/片思い など、多い順に件数感つき）
2. 相談者の「生の言い回し」（絵文字や口語のニュアンスをそのまま。投稿の冒頭フックに転用できるもの）
3. 感情が動いた瞬間（どんな返しに「当たってる」「怖い」などの強い反応が返ってきたか）
4. 投稿に使える切り口（上記から導ける具体的な投稿アイデア。最大5つ）

厳守:
- 名前・生年月日・年齢・職業・地名など、個人が特定できる情報は一切書かない
- 相談の固有ストーリーをそのまま再現しない（言い回しレベルの短い引用はOK）
- 宿曜・宿名などの専門用語は書かない
- 既存のデータがあれば引き継ぎ、新データと矛盾すれば更新し、重複は統合する
- 箇条書き中心で簡潔に。出力はMarkdownの本文のみ（前置き・コードフェンス不要）"""


def mine_customer_voice(*, days: int = 45, min_messages: int = 10) -> dict:
    """LINE相談ログを分析して rules_dir/06_customer_voice.md を更新する。"""
    chats = store.all_line_chats(days=days)
    user_msgs = [c for c in chats if c.get("role") == "user"]
    if len(user_msgs) < min_messages:
        return {"updated": False, "reason": f"相談メッセージが不足（{len(user_msgs)}/{min_messages}件）"}

    # 相談者を匿名ラベル化し、生年月日をマスクした会話ログを組み立てる
    labels: dict[str, str] = {}
    lines: list[str] = []
    for c in chats:
        uid = str(c.get("user_id", ""))
        if uid not in labels:
            labels[uid] = f"相談者{len(labels) + 1}"
        role = "相談者" if c.get("role") == "user" else "椿姉"
        text = _BIRTH_RE.sub("（生年月日）", str(c.get("text", "")).strip())
        lines.append(f"[{labels[uid]}] {role}: {text[:_MAX_CHARS_PER_MSG]}")
    if len(lines) > _MAX_LINES:
        lines = lines[-_MAX_LINES:]

    path = ROOT / active_profile()["rules_dir"] / VOICE_FILE
    current = path.read_text(encoding="utf-8") if path.exists() else "（まだ記録なし）"

    user = (
        "## これまでのお客様の声データ（引き継ぎ・更新の土台）\n" + current + "\n\n"
        f"## 直近{days}日のLINE相談ログ（{len(labels)}人・相談者メッセージ{len(user_msgs)}件）\n"
        + "\n".join(lines) + "\n\n"
        "上記をふまえ、お客様の声データを更新してください。"
    )
    body = complete(_SYS, user, max_tokens=2000, temperature=0.4)

    today = datetime.now().strftime("%Y-%m-%d")
    header = (
        f"# お客様の声（LINE相談ログから自動学習 / 最終更新 {today}）\n\n"
        "※ このファイルは `voice-learn` コマンドが自動生成・上書きします。手で編集しても次回上書きされます。\n"
        "投稿生成時は、ここにある実際の悩み・言い回しの解像度で「私のことだ」と思わせること。\n\n"
    )
    path.write_text(header + body.strip() + "\n", encoding="utf-8")
    return {"updated": True, "users": len(labels), "messages": len(user_msgs), "path": str(path)}

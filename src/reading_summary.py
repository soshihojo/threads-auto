"""A short, source-grounded action page for a completed reading."""
import html
import json

from .llm import complete

FIELDS = {"situation": "今の整理", "action": "まずすること", "avoid": "今は控えること", "review": "見直す目安"}
SYSTEM = """完成した恋愛相談の鑑定書を、相談者が読み返せる短い要点に整理する。
一人称はウチ。穏やかな関西弁で、相談者と相手を尊重する。呼び名は入力の指定通り。
相談者が報告した事実と、鑑定書の解釈を混同しない。相手の内心や未来は事実として断定しない。
本文にない日付・約束・人物・送信文は補わない。本文の矛盾を勝手に解決しない。
具体策は本人が選べる範囲に限る。返事がない場合や拒否がある場合に追いかける行動を勧めない。
見直しは日付がなければ状況の変化を目安にし、不明なら『状況が変わった時に、改めて整理しよな』程度にとどめる。
購入や継続の誘導をしない。追加の情報がない部分は、そのことを明記する。
JSONオブジェクトのみ。キーは situation, action, avoid, review の4つ。
各値は40〜140字の文字列、箇条書きやMarkdownを使わず、各1段落。
"""


def validate(summary):
    if not isinstance(summary, dict) or set(summary) != set(FIELDS):
        raise ValueError("要点ページの項目が不足しています")
    for value in summary.values():
        if not isinstance(value, str) or not 1 <= len(value) <= 160 or "\n" in value:
            raise ValueError("要点ページの文章が長すぎるか、形式が不正です")
        if any(mark in value for mark in ("**", "```", "# ")):
            raise ValueError("要点ページに装飾記号が混じっています")
    return summary


def generate(name, chapters):
    source = "\n\n".join(c["title"] + "\n" + c["body"] for c in chapters)
    response = complete(SYSTEM, f"呼び名：{name}\n\n完成した本文：\n{source}",
                        max_tokens=3000, temperature=0.3, require_complete=True)
    return validate(json.loads(response))


def page(summary):
    validate(summary)
    sections = "".join(f'<section><h3>{title}</h3><p>{html.escape(summary[key])}</p></section>'
                       for key, title in FIELDS.items())
    return ('<div class="page summary"><div class="chapno">読み返すための一枚</div>'
            '<h2>ここからの動き方</h2>' + sections +
            '<p class="summary-note">この要点は本文の整理です。相手の気持ちや結果を保証するものではありません。'
            '実際の言葉や行動を見ながら、あんた自身で選んでな。</p>'
            '<div class="foot">椿｜彼の本音しか視ん</div></div>')

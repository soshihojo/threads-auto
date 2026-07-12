"""投稿文の生成。ルール集（ペルソナ/型/必勝要素/NG）をClaudeに渡して生成する。"""
from __future__ import annotations

import random
import re
from pathlib import Path

from .config import ROOT, active_profile, load_config
from .llm import complete

# 型ファイルから抽出する型の見出し（02_templates.md の "## 型①〜" を想定）
SYSTEM = """あなたは地域店舗の集客に詳しいSNS運用のプロです。
渡されたルール（ペルソナ・型・必勝要素・NG）に従い、Threads向けの日本語投稿を1つだけ作成します。

絶対条件:
- 【最重要】短く書く。全体で概ね60〜120字。長文は読まれない。要素を詰め込まず、削れる言葉は全部削る
- 【最重要】1行目で必ず指を止めさせる。「自分が何をしている人か」か「具体的な結果・数字」を冒頭に置く。説明・前置き・自己紹介の長い導入から入らない
- 自分の拠点・所在地は名乗らない（「大阪で〜しています」のような書き方をしない）。何を提供している人かだけ伝える。地域名は実話や呼びかけ対象の店の地域として使う
- 自分（自社）の実績・事例として語ってよいのは、ルール集の「実話アーカイブ」（06_real_cases.md）にある実話のみ。新しい事例や数字を捏造しない。同じ実話をフック・教訓・文体を変えて使い回すのは推奨
- 実話を使わない回は一般論型で書く（「よくあるのが…」等）。一般論では自分の実績と主張する具体数字を出さない
- 具体的な数字を1つだけ入れる（盛らない・嘘くさくしない）。身近・実体験のリアルさを出す
- 数量・金額はアラビア数字＋カンマ区切りで書く（例: 10,000枚 / 3,000部 / 50,000円）。「1万」「3千」「数万」のような漢数字表記は使わない
- 売り込み・自慢をしない。謙虚で自然なトーン
- 締め（CTA）は必ず「いいね」を促す形に統一する。コメントやDM・返信を求めない（手挙げのハードルを最小にするため）
- 【重要】いいねの対象を必ず明示する。「試してみたい方はいいね」だけでは何を試すのか伝わらない。「チラシでの集客を試してみたい方はいいね」「HPの無料診断を受けてみたい方はいいね」のように、サービス・提供内容をCTAに入れる
- 【重要】そのために、本文のどこか（できれば1行目）で「自分が何を提供している人か」を伝える。読んだ店主が「いいねを押せば、これをやってもらえるんだな」と分かる状態にする
- 「いいねが多ければ次に投稿します」など、実行を約束する表現は絶対に使わない
- NG表現・マーケ横文字を避け、現場の言葉で書く
- 出力は投稿本文のみ。説明・前置き・引用符・ハッシュタグの羅列は不要
- Markdownの装飾記号を一切使わない（**太字**、見出し#、箇条書きの記号*など禁止）。プレーンテキストで書く"""

# プロファイルが system_prompt / variation.closing_directive / judge_* を持たない場合のデフォルト。
# （local_owner=集客プロファイルは従来どおりこのデフォルトで動く）
DEFAULT_CLOSING = (
    "扱うテーマは必ず3商品（ポスティング代行/SNSアカウント運用/HP制作・リニューアル）のいずれか、"
    "またはその組み合わせに関わる内容にする。"
    "全体は60〜120字で短く、1行目で必ず指を止める。具体数字を1つだけ入れ、身近な実体験のリアルさを出す。"
    "締め（CTA）は必ず『いいね』を促す形にし、何に対するいいねか（サービス・提供内容）を明示する。"
    "本文で『自分が何を提供している人か』が伝わり、いいね＝それを頼める合図だと分かるようにする。"
    "コメント・DM・返信は求めない。『次に投稿します』等の実行しない約束は禁止。売り込まない。"
    "過去の投稿と業種・話題・文体・締めが被らないようにし、同一パターンの連発を避ける。"
)
DEFAULT_JUDGE_SYSTEM = (
    "あなたはThreadsのバズ投稿を見抜く編集者です。冒頭フックの強さと手挙げ（コメント）誘発力で評価します。"
)
DEFAULT_JUDGE_GOAL = "店舗オーナーの手挙げを最も取れそうな投稿"


def sanitize(text: str) -> str:
    """Threadsはmarkdownを解釈せず記号がそのまま出るため、装飾記号を除去する。
    特に `**`（太字）は絶対に残さない。"""
    text = text.replace("**", "")          # 太字記号は必ず除去
    text = text.replace("__", "")          # 下線太字
    # 行頭の見出し記号（#, ##…）と箇条書き記号（- , * , ・の直後空白）を除去
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s{0,3}[*\-]\s+", "", text)
    return _ensure_line_url(text.strip())


def _ensure_line_url(text: str) -> str:
    """CTA（生年月日の送信を促す）投稿に、LINEのURLと「無料」の明記を保証する
    （生成AIが書き忘れても配信前にコードで補正する）。"""
    url = active_profile().get("line_url", "")
    if not url or "生年月日" not in text:  # 生年月日を求める回＝無料鑑定CTA。それ以外は触らない
        return text
    if url not in text:
        label = "▶ " + ("" if "無料" in text else "無料鑑定はこちら → ") + url
        return f"{text}\n{label}"
    if "無料" not in text:
        # URLはあるが「無料」の明記が無い → URL行にラベルを付けて補う
        text = text.replace(f"▶ {url}", f"▶ 無料鑑定はこちら → {url}", 1)
        if "無料" not in text:
            text = text.replace(url, f"無料鑑定はこちら → {url}", 1)
    return text


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


def pick_case() -> dict | None:
    """variation.case_focus からネタ元（実話 or 一般論）を1つ選ぶ。無ければNone。"""
    v = active_profile().get("variation") or {}
    cases = v.get("case_focus") or []
    return random.choice(cases) if cases else None


def generate_candidate(force_cta: bool = False) -> tuple[str, str | None]:
    """候補1本を生成し (本文, 地域) を返す。
    実話回はその実話の地域だけを使い、一般論回のみランダム地域を使う（無関係な地域の混入防止）。
    force_cta=True のときは型に関わらず必ずDM/無料鑑定への誘導を入れる。"""
    case = pick_case()
    region = (case or {}).get("region") or pick_region()
    return generate_post(region=region, case=case, force_cta=force_cta), region


def _variation_directives(profile: dict, case: dict | None = None) -> list[str]:
    """profile.variation の各リストから1つずつランダムに選び、生成の振り（指示）を作る。
    似た投稿への収束を防ぐ。variation が無いプロファイルでは空（従来動作）。"""
    v = profile.get("variation") or {}
    labels = v.get("labels") or {}
    out: list[str] = []
    if v.get("industries"):
        lbl = labels.get("industries", "今回の業種（自己投影させる主役）")
        out.append(f"{lbl}: {random.choice(v['industries'])}")
    if v.get("topics"):
        lbl = labels.get("topics", "今回の話題（チラシ以外も積極的に。これを軸に）")
        out.append(f"{lbl}: {random.choice(v['topics'])}")
    if case:
        if case.get("region"):  # 実話回
            out.append(f"今回のネタ元: {case['label']}。"
                       "業種・地域・数字は06_real_cases.mdの事実のまま変えない。"
                       "話題や業種の指定が実話と噛み合わなければ、実話に合わせて寄せる（実話が最優先）。"
                       "実話の地域以外の地域名を本文に出さない。")
        else:  # 一般論回
            out.append(f"今回のネタ元: {case['label']}。"
                       "自分の実績と主張する具体数字は出さず、あるある・考え方で価値を出す。")
    weights = v.get("type_weights") or {}
    if weights:
        chosen = random.choices(list(weights.keys()), weights=list(weights.values()))[0]
        out.append(f"使う型（02_templates.md準拠）: {chosen}")
    if v.get("tones"):
        out.append(f"今回の文体・トーン: {random.choice(v['tones'])}")
    if v.get("ctas"):
        out.append(f"今回の締め方: {random.choice(v['ctas'])}")
    if out:
        out.append(v.get("closing_directive") or DEFAULT_CLOSING)
    return out


def generate_post(type_hint: str | None = None, region: str | None = None,
                  case: dict | None = None, force_cta: bool = False) -> str:
    """1投稿の本文を生成して返す。type_hint で型を指定（無ければ自動選択）。
    region を渡すとその地域名を使う（None なら内部でランダム選択）。
    case はネタ元（pick_case の戻り値）。整合した生成には generate_candidate() を推奨。"""
    profile = active_profile()
    rules = _read_rules(profile["rules_dir"])
    if case is None and not type_hint:
        case = pick_case()
    if region is None:
        region = (case or {}).get("region") or pick_region()

    rules_block = "\n\n".join(f"# {name}\n{body}" for name, body in rules.items())
    instructions = [
        f"アクティブプロファイル: {profile['name']}",
        f"オファー（必要時のみ自然に）: {profile.get('offer','')}",
    ]
    if region:
        instructions.append(f"この地域名を自然に本文へ入れる（文脈に合わなければ無理に入れず省略してよい）: {region}")
    if type_hint:
        instructions.append(f"使う型: {type_hint}")
    else:
        # 業種・話題・型・文体・締めをランダムに振って多様化（収束防止）
        instructions.extend(_variation_directives(profile, case))

    if force_cta:
        instructions.append(
            "【必須・最優先】この投稿は、どの型であっても最後に必ず無料鑑定への誘導を入れる。"
            "誘導は3つセットで促すこと：①コメントに🌙（or🔮）を送る ②フォローする ③二人の生年月日をDMで送る。"
            "『コメントに🌙送って、フォローして、二人の生年月日をDMに送ってきてな。ウチが視たる』のように、"
            "向こうから動く形で締める。コメントだけで終わる誘導は禁止。"
        )

    user = (
        "以下のルールに従って、Threads投稿を1つ作ってください。\n\n"
        f"=== ルール集 ===\n{rules_block}\n\n"
        f"=== 今回の指示 ===\n" + "\n".join(f"- {x}" for x in instructions)
    )
    system = profile.get("system_prompt") or SYSTEM
    return sanitize(complete(system, user, max_tokens=800, temperature=1.0))


def generate_best(candidates: int | None = None, region: str | None = None,
                  case: dict | None = None) -> str:
    """複数候補を生成し、最良の1本を選んで返す。region/case を渡すと全候補で同じものを使う。"""
    cfg = load_config()
    n = candidates or cfg["posting"].get("candidates", 1)
    posts = [generate_post(region=region, case=case) for _ in range(max(1, n))]
    if len(posts) == 1:
        return posts[0]
    return _judge(posts)


def _judge(posts: list[str]) -> str:
    """候補をClaudeに採点させ、最も手挙げ（コメント）を取れそうな1本を選ぶ。"""
    profile = active_profile()
    listed = "\n\n".join(f"[{i}]\n{p}" for i, p in enumerate(posts))
    sys = profile.get("judge_system") or DEFAULT_JUDGE_SYSTEM
    goal = profile.get("judge_goal") or DEFAULT_JUDGE_GOAL
    user = (
        f"次の候補から、{goal}を1つ選び、"
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

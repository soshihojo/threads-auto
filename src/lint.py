"""納品物を出す前の自動検査（lint）。

★2026-08-13：上位商品（潮見・三夜・四十五夜）で「日付」を売り始めるにあたって新設した。

3人の設計者が独立に同じ弱点を白状した——「日付を売るということは、外れが記録されるということ」。
散文の鑑定書は外れが特定されにくいが、「8月22日から29日が窓」と書いた紙は検証可能になる。
外れた時、失望は日付に紐づいて記録され、責任の所在が椿一点に集まる。
せやから、日付を売り始める前に「外れようのない書き方」へ強制する仕組みを先に作る。

検査は3つ。

  1. 予言文法（check_prophecy）
     彼を主語にした未来の断定を弾く。主語は必ず相談者（あんた）に固定する。
     「8月22日に彼から連絡が来る」＝予言（外れる）
     「8月22日はあんたが動いてええ週」＝行動の指定（外れようがない。動いた事実が成果になる）
     同じ情報を売っとるのに、片方だけが崩れる。文法の違いだけやから機械で見える。

  2. 日付の整合（check_dates）
     過ぎた日付・期間の外・存在せん日付・窓の重なりを弾く。
     90日分の日付を毎回人の目で照合するんは無理で、しかもここが一番事故が高うつく。
     「一番リスクの高い部品に一番薄い検品を割り当てるな」（討論での指摘）。

  3. 焼き直し（check_rehash）
     前に渡した鑑定書の言い換えを弾く。
     14,800円を買う人は既に3,980円の鑑定書を読んどる。そこに同じ話が出た瞬間、
     「これ前に読んだ」となって増量商法に見える。二晩目を読む前に冷める。

どれも「直す」んやなく「止める」。文面の判断は人がやる。機械は見落とさへんことだけをやる。

使い方:
    from src import lint
    problems = lint.check_all(body, today="2026-08-13", previous=old_kantei_body)
    if problems:
        for p in problems:
            print(p)

    # 潮見表など、日付が主役の成果物は strict=True（彼を主語にした行を一切許さん）
    problems = lint.check_prophecy(calendar_text, strict=True)

    # コマンドからも叩ける
    #   python -m src.lint 見る対象.txt --today 2026-08-13 --previous 前の鑑定書.txt
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

__all__ = ["Problem", "check_prophecy", "check_dates", "check_rehash", "check_all"]


@dataclass
class Problem:
    """見つかった問題ひとつ。kind＝どの検査か、line＝何行目か、text＝該当箇所。"""
    kind: str
    line: int
    text: str
    why: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.line}行目: {self.text}\n    → {self.why}"


# ──────────────────────────────────────────────────────────────
# 共通の下ごしらえ
# ──────────────────────────────────────────────────────────────

def _lines(text: str) -> list[tuple[int, str]]:
    """行番号つきで返す。空行は飛ばす。"""
    return [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if ln.strip()]


# 文の切れ目。予言の判定は「同じ文の中に日付と彼がおるか」で見るので、
# 文を跨いだ誤検知を避けるために先に割る。
_SENT_SPLIT_RE = re.compile(r"(?<=[。？！\n])")


def _sentences(line: str) -> list[str]:
    return [s for s in _SENT_SPLIT_RE.split(line) if s.strip()]


# ──────────────────────────────────────────────────────────────
# 1. 予言文法
# ──────────────────────────────────────────────────────────────

# 彼を指す言葉。※「あの男」「あいつ」等は別のルール（04_avoid）で既に禁止しとる。
_HIM = r"(?:彼|相手|あの人|向こう)"

# 彼が主語になる「未来の出来事」。断定で書いたら予言になる動詞だけを並べる。
# 「彼の性質は〜や」「彼は寂しがりや」みたいな性質の記述は予言やないので入れへん。
_HIM_FUTURE_RE = re.compile(
    _HIM + r"(?:から|が|は|も|の方から)?[^。！？]{0,20}?"
    r"(?:"
    r"連絡|返信|LINE|ライン|電話|音沙汰|返事"
    r")[^。！？]{0,10}?"
    r"(?:来る|くる|入る|届く|ある|寄こす|よこす|再開する)"
    r"|" + _HIM + r"(?:が|は|も|から)[^。！？]{0,15}?"
    r"(?:動く|戻る|戻ってくる|会いたくなる|気づく|変わる|決める|折れる|"
    r"謝る|寂しくなる|思い出す|連絡してくる|返してくる|現れる)"
)

# 上の形でも、条件・仮定・過去なら予言やない。
#   「彼から連絡が来た時は」＝条件（起きてから何をするかの話）
#   「彼から連絡が来るかもしれん」＝可能性（断定やない）
# ここに当たったら見逃す。
#   「彼がいつ動くかは書かん」＝予言せんと宣言しとる文。これを弾いたら本末転倒や。
#   動詞のすぐ後ろの「か」は、断定やのうて名詞節・疑問の印なので同じ扱いにする。
_HEDGE_RE = re.compile(
    r"^か[はがをにも、。や]"                       # 動くか｜は／を／に…（名詞節・疑問）
    r"|^の[はがをにも]|^こと[はがをにも]"           # 動くのを待つ／動くことは…（名詞節）
    r"|(?:たら|れば|なら|時|とき|場合|際|かどうか|かもしれ|かも知れ|"
    r"こともある|可能性|やろか|やろうか|だろうか|とは限らん|とは限らない|"
    r"保証はでき|約束はでき|書かん|書かない|言わん|言えん|言うつもりはない|"
    r"分からん|わからん|見えん|決めつけ)"
)

# 日付・時期を指す言葉。これと彼の未来が同じ文に同居したら予言や。
_WHEN_RE = re.compile(
    r"\d{1,2}\s*月\s*\d{1,2}\s*日"          # 8月22日
    r"|\d{1,2}\s*/\s*\d{1,2}"               # 8/22
    r"|\d{1,2}\s*日(?:ごろ|頃|以降|まで|から)"  # 22日ごろ
    r"|\d{1,2}\s*週目|第\s*\d{1,2}\s*週"
    r"|来週|再来週|今週末|来月|再来月|月末|月初|上旬|中旬|下旬"
    r"|\d{1,3}\s*日(?:目|後|以内)"
    r"|お盆|年末|年明け|連休"
)


def check_prophecy(text: str, strict: bool = False) -> list[Problem]:
    """彼を主語にした未来の断定を探す。

    strict=True は潮見表・暦など「日付が主役」の成果物用。
    そこでは日付が近くにあるかどうかに関係なく、彼を主語にした未来の記述を全部止める。
    暦の各行は必ず「あんたが◯◯する週」の形で書く、というのが商品の約束やからや。
    """
    out: list[Problem] = []
    for lineno, line in _lines(text):
        for sent in _sentences(line):
            m = _HIM_FUTURE_RE.search(sent)
            if not m:
                continue
            # 動詞の直後を見て、条件・可能性の形やったら見逃す
            tail = sent[m.end():m.end() + 12]
            if _HEDGE_RE.search(tail) or _HEDGE_RE.search(m.group(0)):
                continue
            if strict or _WHEN_RE.search(sent):
                why = ("彼を主語にした未来の断定になっとる。"
                       "主語をあんた（相談者）に替えて、行動を指定する形に直すこと。"
                       "例：「彼から連絡が来る週」→「あんたが動いてええ週」")
                out.append(Problem("予言文法", lineno, sent.strip()[:80], why))
    return out


# ──────────────────────────────────────────────────────────────
# 2. 日付の整合
# ──────────────────────────────────────────────────────────────

_MD_RE = re.compile(r"(\d{1,2})\s*(?:月|/)\s*(\d{1,2})\s*日?")

# ★2026-08-13：納品済みの鑑定書5冊に掛けたら、日付の検査だけ60件も鳴った。
#   中身は「7月2日に別れた」「6月10日が彼の誕生日」——相談者が話してくれた過去の出来事や。
#   これを「過ぎた日付」として弾いたら、鑑定書は一冊も出せんようになる。
#   検査せなあかんのは「これから動く日」として書いた日付だけ。
#   その文が“これからの行動”を指しとるかどうかで見分ける。
_ACTION_CTX_RE = re.compile(
    r"窓|仕込|静観|様子見|動いてええ|動く(?:日|週|とき)|送ってええ|送らん|控え|"
    r"地雷|避けたい|向く日|向いとる|区切り|山場|潮|待ちや|待つ週|手を出さん|"
    r"一手|切り出す|(?:まで|から)は?\s*(?:待|置|動|送)|"
    r"(?:して|やって)ええ|(?:して|やって)みぃ|しとき|おき"
)


# ★同上：行動の言葉が入っとっても、過去形なら「これからの窓」やない。
#   実例（納品済みの2冊）：
#     「あんたが7月4日から今日まで一度も送らんかった、その判断は正しい」
#     「今回長文を送らんかった判断、7月2日の連絡の出し方」
#   どっちも相談者の過去の振る舞いを褒めとる文で、窓の指定やない。
_PAST_RE = re.compile(
    r"かった|だった|やった|しとった|してた|してくれた|くれた|"
    r"(?:送|来|言|会|返|届|置|待|動|切)っ?た(?![らりるれろ])|"
    r"以前|前回|去年|昨年|一昨日|昨日|これまで|今まで|今日まで"
)


def _is_forward(sent: str) -> bool:
    """その文が「これから動く日」の話かどうか。過去の出来事の記述と切り分ける。"""
    return bool(_ACTION_CTX_RE.search(sent)) and not _PAST_RE.search(sent)


def _resolve(month: int, day: int, today: date) -> date | None:
    """年の入ってへん「8月22日」を、今日に一番近い実在の日付に直す。

    12月→1月をまたぐ暦があるので、今年で読んで大きく過去になる時だけ来年で読み直す。
    存在せん日付（2月30日）は None を返して、呼び出し側で弾く。
    """
    for year in (today.year, today.year + 1, today.year - 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if -180 <= (d - today).days <= 185:
            return d
    try:
        return date(today.year, month, day)
    except ValueError:
        return None


def check_dates(text: str, today: str | date | None = None,
                horizon_days: int = 100, scope: str = "action") -> list[Problem]:
    """日付の事故を探す。過ぎた日・期間の外・存在せん日・同じ日の重複。

    horizon_days は「その紙が面倒を見る期間」。90日の暦やから既定は少し余裕を見て100日。

    scope は、どの日付を検査の対象にするか。
      "action"（既定）… これからの行動を指しとる文の日付だけ見る。
                        鑑定書の本文には「7月2日に別れた」みたいな過去の話が出てくる。
                        そこを弾いたら一冊も出せんようになるので見逃す。
      "all"          … 全部の日付を見る。潮見表・暦のように、書いてある日付が
                        全部これからの窓である成果物にはこっちを使う。
    """
    if today is None:
        today = date.today()
    elif isinstance(today, str):
        today = datetime.strptime(today.strip()[:10].replace("/", "-"), "%Y-%m-%d").date()

    out: list[Problem] = []
    seen: dict[date, int] = {}          # 同じ日が何行目に出たか
    for lineno, line in _lines(text):
        for sent in _sentences(line):
            forward = scope == "all" or _is_forward(sent)
            for m in _MD_RE.finditer(sent):
                month, day = int(m.group(1)), int(m.group(2))
                if not (1 <= month <= 12):
                    continue            # 「25/30」みたいな別の数字。日付やない
                frag = m.group(0)
                d = _resolve(month, day, today)
                if d is None:
                    # 存在せん日付だけは、過去の話でも生成の事故やから必ず止める
                    out.append(Problem(
                        "日付の整合", lineno, frag,
                        f"{month}月{day}日は実在せん日付や。生成し直すこと。"))
                    continue
                if not forward:
                    continue            # これからの行動の話やない＝過去の出来事の記述
                delta = (d - today).days
                if delta < 0:
                    out.append(Problem(
                        "日付の整合", lineno, frag,
                        f"{d} は今日（{today}）より前や。過ぎた日を窓に書いたら、"
                        "受け取った人はその場で気づく。"))
                elif delta > horizon_days:
                    out.append(Problem(
                        "日付の整合", lineno, frag,
                        f"{d} は今日から{delta}日先で、この紙が見とる{horizon_days}日を"
                        "はみ出しとる。範囲の中に直すこと。"))
                if d in seen and seen[d] != lineno:
                    out.append(Problem(
                        "日付の整合", lineno, frag,
                        f"{d} は{seen[d]}行目にも出とる。窓が重なっとらんか確かめること。"))
                seen.setdefault(d, lineno)
    return out


# ──────────────────────────────────────────────────────────────
# 3. 焼き直し
# ──────────────────────────────────────────────────────────────

_NOISE_RE = re.compile(r"[\s、。，．,.「」『』（）()｜|・…—－\-〜~！？!?：:；;]")


def _normalize(text: str) -> str:
    """比べる前の下ごしらえ。全角半角と記号の揺れで擦り抜けられんようにする。"""
    return _NOISE_RE.sub("", unicodedata.normalize("NFKC", text))


def check_rehash(new_text: str, previous: str, *, k: int = 16,
                 ratio_limit: float = 0.18, run_limit: int = 60) -> list[Problem]:
    """前に渡した紙の焼き直しを探す。

    k文字の窓を滑らせて、前の紙にそのまま入っとる窓がどれだけあるかを数える。
    ratio_limit＝全体の何割まで許すか（既定18%）。定型の言い回しや相談者本人の
    引用でどうしても多少は被るので、ゼロにはせん。
    run_limit＝連続して一致してええ文字数（既定60字）。ここを超えたら、
    それは言い回しの偶然やのうて、段落ごと持ってきとる。
    """
    a, b = _normalize(new_text), _normalize(previous)
    if len(a) < k or len(b) < k:
        return []

    old = {b[i:i + k] for i in range(len(b) - k + 1)}
    hit = [a[i:i + k] in old for i in range(len(a) - k + 1)]

    out: list[Problem] = []
    ratio = sum(hit) / len(hit)
    if ratio > ratio_limit:
        out.append(Problem(
            "焼き直し", 0, f"前の紙との一致 {ratio:.0%}",
            f"許容の{ratio_limit:.0%}を超えとる。相談者は前の鑑定書を手元に持っとる。"
            "同じ話が出た時点で「これ前に読んだ」になる。新しい素材（報告フォームの回答）"
            "から書き直すこと。"))

    # 連続して一致しとる場所＝段落ごと持ってきた疑いのある箇所を拾う
    i = 0
    while i < len(hit):
        if not hit[i]:
            i += 1
            continue
        j = i
        while j < len(hit) and hit[j]:
            j += 1
        length = (j - i) + k - 1          # 窓の重なりを足し戻した実際の文字数
        if length >= run_limit:
            out.append(Problem(
                "焼き直し", 0, a[i:i + min(length, 70)] + "…",
                f"{length}字が前の紙とそのまま同じや。ここは丸ごと書き直すこと。"))
        i = j
    return out


# ──────────────────────────────────────────────────────────────
# まとめて掛ける
# ──────────────────────────────────────────────────────────────

def check_all(text: str, *, today: str | date | None = None,
              previous: str | None = None, strict_prophecy: bool = False,
              horizon_days: int = 100) -> list[Problem]:
    """3つの検査をまとめて掛ける。何も出んかったら空のリストが返る。"""
    out = check_prophecy(text, strict=strict_prophecy)
    out += check_dates(text, today=today, horizon_days=horizon_days)
    if previous:
        out += check_rehash(text, previous)
    return out


def assert_clean(text: str, **kw) -> None:
    """問題があったら例外で止める。納品パイプラインに挟む用。"""
    problems = check_all(text, **kw)
    if problems:
        print("\n🛑 納品を止めた。出す前に直すもんが残っとる：")
        for p in problems:
            print(f"   ・{p}")
        raise RuntimeError(f"自動検査で {len(problems)} 件。納品せずに止めた。")


def _main() -> int:
    ap = argparse.ArgumentParser(description="納品物の自動検査（予言文法・日付・焼き直し）")
    ap.add_argument("target", help="検査するテキストファイル")
    ap.add_argument("--today", help="今日の日付（例 2026-08-13）。省略時は本日")
    ap.add_argument("--previous", help="前に渡した鑑定書のテキスト（焼き直しの照合用）")
    ap.add_argument("--strict", action="store_true",
                    help="潮見表・暦用。彼を主語にした未来の記述を一切許さん")
    ap.add_argument("--horizon", type=int, default=100, help="面倒を見る日数（既定100）")
    a = ap.parse_args()

    text = Path(a.target).read_text()
    prev = Path(a.previous).read_text() if a.previous else None
    problems = check_all(text, today=a.today, previous=prev,
                         strict_prophecy=a.strict, horizon_days=a.horizon)
    if not problems:
        print(f"✅ {a.target}：問題なし")
        return 0
    print(f"🛑 {a.target}：{len(problems)}件")
    for p in problems:
        print(f"   ・{p}")
    return 1


if __name__ == "__main__":
    sys.exit(_main())

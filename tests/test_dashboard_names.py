"""ダッシュボードに「定義の無い名前」が残ってへんか見る。

★★★2026-09-28：実害が二つ出た。どっちも 2026-08-21（896a758）で
  無料診断の画面を外した時に、そこで定義しとった物まで一緒に消したせいや。
  ・_jp_birthday … 👥会員の画面を開くだけで NameError。★会員の登録も退会も
    画面ごと落ちて、一ヶ月以上できん状態やった。
  ・_to … 鑑定書PDFの登録で、★add_reading は通っとるのに「登録に失敗しました」と出る。
    見た店主がもう一回押したら、同じ鑑定書が二重に入る。
  ★Streamlitの画面は、その画面を開くまで誰も気づかん。せやから機械で見る。
"""
import ast
import builtins
from pathlib import Path

import pytest


def _undefined_names(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    known = set(dir(builtins))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            known.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            known.add(n.id)
        elif isinstance(n, ast.alias):
            known.add((n.asname or n.name).split(".")[0])
        elif isinstance(n, ast.arg):
            known.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            known.add(n.name)
    used: dict[str, int] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            used.setdefault(n.id, n.lineno)
    return {k: v for k, v in used.items() if k not in known}


@pytest.mark.parametrize("name", ["app.py", "src/operations_ui.py"])
def test_no_undefined_names_in_the_dashboard(name):
    path = Path(__file__).resolve().parents[1] / name
    missing = _undefined_names(path)
    assert not missing, f"{name} に定義の無い名前がある: {missing}"


# ★★★2026-09-28：一括送信が「4人に全部届いたあと」に画面だけ落ちた。
#   _consult_board.clear() が AttributeError。キャッシュの指定（@st.cache_data）が
#   関数の直上から離れて、あいだに差し込んだ別の関数に付いとった。
#   ★送信は済んどるのに画面は赤字。店主からは【送れてへん】ように見えて、
#     もう一回押したら同じ返信が二度届く。せやから、この形も機械で見る。
def _clear_calls_on_functions(path: Path) -> dict[str, int]:
    """`なんとか.clear()` のうち、同じファイルの関数を呼んどる物を返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    found: dict[str, int] = {}
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "clear" and isinstance(n.func.value, ast.Name)
                and n.func.value.id in funcs):
            found.setdefault(n.func.value.id, n.lineno)
    return found


def _is_cached(fn) -> bool:
    for d in fn.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        if isinstance(f, ast.Attribute) and f.attr in ("cache_data", "cache_resource"):
            return True
    return False


@pytest.mark.parametrize("name", ["app.py", "src/operations_ui.py"])
def test_clear_is_only_called_on_cached_functions(name):
    path = Path(__file__).resolve().parents[1] / name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    bad = {fname: line for fname, line in _clear_calls_on_functions(path).items()
           if not _is_cached(funcs[fname])}
    assert not bad, (
        f"{name}: キャッシュの指定が無い関数に .clear() を呼んどる（実行したら落ちる）: {bad}")


# ★★2026-09-30：「よその男」が「よ彼」に化けた。綾さんの鑑定書に二箇所出た。
#   雑な男呼びを直すガードが、「よその男」の中の「その男」だけを「彼」に替えて、
#   頭の「よ」が残る。読んだ人には意味の取れん語が並ぶ。
#   ★「よその男」は彼を雑に呼んどるんやのうて【他人の男】の意味やから、
#     「彼」に替えたらそもそも意味が壊れる。「よその人」にする。
def test_other_mans_wording_is_not_mangled():
    from src.diagnosis import soften_rude
    assert soften_rude("よその男が滅多にやらんことや") == "よその人が滅多にやらんことや"
    assert "よ彼" not in soften_rude("よその男の話")
    # 今まで通り直るもんは、そのまま直る
    assert soften_rude("あの男は冷たい") == "彼は冷たい"
    assert soften_rude("そんな男やめとき") == "そんな人やめとき"
    # 別の意味の語は触らん
    assert soften_rude("よその男性の話") == "よその男性の話"
    assert soften_rude("その男前な顔") == "その男前な顔"


# ★★★2026-09-30：「そりゃあいつか離れるでしょ」——彼が言うた言葉の引用や。
#   これは「そりゃあ」＋「いつか」やのに、機械が「あいつ」と見て
#   「そりゃ彼か離れるでしょ」に変えるとこやった。★彼の言葉の捏造になる。
#   雑な言葉が一個漏れるより、引用を壊す方がよっぽど悪い。迷ったら替えん。
def test_rude_pronoun_guard_does_not_break_quotes():
    from src.diagnosis import soften_rude
    for keep in ("そりゃあいつか離れるでしょ", "まあいつかやるわ",
                 "じゃあいつ会うん", "どこいつも同じや"):
        assert soften_rude(keep) == keep, keep
    assert soften_rude("あいつは冷たい") == "彼は冷たい"
    assert soften_rude("あいつから連絡きた") == "彼から連絡きた"
    assert soften_rude("こいつが好きなんや") == "彼が好きなんや"
    assert soften_rude("そいつに任せとき") == "その人に任せとき"
    assert soften_rude("あいつ、来るってよ") == "彼、来るってよ"


# ★★★2026-10-01：処方箋の時期が、もう過ぎとった。
#   りささんの鑑定書。今日が10月1日やのに、一手目が「お盆が明けてから八月の終わりまで」、
#   会う話の目安が「九月の終わりから十月あたり」。★どっちも過ぎとる。
#   内部の材料には今日の日付を渡してあったのに、それだけでは守られんかった。
def test_past_timing_in_a_prescription_is_caught():
    from src.kantei import check_past_timing
    bad = [{"title": "いつ動くか",
            "body": "時期は、お盆が明けてから、八月の終わりまでの間に一通送る。"
                    "目安は、九月の終わりから十月あたりに会う話を出す。"}]
    found = check_past_timing(bad, "2026-10-01")
    assert len(found) == 2 and "8月" in found[0] and "9月" in found[1]
    # 過去の出来事（年が付いとる）は、警告にせん
    assert check_past_timing(
        [{"title": "縁", "body": "2024年8月に会いに来た。2015年1月に連絡が止まった。"}],
        "2026-10-01") == []
    # これからの時期は、年をまたいでも通す
    assert check_past_timing(
        [{"title": "いつ", "body": "十月の半ばまでに一通送る。十二月の半ばから年明けあたりに会う話を出す。"}],
        "2026-10-01") == []


# ★★2026-10-01：呼び捨ての検査が、別の語の一部に当たっとった。
#   ちかさんのヒアリングで「声をかけたんはどっちからや」の【どっ・ちか・らや】を
#   呼び捨ての「ちか」と見て警告を出した。★嘘の警告が続くと、ほんまの警告も流して見る。
def test_bare_name_check_ignores_names_inside_other_words():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "cd", Path(__file__).resolve().parents[1] / "tools" / "check_draft.py")
    cd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cd)

    def bare(text):
        return [b for b in cd.check(text, name="ちかさん") if "呼び捨て" in b]

    assert bare("声をかけたんはどっちからや。") == []
    assert bare("どっちが誘うんや。") == []
    assert bare("ちかは今、止まっとる。")          # 頭の呼び捨ては捕まえる
    assert bare("あんたのこと、ちかって呼ぶで。")   # 読点のあとも捕まえる
    assert bare("ちか専用の鑑定書や。")            # 漢字が続く形も捕まえる
    assert bare("ちかさんのために書いた。") == []   # さん付きは当然つかまらん


# ★★★2026-10-01：名前の置換が【語の中】で起きて、納品済みの紙を壊した。
#   りささんの個別鑑定書。呼び名が「りささん」やったんで、本文の「はっきりさせたい」が
#   「はっきりささんせたい」に化けた。六箇所。相談者はもう読んどる。
#   ★機械が書いた形跡としては、これ以上のもんはない。
def test_honorific_is_not_inserted_inside_other_words():
    from src.diagnosis import add_honorific
    for keep, name in (("白黒はっきりさせたい性分や", "りささん"),
                       ("ゆっくりさせてやり", "りささん"),
                       ("ありさまを見てみ", "りささん"),
                       ("割り算は苦手や", "りささん"),
                       ("積み木は崩れる", "みきさん"),
                       ("編み機は古い", "あみきさん")):
        assert add_honorific(keep, name) == keep, keep
    # 呼び捨ては今まで通り直す
    assert add_honorific("りさという人", "りささん") == "りささんという人"
    assert add_honorific("ウチが視た克子は強い", "克子さん") == "ウチが視た克子さんは強い"
    assert add_honorific("またみきに縁がある", "みきさん") == "またみきさんに縁がある"
    # 既に付いとるもんに重ねん
    assert add_honorific("りささんのために", "りささん") == "りささんのために"


# ★★★2026-10-01：カギカッコの中を書き換えたら、彼の言葉の捏造になる。
#   チカさんの回で出かかった。彼の言葉の引用
#   「距離近く過ごせるのもチカが初めて」が「チカさんが初めて」に変わるとこやった。
def test_honorific_leaves_quoted_words_alone():
    from src.diagnosis import add_honorific
    t = ("彼はこう言うた。「セックスしたいと思えるのはチカだけ」"
         "「距離近く過ごせるのもチカが初めて」。チカは、その言葉を握っとる。")
    r = add_honorific(t, "チカさん")
    assert "「距離近く過ごせるのもチカが初めて」" in r   # 引用はそのまま
    assert "。チカさんは、その言葉を握っとる。" in r      # 外は直る


# ★★★2026-10-04：星座のコメントを手挙げとして拾う。
#   ★返信に診断のURLが付くんは is_lead の時だけや（replies.py）。
#     星座で募っても、ここで拾えてへんかったらコメントだけ増えて診断は増えん。
#   ★★投稿の型を変える前に、受け皿を直しとかなあかん、という話や。
def test_zodiac_comments_are_treated_as_leads():
    from src import leads
    for t, want in (("乙女座", "乙女座"), ("私は牡羊座です", "牡羊座"),
                    ("おとめ座🌙", "おとめ座"), ("彼は天秤座", "天秤座"),
                    ("水瓶座やで", "水瓶座")):
        assert leads.match_keyword(t) == want, t
    # 一字だけ・長文は拾わん（誤爆を避ける）
    for t in ("魚", "蟹", "3年待っとる", "座ってるだけ"):
        assert leads.match_keyword(t) != t
    # 今まで通りのものは壊れてへん
    assert leads.match_keyword("9月生まれ") == "生まれ月"
    assert leads.match_keyword("①") == "願い1"

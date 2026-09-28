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

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

"""Validate generated rules before atomically replacing the last good file."""
import os
import re
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

GUARD = """
集計に含めた標本と対象期間の範囲だけを説明すること。
因果関係・必勝・アルゴリズムの仕組みを断定せず、観測事実と次に検証する仮説を分ける。
リード・返信・クリックを購入や売上に読み替えない。顧客の実話を公開用の体験談へ転用しない。
表題（#で始まる行）・更新日・自動生成の注意書きはシステムが付けるので出力しない。
過去ファイルにある未来の日付や裏付けのない数値は引き継がない。
"""


def write_learning(path: Path, header: str, body: str, today: str):
    limit = date.fromisoformat(today)
    lines = [line for line in body.strip().splitlines()
             if not line.startswith("# ") and not line.startswith("※ このファイルは")]
    body = "\n".join(lines).strip()
    if len(body) < 100 or "```" in body:
        raise ValueError("学習結果の形式が不完全なため、元のファイルを保持しました")
    for year in re.findall(r"\b(20\d{2})(?=[年/.-])", body):
        if int(year) > limit.year:
            raise ValueError("未来の年を含む学習結果のため、元のファイルを保持しました")
    for y, m, d in re.findall(r"(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})", body):
        if date(int(y), int(m), int(d)) > limit:
            raise ValueError("未来の日付を含む学習結果のため、元のファイルを保持しました")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
            temp = Path(f.name)
            f.write(header + body + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)

"""永続化のディスパッチャ。

環境変数 STORE_BACKEND で保存先を切り替える:
  sqlite (既定) … ローカルファイル data/threads.db
  sheets         … Google Sheets（ホスティング時の共有ストア）

呼び出し側は `from . import store; store.add_candidate(...)` のまま変わらない。
"""
from __future__ import annotations

from .config import env

_backend = (env("STORE_BACKEND") or "sqlite").lower()

if _backend == "sheets":
    from .store_sheets import *  # noqa: F401,F403
    from .store_sheets import init_db  # 明示（*でも入るが意図を明確に）
else:
    from .store_sqlite import *  # noqa: F401,F403
    from .store_sqlite import conn, init_db  # noqa: F401  (sqlite専用: conn)

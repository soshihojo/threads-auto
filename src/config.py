"""設定・環境変数のロード。config.yaml と .env を一元管理する。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def active_profile() -> dict[str, Any]:
    cfg = load_config()
    name = cfg["active_profile"]
    profile = dict(cfg["profiles"][name])
    profile["name"] = name
    return profile


def env(key: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.getenv(key, default)
    if required and not val:
        raise RuntimeError(f"環境変数 {key} が未設定です。.env を確認してください。")
    return val


# よく使うパス
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

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


def account_conf(name: str | None = None) -> dict[str, Any]:
    """--account に渡された名前から、そのアカウントの設定を返す。

    ★2026-08-31 新設。Threadsを二本まわすため。
      accounts が config.yaml に無い（古い設定）でも落ちんようにしてある。
    """
    cfg = load_config()
    accounts = cfg.get("accounts") or {}
    if not accounts:
        return {"key": "a", "env_suffix": "", "source": "",
                "profile": cfg.get("active_profile"), "label": cfg.get("active_profile")}
    key = name or cfg.get("default_account") or next(iter(accounts))
    if key not in accounts:
        raise SystemExit(f"アカウント '{key}' は config.yaml の accounts に無い。"
                         f"あるんは {list(accounts)} や")
    conf = dict(accounts[key])
    conf["key"] = key
    conf.setdefault("env_suffix", "")
    conf.setdefault("source", "")
    conf.setdefault("profile", cfg.get("active_profile"))
    return conf


def account_keys() -> list[str]:
    return list((load_config().get("accounts") or {"a": {}}).keys())


def profile_for(name: str | None = None) -> dict[str, Any]:
    """アカウントに紐づいたプロファイルを返す（--account 対応版の active_profile）。"""
    cfg = load_config()
    pname = account_conf(name).get("profile") or cfg["active_profile"]
    profile = dict(cfg["profiles"][pname])
    profile["name"] = pname
    return profile


def env(key: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.getenv(key, default)
    if isinstance(val, str):
        # GitHub Secrets等に紛れ込む前後の改行・空白でAPIのURLが壊れるのを防ぐ
        val = val.strip()
    if required and not val:
        raise RuntimeError(f"環境変数 {key} が未設定です。.env を確認してください。")
    return val


# よく使うパス
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

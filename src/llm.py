"""Claude(Anthropic) 呼び出しの共通ヘルパー。"""
from __future__ import annotations

from anthropic import Anthropic

from .config import env

# 生成は安価なSonnetを既定に（必要に応じてconfig/環境で差し替え可）
DEFAULT_MODEL = "claude-sonnet-4-6"

_client: Anthropic | None = None


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=env("ANTHROPIC_API_KEY", required=True))
    return _client


def complete(system: str, user: str, *, model: str = DEFAULT_MODEL, max_tokens: int = 1024, temperature: float = 1.0) -> str:
    msg = client().messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in msg.content if block.type == "text").strip()

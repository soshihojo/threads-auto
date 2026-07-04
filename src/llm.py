"""Claude(Anthropic) 呼び出しの共通ヘルパー。"""
from __future__ import annotations

from anthropic import Anthropic

from .config import env

# 既定は最上位のOpus。環境変数 CLAUDE_MODEL で差し替え可
# （例: claude-sonnet-5 / claude-sonnet-4-6 / claude-haiku-4-5）
DEFAULT_MODEL = env("CLAUDE_MODEL") or "claude-opus-4-8"

# temperature等のサンプリングパラメータを受け付けないモデル（渡すと400）
_NO_SAMPLING = ("claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5", "claude-fable", "claude-mythos")
# thinkingが既定でON（出力枠をthinkingが消費する）モデル → max_tokensに下限を設ける
_THINKING_ON = ("claude-sonnet-5", "claude-fable", "claude-mythos")

_client: Anthropic | None = None


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=env("ANTHROPIC_API_KEY", required=True))
    return _client


def complete(system: str, user: str, *, model: str | None = None, max_tokens: int = 1024, temperature: float = 1.0) -> str:
    model = model or DEFAULT_MODEL
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if not model.startswith(_NO_SAMPLING):
        kwargs["temperature"] = temperature
    if model.startswith(_THINKING_ON) and max_tokens < 4000:
        kwargs["max_tokens"] = 4000  # thinking分の余白（上限なので未使用分は課金されない）
    msg = client().messages.create(**kwargs)
    return "".join(block.text for block in msg.content if block.type == "text").strip()

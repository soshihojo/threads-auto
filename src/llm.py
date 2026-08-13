"""Claude(Anthropic) 呼び出しの共通ヘルパー。"""
from __future__ import annotations

import time

from anthropic import Anthropic

from .config import env

# 既定は最上位のOpus。環境変数 CLAUDE_MODEL で差し替え可
# （例: claude-sonnet-5 / claude-sonnet-4-6 / claude-haiku-4-5）
DEFAULT_MODEL = env("CLAUDE_MODEL") or "claude-opus-4-8"

# temperature等のサンプリングパラメータを受け付けないモデル（渡すと400）
_NO_SAMPLING = ("claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5", "claude-fable", "claude-mythos")
# thinkingが既定でON（出力枠をthinkingが消費する）モデル → max_tokensに下限を設ける
_THINKING_ON = ("claude-sonnet-5", "claude-fable", "claude-mythos")

# ---- プロンプトキャッシュ ----
# 毎回まるごと送っとる指示文（LINE会話のNURTURE_SYSTEMは約3,700字＝入力の66%）を
# 使い回す。中身は一切変わらんので、返す文面の質・口調・長さには影響せん。
# 安うなるのと、読み直しが要らんぶん少し速うなるだけや。
#
# ttl=1h を選ぶ理由：会話は1時間に6回前後しか呼ばれん。5分キャッシュやと実測で
# ヒット率47%までしか出んかったが、1時間なら88%当たる（書き込みは2倍やが十分ペイする）。
# 短い指示文はキャッシュの最低トークン数に届かず、書き込み料だけ損するので付けん。
#
# ★指示文（system）だけに印を付ける。リクエスト全体に付ける「自動キャッシュ」では
#   一度も当たらんかった（実測：本文が1文字ちごただけで全部書き直しになる）。
#   ウチらの本文は毎回ちがう会話やから、変わらん指示文だけを名指しでキャッシュする。
_CACHE_TTL = env("CLAUDE_CACHE_TTL") or "1h"
_CACHE_MIN_CHARS = int(env("CLAUDE_CACHE_MIN_CHARS") or "1500")
_cache_supported = True   # SDKや API に弾かれたら False に落として、以後は付けん

_client: Anthropic | None = None


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=env("ANTHROPIC_API_KEY", required=True))
    return _client


def complete_vision(system: str, user: str, image_b64: str, media_type: str, *,
                    model: str | None = None, max_tokens: int = 1500) -> str:
    """画像つきの生成（会員から届くスクリーンショットの読み取り等）。"""
    model = model or DEFAULT_MODEL
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
            {"type": "text", "text": user},
        ]}],
    )
    if model.startswith(_THINKING_ON) and max_tokens < 4000:
        kwargs["max_tokens"] = 4000
    msg = client().messages.create(**kwargs)
    return "".join(block.text for block in msg.content if block.type == "text").strip()


def complete(system: str, user: str, *, model: str | None = None, max_tokens: int = 1024,
             temperature: float = 1.0, cache: bool = True) -> str:
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
    return _create(kwargs, cache=cache and len(system) >= _CACHE_MIN_CHARS)


# ★2026-08-14：一時的な過負荷でも巡回が丸ごと死んどった。
#   実害：8/13 23時台の巡回が anthropic.OverloadedError（529）で落ちて、
#   その回のコメント返信が全部飛んだ。SDKも既定で2回は再試行するが、
#   混んどる時はそれでは足りん。間を空けて粘る層をこっちで足す。
_RETRY_WAITS = (4, 12, 30)          # 秒。これでも駄目なら諦めて例外を上げる


def _is_transient(e: Exception) -> bool:
    """待てば直る類か。過負荷・レート制限・上流の一時障害だけを拾う。
    入力の間違い（400）まで再試行したら、同じ失敗を繰り返すだけやからな。"""
    name = type(e).__name__
    if name in ("OverloadedError", "RateLimitError", "APIConnectionError",
                "APITimeoutError", "InternalServerError", "APIStatusError"):
        code = getattr(e, "status_code", None)
        return code is None or code == 429 or code >= 500
    return False


def _create(kwargs: dict, *, cache: bool) -> str:
    """messages.create の実行。キャッシュ指定が弾かれても本番を止めん。

    キャッシュは「安うなる」だけの仕組みで、これが原因で返信が出えへんのは本末転倒や。
    弾かれたら黙って外して投げ直し、以後はこのプロセスでは付けん。
    """
    global _cache_supported
    last: Exception | None = None
    for i, wait in enumerate((0, *_RETRY_WAITS)):
        if wait:
            print(f"[llm] 混んどるみたいや。{wait}秒待って投げ直す（{i}回目）: {last}")
            time.sleep(wait)
        try:
            if cache and _cache_supported and isinstance(kwargs.get("system"), str):
                cached = dict(kwargs)
                cached["system"] = [{"type": "text", "text": kwargs["system"],
                                     "cache_control": {"type": "ephemeral", "ttl": _CACHE_TTL}}]
                try:
                    msg = client().messages.create(**cached)
                    return "".join(b.text for b in msg.content if b.type == "text").strip()
                except Exception as e:
                    if not _is_cache_rejection(e):
                        raise
                    _cache_supported = False
                    print(f"[llm] プロンプトキャッシュが使えんかったので無効化した（生成は続行）: {e}")
            msg = client().messages.create(**kwargs)
            return "".join(b.text for b in msg.content if b.type == "text").strip()
        except Exception as e:
            if not _is_transient(e):
                raise
            last = e
    raise last  # type: ignore[misc]


def _is_cache_rejection(e: Exception) -> bool:
    """キャッシュ指定そのものを拒まれたか（SDKが知らん引数／APIが400を返した）。
    レート制限や通信断まで飲み込んでキャッシュのせいにせんよう、種類を絞る。"""
    if isinstance(e, TypeError):
        return "cache_control" in str(e)
    return type(e).__name__ in ("BadRequestError", "UnprocessableEntityError") and \
        "cache" in str(e).lower()

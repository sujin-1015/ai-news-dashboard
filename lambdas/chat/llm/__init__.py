from __future__ import annotations

from .base import LLMClient, LLMError, LLMResult, Message, Overloaded, RateLimited


def get_client(provider: str, api_key: str, model: str) -> LLMClient:
    """공급자 선택. 새 공급자(Bedrock, Claude API 등)는 여기에 분기를 추가한다."""
    if provider == "gemini":
        from .gemini import GeminiClient

        return GeminiClient(api_key, model)
    raise LLMError(f"지원하지 않는 LLM_PROVIDER: {provider}")


__all__ = ["LLMClient", "LLMError", "LLMResult", "Message", "Overloaded", "RateLimited", "get_client"]

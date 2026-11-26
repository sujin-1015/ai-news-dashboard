"""LLM 호출 인터페이스.

핸들러는 이 인터페이스에만 의존한다. Gemini → Bedrock/Claude API로 바꿀 때는
새 구현 클래스를 추가하고 LLM_PROVIDER 환경변수만 바꾸면 된다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class Message:
    role: Literal["user", "model"]
    text: str


class LLMError(Exception):
    """일반 실패 (5xx, 파싱 실패 등)."""


class RateLimited(LLMError):
    """공급자 한도 초과 (HTTP 429)."""


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0  # 공급자가 알려준 사용량 (비용 표시용)
    output_tokens: int = 0


class LLMClient(Protocol):
    def generate(self, system: str, messages: list[Message], max_tokens: int = 800) -> LLMResult: ...


class Overloaded(LLMError):
    """공급자 일시 과부하 (HTTP 503 / 타임아웃)."""

"""Gemini API 구현 (REST, 표준 라이브러리만 사용)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import LLMError, Message, RateLimited

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout: float = 25.0):
        if not api_key or not model:
            raise LLMError("Gemini 키 또는 모델명이 설정되지 않음")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def generate(self, system: str, messages: list[Message], max_tokens: int = 800) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": m.role, "parts": [{"text": m.text}]} for m in messages],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.4},
        }
        req = urllib.request.Request(
            ENDPOINT.format(model=self.model),
            data=json.dumps(body).encode("utf-8"),
            # 키는 URL(?key=)이 아니라 헤더로 → 접근 로그 등에 남지 않게
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited("Gemini 무료 티어 한도 초과") from e
            detail = e.read()[:300].decode("utf-8", "ignore")
            raise LLMError(f"Gemini HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError(f"Gemini 연결 실패: {e}") from e
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
        except (KeyError, IndexError) as e:
            raise LLMError(f"Gemini 응답 형식 오류: {str(data)[:200]}") from e
        if not text:
            raise LLMError("빈 응답 (안전 필터 등)")
        return text

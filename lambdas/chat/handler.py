"""챗봇 프록시 Lambda (API Gateway HTTP API → 여기 → Gemini).

남용 방지 (여러 겹)
1. API Gateway 스테이지 스로틀 (rate/burst)  ← 인프라
2. CORS: 내 CloudFront 도메인만 허용        ← 인프라 + 아래 Origin 재확인
3. 입력 길이 / 대화 이력 개수 제한           ← 여기
4. 일일 호출 상한 (DynamoDB 원자적 카운터)   ← 여기
Gemini 키는 SSM Parameter Store(SecureString)에서 읽고, 프론트/저장소에는 없다.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from llm import LLMError, Message, Overloaded, RateLimited, get_client

log = logging.getLogger()
log.setLevel(logging.INFO)

KST = timezone(timedelta(hours=9))
ALLOWED_ORIGINS = {o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}
USAGE_TABLE = os.environ["USAGE_TABLE"]
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "200"))
KEY_PARAM = os.environ["GEMINI_KEY_PARAM"]
MODEL_PARAM = os.environ["GEMINI_MODEL_PARAM"]
PROVIDER = os.environ.get("LLM_PROVIDER", "gemini")
MAX_QUESTION = 1000
MAX_HISTORY = 6
MAX_FIELD = 300
CACHE_SECONDS = 600
# 예상 비용 계산용 단가 (USD / 100만 토큰). 무료 티어는 0. 유료 모델로 바꾸면 cdk.json에서 설정.
PRICE_IN = float(os.environ.get("PRICE_INPUT_PER_M", "0"))
PRICE_OUT = float(os.environ.get("PRICE_OUTPUT_PER_M", "0"))

ssm = boto3.client("ssm")
usage = boto3.resource("dynamodb").Table(USAGE_TABLE)
_cache: dict[str, tuple[float, str]] = {}

SYSTEM = """너는 AI·IT 직무를 준비하는 취업준비생을 돕는 학습 도우미다.
- 한국어로, 쉬운 말로, 핵심부터 답한다. 보통 5~10문장, 필요하면 짧은 목록을 쓴다.
- 모르는 용어는 정의 → 왜 중요한지 → 기사 맥락에서의 의미 → 면접에서 쓸 수 있는 한 줄 순서로 설명한다.
- 너는 기사 본문을 읽지 않았다. 제목·(있으면)요약만 안다. 본문 내용을 지어내지 말고, 세부 사항은 원문 링크를 확인하라고 안내한다.
- 확실하지 않은 수치·날짜·사실은 단정하지 않는다.
- 기사와 무관한 질문도 IT·커리어 학습 범위라면 답한다. 개인정보는 요구하지 않는다."""


def resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "application/json; charset=utf-8"},
            "body": json.dumps(body, ensure_ascii=False)}


def param(name: str, decrypt: bool = False) -> str:
    """SSM 값을 10분 캐시 (호출마다 SSM API를 부르지 않게). 모델명은 배포 없이 SSM에서 바꿀 수 있다."""
    hit = _cache.get(name)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    v = ssm.get_parameter(Name=name, WithDecryption=decrypt)["Parameter"]["Value"]
    _cache[name] = (time.time(), v)
    return v


def today_key() -> dict:
    return {"pk": f"chat#{datetime.now(KST).date().isoformat()}"}


def take_quota() -> bool:
    """오늘 카운터 +1. 상한에 도달했으면 False (조건부 업데이트라 동시 요청에도 정확)."""
    try:
        usage.update_item(
            Key=today_key(),
            UpdateExpression="ADD #c :one SET expiresAt = if_not_exists(expiresAt, :exp)",
            ConditionExpression="attribute_not_exists(#c) OR #c < :max",
            ExpressionAttributeNames={"#c": "count"},
            ExpressionAttributeValues={":one": 1, ":max": DAILY_LIMIT,
                                       ":exp": int((datetime.now(KST) + timedelta(days=7)).timestamp())},
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def summarize(item: dict) -> dict:
    tin, tout = int(item.get("inTok", 0)), int(item.get("outTok", 0))
    return {
        "count": int(item.get("count", 0)),
        "limit": DAILY_LIMIT,
        "inputTokens": tin,
        "outputTokens": tout,
        "estCostUsd": round(tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT, 6),
        "freeTier": PRICE_IN == 0 and PRICE_OUT == 0,
    }


def add_tokens(tin: int, tout: int) -> dict:
    """오늘 토큰 합계를 원자적으로 더하고, 갱신된 오늘 사용량을 돌려준다."""
    r = usage.update_item(
        Key=today_key(),
        UpdateExpression="ADD inTok :i, outTok :o",
        ExpressionAttributeValues={":i": tin, ":o": tout},
        ReturnValues="ALL_NEW",
    )
    return summarize(r.get("Attributes", {}))


def get_usage() -> dict:
    return summarize(usage.get_item(Key=today_key()).get("Item") or {})


def clip(v, n: int = MAX_FIELD) -> str:
    return str(v or "")[:n]


def parse_request(event: dict) -> tuple[str, dict | None, list[Message]]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if len(raw) > 20_000:
        raise ValueError("too_long")
    data = json.loads(raw)
    q = str(data.get("question", "")).strip()
    if not q:
        raise ValueError("empty")
    if len(q) > MAX_QUESTION:
        raise ValueError("too_long")
    art = data.get("article")
    article = None
    if isinstance(art, dict) and art.get("title"):
        article = {k: clip(art.get(k)) for k in ("title", "source", "link", "summary") if art.get(k)}
    history: list[Message] = []
    for m in (data.get("history") or [])[-MAX_HISTORY:]:
        if isinstance(m, dict) and m.get("role") in ("user", "model") and m.get("text"):
            history.append(Message(m["role"], clip(m["text"], MAX_QUESTION * 2)))
    while history and history[0].role != "user":  # Gemini는 user로 시작해야 한다
        history.pop(0)
    return q, article, history


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    origin = headers.get("origin", "")
    if ALLOWED_ORIGINS and origin not in ALLOWED_ORIGINS:
        return resp(403, {"error": "forbidden"})
    if event.get("requestContext", {}).get("http", {}).get("method") == "GET":  # GET /usage
        return resp(200, {"usage": get_usage()})
    try:
        question, article, history = parse_request(event)
    except ValueError as e:
        return resp(400, {"error": str(e) if str(e) in ("too_long", "empty") else "bad_request"})
    except json.JSONDecodeError:
        return resp(400, {"error": "bad_request"})

    if not take_quota():
        log.info(json.dumps({"event": "daily_limit"}))
        return resp(429, {"error": "daily_limit"})

    system = SYSTEM
    if article:
        system += "\n\n[지금 사용자가 보고 있는 기사]\n" + "\n".join(f"{k}: {v}" for k, v in article.items())

    try:
        client = get_client(PROVIDER, param(KEY_PARAM, decrypt=True), param(MODEL_PARAM))
        result = client.generate(system, [*history, Message("user", question)])
    except ssm.exceptions.ParameterNotFound:
        log.error(json.dumps({"event": "not_configured", "param": KEY_PARAM}))
        return resp(503, {"error": "not_configured"})
    except RateLimited:
        return resp(429, {"error": "rate_limited"})
    except Overloaded as e:
        log.warning(json.dumps({"event": "llm_overloaded", "error": str(e)[:200]}, ensure_ascii=False))
        return resp(503, {"error": "overloaded"})
    except LLMError as e:
        log.error(json.dumps({"event": "llm_error", "error": str(e)[:300]}, ensure_ascii=False))
        return resp(502, {"error": "llm_error"})

    today = add_tokens(result.input_tokens, result.output_tokens)
    # 질문 내용은 로그에 남기지 않는다 (길이와 토큰 수만)
    log.info(json.dumps({"event": "chat_ok", "q_len": len(question), "a_len": len(result.text), "ctx": bool(article),
                         "in_tok": result.input_tokens, "out_tok": result.output_tokens}))
    return resp(200, {"answer": result.text, "usage": today,
                      "tokens": {"input": result.input_tokens, "output": result.output_tokens}})

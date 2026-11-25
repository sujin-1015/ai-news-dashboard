import importlib
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://d123.cloudfront.net"


@pytest.fixture()
def h(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    monkeypatch.setenv("USAGE_TABLE", "usage")
    monkeypatch.setenv("GEMINI_KEY_PARAM", "/k")
    monkeypatch.setenv("GEMINI_MODEL_PARAM", "/m")
    monkeypatch.setenv("DAILY_LIMIT", "2")
    monkeypatch.syspath_prepend(str(ROOT / "lambdas" / "chat"))
    sys.modules.pop("handler", None)
    mod = importlib.import_module("handler")

    class Usage:
        n = 0

        def update_item(self, **kw):
            if self.n >= kw["ExpressionAttributeValues"][":max"]:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            self.n += 1

    class SSM:
        def get_parameter(self, Name, WithDecryption=False):
            return {"Parameter": {"Value": {"/k": "secret", "/m": "gemini-test"}[Name]}}

    class FakeLLM:
        calls = []

        def generate(self, system, messages, max_tokens=800):
            FakeLLM.calls.append((system, messages))
            return "답변"

    monkeypatch.setattr(mod, "usage", Usage())
    monkeypatch.setattr(mod, "ssm", SSM())
    monkeypatch.setattr(mod, "get_client", lambda p, k, m: FakeLLM())
    mod.FakeLLM = FakeLLM
    return mod


def ev(body, origin=ORIGIN):
    return {"headers": {"Origin": origin}, "body": json.dumps(body, ensure_ascii=False)}


def test_ok_with_article_context(h):
    r = h.handler(ev({"question": "HBM이 뭐야?", "article": {"title": "삼성 HBM4", "link": "https://x"}}), None)
    assert r["statusCode"] == 200 and json.loads(r["body"])["answer"] == "답변"
    system, msgs = h.FakeLLM.calls[-1]
    assert "삼성 HBM4" in system and msgs[-1].text == "HBM이 뭐야?"


def test_rejects_other_origin(h):
    assert h.handler(ev({"question": "q"}, origin="https://evil.example"), None)["statusCode"] == 403


def test_input_length_limit(h):
    r = h.handler(ev({"question": "가" * 1001}), None)
    assert r["statusCode"] == 400 and json.loads(r["body"])["error"] == "too_long"


def test_daily_limit(h):
    assert h.handler(ev({"question": "1"}), None)["statusCode"] == 200
    assert h.handler(ev({"question": "2"}), None)["statusCode"] == 200
    r = h.handler(ev({"question": "3"}), None)
    assert r["statusCode"] == 429 and json.loads(r["body"])["error"] == "daily_limit"


def test_provider_rate_limit_maps_to_429(h, monkeypatch):
    class Limited:
        def generate(self, *a, **k):
            raise h.RateLimited("429")

    monkeypatch.setattr(h, "get_client", lambda p, k, m: Limited())
    r = h.handler(ev({"question": "q"}), None)
    assert r["statusCode"] == 429 and json.loads(r["body"])["error"] == "rate_limited"


def test_history_must_start_with_user(h):
    hist = [{"role": "model", "text": "x"}, {"role": "user", "text": "a"}, {"role": "model", "text": "b"}]
    h.handler(ev({"question": "q", "history": hist}), None)
    _, msgs = h.FakeLLM.calls[-1]
    assert [m.role for m in msgs] == ["user", "model", "user"]

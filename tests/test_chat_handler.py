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
    from llm import LLMResult

    mod.LLMResult = LLMResult

    class Usage:
        def __init__(self):
            self.item = {}

        def update_item(self, **kw):
            v = kw["ExpressionAttributeValues"]
            if ":max" in v:  # take_quota
                if self.item.get("count", 0) >= v[":max"]:
                    raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
                self.item["count"] = self.item.get("count", 0) + 1
                return {}
            self.item["inTok"] = self.item.get("inTok", 0) + v[":i"]  # add_tokens
            self.item["outTok"] = self.item.get("outTok", 0) + v[":o"]
            return {"Attributes": dict(self.item)}

        def get_item(self, Key):
            return {"Item": dict(self.item)} if self.item else {}

    class SSM:
        class exceptions:
            class ParameterNotFound(Exception):
                pass

        def get_parameter(self, Name, WithDecryption=False):
            return {"Parameter": {"Value": {"/k": "secret", "/m": "gemini-test"}[Name]}}

    class FakeLLM:
        calls = []

        def generate(self, system, messages, max_tokens=800):
            FakeLLM.calls.append((system, messages))
            return mod.LLMResult("답변", input_tokens=100, output_tokens=50)

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


def test_missing_key_returns_503(h, monkeypatch):
    def missing(Name, WithDecryption=False):
        raise h.ssm.exceptions.ParameterNotFound()

    monkeypatch.setattr(h.ssm, "get_parameter", missing)
    h._cache.clear()
    r = h.handler(ev({"question": "q"}), None)
    assert r["statusCode"] == 503 and json.loads(r["body"])["error"] == "not_configured"


def test_overloaded_maps_to_503(h, monkeypatch):
    class Busy:
        def generate(self, *a, **k):
            raise h.Overloaded("503")

    monkeypatch.setattr(h, "get_client", lambda p, k, m: Busy())
    r = h.handler(ev({"question": "q"}), None)
    assert r["statusCode"] == 503 and json.loads(r["body"])["error"] == "overloaded"


def test_gemini_retries_once_on_overload(monkeypatch):
    sys.path.insert(0, str(ROOT / "lambdas" / "chat"))
    from llm import gemini
    from llm.base import Message, Overloaded

    calls = []
    c = gemini.GeminiClient("k", "m")
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)

    def flaky(*a):
        calls.append(1)
        if len(calls) == 1:
            raise Overloaded("503")
        return "ok"

    monkeypatch.setattr(c, "_call", flaky)
    assert c.generate("s", [Message("user", "q")]) == "ok" and len(calls) == 2


def test_gemini_parses_usage_metadata(monkeypatch):
    sys.path.insert(0, str(ROOT / "lambdas" / "chat"))
    import io
    from llm import gemini
    from llm.base import Message

    payload = {"candidates": [{"content": {"parts": [{"text": "답"}]}}],
               "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 30, "thoughtsTokenCount": 5}}

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(gemini.urllib.request, "urlopen", lambda req, timeout: R(json.dumps(payload).encode()))
    r = gemini.GeminiClient("k", "m").generate("s", [Message("user", "q")])
    assert (r.text, r.input_tokens, r.output_tokens) == ("답", 12, 35)


def test_usage_tracked_and_returned(h):
    r = h.handler(ev({"question": "1"}), None)
    u = json.loads(r["body"])["usage"]
    assert u == {"count": 1, "limit": 2, "inputTokens": 100, "outputTokens": 50, "estCostUsd": 0.0, "freeTier": True}
    h.handler(ev({"question": "2"}), None)
    get = {"headers": {"Origin": ORIGIN}, "requestContext": {"http": {"method": "GET"}}}
    u = json.loads(h.handler(get, None)["body"])["usage"]
    assert u["count"] == 2 and u["inputTokens"] == 200 and u["outputTokens"] == 100


def test_estimated_cost_with_prices(h, monkeypatch):
    monkeypatch.setattr(h, "PRICE_IN", 0.10)
    monkeypatch.setattr(h, "PRICE_OUT", 0.40)
    u = json.loads(h.handler(ev({"question": "1"}), None)["body"])["usage"]
    assert u["freeTier"] is False and u["estCostUsd"] == round(100 / 1e6 * 0.10 + 50 / 1e6 * 0.40, 6)


def test_usage_get_rejects_other_origin(h):
    get = {"headers": {"Origin": "https://evil.example"}, "requestContext": {"http": {"method": "GET"}}}
    assert h.handler(get, None)["statusCode"] == 403

import time

from collector import body

ARTICLE_HTML = """<html><head><title>t</title></head><body>
<nav>홈 | 정치 | 경제 | IT</nav>
<article><h1>인터넷우체국 해킹</h1>
<p>인터넷우체국이 해외 IP 주소를 이용한 해킹 공격을 받아 고객 개인정보가 유출된 것으로 확인됐다. 우정사업본부는 27일 이같이 밝혔다.</p>
<p>해커들은 모바일 인터넷우체국의 수취인 배달장소 변경서비스에 접근한 것으로 파악됐다. 현재까지 분석된 하루 분량의 유출 고객은 11만3855명이다.</p>
<p>우정사업본부는 해당 서비스를 중단하고 해외 IP 접속을 전면 차단했으며, 경찰에 수사를 의뢰했다고 설명했다.</p>
</article><footer>Copyright 무단전재 금지</footer></body></html>"""


def test_extract_article_text_only():
    text = body.extract(ARTICLE_HTML)
    assert text and "11만3855명" in text
    assert "무단전재" not in text and "홈 | 정치" not in text


def test_extract_too_short_is_none():
    assert body.extract("<html><body><p>사진만 있는 기사</p></body></html>") is None


def test_encode_url_with_korean_path():
    url = body._encode_url("https://www.itworld.co.kr/article/123/프로덕션이-된-노트북.html?x=1")
    assert url.isascii() and url.endswith("?x=1") and "%ED" in url


def test_fetch_bodies_groups_by_host_and_respects_deadline(monkeypatch):
    calls = []
    monkeypatch.setattr(body, "PER_HOST_DELAY", 0)
    monkeypatch.setattr(body, "fetch_body", lambda url, robots, timeout=10.0: calls.append(url) or f"본문 {url}")
    arts = [{"id": str(i), "link": f"https://{h}/a{i}"} for i, h in enumerate(["a.com", "a.com", "b.com"])]
    got = body.fetch_bodies(arts)
    assert set(got) == {"0", "1", "2"} and len(calls) == 3
    calls.clear()
    assert body.fetch_bodies(arts, deadline=time.monotonic() - 1) == {} and calls == []


def test_robots_disallow_skips_fetch(monkeypatch):
    class R:
        def allowed(self, url):
            return False
    monkeypatch.setattr(body.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("요청하면 안 됨")))
    assert body.fetch_body("https://ex.com/a", R()) is None

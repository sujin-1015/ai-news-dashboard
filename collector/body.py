"""기사 본문 가져오기 (요약용). 본문은 요약을 만드는 데만 쓰고 저장하지 않는다.

- 새 기사만, 기사당 1번 요청
- robots.txt를 지킨다 (막힌 주소는 받지 않음)
- 같은 사이트는 PER_HOST_DELAY초 간격으로 차례대로, 사이트끼리는 병렬
- 본문 추출: trafilatura (광고·메뉴·댓글을 걷어내고 기사 본문만)
- 실패하면 None → 호출한 쪽이 RSS 소개글 요약으로 대신한다
"""
from __future__ import annotations

import time
import urllib.request
import urllib.robotparser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlsplit, urlunsplit

from .fetch import USER_AGENT

MAX_HTML = 3 * 1024 * 1024
MIN_BODY = 200  # 이보다 짧으면 추출 실패로 본다 (사진·표만 있는 기사 등)
PER_HOST_DELAY = 1.0


def _encode_url(url: str) -> str:
    """경로에 한글이 그대로 든 주소(ITWorld 등)를 요청 가능한 형태로."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, quote(p.path, safe="/%:@!$&'()*+,;="), p.query, ""))


class _Robots:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self.cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self.cache:
            rp = urllib.robotparser.RobotFileParser()
            try:
                req = urllib.request.Request(f"{host}/robots.txt", headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    rp.parse(r.read(500_000).decode("utf-8", "replace").splitlines())
            except Exception:  # noqa: BLE001 - robots.txt가 없거나 못 받으면 관례대로 허용
                rp = None
            self.cache[host] = rp
        rp = self.cache[host]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)


def extract(html: bytes | str) -> str | None:
    import trafilatura  # Lambda 콜드 스타트 때 필요할 때만 불러온다

    body = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
    return body if len(body) >= MIN_BODY else None


def fetch_body(url: str, robots: _Robots, timeout: float = 10.0) -> str | None:
    if not robots.allowed(url):
        return None
    try:
        req = urllib.request.Request(_encode_url(url), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read(MAX_HTML)
        return extract(html)
    except Exception:  # noqa: BLE001 - 본문 1건 실패는 RSS 요약으로 대신하면 된다
        return None


def fetch_bodies(articles: list[dict], workers: int = 12, timeout: float = 10.0,
                 deadline: float | None = None) -> dict[str, str]:
    """기사 id → 본문. deadline(time.monotonic 기준)을 넘기면 남은 기사는 건너뛴다."""
    robots = _Robots(timeout)
    by_host: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        by_host[urlsplit(a["link"]).netloc].append(a)

    def work(host: str) -> list[tuple[str, str]]:
        out = []
        for i, a in enumerate(by_host[host]):
            if deadline is not None and time.monotonic() > deadline:
                break
            if i:
                time.sleep(PER_HOST_DELAY)
            body = fetch_body(a["link"], robots, timeout)
            if body:
                out.append((a["id"], body))
        return out

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return {k: v for lst in ex.map(work, list(by_host)) for k, v in lst}

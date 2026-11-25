"""피드 병렬 수집.

- ThreadPool로 동시에 받는다 (I/O 대기 위주라 스레드로 충분).
- 피드별 타임아웃 + 1회 재시도. 실패해도 예외를 던지지 않고 결과에 기록 → 전체 수집은 계속.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

USER_AGENT = "Mozilla/5.0 (compatible; ai-news-dashboard/1.0; personal study project)"
MAX_BYTES = 5 * 1024 * 1024


@dataclass
class FetchResult:
    feed: dict
    body: bytes | None
    error: str | None
    elapsed_ms: int


def fetch_one(feed: dict, timeout: float = 10.0, retries: int = 1) -> FetchResult:
    start = time.monotonic()
    last_err = ""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                feed["url"],
                headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("응답이 너무 큼")
            return FetchResult(feed, body, None, int((time.monotonic() - start) * 1000))
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code < 500:  # 4xx는 재시도해도 같다
                break
        except Exception as e:  # noqa: BLE001 - 어떤 실패든 기록하고 넘어간다
            last_err = f"{type(e).__name__}: {e}"[:200]
        if attempt < retries:
            time.sleep(1.0)
    return FetchResult(feed, None, last_err, int((time.monotonic() - start) * 1000))


def fetch_all(feeds: list[dict], workers: int = 16, timeout: float = 10.0) -> list[FetchResult]:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda f: fetch_one(f, timeout=timeout), feeds))

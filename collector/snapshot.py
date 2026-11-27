"""프론트가 읽는 최신 스냅샷(articles.json) 생성."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from . import cluster, keywords
from .parse import KST

SCHEMA_VERSION = 1


def merge(previous: list[dict], new: list[dict], now: datetime, window_days: int = 7) -> list[dict]:
    """이전 스냅샷 + 신규 기사 → 기간 내 기사만 최신순. 같은 id는 기존 것을 유지."""
    cutoff = now - timedelta(days=window_days)
    by_id: dict[str, dict] = {}
    for a in [*new, *previous]:  # previous가 나중 → 같은 id면 previous로 덮어씀(최초 수집본 유지)
        by_id[a["id"]] = a
    kept = [a for a in by_id.values() if datetime.fromisoformat(a["publishedAt"]) >= cutoff]
    kept.sort(key=lambda a: a["publishedAt"], reverse=True)
    return kept


def enrich(articles: list[dict], company_terms: set[str]) -> int:
    """스냅샷 전체 기준으로 비슷한 기사 묶음과 이슈 키워드를 다시 계산한다 (매 수집마다).
    반환: 2건 이상 묶음 수."""
    keywords.annotate(articles, company_terms)
    return cluster.assign(articles)


def build(articles: list[dict], statuses: list, now: datetime, window_days: int, topic_groups: dict) -> dict:
    return {
        "version": SCHEMA_VERSION,
        "generatedAt": now.astimezone(KST).isoformat(timespec="seconds"),
        "windowDays": window_days,
        "count": len(articles),
        "topicGroups": topic_groups,
        "feeds": [asdict(s) if not isinstance(s, dict) else s for s in statuses],
        "articles": articles,
    }

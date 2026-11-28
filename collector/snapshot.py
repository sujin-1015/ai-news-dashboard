"""프론트가 읽는 최신 스냅샷(articles.json) 생성."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from . import cluster, keywords
from .classify import Classifier, is_relevant
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


def keep_relevant(articles: list[dict], clf: Classifier, feed_kinds: dict[str, str]) -> tuple[list[dict], int]:
    """관련 없는 기사를 뺀다 (이미 스냅샷에 있는 기사 포함). 반환: (남은 기사, 뺀 건수).

    기준에 안 맞는 기사는 지금 사전으로 제목만 다시 분류해 본다 (사전을 고친 뒤 새로 주제가 붙는 기사는
    살리고 분류도 갱신). 요약은 처음 분류할 때 쓴 소개글보다 길어 '정책'·'주식' 같은 넓은 말이 잘못 걸리므로 쓰지 않는다."""
    kept: list[dict] = []
    for a in articles:
        kind = feed_kinds.get(a.get("feedId", ""), "media")
        if not is_relevant(a.get("topics", []), a.get("companies", []), kind):
            c = clf.classify(a["title"], "", kind)
            if not is_relevant(c.topics, c.companies, kind):
                continue
            a.update(industries=c.industries, topics=c.topics, companies=c.companies, why=c.why)
        kept.append(a)
    return kept, len(articles) - len(kept)


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

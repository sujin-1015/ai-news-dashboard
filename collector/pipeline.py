"""수집 → 파싱 → 정규화 → 분류 → 중복 제거 파이프라인 (순수 로직)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .classify import Classifier
from .fetch import FetchResult, fetch_all
from .parse import KST, article_id, clean_text, parse_date, parse_feed, truncate


@dataclass
class FeedStatus:
    id: str
    name: str
    ok: bool
    items: int = 0
    error: str | None = None
    elapsed_ms: int = 0


@dataclass
class CollectResult:
    articles: list[dict]
    statuses: list[FeedStatus]
    skipped_known: int = 0
    skipped_old: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success_ratio(self) -> float:
        return sum(s.ok for s in self.statuses) / max(len(self.statuses), 1)


def build_article(item, feed: dict, clf: Classifier, now: datetime) -> dict | None:
    title = clean_text(item.title)
    link = item.link.strip()
    if not title or not link.startswith(("http://", "https://")):
        return None
    summary = truncate(clean_text(item.summary_raw))
    published, guessed = parse_date(item.published, now)
    c = clf.classify(title, summary, feed.get("kind", "media"))
    pub_kst = published.astimezone(KST)
    art = {
        "id": article_id(link),
        "title": title,
        "link": link,
        "source": feed["name"],
        "feedId": feed["id"],
        "publishedAt": pub_kst.isoformat(timespec="seconds"),
        "day": pub_kst.date().isoformat(),
        "summary": summary,
        "industries": c.industries,
        "topics": c.topics,
        "companies": c.companies,
        "why": c.why,
        "collectedAt": now.astimezone(KST).isoformat(timespec="seconds"),
    }
    if guessed:
        art["dateGuessed"] = True
    return art


def process(
    results: list[FetchResult],
    clf: Classifier,
    now: datetime,
    known_ids: set[str] | None = None,
    max_age_days: int = 7,
) -> CollectResult:
    known_ids = known_ids or set()
    cutoff = now - timedelta(days=max_age_days)
    seen: set[str] = set()
    out = CollectResult(articles=[], statuses=[])
    for r in results:
        f = r.feed
        st = FeedStatus(f["id"], f["name"], ok=False, elapsed_ms=r.elapsed_ms)
        out.statuses.append(st)
        if r.error:
            st.error = r.error
            continue
        try:
            items = parse_feed(r.body or b"")
        except Exception as e:  # noqa: BLE001
            st.error = f"parse: {e}"[:200]
            continue
        st.ok, st.items = True, len(items)
        for it in items:
            try:
                a = build_article(it, f, clf, now)
            except Exception as e:  # noqa: BLE001 - 기사 1건 문제로 피드 전체를 버리지 않는다
                out.errors.append(f"{f['id']}: {e}"[:200])
                continue
            if a is None or a["id"] in seen:
                continue
            seen.add(a["id"])
            if a["id"] in known_ids:
                out.skipped_known += 1
                continue
            if datetime.fromisoformat(a["publishedAt"]) < cutoff:
                out.skipped_old += 1
                continue
            out.articles.append(a)
    return out


def collect(
    feeds: list[dict],
    clf: Classifier,
    now: datetime,
    known_ids: set[str] | None = None,
    max_age_days: int = 7,
    workers: int = 16,
    timeout: float = 10.0,
) -> CollectResult:
    active = [f for f in feeds if f.get("enabled", True)]
    return process(fetch_all(active, workers, timeout), clf, now, known_ids, max_age_days)

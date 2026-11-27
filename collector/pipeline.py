"""수집 → 파싱 → 정규화 → 분류 → 중복 제거 파이프라인 (순수 로직)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .classify import Classifier
from .fetch import FetchResult, fetch_all
from .parse import KST, article_id, clean_text, parse_date, parse_feed, truncate
from .summarize import Idf, clean_description, summarize, summarize_body


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
    body_summaries: int = 0  # 기사 본문으로 요약한 건수 (나머지는 RSS 소개글 요약)
    errors: list[str] = field(default_factory=list)

    @property
    def success_ratio(self) -> float:
        return sum(s.ok for s in self.statuses) / max(len(self.statuses), 1)


def build_article(item, feed: dict, clf: Classifier, now: datetime) -> dict | None:
    title = clean_text(item.title)
    link = item.link.strip()
    if not title or not link.startswith(("http://", "https://")):
        return None
    desc = clean_text(item.summary_raw)
    summary = summarize(title, desc)
    published, guessed = parse_date(item.published, now)
    # 분류는 요약(고른 1~2문장)이 아니라 정리한 소개글 앞부분으로 (기존과 같은 범위)
    c = clf.classify(title, truncate(clean_description(desc)), feed.get("kind", "media"))
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
    body_fetcher=None,
) -> CollectResult:
    """body_fetcher(articles) → {id: 본문}. 주면 새 기사는 본문 전체에서 요약한다 (없거나 실패하면 RSS 소개글 요약)."""
    known_ids = known_ids or set()
    docs: list[str] = []  # 단어 희귀도(IDF) 계산용: 이번 수집의 모든 RSS 제목+소개글
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
            docs.append(f"{it.title} {clean_text(it.summary_raw)}")
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
    if body_fetcher and out.articles:
        apply_body_summaries(out, body_fetcher(out.articles), Idf(docs))
    return out


def apply_body_summaries(out: CollectResult, bodies: dict[str, str], idf: Idf) -> None:
    for a in out.articles:
        body = bodies.get(a["id"])
        if not body:
            continue
        try:
            s = summarize_body(a["title"], body, idf)
        except Exception as e:  # noqa: BLE001 - 요약 1건 실패는 RSS 요약 유지
            out.errors.append(f"body_summary {a['id']}: {e}"[:200])
            continue
        if s:
            a["summary"] = s
            out.body_summaries += 1


def collect(
    feeds: list[dict],
    clf: Classifier,
    now: datetime,
    known_ids: set[str] | None = None,
    max_age_days: int = 7,
    workers: int = 16,
    timeout: float = 10.0,
    body_fetcher=None,
) -> CollectResult:
    active = [f for f in feeds if f.get("enabled", True)]
    return process(fetch_all(active, workers, timeout), clf, now, known_ids, max_age_days, body_fetcher)

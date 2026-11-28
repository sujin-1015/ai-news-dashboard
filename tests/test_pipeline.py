from datetime import datetime, timedelta
from pathlib import Path

from collector import snapshot
from collector.classify import Classifier
from collector.config import load_all
from collector.fetch import FetchResult
from collector.parse import KST
from collector.pipeline import process

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 25, 15, 0, tzinfo=KST)
cfg = load_all(ROOT / "config")
CLF = Classifier(cfg["taxonomy"], cfg["companies"])


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><pubDate>{d}</pubDate><description>{'긴요약' * 200}</description></item>"
        for t, l, d in items
    )
    return f"<rss><channel>{body}</channel></rss>".encode()


def test_failed_feed_does_not_stop_others_and_dedups_across_feeds():
    f1 = {"id": "a", "name": "A", "url": "x"}
    f2 = {"id": "b", "name": "B", "url": "y"}
    f3 = {"id": "c", "name": "C", "url": "z"}
    results = [
        FetchResult(f1, rss(("삼성전자 HBM", "https://ex.com/1?utm_source=a", "2026-11-25 10:00:00")), None, 10),
        FetchResult(f2, None, "HTTP 503", 10),
        FetchResult(f3, rss(("같은 기사", "http://www.ex.com/1", "2026-11-25 10:00:00"),
                            ("옛날 AI 기사", "https://ex.com/old", "2026-10-01 10:00:00")), None, 10),
    ]
    r = process(results, CLF, NOW)
    assert [s.ok for s in r.statuses] == [True, False, True]
    assert len(r.articles) == 1  # 링크 정규화로 중복 제거, 오래된 기사 제외
    assert r.skipped_old == 1
    a = r.articles[0]
    assert len(a["summary"]) <= 200  # 저작권: 요약 길이 제한
    assert "삼성전자" in a["companies"]


def test_known_ids_skipped():
    f = {"id": "a", "name": "A", "url": "x"}
    first = process([FetchResult(f, rss(("AI 소식", "https://ex.com/1", "2026-11-25 10:00:00")), None, 1)], CLF, NOW)
    known = {a["id"] for a in first.articles}
    again = process([FetchResult(f, rss(("AI 소식", "https://ex.com/1", "2026-11-25 10:00:00")), None, 1)], CLF, NOW, known)
    assert again.articles == [] and again.skipped_known == 1


def test_unrelated_articles_skipped():
    f = {"id": "a", "name": "A", "url": "x"}
    r = process([FetchResult(f, rss(("주말 먹거리 딸기 특가", "https://ex.com/9", "2026-11-25 10:00:00"),
                                    ("AI 반도체 투자", "https://ex.com/8", "2026-11-25 10:00:00")), None, 1)], CLF, NOW)
    assert [a["title"] for a in r.articles] == ["AI 반도체 투자"] and r.skipped_irrelevant == 1
    gov = {"id": "g", "name": "G", "url": "x", "kind": "gov"}
    r = process([FetchResult(gov, rss(("장관 현장 방문", "https://ex.com/7", "2026-11-25 10:00:00")), None, 1)], CLF, NOW)
    assert len(r.articles) == 1  # 정부·공공 피드는 주제가 없어도 남긴다


def test_snapshot_merge_window_and_order():
    old = {"id": "o", "publishedAt": (NOW - timedelta(days=10)).isoformat()}
    keep = {"id": "k", "publishedAt": (NOW - timedelta(days=1)).isoformat(), "v": "prev"}
    new = [{"id": "n", "publishedAt": NOW.isoformat()}, {"id": "k", "publishedAt": keep["publishedAt"], "v": "new"}]
    merged = snapshot.merge([old, keep], new, NOW, 7)
    assert [a["id"] for a in merged] == ["n", "k"]
    assert merged[1]["v"] == "prev"


def test_body_fetcher_replaces_summary_and_falls_back():
    f = {"id": "a", "name": "A", "url": "x"}
    feed = rss(("인터넷우체국 해킹 개인정보 유출", "https://ex.com/1", "2026-11-25 10:00:00"),
               ("다른 AI 기사", "https://ex.com/2", "2026-11-25 11:00:00"))
    text = ("인터넷우체국이 해킹 공격을 받아 고객 개인정보가 유출된 것으로 확인됐다.\n"
            "해커들은 배달장소 변경서비스에 접근한 것으로 파악됐다.\n"
            "하루 분량의 유출 고객은 총 11만3855명으로 확인됐다.\n"
            "우정사업본부는 해당 서비스를 중단하고 경찰에 수사를 의뢰했다.")

    def fetcher(arts):
        return {arts[0]["id"]: text}  # 두 번째 기사는 본문 실패

    r = process([FetchResult(f, feed, None, 1)], CLF, NOW, body_fetcher=fetcher)
    by_title = {a["title"]: a for a in r.articles}
    assert r.body_summaries == 1
    assert "11만3855명" in by_title["인터넷우체국 해킹 개인정보 유출"]["summary"]
    plain = {a["title"]: a for a in process([FetchResult(f, feed, None, 1)], CLF, NOW).articles}
    assert by_title["다른 AI 기사"]["summary"] == plain["다른 AI 기사"]["summary"]  # 실패하면 RSS 소개글 요약 그대로


def test_is_relevant_rule():
    from collector.classify import is_relevant
    assert is_relevant(["AI 일반"], [], "media")
    assert is_relevant([], ["삼성전자"], "media")
    assert is_relevant([], [], "gov")
    assert not is_relevant([], [], "media")


def test_keep_relevant_drops_unrelated_and_rescues_with_current_dictionary():
    from pathlib import Path
    from collector import snapshot
    from collector.classify import Classifier
    from collector.config import load_all
    cfg = load_all(Path(__file__).resolve().parents[1] / "config")
    clf = Classifier(cfg["taxonomy"], cfg["companies"])
    base = {"summary": "", "industries": ["기타"], "why": {}}
    arts = [
        {**base, "id": "a", "feedId": "x", "title": "주말 먹거리 할인 행사", "topics": [], "companies": []},
        # 예전 사전에서는 주제가 없었지만 지금 사전으로는 '보안' 주제가 붙는 기사 → 살리고 분류 갱신
        {**base, "id": "b", "feedId": "x", "title": "IT 자산 통합 보안관리 플랫폼 출시", "topics": [], "companies": []},
        {**base, "id": "c", "feedId": "gov1", "title": "장관 현장 방문", "topics": [], "companies": []},
        {**base, "id": "d", "feedId": "x", "title": "AI 서비스 출시", "topics": ["AI 일반"], "companies": []},
    ]
    kept, dropped = snapshot.keep_relevant(arts, clf, {"gov1": "gov"})
    assert [a["id"] for a in kept] == ["b", "c", "d"] and dropped == 1
    assert "보안" in kept[0]["topics"]

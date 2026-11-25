from datetime import datetime

import pytest

from collector.parse import KST, article_id, clean_text, normalize_link, parse_date, parse_feed, truncate

NOW = datetime(2026, 11, 25, 15, 0, 0, tzinfo=KST)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Wed, 25 Nov 2026 14:26:40 +0900", "2026-11-25T14:26:40+09:00"),  # RFC822
        ("Wed, 25 Nov 2026 05:26:40 GMT", "2026-11-25T14:26:40+09:00"),  # GMT
        ("Wed, 25 Nov 2026 14:26:40 +09:00", "2026-11-25T14:26:40+09:00"),  # 매경 +09:00
        ("Tue,24 Nov 2026 17:49:29 +0900", "2026-11-24T17:49:29+09:00"),  # 파이낸셜뉴스: 쉼표 뒤 공백 없음
        ("2026-11-24 11:56:18", "2026-11-24T11:56:18+09:00"),  # AI타임스: 시간대 없음 → KST
        ("2026-11-25T13:44:00+09:00", "2026-11-25T13:44:00+09:00"),  # 경향 ISO8601
        ("수, 25 11월 2026 10:00:00 KST", "2026-11-25T10:00:00+09:00"),  # 행안부 한글 로캘
        ("2026.11.20", "2026-11-20T12:00:00+09:00"),  # 과기정통부: 날짜만(과거)
        ("2026-11-24", "2026-11-24T12:00:00+09:00"),  # KISA: 날짜만(과거)
    ],
)
def test_parse_date_formats(raw, expected):
    dt, _ = parse_date(raw, NOW)
    assert dt.astimezone(KST).isoformat() == expected


def test_parse_date_today_date_only_uses_now():
    dt, guessed = parse_date("2026.11.25", NOW)
    assert dt == NOW and guessed


@pytest.mark.parametrize("raw", ["", "알 수 없음", "2027-01-01 00:00:00"])  # 없음 / 이상값 / 미래
def test_parse_date_fallback_to_now(raw):
    dt, guessed = parse_date(raw, NOW)
    assert dt == NOW and guessed


def test_clean_text_strips_html_and_double_escape():
    assert clean_text("<p>티빙 &amp;apos;이니&amp;apos;</p>\n  출시") == "티빙 '이니' 출시"
    assert clean_text("<table><tr><td><img src=x></td></tr></table>") == ""


def test_truncate():
    assert truncate("가" * 300) == "가" * 199 + "…"
    assert truncate("짧다") == "짧다"


def test_normalize_link_dedup_key():
    a = "https://www.aitimes.com/news/articleView.html?idxno=1&utm_source=rss"
    b = "http://aitimes.com/news/articleView.html?idxno=1"
    assert normalize_link(a) == normalize_link(b)
    assert article_id(a) == article_id(b)
    assert article_id(a) != article_id("https://www.aitimes.com/news/articleView.html?idxno=2")


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t</title>
<item><title><![CDATA[제목 & 기호]]></title><link>https://ex.com/a</link>
<description><![CDATA[<b>요약</b>]]></description><pubDate>2026-11-25 10:00:00</pubDate></item>
<item><title>guid만 있음</title><guid>https://ex.com/b</guid></item>
</channel></rss>""".encode()

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
<entry><title>A</title><link rel="alternate" href="https://ex.com/x"/><updated>2026-11-25T01:00:00Z</updated>
<summary>s</summary></entry></feed>"""


def test_parse_rss_and_guid_fallback():
    items = parse_feed(RSS)
    assert items[0].title == "제목 & 기호" and items[0].link == "https://ex.com/a"
    assert items[1].link == "https://ex.com/b"


def test_parse_atom():
    items = parse_feed(ATOM)
    assert items[0].link == "https://ex.com/x" and items[0].published.startswith("2026-11-25")


def test_parse_rejects_html():
    with pytest.raises(ValueError):
        parse_feed(b"<!DOCTYPE html><html><body>not rss</body></html>")


def test_parse_bare_ampersand_recovery():
    body = b"<rss><channel><item><title>A & B</title><link>https://ex.com/?a=1&b=2</link></item></channel></rss>"
    assert parse_feed(body)[0].title == "A & B"

"""RSS/Atom 파싱과 매체별 차이 정규화.

매체마다 다른 점 (2026-11 실측):
- pubDate: RFC822(+0900 / +09:00 / GMT / +0000), "2026-11-25 13:51:56"(시간대 없음, KST),
  ISO8601("2026-11-25T15:44:00+09:00"), "2026.11.25", "2026-11-24"(날짜만),
  "Tue,24 Nov 2026 ..."(쉼표 뒤 공백 없음), "수, 25 11월 2026 15:00:00 KST"(한글 로캘), 없음(한겨레)
- description: 0자 ~ 8만 자, HTML 태그/이미지 테이블 포함, 이중 이스케이프(&amp;apos;)
- guid 없는 곳 다수 → 중복 제거는 link 기준
"""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

KST = timezone(timedelta(hours=9))
SUMMARY_MAX = 200  # 저작권: 짧게 자른 요약만 저장

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"


@dataclass
class RawItem:
    title: str
    link: str
    published: str  # 원문 그대로 (디버깅용)
    summary_raw: str


# ── XML ──────────────────────────────────────────────────────────────

_BARE_AMP = re.compile(rb"&(?!#\d+;|#x[0-9a-fA-F]+;|[a-zA-Z][a-zA-Z0-9]*;)")


def _parse_xml(body: bytes) -> ET.Element:
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        # 일부 매체는 &를 이스케이프하지 않는다 → 한 번만 보정 시도
        return ET.fromstring(_BARE_AMP.sub(b"&amp;", body))


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_feed(body: bytes) -> list[RawItem]:
    """RSS 2.0 / Atom 본문 → RawItem 목록. HTML 응답이면 ValueError."""
    head = body[:512].lstrip().lower()
    if head.startswith(b"<!doctype html") or b"<html" in head:
        raise ValueError("HTML 응답 (RSS 아님)")
    root = _parse_xml(body)
    items: list[RawItem] = []
    for it in root.iter("item"):
        items.append(
            RawItem(
                title=_text(it.find("title")),
                link=_text(it.find("link")) or _text(it.find("guid")),
                published=_text(it.find("pubDate")) or _text(it.find(f"{DC}date")),
                summary_raw=_text(it.find("description")),
            )
        )
    if not items:
        for e in root.iter(f"{ATOM}entry"):
            link_el = e.find(f"{ATOM}link[@rel='alternate']")
            if link_el is None:  # Element는 자식이 없으면 falsy라서 `or`를 쓰면 안 된다
                link_el = e.find(f"{ATOM}link")
            items.append(
                RawItem(
                    title=_text(e.find(f"{ATOM}title")),
                    link=(link_el.get("href", "") if link_el is not None else "").strip(),
                    published=_text(e.find(f"{ATOM}published")) or _text(e.find(f"{ATOM}updated")),
                    summary_raw=_text(e.find(f"{ATOM}summary")) or _text(e.find(f"{ATOM}content")),
                )
            )
    if not items:
        raise ValueError("item/entry 없음")
    return items


# ── 텍스트 ───────────────────────────────────────────────────────────

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(s: str) -> str:
    """HTML 태그 제거 + 엔티티 해제(이중 이스케이프 대응) + 공백 정리."""
    if not s:
        return ""
    for _ in range(2):  # "&amp;apos;" → "&apos;" → "'"
        s = html.unescape(s)
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", s).strip()


def truncate(s: str, n: int = SUMMARY_MAX) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ── 링크 / ID ────────────────────────────────────────────────────────

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|ref$|from$|rssid$)", re.I)


def normalize_link(link: str) -> str:
    """중복 판정용 정규화 키 (표시용 링크는 원본 유지).
    scheme 무시, host 소문자, 추적 파라미터/fragment/끝 슬래시 제거."""
    p = urlsplit(link.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)]
    path = p.path.rstrip("/") or "/"
    return urlunsplit(("", p.netloc.lower().removeprefix("www."), path, urlencode(sorted(q)), ""))


def article_id(link: str) -> str:
    return hashlib.sha256(normalize_link(link).encode("utf-8")).hexdigest()[:16]


# ── 날짜 ─────────────────────────────────────────────────────────────

_YMD_HMS = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?$")
_YMD = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})\.?$")
_KO_LOCALE = re.compile(r"(\d{1,2})\s+(\d{1,2})월\s+(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})")


def parse_date(raw: str, now: datetime) -> tuple[datetime, bool]:
    """여러 형식의 날짜 → (aware datetime, 추정 여부).

    - 시간대 없는 값은 KST로 간주
    - 날짜만 있으면: 오늘이면 수집 시각, 과거면 그날 12:00 KST
    - 파싱 실패/값 없음 → 수집 시각 (추정=True)
    - 미래 시각(1시간 이상)은 수집 시각으로 보정
    """
    s = (raw or "").strip()
    dt: datetime | None = None
    guessed = False
    if s:
        m = _YMD_HMS.match(s)
        if m:
            y, mo, d, h, mi, se = m.groups()
            dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(se or 0), tzinfo=KST)
        elif (m := _YMD.match(s)):
            y, mo, d = map(int, m.groups())
            day = datetime(y, mo, d, tzinfo=KST)
            today = now.astimezone(KST).date()
            dt = now if day.date() >= today else day.replace(hour=12)
            guessed = True
        elif (m := _KO_LOCALE.search(s)):
            d, mo, y, h, mi, se = map(int, m.groups())
            dt = datetime(y, mo, d, h, mi, se, tzinfo=KST)
        else:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                try:
                    dt = parsedate_to_datetime(re.sub(r",(?=\S)", ", ", s))
                except (TypeError, ValueError, IndexError):
                    dt = None
            if dt is not None and dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
    if dt is None:
        return now, True
    if dt > now + timedelta(hours=1):
        return now, True
    return dt, guessed

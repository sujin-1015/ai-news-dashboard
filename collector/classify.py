"""키워드 규칙 기반 분류기 (LLM 호출 없음).

- 업종(industries) / 주제(topics) / 기업(companies) 다중 라벨
- 분류 근거(why)를 함께 반환 → 오분류를 화면에서 확인하고 사전을 고칠 수 있다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

OTHER = "기타"
_ASCII = re.compile(r"[A-Za-z0-9]")


def _compile(term: str) -> re.Pattern[str]:
    """한글 포함 → 부분 문자열, 영문/숫자로 시작·끝 → 앞뒤에 영문·숫자가 없을 때만."""
    esc = re.escape(term)
    pre = r"(?<![A-Za-z0-9])" if _ASCII.match(term[0]) else ""
    post = r"(?![A-Za-z0-9])" if _ASCII.match(term[-1]) else ""
    return re.compile(pre + esc + post, re.IGNORECASE)


@dataclass
class _Rule:
    label: str
    patterns: list[tuple[str, re.Pattern[str]]]
    excludes: list[re.Pattern[str]] = field(default_factory=list)
    industry: str | None = None  # 기업 규칙: 소속 업종
    group: str | None = None  # 주제 규칙: 묶음
    fallback_industry: str | None = None  # 주제 규칙: 업종이 없을 때 대신 붙일 업종

    def match(self, text: str) -> list[str]:
        for ex in self.excludes:
            text = ex.sub(" ", text)
        return [term for term, p in self.patterns if p.search(text)]


def _rule(label: str, keywords: list[str], exclude: list[str] | None = None, **kw) -> _Rule:
    pats = [(k, _compile(k)) for k in keywords]
    exs = [re.compile(re.escape(e), re.IGNORECASE) for e in (exclude or [])]
    return _Rule(label, pats, exs, **kw)


@dataclass
class Classification:
    industries: list[str]
    topics: list[str]
    companies: list[str]
    why: dict[str, list[str]]  # "업종:금융" → ["은행", "기업:KB금융"]


class Classifier:
    def __init__(self, taxonomy: dict, companies: dict):
        self.industries = [
            _rule(name, v.get("keywords", []), v.get("exclude")) for name, v in taxonomy["industries"].items()
        ]
        self.topics = [
            _rule(name, v.get("keywords", []), v.get("exclude"), group=v.get("group"),
                  fallback_industry=v.get("fallback_industry"))
            for name, v in taxonomy["topics"].items()
        ]
        self.companies = [
            _rule(c["name"], c["aliases"], c.get("exclude"), industry=ind)
            for ind, lst in companies.items()
            for c in lst
        ]
        self.topic_groups = {r.label: r.group for r in self.topics}

    def classify(self, title: str, summary: str, feed_kind: str = "media") -> Classification:
        text = f"{title}\n{summary}"
        why: dict[str, list[str]] = {}
        industries: list[str] = []
        topics: list[str] = []
        companies: list[str] = []

        def add(bucket: list[str], prefix: str, label: str, reasons: list[str]) -> None:
            if label not in bucket:
                bucket.append(label)
            lst = why.setdefault(f"{prefix}:{label}", [])
            for r in reasons:
                if r not in lst:
                    lst.append(r)

        for r in self.companies:
            hits = r.match(text)
            if hits:
                add(companies, "기업", r.label, hits)
                add(industries, "업종", r.industry, [f"기업:{r.label}"])
        for r in self.industries:
            hits = r.match(text)
            if hits:
                add(industries, "업종", r.label, hits)
        if feed_kind == "gov":
            add(industries, "업종", "공공", ["정부·공공 피드"])
        fallback: list[_Rule] = []
        for r in self.topics:
            hits = r.match(text)
            if hits:
                add(topics, "주제", r.label, hits)
                if r.fallback_industry:
                    fallback.append(r)

        if not industries and fallback:
            r = fallback[0]
            add(industries, "업종", r.fallback_industry, [f"주제:{r.label}(기본값)"])
        if not industries:
            industries = [OTHER]
        order = {r.label: i for i, r in enumerate(self.industries)}
        industries.sort(key=lambda x: order.get(x, 99))
        return Classification(industries, topics, companies, why)

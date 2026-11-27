"""비슷한 기사 묶기: 같은 사건을 다룬 다른 매체(또는 같은 매체의 다른 링크) 기사를 한 묶음으로.

링크 기준 중복 제거(parse.article_id)로는 못 잡는 경우를 잡는다.
  예) 조선비즈 ↔ 조선일보 같은 기사, 전자신문 피드 4개의 같은 기사, 연합 기사를 받아 쓴 매체들

방법
- 제목 정리: [단독]·괄호·따옴표·문장부호·띄어쓰기 제거
- 글자 2개씩 자른 조각의 겹침 비율(자카드). 띄어쓰기·조사·표기가 달라도 잘 잡힌다.
- 판정: 유사도 ≥ STRONG 이면 같은 묶음. WEAK~STRONG이면 요약 유사도나 공통 기업·숫자가 있을 때만.
- 시간: WINDOW_HOURS 안에 나온 기사끼리만. 같은 매체끼리는 SAME_SOURCE_HOURS 안에서만
  (매일 같은 제목으로 나오는 "오늘의 시세" 같은 연재가 날짜를 넘어 묶이지 않게).
- A≈B, B≈C면 A·B·C 한 묶음 (Union-Find). 묶음 id = 가장 먼저 발행된 기사 id.
- 대표 기사: 요약이 가장 충실한 기사 (요약 길이 → 발행 시각 순).
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

STRONG = 0.6
WEAK = 0.4
SUMMARY_SIM = 0.3
WINDOW_HOURS = 48
SAME_SOURCE_HOURS = 12
_RARE_DF = 60  # 이보다 흔한 글자 조각은 후보 찾기 색인에서 뺀다 (속도)

_BRACKETS = re.compile(r"\[[^\]]*\]|【[^】]*】|\([^)]*\)")
_NON_WORD = re.compile(r"[^\w가-힣]")
_NUM = re.compile(r"\d[\d,.]*\d|\d")


def normalize_title(title: str) -> str:
    return _NON_WORD.sub("", _BRACKETS.sub("", title)).lower()


def bigrams(s: str) -> frozenset[str]:
    return frozenset(s[i:i + 2] for i in range(len(s) - 1))


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _numbers(s: str) -> set[str]:
    return {n for n in _NUM.findall(s) if len(n) >= 2}


class _UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _subject(title: str) -> frozenset:
    """'그라비티 네오싸이언, 우수기업 선정' → 쉼표 앞 주어 부분의 글자 조각 (없으면 빈 집합)."""
    head = _BRACKETS.sub("", title).split(",", 1)
    return bigrams(normalize_title(head[0])) if len(head) == 2 and len(head[0]) <= 20 else frozenset()


def _same_story(a: dict, b: dict, ta: frozenset, tb: frozenset, sa: frozenset, sb: frozenset) -> bool:
    hours = abs((datetime.fromisoformat(a["publishedAt"]) - datetime.fromisoformat(b["publishedAt"])).total_seconds()) / 3600
    if hours > (SAME_SOURCE_HOURS if a["source"] == b["source"] else WINDOW_HOURS):
        return False
    sim = jaccard(ta, tb)
    # 주어(쉼표 앞)가 다른 회사면 다른 기사: "A사, 우수기업 선정" ≠ "B사, 우수기업 선정"
    # 단, 같은 기업으로 매칭됐으면(LG유플러스 ↔ LGU+) 표기만 다른 것
    ja, jb = _subject(a["title"]), _subject(b["title"])
    if ja and jb and jaccard(ja, jb) < 0.2 and sim < 0.85 and not set(a.get("companies", [])) & set(b.get("companies", [])):
        return False
    if sim >= STRONG:
        return True
    if sim < WEAK:
        return False
    if jaccard(sa, sb) >= SUMMARY_SIM:
        return True
    if set(a.get("companies", [])) & set(b.get("companies", [])):
        return True
    return bool(_numbers(a["title"]) & _numbers(b["title"]))


def assign(articles: list[dict]) -> int:
    """기사 목록에 묶음 정보를 붙인다 (제자리 수정). 2건 이상 묶음 수를 돌려준다.

    붙이는 필드 (2건 이상 묶음에만): cluster = 묶음 id, clusterRep = 대표 기사면 True
    """
    n = len(articles)
    titles = [bigrams(normalize_title(a["title"])) for a in articles]
    summaries = [bigrams(normalize_title(a.get("summary", "")[:80])) for a in articles]

    index: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(titles):
        for g in t:
            index[g].append(i)
    uf = _UnionFind(n)
    for i, t in enumerate(titles):
        cand: set[int] = set()
        for g in t:
            lst = index[g]
            if len(lst) <= _RARE_DF:
                cand.update(j for j in lst if j > i)
        for j in cand:
            if uf.find(i) != uf.find(j) and _same_story(articles[i], articles[j], t, titles[j], summaries[i], summaries[j]):
                uf.union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    multi = 0
    for a in articles:
        a.pop("cluster", None)
        a.pop("clusterRep", None)
    for members in groups.values():
        if len(members) < 2:
            continue
        multi += 1
        first = min(members, key=lambda i: (articles[i]["publishedAt"], articles[i]["id"]))
        rep = max(members, key=lambda i: (len(articles[i].get("summary", "")), -_ts(articles[i])))
        for i in members:
            articles[i]["cluster"] = articles[first]["id"]
            if i == rep:
                articles[i]["clusterRep"] = True
    return multi


def _ts(a: dict) -> float:
    return datetime.fromisoformat(a["publishedAt"]).timestamp()

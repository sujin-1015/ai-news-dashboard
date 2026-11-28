"""비슷한 기사 묶기: 같은 사건을 다룬 다른 매체(또는 같은 매체의 다른 링크) 기사를 한 묶음으로.

링크 기준 중복 제거(parse.article_id)로는 못 잡는 경우를 잡는다.
  예) 전자신문 피드 4개의 같은 기사, 연합뉴스 기사를 받아 쓴 매체들, 같은 보도자료를 쓴 여러 매체

방법
- 제목 정리: [단독]·괄호·따옴표·문장부호·띄어쓰기 제거
- 글자 2개씩 자른 조각의 겹침 비율(자카드). 띄어쓰기·조사·표기가 달라도 잘 잡힌다.
- 판정: 유사도 ≥ STRONG 이면 같은 묶음. WEAK~STRONG이면 요약 유사도나 공통 기업·숫자가 있을 때만.
- 시간: WINDOW_HOURS 안에 나온 기사끼리만. 같은 매체끼리는 SAME_SOURCE_HOURS 안에서만
  (매일 같은 제목으로 나오는 "오늘의 시세" 같은 연재가 날짜를 넘어 묶이지 않게).
- A≈B, B≈C면 A·B·C 한 묶음 (Union-Find). 묶음 id = 가장 먼저 발행된 기사 id.
- 대표 기사: 요약이 가장 충실한 기사 (요약 길이 → 발행 시각 순).
- 내용 비교(2차): 제목은 달라도 내용이 거의 같은 다른 매체 기사를 묶는다.
  제목(2배 가중)+요약(본문에서 뽑은 핵심 문장)의 낱말을 TF-IDF로 바꿔 코사인 유사도 ≥ CONTENT_SIM.
  흔한 낱말(보도자료 상투어)은 가중치가 낮고 기업명·인명·숫자처럼 드문 낱말이 크게 반영된다.
  같은 매체끼리는 쓰지 않는다 (속보·공시·보도자료 양식이 같아 내용이 비슷해 보임).
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
import math

from .summarize import content_tokens

STRONG = 0.6
WEAK = 0.4
SUMMARY_SIM = 0.3
WINDOW_HOURS = 48
SAME_SOURCE_HOURS = 12
CONTENT_SIM = 0.5
GROUP_SIM = 0.4  # 이미 묶인 두 묶음은 기사끼리 평균 유사도가 이 이상일 때만 합친다
CONTENT_OVERRIDE = 0.7  # 이 이상이면 주어(쉼표 앞) 표기가 달라도 같은 기사 (KIRO ↔ 한국로봇융합硏)
_MIN_IDF = 1.5  # 너무 흔한 낱말은 내용 비교에서 뺀다
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
        self.size = [1] * n

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            lo, hi = min(ra, rb), max(ra, rb)
            self.p[hi] = lo
            self.size[lo] += self.size[hi]


def _subject(title: str) -> frozenset:
    """'그라비티 네오싸이언, 우수기업 선정' → 쉼표 앞 주어 부분의 글자 조각 (없으면 빈 집합)."""
    head = _BRACKETS.sub("", title).split(",", 1)
    return bigrams(normalize_title(head[0])) if len(head) == 2 and len(head[0]) <= 20 else frozenset()


def _hours(a: dict, b: dict) -> float:
    return abs((datetime.fromisoformat(a["publishedAt"]) - datetime.fromisoformat(b["publishedAt"])).total_seconds()) / 3600


def _subject_veto(a: dict, b: dict, sim: float) -> bool:
    """주어(쉼표 앞)가 다른 회사면 다른 기사: "A사, 우수기업 선정" ≠ "B사, 우수기업 선정".
    단, 같은 기업으로 매칭됐으면(LG유플러스 ↔ LGU+) 표기만 다른 것."""
    ja, jb = _subject(a["title"]), _subject(b["title"])
    return bool(ja and jb and jaccard(ja, jb) < 0.2 and sim < 0.85
                and not set(a.get("companies", [])) & set(b.get("companies", [])))


def content_vectors(articles: list[dict]) -> list[dict[str, float]]:
    """제목(2배)+요약 낱말의 TF-IDF 벡터 (길이 1로 정규화)."""
    counts = []
    for a in articles:
        c: dict[str, int] = defaultdict(int)
        for w in content_tokens(a["title"]):
            c[w] += 2
        for w in content_tokens(a.get("summary", "")):
            c[w] += 1
        counts.append(c)
    df: dict[str, int] = defaultdict(int)
    for c in counts:
        for w in c:
            df[w] += 1
    n = len(articles)
    out = []
    for c in counts:
        v = {}
        for w, k in c.items():
            idf = math.log(n / (1 + df[w]))
            if idf > _MIN_IDF:
                v[w] = k * idf
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({w: x / norm for w, x in v.items()})
    return out


def _dot(u: dict[str, float], v: dict[str, float]) -> float:
    if len(u) > len(v):
        u, v = v, u
    return sum(x * v.get(w, 0.0) for w, x in u.items())


def _same_story(a: dict, b: dict, ta: frozenset, tb: frozenset, sa: frozenset, sb: frozenset) -> bool:
    if _hours(a, b) > (SAME_SOURCE_HOURS if a["source"] == b["source"] else WINDOW_HOURS):
        return False
    sim = jaccard(ta, tb)
    if _subject_veto(a, b, sim):
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

    # 2차: 내용 비교 (다른 매체끼리만). 따로 남은 기사를 묶음에 붙이거나 둘을 짝짓기만 한다.
    vecs = content_vectors(articles)
    members: dict[int, list[int]] = defaultdict(list)
    for k in range(n):
        members[uf.find(k)].append(k)
    windex: dict[str, list[int]] = defaultdict(list)
    for i, v in enumerate(vecs):
        for w in v:
            windex[w].append(i)
    for i, v in enumerate(vecs):
        score: dict[int, float] = defaultdict(float)
        for w, x in v.items():
            for j in windex[w]:
                if j > i:
                    score[j] += x * vecs[j][w]
        for j, c in score.items():
            ri, rj = uf.find(i), uf.find(j)
            if c < CONTENT_SIM or ri == rj:
                continue
            a, b = articles[i], articles[j]
            if a["source"] == b["source"] or _hours(a, b) > WINDOW_HOURS:
                continue
            if c < CONTENT_OVERRIDE and _subject_veto(a, b, jaccard(titles[i], titles[j])):
                continue
            # 이미 묶인 두 묶음은 기사 한 쌍이 아니라 전체가 비슷할 때만 합친다: 관련 있지만 다른 사건이
            # 사슬처럼 이어지는 것 방지 (예: "LGU+ 배송로봇 5G SA 실증" + "통신 3사 5G SA 상용화")
            if uf.size[ri] > 1 and uf.size[rj] > 1:
                mi, mj = members[ri], members[rj]
                avg = sum(_dot(vecs[x], vecs[y]) for x in mi for y in mj) / (len(mi) * len(mj))
                if avg < GROUP_SIM:
                    continue
            uf.union(i, j)
            root = uf.find(i)
            members[root].extend(members.pop(rj if root == ri else ri))

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

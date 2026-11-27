"""LLM 없는 추출 요약: RSS 소개글에서 핵심 문장 1~2개를 골라 그대로 보여준다.

방법 (2026-11 비교 실험에서 채택: TF-IDF·TextRank·LSA·한국어 BERT 임베딩 3종·KoBART보다
같은 사건을 다룬 다른 매체 기준 ROUGE-2·핵심 숫자 보존율이 높고, 기사당 1ms 이하)
1. 군더더기 제거: 기자 머리말, 사진 설명, 기사 끝 저작권·이메일, 붙어 있는 문장 띄우기
2. 문장 분리: 매체가 중간에 자른 마지막 조각은 버린다
3. 점수 = 제목과 겹치는 글자 조각 비율 + 앞쪽 문장 가산점 − 짧은 문장·인용문 감점
4. 점수 순으로 SUMMARY_LIMIT자 안에 들어가는 문장을 최대 2개 골라 원래 순서로 (문장을 자르지 않는다)
5. 완결된 문장이 없으면(매체가 중간에 잘라 보냄) 요약을 비운다 — 끝이 '…'로 끊긴 요약은 만들지 않는다
"""
from __future__ import annotations

import re

from .parse import SUMMARY_MAX

SUMMARY_LIMIT = 170  # 두 문장 합계 목표 길이
# 한 문장만 쓸 때는 SUMMARY_MAX(200자, 저작권: 짧은 요약만)까지 허용, 그보다 긴 문장은 쓰지 않는다

_BYLINE = [
    # [서울=뉴시스] 이정필 기자 = / (서울=연합뉴스) 홍길동 기자 = / [시사저널e=송준영 기자]
    re.compile(r"^\s*[\[(【][^\])】]{0,30}=[^\])】]{0,20}[\])】]\s*(?:[가-힣]{2,4}\s*(?:기자|특파원|에디터|통신원)\s*)?=?\s*"),
    re.compile(r"^\s*\[[^\]]{1,20}\]\s*"),  # [데이터넷], [정보통신신문=서유덕기자]
    re.compile(r"^\s*[가-힣]{2,4}\s*(?:기자|특파원)\s*=\s*"),  # 홍길동 기자 =
]
_INLINE_NOISE = [
    re.compile(r"\[사진=[^\]]*\]|\(사진=[^)]*\)|〈본지[^〉]*〉|<본지[^>]*>"),
    re.compile(r"\s(?:◎|ⓒ|©|▶|☞).*$"),  # 기사 끝 저작권·관련 기사 안내
    re.compile(r"\S+@\S+\.\w+"),  # 기자 이메일
]
_NOISE_SENT = re.compile(r"사진은|사진=|\(왼쪽부터\)|\(오른쪽부터\)|무단\s*전재|재배포|저작권자|공감언론|기사제보")
_GLUED = re.compile(r"([다요음함]\.)(?=[가-힣A-Za-z\"'“‘(\[●■▲◆△▶])")
_SPLIT = re.compile(r"(?<=[다요음함]\.)\s+|(?<=[?!])\s+|(?<=[다요]\.[\"”’'])\s+")
_TERMINAL = re.compile(r"(?<![.])[.?!][\"”’']?$")  # "..", "..."로 끝나면 매체가 자른 것
# 소제목이 본문 첫 문장에 붙어 들어온 경우("○○ 5조 매출… 신작 열풍13년 만에 …했다.")
_SUBHEAD = re.compile(r"…|···|⋯")
_QUOTE = re.compile(r"[\"“].*[\"”]\s*(?:며|고\s*(?:말|강조|설명)했다)|고\s*말했다\.$")
_LEAD_SYMBOL = re.compile(r"^[\s▲■◆●△▶]+")


def clean_description(text: str) -> str:
    """정리된(HTML 제거된) 소개글에서 머리말·사진 설명·꼬리말을 걷어낸다."""
    s = text or ""
    for p in _BYLINE:
        s = p.sub("", s, count=1)
    for p in _INLINE_NOISE:
        s = p.sub(" ", s)
    s = _GLUED.sub(r"\1 ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_sentences(text: str) -> list[str]:
    """완결된 문장만 돌려준다 (사진 설명 등 잡음 문장, 잘린 마지막 조각 제외)."""
    out = []
    for p in _SPLIT.split(clean_description(text)):
        p = _LEAD_SYMBOL.sub("", p).strip()
        if p and _TERMINAL.search(p) and not _NOISE_SENT.search(p):
            out.append(p)
    return out


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"[^\w가-힣]", "", s).lower()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def score_sentences(title: str, sents: list[str]) -> list[float]:
    t = _bigrams(title)
    scores = []
    for i, s in enumerate(sents):
        cover = len(_bigrams(s) & t) / len(t) if t else 0.0
        position = max(0.0, 1 - 0.15 * i)
        penalty = ((1.0 if len(s) < 25 else 0.0)
                   + (0.4 if _QUOTE.search(s) else 0.0)
                   + (1.0 if _SUBHEAD.search(s) else 0.0))
        scores.append(cover + 0.5 * position - penalty)
    return scores


def summarize(title: str, text: str, limit: int = SUMMARY_LIMIT, max_sentences: int = 2) -> str:
    sents = split_sentences(text)
    if not sents:
        return ""  # 완결 문장 없음 → 잘린 글을 보여주지 않는다
    scores = score_sentences(title, sents)
    picked: list[int] = []
    for i in sorted(range(len(sents)), key=lambda i: -scores[i]):
        total = sum(len(sents[j]) + 1 for j in picked) + len(sents[i])
        if (not picked and len(sents[i]) <= SUMMARY_MAX) or (picked and total <= limit):
            picked.append(i)
        if len(picked) >= max_sentences:
            break
    return " ".join(sents[i] for i in sorted(picked))


# ── 기사 본문 요약 ───────────────────────────────────────────────────
# 2026-11 비교 실험(같은 사건 3곳 이상 매체 25건, 다른 매체 기사를 의사 참조 요약으로 쓴 다중 참조 평가)에서
# 본문 참조 ROUGE-1·ROUGE-2 F1 1위, BERTScore F1 상위권, 리드 참조에서도 상위권이었던 방식:
#   점수 = 0.5 × TextRank(TF-IDF 문장 유사도 그래프) + 0.3 × 제목 유사도 + 0.2 × 위치(앞일수록 높게)
#   → MMR(λ=0.7)로 앞서 고른 문장과 겹치는 문장을 피하며 최대 3문장, BODY_LIMIT자 이내, 원래 순서로
# 한국어 형태소 분석기 없이 동작하도록 단어는 띄어쓰기 + 조사 떼기로 자른다.

BODY_LIMIT = 250
BODY_SENTENCES = 3
_W = {"rank": 0.5, "title": 0.3, "position": 0.2}
MMR_LAMBDA = 0.7

_WORD = re.compile(r"[A-Za-z0-9]+|[가-힣]+")
_PARTICLES = ("에서는", "으로는", "에게서", "이라는", "에서", "으로", "에게", "까지", "부터", "보다", "처럼",
              "이나", "이며", "라는", "에는", "에도", "로는", "은", "는", "이", "가", "을", "를", "의", "에",
              "도", "만", "로", "와", "과")


def content_tokens(s: str) -> list[str]:
    out = []
    for w in _WORD.findall(s.lower()):
        if w[0] >= "가":
            for p in _PARTICLES:
                if w.endswith(p) and len(w) - len(p) >= 2:
                    w = w[: -len(p)]
                    break
        if len(w) >= 2 or w.isdigit():
            out.append(w)
    return out


class Idf:
    """문서 묶음(이번 수집의 RSS 제목+소개글)으로 만든 단어 희귀도."""

    def __init__(self, docs: list[str]):
        from collections import Counter
        import math

        df = Counter()
        for d in docs:
            df.update(set(content_tokens(d)))
        n = len(docs)
        self._log = math.log
        self._n, self._df = n, df

    def __call__(self, t: str) -> float:
        return self._log((self._n + 1) / (self._df.get(t, 0) + 1)) + 1


def _tfidf(s: str, idf: Idf) -> dict[str, float]:
    from collections import Counter
    import math

    v = {t: c * idf(t) for t, c in Counter(content_tokens(s)).items()}
    n = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / n for t, x in v.items()}


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def _pagerank(sim: list[list[float]], d: float = 0.85, iters: int = 60) -> list[float]:
    n = len(sim)
    out_sum = [sum(row[j] for j in range(n) if j != i) or 1.0 for i, row in enumerate(sim)]
    r = [1.0 / n] * n
    for _ in range(iters):
        r = [(1 - d) / n + d * sum(sim[j][i] / out_sum[j] * r[j] for j in range(n) if j != i) for i in range(n)]
    return r


def _norm01(xs: list[float]) -> list[float]:
    lo, hi = min(xs), max(xs)
    return [1.0] * len(xs) if hi - lo < 1e-12 else [(x - lo) / (hi - lo) for x in xs]


def body_sentences(body: str) -> list[str]:
    """본문은 문단(줄) 단위로 먼저 나눈다 → 마침표 없는 소제목·사진 설명 줄은 빠진다."""
    return [s for para in body.splitlines() for s in split_sentences(para)]


def summarize_body(title: str, body: str, idf: Idf, limit: int = BODY_LIMIT, k: int = BODY_SENTENCES) -> str:
    """기사 본문 전체에서 핵심 문장 최대 k개. 쓸 만한 문장이 3개 미만이면 빈 문자열(호출한 쪽이 RSS 요약 사용)."""
    sents = [s for s in body_sentences(body) if len(s) <= limit]
    if len(sents) < 3:
        return ""
    vecs = [_tfidf(s, idf) for s in sents]
    sim = [[_cos(a, b) for b in vecs] for a in vecs]
    tv = _tfidf(title, idf)
    rank = _norm01(_pagerank(sim))
    tsim = _norm01([_cos(v, tv) for v in vecs])
    n = len(sents)
    score = [_W["rank"] * rank[i] + _W["title"] * tsim[i] + _W["position"] * (1 - i / n) for i in range(n)]

    picked: list[int] = []
    cand = list(range(n))
    while cand and len(picked) < k:
        best = max(cand, key=lambda i: MMR_LAMBDA * score[i]
                   - (1 - MMR_LAMBDA) * (max(sim[i][j] for j in picked) if picked else 0.0))
        cand.remove(best)
        if sum(len(sents[j]) + 1 for j in picked) + len(sents[best]) <= limit:
            picked.append(best)
    return " ".join(sents[i] for i in sorted(picked))

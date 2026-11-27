"""핵심 이슈 키워드: 기사 제목에서 단어를 뽑는다 (형태소 분석기·LLM 없이).

- 띄어쓰기·문장부호로 자르고, 끝에 붙은 조사를 뗀다.
  한 글자 조사(이·가·은·는·도·의 …)는 떼고 남은 말이 다른 제목에서도 단독으로 쓰였을 때만 뗀다.
  ("와이파이"의 '이', "디스플레이"의 '이'를 잘못 떼지 않게 — 전체 제목 묶음을 사전처럼 쓴다)
- 숫자만 있거나 숫자+한글(3만대, 2026년)은 버리고, 영문이 섞인 것(HBM4, 5G, GPT-5)은 남긴다.
- 너무 흔한 말(불용어)과 기업 이름(기업 순위가 따로 있음)은 뺀다.
- 기사당 최대 MAX_PER_ARTICLE개. 순위·급상승 계산은 화면이 현재 필터 기준으로 한다.
"""
from __future__ import annotations

import re
from collections import Counter

MAX_PER_ARTICLE = 5

_SPLIT = re.compile(r"[^\w가-힣+#.-]+")
_EDGE = re.compile(r"^[.\-+#]+|[.\-]+$")
_MULTI = ("에서는", "으로는", "에게서", "이라는", "에서", "으로", "에게", "까지", "부터", "보다", "처럼", "마저",
          "조차", "이나", "이며", "이고", "라는", "하고", "와의", "과의", "에는", "에도", "에선", "로는")
_SINGLE = ("은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "로", "와", "과", "서")
_HAS_DIGIT = re.compile(r"\d")
_HAS_LATIN = re.compile(r"[A-Za-z]")
_HANGUL = re.compile(r"[가-힣]")
_UNIT = re.compile(r"^\d[\d.,]*(?:km|kg|cm|mm|ml|gb|tb|mb|kb|kw|mw|gw|kwh|mwh|gwh|m|g)$", re.I)  # 112km, 3.5GB
_LATIN_PARTICLE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9+#.\-]*?)([가-힣]{1,3})$")  # HBM4와, KT가

STOPWORDS = frozenset("""
AI 인공지능 에이아이 기업 기업들 정부 국내 글로벌 세계 시장 업계 산업 서비스 사업 기술 플랫폼 솔루션 시스템
출시 개최 발표 진행 추진 확대 강화 지원 협력 체결 협약 업무협약 MOU 선정 공개 도입 구축 완료 개발 운영 제공
나서 나선 본격 돌입 착수 성료 마련 계획 예정 전망 우려 기대 가능 필요 위해 대한 통해 관련 이번 올해 내년 지난해
오늘 내일 어제 최근 현재 연속 최대 최초 최고 사상 역대 신규 신규 첫 새 등 및 또 더 수 것 중 위 앞 뒤 속
대표 회장 사장 부회장 장관 위원장 의원 원장 총장 교수 기자 단독 종합 속보 포토 인사 부고 영상 사진 인터뷰 일문일답
1위 2위 3위 억원 조원 만원 달러 퍼센트 전년 대비 분기 상반기 하반기 연간 개월 하루 이틀 주간 오전 오후
한국 국민 고객 이용자 소비자 시민 사람 모두 함께 우리 다시 이제 계속 가장 크게 대폭 대규모
시대 기반 대상 성과 규모 전국 해외 국내외 넘어 대한민국 만에 성장 확보 현장 맞춤형 업무 수상 디지털 서울
활용 적용 위한 통한 대응 개선 혁신 미래 국가 지역 전략 경쟁력 분야 생태계 차세대 본격화 강조 논의 점검 실시
추가 역량 공유 속도 공급 경쟁 돌파 표창 신고가 MAI부동산 앞둬 연다 공략 박차 속도전 주목
삼성 LG SK 현대 CJ 롯데 한화 신세계 포스코 두산 GS HD 그룹 계열사 자회사 주식 증시 기념 행사 캠페인 이벤트 혜택
""".split())


def _tokens(title: str) -> list[str]:
    out = []
    for t in _SPLIT.split(title):
        t = _EDGE.sub("", t)
        if t:
            out.append(t)
    return out


def _strip_multi(t: str) -> str:
    for p in _MULTI:
        if t.endswith(p) and len(t) - len(p) >= 2 and _HANGUL.search(t[-len(p) - 1]):
            return t[: -len(p)]
    return t


def build_vocab(titles: list[str]) -> Counter:
    """조사를 뗄지 판단할 때 쓰는 '단독으로 쓰인 단어' 빈도."""
    return Counter(_strip_multi(t) for title in titles for t in _tokens(title))


def normalize_token(t: str, vocab: Counter) -> str:
    m = _LATIN_PARTICLE.match(t)
    if m and _HAS_LATIN.search(m.group(1)) and m.group(2) in (*_MULTI, *_SINGLE):
        return m.group(1)
    t = _strip_multi(t)
    if len(t) >= 3 and t.endswith(_SINGLE) and _HANGUL.search(t[-2]):
        base = t[:-1]
        if vocab.get(base, 0) >= 1 and vocab.get(base, 0) >= vocab.get(t, 0) / 2:
            return base
    return t


def _keep(t: str, banned: set[str]) -> bool:
    if len(t) < 2 or t in STOPWORDS or t.lower() in banned:
        return False
    if _HAS_DIGIT.search(t) and not _HAS_LATIN.search(t):
        return False  # 3만대, 2026년, 54명
    if _UNIT.match(t) and t.upper() not in {"5G", "6G", "4G", "3G"}:
        return False  # 112km, 3.5GB (단위가 붙은 숫자)
    return bool(_HANGUL.search(t) or _HAS_LATIN.search(t))


def _norm_tokens(title: str, vocab: Counter) -> list[str]:
    out = []
    for raw in _tokens(title):
        t = normalize_token(raw, vocab)
        if t.isascii():
            t = t.upper() if len(t) <= 5 else t  # 5g → 5G, hbm → HBM (긴 영문은 원래 표기)
        out.append(t)
    return out


def _phrase_ok(t: str) -> bool:
    return len(t) >= 2 and not (_HAS_DIGIT.search(t) and not _HAS_LATIN.search(t))


def build_phrases(token_lists: list[list[str]], min_count: int = 3, ratio: float = 0.6) -> set[tuple[str, str]]:
    """자주 붙어 다니는 두 단어 → 한 구절 ("피지컬 AI", "5G SA", "AI 에이전트", "개인정보 유출").
    두 단어 중 하나라도 등장의 ratio 이상을 이 짝으로 나오고, 둘 다 불용어는 아닐 때."""
    single, pair = Counter(), Counter()
    for ts in token_lists:
        single.update(set(ts))
        pair.update({(a, b) for a, b in zip(ts, ts[1:]) if _phrase_ok(a) and _phrase_ok(b)})
    return {
        (a, b) for (a, b), c in pair.items()
        if c >= min_count and not (a in STOPWORDS and b in STOPWORDS)
        and (c >= ratio * single[a] or c >= ratio * single[b])
    }


def extract(title: str, vocab: Counter, banned: set[str], phrases: set[tuple[str, str]] = frozenset()) -> list[str]:
    ts = _norm_tokens(title, vocab)
    seen: list[str] = []
    i = 0
    while i < len(ts) and len(seen) < MAX_PER_ARTICLE:
        if i + 1 < len(ts) and (ts[i], ts[i + 1]) in phrases:
            t, i = f"{ts[i]} {ts[i + 1]}", i + 2
            ok = not any(p.lower() in banned for p in t.split())
        else:
            t, i = ts[i], i + 1
            ok = _keep(t, banned)
        if ok and t not in seen:
            seen.append(t)
    return seen


def annotate(articles: list[dict], banned_terms: set[str]) -> None:
    """각 기사에 keywords 필드를 붙인다 (제자리 수정). banned_terms: 기업 이름·별칭 등 뺄 말."""
    banned = {b.lower() for b in banned_terms}
    titles = [a["title"] for a in articles]
    vocab = build_vocab(titles)
    phrases = build_phrases([_norm_tokens(t, vocab) for t in titles])
    for a in articles:
        a["keywords"] = extract(a["title"], vocab, banned, phrases)

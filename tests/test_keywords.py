from collections import Counter

from collector.keywords import annotate, build_vocab, extract, normalize_token

TITLES = [
    "LGU+, 전국 시내버스 3만대에 와이파이 7 구축 완료",
    "버스 와이파이 고도화… 와이파이7 전환",
    "Sh수협은행, 해킹으로 고객 54명 개인정보 유출",
    "해킹 피해 은행 5곳으로 늘어",
    "개인정보 유출 사고 잇따라",
    "삼성디스플레이가 마이크로 LED 투자",
    "HBM 수요에 SK하이닉스 실적 개선",
]


def test_particles_stripped_only_when_base_word_exists():
    v = build_vocab(TITLES)
    assert normalize_token("해킹으로", v) == "해킹"  # 여러 글자 조사는 항상
    assert normalize_token("와이파이", v) == "와이파이"  # '이'를 조사로 오인하지 않음
    assert normalize_token("시내버스", v) == "시내버스"


def test_extract_drops_numbers_stopwords_and_companies():
    v = build_vocab(TITLES)
    kw = extract("Sh수협은행, 해킹으로 고객 54명 개인정보 유출", v, {"sh수협은행"})
    assert kw == ["해킹", "개인정보", "유출"]  # 고객=불용어, 54명=숫자, 수협은행=기업 순위에 따로
    assert extract("LGU+, 전국 시내버스 3만대에 와이파이 7 구축 완료", v, set()) == ["LGU+", "시내버스", "와이파이"]  # 전국·구축·완료=불용어


def test_extract_keeps_latin_mixed_and_uppercases_short():
    v = Counter()
    assert extract("hbm4와 5g 투자 확대", v, set()) == ["HBM4", "5G", "투자"]


def test_annotate_sets_keywords_field():
    arts = [{"title": t} for t in TITLES]
    annotate(arts, {"삼성디스플레이", "SK하이닉스"})
    assert all(isinstance(a["keywords"], list) for a in arts)
    assert "삼성디스플레이" not in arts[5]["keywords"] and "LED" in arts[5]["keywords"]


def test_frequent_pairs_become_phrases():
    titles = ["LGU+, 배송로봇에 5G SA 적용", "현대차 로봇에 5G SA 실증", "5G SA 상용화 앞둬",
              "피지컬 AI 시대 연다", "피지컬 AI 로봇 공개", "피지컬 AI 투자 확대"]
    arts = [{"title": t} for t in titles]
    annotate(arts, set())
    assert "5G SA" in arts[0]["keywords"] and "5G" not in arts[0]["keywords"]
    assert arts[3]["keywords"][0] == "피지컬 AI"


def test_numbers_with_units_dropped_but_5g_kept():
    v = Counter({"강풍": 2})
    assert extract("최고 112km 강풍에 5G 기지국 3.5GB 피해", v, set()) == ["강풍", "5G", "기지국", "피해"]

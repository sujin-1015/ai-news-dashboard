import random

from collector.cluster import assign, normalize_title


def art(i, title, source, published="2026-11-26T10:00:00+09:00", summary="", companies=()):
    return {"id": f"id{i}", "title": title, "source": source, "publishedAt": published,
            "summary": summary, "companies": list(companies)}


def test_normalize_title():
    assert normalize_title("[단독] LGU+, ‘와이파이 7’ 구축 (종합)") == "lgu와이파이7구축"


def test_cross_outlet_same_story_grouped_and_rep_has_longest_summary():
    arts = [
        # 제목 표기가 꽤 다르다(LG유플러스/LGU+) → 같은 기업 매칭이 근거가 되어 묶인다
        art(1, "LG유플러스, 전국 버스 3.3만대 와이파이7 구축 완료", "전자신문", summary="짧은 요약.",
            companies=["LG유플러스"]),
        art(2, "LGU+, 전국 시내버스 3만대에 와이파이 7 구축 완료", "아주경제", "2026-11-26T09:24:00+09:00",
            summary="LG유플러스가 전국 시내버스 3만2857대에 와이파이 7 구축을 완료했다. 긴 요약입니다.",
            companies=["LG유플러스"]),
        art(3, "LG U+, 전국 시내버스 3만2857대에 와이파이7 구축", "디일렉", summary="중간 길이 요약입니다.",
            companies=["LG유플러스"]),
        art(4, "샤오미, 메모리값 급등에 3분기 순이익 68% 급감", "조선비즈"),
    ]
    assert assign(arts) == 1
    assert {a["id"] for a in arts if a.get("cluster")} == {"id1", "id2", "id3"}
    assert all(a["cluster"] == "id2" for a in arts[:3])  # 묶음 id = 가장 먼저 발행된 기사
    assert [a["id"] for a in arts if a.get("clusterRep")] == ["id2"]
    assert "cluster" not in arts[3]


def test_same_title_different_links_grouped():
    arts = [art(1, "샤오미, 메모리값 급등에 3분기 순이익 68% 급감", "조선비즈"),
            art(2, "샤오미, 메모리값 급등에 3분기 순이익 68% 급감", "조선일보")]
    assert assign(arts) == 1


def test_weak_title_needs_supporting_signal():
    a = art(1, "삼성디스플레이, 3100억원 투자해 ams 오스람 최대주주", "조선비즈", companies=["삼성디스플레이"])
    b = art(2, "삼성D, 2억1000만 유로 투자해 ams 오스람 최대주주 오른다", "아이뉴스24", companies=["삼성디스플레이"])
    assert assign([a, b]) == 1
    c = art(3, "삼성D, 2억1000만 유로 투자해 ams 오스람 최대주주 오른다", "아이뉴스24")
    d = art(4, "LG전자, 유로 투자해 오스람 지분 확보", "전자신문")
    assert assign([c, d]) == 0  # 제목이 조금 비슷할 뿐 근거 없음


def test_daily_series_from_same_source_not_grouped_across_days():
    arts = [art(1, "오늘의 로봇 기업 주식 시세(2026-11-25)", "로봇신문", "2026-11-25T16:00:00+09:00"),
            art(2, "오늘의 로봇 기업 주식 시세(2026-11-26)", "로봇신문", "2026-11-26T16:00:00+09:00")]
    assert assign(arts) == 0


def test_old_story_outside_window_not_grouped():
    arts = [art(1, "원·달러 환율, 0.8원 오른 1343.2원 마감", "뉴시스", "2026-11-20T16:00:00+09:00"),
            art(2, "원·달러 환율, 0.8원 오른 1343.2원 마감", "동아일보", "2026-11-26T16:00:00+09:00")]
    assert assign(arts) == 0


def test_reassign_clears_stale_fields():
    arts = [art(1, "같은 제목 기사입니다", "A"), art(2, "같은 제목 기사입니다", "B")]
    assign(arts)
    arts[1]["title"] = "완전히 다른 내용의 기사"
    assert assign(arts) == 0
    assert not any("cluster" in a or "clusterRep" in a for a in arts)


def test_different_subject_companies_not_chained():
    # 같은 행사에 선정된 서로 다른 회사들의 보도자료는 한 묶음이 아니다
    arts = [art(1, "그라비티 네오싸이언, 2026년 대한민국 일·생활 균형 우수기업 선정", "아이뉴스24"),
            art(2, "삼보컴퓨터, 2026년 대한민국 일·생활 균형 우수기업 선정", "전자신문"),
            art(3, "캐논코리아, '2026년 일·생활 균형 우수 기업' 선정", "전자신문")]
    assert assign(arts) == 0


def _filler(n=40):
    """IDF가 의미를 갖도록 서로 관계없는 기사들 (무작위 음절로 만든 제목·요약, 시드 고정)."""
    rnd = random.Random(7)
    word = lambda: "".join(chr(0xAC00 + rnd.randrange(11172)) for _ in range(rnd.randint(2, 4)))
    return [art(100 + k, " ".join(word() for _ in range(5)), f"매체{k}",
                summary=" ".join(word() for _ in range(12)) + "다.")
            for k in range(n)]


def test_different_titles_same_content_grouped():
    # 제목은 거의 겹치지 않지만 본문 핵심 내용(기업·숫자·고유명사)이 같은 다른 매체 기사
    a = art(1, "로봇 배달원 ‘달이’, 끊김없이 움직인다", "헤럴드경제",
            summary="LG유플러스가 현대차·기아 로보틱스랩 배송로봇 달이에 5G 단독모드를 적용해 통신 품질을 검증했다. "
                    "로봇은 의왕 연구소 일대 2킬로미터 구간을 주행했다.", companies=["LG유플러스"])
    b = art(2, "LG U+, 현대차기아 배송로봇에 5G SA 실증", "디일렉",
            summary="LG유플러스가 현대차·기아 로보틱스랩 배송로봇 달이에 5G 단독모드를 적용해 통신 품질을 검증했다. "
                    "로봇은 의왕 연구소 일대 2킬로미터 구간을 주행하며 영상을 실시간 전송했다.", companies=["LG유플러스"])
    arts = [a, b] + _filler()
    assign(arts)
    assert a.get("cluster") and a["cluster"] == b.get("cluster")


def test_similar_content_same_source_not_grouped_by_content():
    # 같은 매체의 양식 기사(공시·속보)는 내용 비교로 묶지 않는다
    a = art(1, "핌스 배성일 전무 보유 변동", "디지털투데이",
            summary="핌스 임원 배성일 전무가 소유 주식 수량 16만1266주 감소를 공시했다. 소유 지분율 변동 내역이다.")
    b = art(2, "핌스 김영주 사장 보유 변동", "디지털투데이",
            summary="핌스 임원 김영주 사장이 소유 주식 수량 337만주 감소를 공시했다. 소유 지분율 변동 내역이다.")
    arts = [a, b] + _filler()
    assign(arts)
    assert not a.get("cluster")


def test_rare_quoted_name_links_press_release_with_boilerplate_summary():
    # 본문을 못 가져와 요약이 상투문인 보도자료도 드문 고유 이름('내 칩 경진대회')으로 이어진다
    a = art(1, "대학생이 직접 만든 반도체 한자리에…'내 칩 경진대회' 개최", "디지털투데이",
            summary="대학생과 대학원생이 직접 설계하고 제작한 반도체 칩의 기술력을 겨루는 자리가 마련됐다. "
                    "과학기술정보통신부는 27일 대한전자공학회 2026년 추계학술대회에서 전국 대학생·대학원생을 대상으로 "
                    "'제2회 내 칩(My Chip) 경진대회'를 개최했다. 이번 경진대회에는 전국 대학생·대학원생 43개팀(108명)이 참가했다.")
    b = art(2, "대학(원)생이 설계한 반도체, 실제 칩으로 선보여 - 과기정통부, 「제2회 내 칩 경진대회」 개최 -", "과기정통부",
            summary="대학(원)생이 설계한 반도체, 실제 칩으로 선보여 - 과기정통부, 「제2회 내 칩 경진대회」 개최 - "
                    "관련 보도자료 내용입니다. 자세한 내용은 첨부파일을 참고하시기 바랍니다.")
    arts = [a, b] + _filler()
    assign(arts)
    assert a.get("cluster") and a["cluster"] == b.get("cluster")


def test_common_quoted_phrase_does_not_link():
    # 여러 기사에 흔히 나오는 따옴표 말('역대 최대')은 고유 이름이 아니다
    heads = ["해외 카드 결제액", "은행 이자이익", "ESS 입찰 규모", "수출 실적", "게임 동시접속자", "배당 총액", "전시회 참가국"]
    arts = [art(k, f"{h} '역대 최대'", f"매체{k}", summary=f"{h} 관련 발표가 나왔다.")
            for k, h in enumerate(heads)] + _filler()
    assign(arts)
    assert not any(a.get("cluster") for a in arts[:7])


def test_mixed_word_and_char_similarity_groups_differently_summarized_reports():
    # 매체마다 요약에 다른 문장이 뽑혀 낱말 유사도만으로는 기준(0.5)에 못 미치는 같은 사건
    t = "2026-11-28T15:00:00+09:00"
    a = art(1, "인터넷우체국 대규모 해킹…하루치만 11만명 넘어, 추가 피해 확대 우려", "데일리시큐", t,
            summary="국민 생활과 밀접한 국가 우편서비스인 인터넷우체국이 외부 해킹 공격을 받아 등기우편 이용 고객의 개인정보가 대규모로 유출됐다. "
                    "전체 조사 대상 기간 중 하루 분량의 정보를 분석한 결과로, 추가 조사에 따라 피해 규모가 크게 늘어날 가능성이 있다. "
                    "과학기술정보통신부 우정사업본부(본부장 박인환)는 27일 밤 외부 해킹 공격으로 고객 개인정보가 포함된 우편물 접수 정보가 "
                    "유출된 사실을 확인하고 정확한 피해 대상과 규모를 조사하고 있다고 밝혔다.")
    b = art(2, "인터넷우체국 해킹에 11만4000명 개인정보 유출", "보안뉴스", t,
            summary="인터넷우체국 해킹으로 11만명 이상의 고객 개인정보가 유출된 것으로 확인됐다. "
                    "현재 정확한 개인정보 유출 대상과 전체 피해 규모를 파악 중이지만, 하루 분량의 개인정보 유출 고객은 법인을 포함해 "
                    "총 11만3855명으로 확인됐다. 우정사업본부가 해킹에 따른 개인정보 유출 정황을 처음 인지한 시점은 25일 오전 10시30분께다.")
    arts = [a, b] + _filler()
    assign(arts)
    assert a.get("cluster") and a["cluster"] == b.get("cluster")

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

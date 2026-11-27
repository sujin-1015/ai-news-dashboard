import pytest

from collector.summarize import SUMMARY_LIMIT, clean_description, split_sentences, summarize

# 2026-11 실제 RSS 소개글 형태를 줄인 예시
NEWSIS = (
    "[서울=뉴시스] 이정필 기자 = Sh수협은행에서 해킹 공격으로 고객 정보가 유출되는 침해 사고가 발생했다. "
    "최근 인공지능(AI) 등을 이용한 금융권 해킹 사태로 피해가 확인된 은행은 5곳으로 늘었다. "
    "27일 금융권에 따르면 수협은행은 전일 해킹 공격으로 고객 54명의 개인정보가 유출된 사실을 파악하고 금융감독원에 보고했다. "
    "유출된 정보에는 고객의 대출 정보와 신용정보, 주소 등이 포함된 것으로 나타났다. ◎공감언론 뉴시스 roman@newsis.com"
)
AJU = (
    "LG유플러스가 전국 시내버스 3만2857대에 최신 Wi-Fi 7 기반 공공와이파이 구축을 완료했다고 26일 밝혔다. "
    "사진은 행사에서 시내버스 공공와이파이를 소개하는 LG유플러스 직원의 모습. [사진=LG유플러스] "
    "26일 LG유플러스는 이번 망 구축이 한국지능정보사회진흥원(NIA)이 추진하는 사업의 일환으로 진행됐다고 밝혔다. "
    "LG유플러스는 전국 15개 광역지자체와 157개 기초지자체, 434개 운수사의 시내버스 3만2857대에 설치된 기존 와이파이 장비를 최신 와이파이 7 장비로 전면 교체했다. "
    "LG유플러스는 구축 완료를 기념해 이틀간 MBC 문화광장에서 개통식을 개최했다."
)


@pytest.mark.parametrize("raw, start", [
    ("[서울=뉴시스] 이정필 기자 = 수협은행이 밝혔다.", "수협은행이"),
    ("(서울=연합뉴스) 홍길동 기자 = 정부가 발표했다.", "정부가"),
    ("[시사저널e=송준영 기자] LG에너지솔루션이 인사를 했다.", "LG에너지솔루션이"),
    ("[데이터넷] HS효성인포메이션시스템은 공개했다.", "HS효성인포메이션시스템은"),
    ("[정보통신신문=서유덕기자]전국 버스 와이파이가 개선됐다.", "전국"),
])
def test_clean_removes_byline(raw, start):
    assert clean_description(raw).startswith(start)


def test_clean_spaces_glued_sentences_and_drops_tail():
    s = clean_description("확인됐다.한국생산성본부는 공지했다. ◎공감언론 뉴시스 a@b.com")
    assert s == "확인됐다. 한국생산성본부는 공지했다."


def test_split_drops_photo_caption_and_cut_fragment():
    ss = split_sentences(AJU + " 와이파이 7은 기존 대비 대역폭을")  # 매체가 중간에 자른 조각
    assert not any("사진" in s for s in ss)
    assert ss[-1].endswith("개최했다.")


def test_summary_picks_title_related_sentences():
    out = summarize("Sh수협은행, 해킹으로 고객 54명 개인정보 유출", NEWSIS)
    assert out.startswith("Sh수협은행에서 해킹 공격으로")
    assert "54명" in out  # 핵심 숫자가 담긴 문장을 고른다 (리드 2문장이면 빠짐)
    assert "뉴시스" not in out and "@" not in out


def test_summary_skips_caption_and_ceremony():
    out = summarize("LGU+, 전국 시내버스 3만대에 와이파이 7 구축 완료", AJU)
    assert out.startswith("LG유플러스가 전국 시내버스 3만2857대")
    assert "434개 운수사" in out
    assert "사진" not in out and "개통식" not in out


def test_summary_length_and_whole_sentences():
    out = summarize("제목", AJU)
    assert len(out) <= SUMMARY_LIMIT <= 200  # 저작권: 짧은 요약만
    assert out.endswith(".")


def test_summary_fallback_when_no_complete_sentence():
    raw = "삼성디스플레이가 약 2억1000만 유로를 투자해 ams 오스람의 최대주주에 오른다는 내용으로 " * 5
    out = summarize("삼성D ams 오스람", raw)
    assert 0 < len(out) <= SUMMARY_LIMIT and out.endswith("…")


def test_summary_empty():
    assert summarize("제목", "") == ""


def test_split_on_glued_bullet_subhead():
    raw = "정부는 응시 제한을 10년까지 연장할 계획이다.● 스마트기기 부정행위 증가 교육부는 대책을 발표했다."
    assert split_sentences(raw)[0] == "정부는 응시 제한을 10년까지 연장할 계획이다."

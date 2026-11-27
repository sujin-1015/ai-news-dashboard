import pytest

from collector.summarize import clean_description, split_sentences, summarize

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
    assert len(out) <= 200  # 저작권: 짧은 요약만
    assert out.endswith(".") and "…" not in out


def test_summary_empty_when_no_complete_sentence():
    # 매체가 문장 중간에서 잘라 보낸 글은 '…'로 끊어 보여주지 않고 요약을 비운다
    raw = "삼성디스플레이가 약 2억1000만 유로를 투자해 ams 오스람의 최대주주에 오른다는 내용으로 " * 5
    assert summarize("삼성D ams 오스람", raw) == ""


def test_summary_never_cuts_a_sentence():
    long_first = "가" * 210 + "했다."  # 200자를 넘는 문장은 쓰지 않는다
    raw = f"{long_first} 삼성전자가 새 반도체 공장을 짓는다고 밝혔다. 투자 규모는 10조원이다."
    out = summarize("삼성전자 반도체 공장", raw)
    assert out == "삼성전자가 새 반도체 공장을 짓는다고 밝혔다. 투자 규모는 10조원이다."
    assert "…" not in out


def test_summary_empty():
    assert summarize("제목", "") == ""


def test_split_on_glued_bullet_subhead():
    raw = "정부는 응시 제한을 10년까지 연장할 계획이다.● 스마트기기 부정행위 증가 교육부는 대책을 발표했다."
    assert split_sentences(raw)[0] == "정부는 응시 제한을 10년까지 연장할 계획이다."


def test_publisher_cut_with_dots_is_not_a_sentence():
    raw = "이중근 부영회장이 우정학원의 신임 이사장으로 취임했다. 부영그룹은 우정학원이 지난 2.."
    assert split_sentences(raw) == ["이중근 부영회장이 우정학원의 신임 이사장으로 취임했다."]


def test_sentence_with_glued_subhead_is_avoided():
    raw = ("출시 첫날 5조 매출… 신작 게임 열풍13년 만에 돌아온 신작 게임이 출시 첫날 매출 5조 원을 기록했다. "
           "미국 록스타게임스가 19일 출시한 신작 게임에 전 세계가 들썩이고 있다.")
    out = summarize("신작 게임 출시 첫날 5조 매출", raw, max_sentences=1)
    assert "…" not in out


# ── 본문 요약 ───────────────────────────────────────────────────────
from collector.summarize import BODY_LIMIT, Idf, content_tokens, summarize_body  # noqa: E402

BODY = """해외 IP 통해 등기우편물 고객 정보 조회
인터넷우체국이 해외 IP 주소를 이용한 해킹 공격을 받아 고객 개인정보가 유출된 것으로 확인됐다.
과학기술정보통신부 우정사업본부는 외부의 불법적인 해킹 공격으로 우편물 접수 정보가 유출된 사실을 확인했다고 27일 밝혔다.
해커들은 모바일 인터넷우체국의 수취인 배달장소 변경서비스에 접근한 것으로 파악됐다.
현재까지 분석이 이뤄진 하루 분량의 개인정보 유출 고객은 총 11만3855명으로 확인됐다.
우정사업본부는 해당 서비스를 중단하고 해외 IP 접속을 전면 차단했다.
우정사업본부는 경찰에 수사를 의뢰하고 2차 피해 예방에 나섰다.
김지웅 기자 abc@example.com"""
IDF = Idf(["인터넷우체국 해킹 개인정보 유출", "삼성전자 반도체 투자", "정부 예산 심사", "LG유플러스 5G 실증"] * 5)


def test_content_tokens_strip_particles():
    assert content_tokens("우정사업본부는 경찰에 수사를 의뢰했다") == ["우정사업본부", "경찰", "수사", "의뢰했다"]


def test_body_summary_picks_from_whole_article():
    out = summarize_body("인터넷우체국 해킹…하루치만 11.3만명 개인정보 유출", BODY, IDF)
    assert 0 < len(out) <= BODY_LIMIT and out.endswith(".")
    assert "해외 IP 통해" not in out  # 마침표 없는 소제목 줄은 빠진다
    assert "@" not in out
    picked = [s for s in BODY.splitlines() if s in out]
    assert 2 <= len(picked) <= 3


def test_body_summary_empty_when_too_few_sentences():
    assert summarize_body("제목", "한 문장뿐인 본문이다.\n두 번째 문장이다.", IDF) == ""

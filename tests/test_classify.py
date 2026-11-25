from pathlib import Path

import pytest

from collector.classify import OTHER, Classifier
from collector.config import load_all

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def clf():
    cfg = load_all(ROOT / "config")
    return Classifier(cfg["taxonomy"], cfg["companies"])


def test_multi_label_with_reasons(clf):
    c = clf.classify("KB국민은행, 생성형 AI 상담 도입…개인정보 유출 방지 강화", "")
    assert "KB금융" in c.companies
    assert "금융" in c.industries
    assert {"생성형AI", "해킹·유출"} <= set(c.topics)
    assert "KB국민은행" in c.why["기업:KB금융"]


def test_ascii_boundary(clf):
    assert "KB금융" not in clf.classify("KBS 9시 뉴스", "").companies  # KBS ≠ KB
    assert "오픈AI" in clf.classify("OpenAI, 새 모델 공개", "").companies


def test_excludes_prevent_false_positive(clf):
    assert "메타" not in clf.classify("메타버스 플랫폼 확대", "").companies
    assert "애플" not in clf.classify("애플리케이션 보안 점검", "").companies
    assert "토스" not in clf.classify("토스트 프랜차이즈 창업", "").companies
    c = clf.classify("카카오뱅크 3분기 실적", "")
    assert "카카오뱅크" in c.companies and "카카오" not in c.companies


def test_gov_feed_is_public(clf):
    c = clf.classify("2027년 달라지는 제도 안내", "", feed_kind="gov")
    assert c.industries == ["공공"]


def test_other_when_nothing_matches(clf):
    c = clf.classify("오늘의 날씨", "")
    assert c.industries == [OTHER] and c.topics == [] and c.companies == []


def test_topic_fallback_industry(clf):
    c = clf.classify("AI가 바꾸는 일하는 방식", "")
    assert c.industries == ["IT"] and "주제:AI 일반(기본값)" in c.why["업종:IT"]
    c = clf.classify("KB국민은행 AI 상담", "")
    assert "IT" not in c.industries  # 업종이 이미 있으면 fallback 안 함

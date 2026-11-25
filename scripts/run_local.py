"""로컬 수집·분류 실행 + 리포트.

    python scripts/run_local.py            # 실제 RSS 수집 → frontend/data/articles.json + out/report.md
    python scripts/run_local.py --offline  # 마지막 수집 결과를 재분류만 (사전 수정 후 빠른 확인)

AWS 없이 동작한다 (collector 순수 모듈만 사용).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector import snapshot  # noqa: E402
from collector.classify import OTHER, Classifier  # noqa: E402
from collector.config import load_all  # noqa: E402
from collector.parse import KST  # noqa: E402
from collector.pipeline import collect  # noqa: E402

OUT_JSON = ROOT / "frontend" / "data" / "articles.json"
REPORT = ROOT / "out" / "report.md"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="기존 articles.json을 재분류만")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    cfg = load_all(ROOT / "config")
    clf = Classifier(cfg["taxonomy"], cfg["companies"])
    now = datetime.now(KST)

    if args.offline:
        snap = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        kind = {f["id"]: f.get("kind", "media") for f in cfg["feeds"]["feeds"]}
        arts = snap["articles"]
        for a in arts:
            c = clf.classify(a["title"], a["summary"], kind.get(a["feedId"], "media"))
            a.update(industries=c.industries, topics=c.topics, companies=c.companies, why=c.why)
        statuses = snap["feeds"]
    else:
        res = collect(cfg["feeds"]["feeds"], clf, now, max_age_days=args.days)
        arts = snapshot.merge([], res.articles, now, args.days)
        statuses = res.statuses
        print(f"피드 성공 {sum(s.ok for s in statuses)}/{len(statuses)}, 기사 {len(arts)}건 "
              f"(기간 밖 {res.skipped_old}건 제외)")
        for s in statuses:
            if not s.ok:
                print(f"  ✗ {s.id}: {s.error}")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(snapshot.build(arts, statuses, now, args.days, clf.topic_groups), ensure_ascii=False),
        encoding="utf-8",
    )
    write_report(arts, statuses)
    print(f"→ {OUT_JSON.relative_to(ROOT)} ({OUT_JSON.stat().st_size // 1024} KB)")
    print(f"→ {REPORT.relative_to(ROOT)}")


def write_report(arts: list[dict], statuses: list) -> None:
    n = len(arts) or 1
    ind = Counter(i for a in arts for i in a["industries"])
    top = Counter(t for a in arts for t in a["topics"])
    com = Counter(c for a in arts for c in a["companies"])
    src = Counter(a["source"] for a in arts)
    no_ind = [a for a in arts if a["industries"] == [OTHER]]
    none = [a for a in no_ind if not a["topics"]]
    guessed = sum(1 for a in arts if a.get("dateGuessed"))

    L = [f"# 분류 리포트 ({datetime.now(KST):%Y-%m-%d %H:%M} KST)", "",
         f"- 기사 {len(arts)}건, 매체 {len(src)}곳",
         f"- 업종 미분류(기타): {len(no_ind)}건 ({len(no_ind) / n:.0%})",
         f"- 업종·주제 모두 없음: {len(none)}건 ({len(none) / n:.0%})",
         f"- 발행시각 추정(날짜만/없음): {guessed}건", ""]
    L += ["## 업종", *[f"- {k}: {v} ({v / n:.0%})" for k, v in ind.most_common()], ""]
    L += ["## 주제", *[f"- {k}: {v} ({v / n:.0%})" for k, v in top.most_common()], ""]
    L += ["## 기업 Top 30", *[f"- {k}: {v}" for k, v in com.most_common(30)], ""]
    L += ["## 매체별 기사 수", *[f"- {k}: {v}" for k, v in src.most_common()], ""]

    # 오분류 의심: 근거가 키워드 1개뿐인 분류 중 샘플
    rnd = random.Random(42)
    L += ["## 오분류 의심 샘플 (근거 키워드 1개짜리 무작위)"]
    weak = [(a, k, v) for a in arts for k, v in a["why"].items() if len(v) == 1 and not v[0].startswith(("기업:", "정부"))]
    for a, k, v in rnd.sample(weak, min(25, len(weak))):
        L.append(f"- [{k} ← '{v[0]}'] {a['title']} ({a['source']})")
    L += ["", "## 미분류 샘플 (업종·주제 없음)"]
    for a in rnd.sample(none, min(20, len(none))):
        L.append(f"- {a['title']} ({a['source']})")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

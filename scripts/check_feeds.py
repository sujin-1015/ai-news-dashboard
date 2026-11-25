"""feeds.yaml의 모든 피드를 실제로 받아서 유효한 RSS인지 검증한다.

    python scripts/check_feeds.py            # 전체
    python scripts/check_feeds.py <url>      # 새 후보 주소 1개 검증
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.config import load_all  # noqa: E402
from collector.fetch import fetch_all  # noqa: E402
from collector.parse import KST, clean_text, parse_date, parse_feed  # noqa: E402


def main() -> int:
    if len(sys.argv) > 1:
        feeds = [{"id": "candidate", "name": "후보", "url": u} for u in sys.argv[1:]]
    else:
        feeds = load_all(ROOT / "config")["feeds"]["feeds"]
    now = datetime.now(KST)
    bad = 0
    for r in fetch_all(feeds):
        f = r.feed
        if r.error:
            print(f"✗ {f['id']:<14} {r.error}")
            bad += 1
            continue
        try:
            items = parse_feed(r.body or b"")
        except ValueError as e:
            print(f"✗ {f['id']:<14} {e}")
            bad += 1
            continue
        first = items[0]
        _, guessed = parse_date(first.published, now)
        lens = [len(clean_text(i.summary_raw)) for i in items]
        print(f"✓ {f['id']:<14} {len(items):>3}건  pubDate='{first.published[:31]}'{' (추정)' if guessed else ''}"
              f"  요약 {min(lens)}~{max(lens)}자")
    print(f"\n{len(feeds) - bad}/{len(feeds)} 유효")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

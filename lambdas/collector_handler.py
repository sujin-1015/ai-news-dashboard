"""수집 Lambda 핸들러 (EventBridge Scheduler가 매시간 호출).

AWS 입출력만 담당하고, 수집·분류 로직은 collector 패키지(순수 모듈)를 그대로 쓴다.

흐름
1. S3의 직전 스냅샷(data/articles.json)을 읽어 이미 본 기사 id 집합을 만든다.
   (스냅샷이 없으면 DynamoDB GSI byDay로 최근 N일을 조회해 복구)
2. 피드 병렬 수집 → 신규 기사만 남김
3. 신규 기사만 DynamoDB에 조건부 쓰기 (같은 id가 있으면 건너뜀 = DB 수준 중복 방지)
4. 스냅샷 병합 후 S3에 덮어쓰기. 단, 피드 성공률이 낮으면 덮어쓰지 않는다(빈 화면 방지).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from collector import snapshot
from collector.classify import Classifier
from collector.config import load_all
from collector.parse import KST
from collector.pipeline import collect

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE = os.environ["ARTICLES_TABLE"]
BUCKET = os.environ["SITE_BUCKET"]
SNAPSHOT_KEY = os.environ.get("SNAPSHOT_KEY", "data/articles.json")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "7"))
TTL_DAYS = int(os.environ.get("TTL_DAYS", "180"))
MIN_SUCCESS = float(os.environ.get("MIN_SUCCESS_RATIO", "0.5"))
CONFIG_DIR = Path(__file__).parent / "config"

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(TABLE)
_cfg = load_all(CONFIG_DIR)  # 콜드 스타트 때 한 번만
_clf = Classifier(_cfg["taxonomy"], _cfg["companies"])


def jlog(event: str, **kw) -> None:
    """CloudWatch Logs Insights에서 필터하기 쉬운 구조화 로그."""
    log.info(json.dumps({"event": event, **kw}, ensure_ascii=False, default=str))


def load_previous() -> list[dict] | None:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=SNAPSHOT_KEY)
        return json.loads(obj["Body"].read())["articles"]
    except ClientError as e:
        # ListBucket 권한을 주지 않았으므로 객체가 없으면 404 대신 403(AccessDenied)이 온다
        if e.response["Error"]["Code"] in ("NoSuchKey", "AccessDenied", "404", "403"):
            return None
        raise
    except Exception as e:  # noqa: BLE001 - 깨진 스냅샷도 DB에서 복구
        jlog("snapshot_read_failed", error=str(e))
        return None


def rebuild_from_db(now: datetime) -> list[dict]:
    items: list[dict] = []
    for i in range(WINDOW_DAYS + 1):
        day = (now - timedelta(days=i)).astimezone(KST).date().isoformat()
        kw = {"IndexName": "byDay", "KeyConditionExpression": Key("day").eq(day)}
        while True:
            page = table.query(**kw)
            items += page["Items"]
            if "LastEvaluatedKey" not in page:
                break
            kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    for a in items:
        a.pop("expiresAt", None)
        a["why"] = json.loads(a["why"]) if isinstance(a.get("why"), str) else a.get("why", {})
    jlog("snapshot_rebuilt_from_db", count=len(items))
    return items


def save_new(articles: list[dict], now: datetime) -> int:
    expires = int((now + timedelta(days=TTL_DAYS)).timestamp())
    written = 0
    for a in articles:
        item = {**a, "why": json.dumps(a["why"], ensure_ascii=False), "expiresAt": expires}
        try:
            table.put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
            written += 1
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    return written


def handler(event, context):
    t0 = time.monotonic()
    now = datetime.now(KST)
    previous = load_previous()
    if previous is None:
        previous = rebuild_from_db(now)
    known = {a["id"] for a in previous}

    res = collect(_cfg["feeds"]["feeds"], _clf, now, known_ids=known, max_age_days=WINDOW_DAYS)
    for s in res.statuses:
        if not s.ok:
            jlog("feed_failed", feed=s.id, error=s.error, ms=s.elapsed_ms)
    for err in res.errors[:20]:
        jlog("item_failed", error=err)

    written = save_new(res.articles, now)
    summary = {
        "feeds_ok": sum(s.ok for s in res.statuses),
        "feeds_total": len(res.statuses),
        "new": len(res.articles),
        "written": written,
        "skipped_known": res.skipped_known,
    }

    if res.success_ratio < MIN_SUCCESS:
        jlog("snapshot_skipped_low_success", ratio=round(res.success_ratio, 2), **summary)
        return {"ok": False, **summary}

    merged = snapshot.merge(previous, res.articles, now, WINDOW_DAYS)
    body = snapshot.build(merged, res.statuses, now, WINDOW_DAYS, _clf.topic_groups)
    s3.put_object(
        Bucket=BUCKET,
        Key=SNAPSHOT_KEY,
        Body=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
        CacheControl="public, max-age=300",  # CloudFront 무효화(유료 구간) 대신 5분 캐시
    )
    summary.update(snapshot_count=len(merged), seconds=round(time.monotonic() - t0, 1))
    jlog("collect_done", **summary)
    return {"ok": True, **summary}

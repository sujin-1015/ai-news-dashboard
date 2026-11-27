"""대시보드 전체 리소스 (서버리스, VPC 없음, 상시 켜지는 리소스 없음)."""
from __future__ import annotations

import os
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Fn,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as targets
from aws_cdk import aws_ssm as ssm
from constructs import Construct

RETENTION = {
    1: logs.RetentionDays.ONE_DAY, 3: logs.RetentionDays.THREE_DAYS, 5: logs.RetentionDays.FIVE_DAYS,
    7: logs.RetentionDays.ONE_WEEK, 14: logs.RetentionDays.TWO_WEEKS, 30: logs.RetentionDays.ONE_MONTH,
}


class DashboardStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, assets: dict[str, Path], frontend_dir: Path, **kw) -> None:
        super().__init__(scope, cid, **kw)
        ctx = self.node.try_get_context
        retention = RETENTION[int(ctx("logRetentionDays") or 14)]

        def log_group(name: str) -> logs.LogGroup:
            # 로그 그룹을 직접 만들어 보존 기간을 강제 (기본값은 '영구 보관'이라 비용이 쌓일 수 있음)
            return logs.LogGroup(self, f"{name}Logs", retention=retention, removal_policy=RemovalPolicy.DESTROY)

        # ── 저장소 ─────────────────────────────────────────────
        # 정적 사이트 + 스냅샷. 퍼블릭 접근 차단, CloudFront(OAC)만 읽을 수 있다.
        bucket = s3.Bucket(
            self, "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,  # 실습용: cdk destroy 시 함께 삭제
            auto_delete_objects=True,
        )

        # 기사 누적 저장. PK=정규화 link의 해시 → 같은 기사는 항상 같은 키 (조건부 쓰기로 중복 방지)
        articles = ddb.TableV2(
            self, "Articles",
            partition_key=ddb.Attribute(name="id", type=ddb.AttributeType.STRING),
            billing=ddb.Billing.on_demand(),
            time_to_live_attribute="expiresAt",
            global_secondary_indexes=[
                # "최근 N일 기사" 조회용 (스냅샷 복구). Scan 없이 날짜별 Query.
                ddb.GlobalSecondaryIndexPropsV2(
                    index_name="byDay",
                    partition_key=ddb.Attribute(name="day", type=ddb.AttributeType.STRING),
                    sort_key=ddb.Attribute(name="publishedAt", type=ddb.AttributeType.STRING),
                )
            ],
            removal_policy=RemovalPolicy.DESTROY,
        )

        # 챗봇 일일 호출 카운터 (pk="chat#YYYY-MM-DD", TTL 7일)
        usage = ddb.TableV2(
            self, "Usage",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            billing=ddb.Billing.on_demand(),
            time_to_live_attribute="expiresAt",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── 접속 보호: CloudFront Function Basic 인증 ───────────
        # 비밀번호 해시는 KeyValueStore에만 둔다 (코드/저장소/템플릿에 없음). 설정: scripts/set-site-password.ps1
        auth_kvs = cf.KeyValueStore(self, "SiteAuthStore", comment="sha256(user:password) for site basic auth")
        js = (Path(__file__).resolve().parents[1] / "cloudfront" / "basic_auth.js").read_text(encoding="utf-8")
        before, after = js.split("__KVS_ID__")
        auth_fn = cf.Function(
            self, "SiteAuthFn",
            comment="Basic auth for the dashboard",
            runtime=cf.FunctionRuntime.JS_2_0,
            key_value_store=auth_kvs,
            code=cf.FunctionCode.from_inline(Fn.join("", [before, auth_kvs.key_value_store_id, after])),
        )

        # ── 화면: CloudFront ───────────────────────────────────
        distribution = cf.Distribution(
            self, "Cdn",
            default_root_object="index.html",
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,  # gzip/brotli 압축 포함
                response_headers_policy=cf.ResponseHeadersPolicy.SECURITY_HEADERS,
                compress=True,
                function_associations=[cf.FunctionAssociation(
                    event_type=cf.FunctionEventType.VIEWER_REQUEST, function=auth_fn,
                )],
            ),
            price_class=cf.PriceClass.PRICE_CLASS_200,  # 한국 엣지 포함, 남미·호주 제외
            http_version=cf.HttpVersion.HTTP2_AND_3,
        )
        site_origin = f"https://{distribution.distribution_domain_name}"

        # ── 수집: Lambda + EventBridge Scheduler ────────────────
        collector = lambda_.Function(
            self, "Collector",
            description="RSS 수집·분류 후 DynamoDB 저장 + S3 스냅샷 갱신",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,  # x86보다 GB-초 단가가 약 20% 저렴
            handler="collector_handler.handler",
            code=lambda_.Code.from_asset(str(assets["collector"])),
            memory_size=512,
            # 새 기사 본문 수집(사이트별 1초 간격, 최대 BODY_BUDGET_SECONDS) + 분류·묶기 → 여유 있게 5분
            timeout=Duration.minutes(5),
            log_group=log_group("Collector"),
            environment={
                "ARTICLES_TABLE": articles.table_name,
                "SITE_BUCKET": bucket.bucket_name,
                "SNAPSHOT_KEY": "data/articles.json",
                "WINDOW_DAYS": str(ctx("windowDays") or 7),
                "TTL_DAYS": str(ctx("articleTtlDays") or 180),
                "FETCH_BODIES": "1",  # 새 기사는 본문을 받아 본문 전체에서 요약 (본문은 저장 안 함)
                "BODY_BUDGET_SECONDS": "150",
            },
        )
        # 최소 권한: 필요한 동작만
        collector.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem", "dynamodb:Query"],
            resources=[articles.table_arn, f"{articles.table_arn}/index/byDay"],
        ))
        collector.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject"],
            resources=[bucket.arn_for_objects("data/articles.json")],
        ))

        every = int(ctx("collectEveryMinutes") or 60)
        scheduler.Schedule(
            self, "CollectSchedule",
            description=f"{every}분마다 RSS 수집",
            schedule=scheduler.ScheduleExpression.rate(Duration.minutes(every)),
            target=targets.LambdaInvoke(collector, retry_attempts=0),  # 다음 회차가 곧 오므로 재시도 안 함
        )

        # ── 챗봇: HTTP API → Lambda → Gemini ───────────────────
        key_param_name = ctx("geminiKeyParam") or "/ai-news/gemini-api-key"
        model_param = ssm.StringParameter(
            self, "GeminiModel",
            parameter_name="/ai-news/gemini-model",
            string_value=ctx("geminiModel") or "gemini-3.5-flash-lite",
            description="Gemini model ID used by the chat Lambda (not hardcoded)",
        )
        chat = lambda_.Function(
            self, "Chat",
            description="챗봇 프록시: 입력 검증·일일 상한 후 Gemini 호출",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(assets["chat"])),
            memory_size=256,
            timeout=Duration.seconds(30),
            log_group=log_group("Chat"),
            environment={
                "ALLOWED_ORIGINS": site_origin,
                "USAGE_TABLE": usage.table_name,
                "DAILY_LIMIT": str(ctx("chatDailyLimit") or 200),
                "GEMINI_KEY_PARAM": key_param_name,
                "GEMINI_MODEL_PARAM": model_param.parameter_name,
                "LLM_PROVIDER": "gemini",
                # 예상 비용 표시용 단가(USD/100만 토큰). 무료 티어는 0
                "PRICE_INPUT_PER_M": str(ctx("llmPriceInputPerM") or 0),
                "PRICE_OUTPUT_PER_M": str(ctx("llmPriceOutputPerM") or 0),
            },
        )
        chat.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem", "dynamodb:GetItem"], resources=[usage.table_arn],
        ))
        chat.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                model_param.parameter_arn,
                # SecureString은 CloudFormation으로 만들 수 없어 CLI로 직접 생성 (README 참고)
                self.format_arn(service="ssm", resource="parameter", resource_name=key_param_name.lstrip("/")),
            ],
        ))
        # aws/ssm(AWS 관리형 KMS 키)은 키 정책이 SSM 경유 복호화를 허용하므로 kms 권한 추가 불필요

        api = apigw.HttpApi(
            self, "ChatApi",
            description="학습 도우미 챗봇 API",
            create_default_stage=False,
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=[site_origin],  # 내 CloudFront 도메인만
                allow_methods=[apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.GET],
                allow_headers=["content-type"],
                max_age=Duration.hours(1),
            ),
        )
        apigw.HttpStage(
            self, "ChatApiStage",
            http_api=api,
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(  # 남용 방지: 초당 요청 수 상한
                rate_limit=int(ctx("chatRateLimitPerSec") or 1),
                burst_limit=int(ctx("chatBurstLimit") or 5),
            ),
        )
        api.add_routes(
            path="/chat",
            methods=[apigw.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration("ChatIntegration", chat),
        )
        api.add_routes(  # 오늘 챗봇 사용량(횟수·토큰·예상 비용) 조회
            path="/usage",
            methods=[apigw.HttpMethod.GET],
            integration=integrations.HttpLambdaIntegration("UsageIntegration", chat),
        )
        chat_url = f"{api.api_endpoint}/chat"

        # ── 프론트 배포 (+ 런타임 설정 config.json 주입) ────────
        s3deploy.BucketDeployment(
            self, "DeploySite",
            sources=[
                s3deploy.Source.asset(str(frontend_dir), exclude=["data", "data/*", "config.json"]),
                s3deploy.Source.json_data("config.json", {"chatApiUrl": chat_url}),
            ],
            destination_bucket=bucket,
            exclude=["data/*"],  # 수집 Lambda가 쓰는 스냅샷은 지우지 않는다
            prune=True,
            distribution=distribution,
            distribution_paths=["/*"],  # 무효화 1건 (월 1,000건 무료)
            cache_control=[s3deploy.CacheControl.from_string("public, max-age=300")],
            memory_limit=256,
            log_group=log_group("DeploySite"),
        )

        # ── 예산 알림 ───────────────────────────────────────────
        # 이메일은 공개 저장소에 커밋하지 않도록 환경변수(ALERT_EMAIL, CI에서는 GitHub Secret)로도 받는다
        email = ctx("alertEmail") or os.environ.get("ALERT_EMAIL")
        if email:
            for limit in ctx("budgetLimitsUsd") or [1, 5]:
                budgets.CfnBudget(
                    self, f"Budget{limit}Usd",
                    budget=budgets.CfnBudget.BudgetDataProperty(
                        budget_name=f"ai-news-monthly-{limit}usd",
                        budget_type="COST",
                        time_unit="MONTHLY",
                        budget_limit=budgets.CfnBudget.SpendProperty(amount=limit, unit="USD"),
                    ),
                    notifications_with_subscribers=[
                        budgets.CfnBudget.NotificationWithSubscribersProperty(
                            notification=budgets.CfnBudget.NotificationProperty(
                                notification_type=ntype, comparison_operator="GREATER_THAN",
                                threshold=pct, threshold_type="PERCENTAGE",
                            ),
                            subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=email)],
                        )
                        for ntype, pct in (("ACTUAL", 80), ("FORECASTED", 100))
                    ],
                )

        CfnOutput(self, "SiteUrl", value=site_origin)
        CfnOutput(self, "ChatApiUrl", value=chat_url)
        CfnOutput(self, "CollectorFunctionName", value=collector.function_name)
        CfnOutput(self, "SiteBucketName", value=bucket.bucket_name)
        CfnOutput(self, "SiteAuthStoreArn", value=auth_kvs.key_value_store_arn)

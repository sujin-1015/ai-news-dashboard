#!/usr/bin/env python3
"""CDK 앱 진입점.

    cd infra
    npx cdk diff     # 변경 미리보기
    npx cdk deploy --all

설정값은 cdk.json의 "context"에서 바꾼다 (alertEmail, githubRepo, geminiModel 등).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import aws_cdk as cdk

ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(__file__).resolve().parent / "build"
sys.path.insert(0, str(ROOT))

from collector.config import yaml_to_json  # noqa: E402
from stacks.dashboard_stack import DashboardStack  # noqa: E402
from stacks.github_oidc_stack import GithubOidcStack  # noqa: E402


def build_lambda_assets() -> dict[str, Path]:
    """Docker 없이 Lambda 패키지 폴더를 만든다. (외부 의존성이 없어서 복사만 하면 된다)

    collector: collector 패키지 + 핸들러 + 설정(YAML→JSON)
    chat:      chat 핸들러 + llm 패키지
    """
    if BUILD.exists():
        shutil.rmtree(BUILD)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    col = BUILD / "collector"
    shutil.copytree(ROOT / "collector", col / "collector", ignore=ignore)
    shutil.copy2(ROOT / "lambdas" / "collector_handler.py", col)
    yaml_to_json(ROOT / "config", col / "config")
    chat = BUILD / "chat"
    shutil.copytree(ROOT / "lambdas" / "chat", chat, ignore=ignore)
    return {"collector": col, "chat": chat}


app = cdk.App()
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=app.node.try_get_context("region") or "ap-northeast-2",
)
assets = build_lambda_assets()

DashboardStack(app, "AiNewsDashboard", env=env, assets=assets, frontend_dir=ROOT / "frontend")

repo = app.node.try_get_context("githubRepo")
if repo:
    GithubOidcStack(app, "AiNewsGithubOidc", env=env, repo=repo,
                    branch=app.node.try_get_context("githubBranch") or "main",
                    sub_prefix=app.node.try_get_context("githubSubPrefix") or None)

cdk.Tags.of(app).add("project", "ai-news-dashboard")
app.synth()

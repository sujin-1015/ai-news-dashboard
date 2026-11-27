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
import subprocess
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
    """Docker 없이 Lambda 패키지 폴더를 만든다.

    collector: collector 패키지 + 핸들러 + 설정(YAML→JSON) + 본문 추출 라이브러리(trafilatura)
               → Lambda와 같은 arm64·Python 3.13용 휠을 pip로 받아 넣는다 (컴파일·Docker 불필요)
    chat:      chat 핸들러 + llm 패키지 (외부 의존성 없음, 복사만)
    """
    if BUILD.exists():
        shutil.rmtree(BUILD)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    col = BUILD / "collector"
    shutil.copytree(ROOT / "collector", col / "collector", ignore=ignore)
    shutil.copy2(ROOT / "lambdas" / "collector_handler.py", col)
    yaml_to_json(ROOT / "config", col / "config")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
         "--target", str(col), "--platform", "manylinux2014_aarch64", "--implementation", "cp",
         "--python-version", "3.13", "--only-binary=:all:",
         "-r", str(ROOT / "lambdas" / "collector-requirements.txt")],
        check=True, env={**os.environ, "PYTHONUTF8": "1"},  # Windows에서 한글 주석이 든 요구사항 파일 읽기
    )
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

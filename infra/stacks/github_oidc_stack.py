"""GitHub Actions → AWS 배포용 OIDC 신뢰 + 최소 권한 역할.

- 저장소에 장기 액세스 키를 넣지 않는다. Actions가 실행될 때마다 GitHub이 발급한 OIDC 토큰으로
  이 역할을 assume 해서 1시간짜리 임시 자격 증명을 받는다.
- 신뢰 조건: 지정한 저장소의 지정한 브랜치(main)에서 실행된 워크플로만.
- 권한: CDK bootstrap이 만든 역할(deploy / file-publishing / lookup)을 assume 하는 것만 허용.
  실제 리소스 변경은 CloudFormation이 cdk 실행 역할로 수행한다.
- 이 스택은 사람이 로컬에서 한 번만 배포한다 (Actions가 자기 권한을 바꾸지 못하게).
"""
from __future__ import annotations

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"


class GithubOidcStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, repo: str, branch: str = "main", **kw) -> None:
        super().__init__(scope, cid, **kw)
        provider = iam.OidcProviderNative(
            self, "GithubOidc",
            url=GITHUB_OIDC_URL,
            client_ids=["sts.amazonaws.com"],
        )
        role = iam.Role(
            self, "DeployRole",
            role_name="ai-news-github-deploy",
            description=f"Deploy-only role for GitHub Actions ({repo}@{branch})",  # IAM 설명은 ASCII만 허용
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider.oidc_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": f"repo:{repo}:ref:refs/heads/{branch}",
                    }
                },
            ),
        )
        role.add_to_policy(iam.PolicyStatement(
            sid="AssumeCdkBootstrapRoles",
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*-role-{self.account}-{self.region}"],
        ))
        CfnOutput(self, "DeployRoleArn", value=role.role_arn)

"""RSS 수집·분류 순수 모듈.

AWS(boto3)에 의존하지 않는다. Lambda 핸들러(lambdas/collector_handler.py)와
로컬 스크립트(scripts/run_local.py)가 같은 코드를 재사용한다.
표준 라이브러리만 사용 → Lambda 패키지에 외부 의존성이 없다.
"""

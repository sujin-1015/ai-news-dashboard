"""설정 파일 로더.

로컬에서는 config/*.yaml을 읽고(PyYAML 필요), Lambda에서는 배포 시점에
YAML을 변환해 둔 *.json을 읽는다(외부 의존성 없음).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

NAMES = ("feeds", "taxonomy", "companies")


def load_one(config_dir: Path, name: str) -> Any:
    json_path = config_dir / f"{name}.json"
    if json_path.exists():
        return json.loads(json_path.read_text(encoding="utf-8"))
    import yaml  # 로컬 전용

    return yaml.safe_load((config_dir / f"{name}.yaml").read_text(encoding="utf-8"))


def load_all(config_dir: str | Path) -> dict[str, Any]:
    d = Path(config_dir)
    return {n: load_one(d, n) for n in NAMES}


def yaml_to_json(src_dir: str | Path, dst_dir: str | Path) -> None:
    """배포 패키지용: YAML → JSON 변환 (CDK synth 시점에 실행)."""
    import yaml

    src, dst = Path(src_dir), Path(dst_dir)
    dst.mkdir(parents=True, exist_ok=True)
    for n in NAMES:
        data = yaml.safe_load((src / f"{n}.yaml").read_text(encoding="utf-8"))
        (dst / f"{n}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

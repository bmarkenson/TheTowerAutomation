from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_compatibility_wrapper_preserves_repository_interpreter(tmp_path):
    output = tmp_path / "farm_t18.strategy.yaml"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "strategy" / "build_strategy.py"),
            str(ROOT / "config" / "strategies" / "farm_t18.source.yaml"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    generated = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert generated["meta"]["name"] == "farm_t18"
    assert generated["run_configuration"]["tier"] == 18

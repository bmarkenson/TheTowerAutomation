from pathlib import Path
import subprocess
import sys

import yaml

from tools.strategy_builders.lib import build_strategy_yaml


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
    assert generated["session_preflight"]["requirements"][
        "perk_first_choice"
    ] == "perk_wave_requirement"
    assert generated["runtime_policy"]["player_save_preflight"] == "save_first"


def test_first_perk_choice_changes_independently_of_auto_pick_order():
    source = yaml.safe_load(
        (
            ROOT / "config" / "strategies" / "farm_t18.source.yaml"
        ).read_text(encoding="utf-8")
    )
    source["setup"] = {
        "settings": {
            "perk_first_choice": "damage",
        }
    }

    generated = build_strategy_yaml(source)
    requirements = generated["session_preflight"]["requirements"]

    assert requirements["perk_first_choice"] == "damage"
    assert requirements["perk_auto_pick_order"] == list(
        yaml.safe_load(
            (ROOT / "config/run_profiles/farm.yaml").read_text(
                encoding="utf-8"
            )
        )["invariants"]["perk_auto_pick_order"]
    )


def test_runtime_policy_supports_force_ui_and_comparison_audit_modes():
    source = yaml.safe_load(
        (
            ROOT / "config" / "strategies" / "farm_t18.source.yaml"
        ).read_text(encoding="utf-8")
    )
    source["runtime_policy"] = {"player_save_preflight": "force_ui"}
    forced = build_strategy_yaml(source)
    source["runtime_policy"] = {
        "player_save_preflight": "comparison_audit"
    }
    audited = build_strategy_yaml(source)

    assert forced["runtime_policy"]["player_save_preflight"] == "force_ui"
    assert (
        audited["runtime_policy"]["player_save_preflight"]
        == "comparison_audit"
    )


def test_tournament_does_not_invent_a_first_perk_requirement():
    source = yaml.safe_load(
        (
            ROOT / "config" / "strategies" / "tournament.source.yaml"
        ).read_text(encoding="utf-8")
    )

    generated = build_strategy_yaml(source)

    assert "perk_first_choice" not in generated["session_preflight"][
        "requirements"
    ]

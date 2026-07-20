from pathlib import Path

import yaml

from core.free_upgrade_locks import FARM_FREE_UPGRADE_LOCKS
from tools.strategy_builders.lib import build_strategy_yaml


ROOT = Path(__file__).resolve().parents[1]


def _build(name: str):
    source = yaml.safe_load(
        (
            ROOT / "config" / "strategies" / f"{name}.source.yaml"
        ).read_text(encoding="utf-8")
    )
    return build_strategy_yaml(source)


def test_every_farm_profile_inherits_free_upgrade_lock_gate():
    for name in ("farm_t18", "farm_t19_experiment"):
        plan = _build(name)
        requirements = plan["session_preflight"]["requirements"]
        configuration = plan["run_configuration"]

        assert requirements["free_upgrade_locks"] == list(
            FARM_FREE_UPGRADE_LOCKS
        )
        assert configuration["settings"]["free_upgrade_locks"] == list(
            FARM_FREE_UPGRADE_LOCKS
        )

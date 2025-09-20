#!/usr/bin/env python3
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch


def test_find_upgrade_cli_success(capsys):
    from tools.cli import find_upgrade as cli

    stub_result = SimpleNamespace(
        menu="attack",
        column="left",
        index=3,
        label="Damage",
        box=SimpleNamespace(
            rect=(1, 2, 3, 4),
            text="Damage",
            affordability="affordable",
            toggles=None,
        ),
        buy_quantity="x10",
        purchase_attempted=True,
        purchase_sent=True,
        purchase_reason="tapped_cost_panel",
    )

    with patch("tools.cli.find_upgrade.find_upgrade", return_value=stub_result):
        rc = cli.main(["Damage", "--menu", "attack"])

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["label"] == "Damage"
    assert payload["purchase"]["sent"] is True


def test_find_upgrade_cli_not_found(capsys):
    from tools.cli import find_upgrade as cli

    with patch("tools.cli.find_upgrade.find_upgrade", return_value=None):
        rc = cli.main(["MissingUpgrade"])

    assert rc == 1
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["result"] is None
    assert payload["error"] == "upgrade not found"

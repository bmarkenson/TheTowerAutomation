#!/usr/bin/env python3
from __future__ import annotations

import json
from unittest.mock import patch


def test_set_buy_quantity_cli_success(capsys):
    from tools.cli import set_buy_quantity as cli

    with patch("tools.cli.set_buy_quantity.apply_menu_buy_quantities", return_value={"attack": "x10"}):
        with patch("core.ss_capture.capture_adb_screenshot", return_value=None):
            rc = cli.main(["--menu-quantity", "attack=x10"])

    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["menu_quantities"]["attack"] == "x10"


def test_set_buy_quantity_cli_failure(capsys):
    from tools.cli import set_buy_quantity as cli

    with patch(
        "tools.cli.set_buy_quantity.apply_menu_buy_quantities",
        side_effect=RuntimeError("boom"),
    ), patch("core.ss_capture.capture_adb_screenshot", return_value=None):
        rc = cli.main(["--menu-quantity", "utility=max"])

    assert rc == 2
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["error"] == "boom"

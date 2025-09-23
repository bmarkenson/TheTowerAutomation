"""Cash-accumulation friendly upgrade buying strategy."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List

from automation.strategies.base import BaseStrategy
from utils.logger import log_mission
from core.upgrade_buy_quantity import BuyQuantity
from core.upgrade_navigation import apply_menu_buy_quantities, find_upgrade


@dataclass(frozen=True)
class _UpgradeTarget:
    menu: str
    label: str
    display: str


class BlenderStrategy(BaseStrategy):
    """Sequentially purchases a curated upgrade list with cooldowns between buys."""

    name = "blender"

    _WAIT_SECONDS = 20.0
    _MENU_QUANTITIES: Dict[str, BuyQuantity] = {
        "attack": "max",
        "defense": "max",
        "utility": "max",
    }

    _ORDER: List[_UpgradeTarget] = [
        _UpgradeTarget("utility", "Free Attack Upgrade", "Free Attack"),
        _UpgradeTarget("utility", "Free Defense Upgrade", "Free Defense"),
        _UpgradeTarget("utility", "Free Utility Upgrade", "Free Utility"),
        _UpgradeTarget("attack", "Attack Speed", "Attack Speed"),
        _UpgradeTarget("attack", "Range", "Range"),
        _UpgradeTarget("attack", "Damage Per Meter", "Damage Per Meter"),
        _UpgradeTarget("attack", "Multishot Chance", "Multishot Chance"),
        _UpgradeTarget("attack", "Rapid Fire Chance", "Rapid Fire Chance"),
        _UpgradeTarget("attack", "Bounce Shot Chance", "Bounce Shot Chance"),
        _UpgradeTarget("defense", "Knockback Chance", "Knockback Chance"),
        _UpgradeTarget("defense", "Orb Speed", "Orb Speed"),
        _UpgradeTarget("defense", "Orbs", "Orbs"),
        _UpgradeTarget("defense", "Land Mine Chance", "Land Mine Chance"),
        _UpgradeTarget("defense", "Land Mine Radius", "Land Mine Radius"),
        _UpgradeTarget("defense", "Land Mine Damage", "Land Mine Damage"),
        _UpgradeTarget("defense", "Health", "Health"),
        _UpgradeTarget("defense", "Wall Health", "Wall Health"),
        _UpgradeTarget("utility", "Enemy Attack Level Skip", "EALS"),
        _UpgradeTarget("utility", "Enemy Health Level Skip", "EHLS"),
        _UpgradeTarget("attack", "Super Crit Mult", "Super Crit Mult"),
        _UpgradeTarget("attack", "Rend Armor Chance", "Rend Armor Chance"),
        _UpgradeTarget("attack", "Rend Armor Mult", "Rend Armor Mult"),
        _UpgradeTarget("attack", "Damage", "Damage"),
        _UpgradeTarget("defense", "Orbs", "Orbs (final)"),
    ]

    # Context state keys
    _CTX_KEY = "blender_strategy"

    def _initial_state(self) -> Dict[str, object]:
        return {
            "current_index": 0,
            "last_attempt_ts": 0.0,
            "last_quantity_attempt_ts": 0.0,
            "quantities_initialized": False,
            "cycle_count": 0,
            "completed": False,
            "maxed_flags": {target.label.lower(): False for target in self._ORDER},
        }

    def on_start(self, ctx) -> None:
        ctx.data.setdefault(self._CTX_KEY, self._initial_state())

    def on_run_start(self, ctx) -> None:
        if self._CTX_KEY not in ctx.data:
            ctx.data[self._CTX_KEY] = self._initial_state()

    def tick(self, ctx, screen, detection):  # noqa: D401
        state = ctx.data.setdefault(
            self._CTX_KEY,
            self._initial_state(),
        )

        if state.get("completed"):
            return []

        now = time.time()
        maxed_flags: Dict[str, bool] = state.setdefault(
            "maxed_flags",
            {target.label.lower(): False for target in self._ORDER},
        )

        if not state.get("quantities_initialized"):
            last_q_attempt = state.get("last_quantity_attempt_ts", 0.0)
            if now - last_q_attempt < self._WAIT_SECONDS:
                return []
            try:
                apply_menu_buy_quantities(self._MENU_QUANTITIES)
                state["quantities_initialized"] = True
                log_mission("[BLENDER] Set buy quantities to MAX for attack/defense/utility.", "INFO")
            except Exception as exc:
                log_mission(f"[BLENDER] Failed to set buy quantities: {exc}", "WARN")
                state["last_quantity_attempt_ts"] = now
            return []

        idx = int(state.get("current_index", 0))
        original_idx = idx
        while idx < len(self._ORDER):
            label_key = self._ORDER[idx].label.lower()
            if not maxed_flags.get(label_key):
                break
            log_mission(
                f"[BLENDER] '{self._ORDER[idx].display}' already maxed — skipping lookup.",
                "DEBUG",
            )
            idx += 1
        skipped_maxed = idx != original_idx
        if skipped_maxed:
            state["current_index"] = idx
        if idx >= len(self._ORDER):
            state["cycle_count"] = int(state.get("cycle_count", 0)) + 1
            if maxed_flags and all(maxed_flags.values()):
                log_mission("[BLENDER] All tracked upgrades are maxed. Strategy complete.", "INFO")
                state["completed"] = True
                return []
            log_mission(
                f"[BLENDER] Completed upgrade cycle #{state['cycle_count']}. Restarting sequence.",
                "INFO",
            )
            state["current_index"] = 0
            state["last_attempt_ts"] = now
            return []

        last_attempt = float(state.get("last_attempt_ts", 0.0))
        if not skipped_maxed and now - last_attempt < self._WAIT_SECONDS:
            return []

        target = self._ORDER[idx]
        log_mission(
            f"[BLENDER] Attempting '{target.display}' in {target.menu.title()} menu...",
            "INFO",
        )
        try:
            result = find_upgrade(
                target.menu,
                target.label,
                attempt_purchase=True,
                menu_buy_quantities=self._MENU_QUANTITIES,
            )
        except Exception as exc:
            log_mission(f"[BLENDER] Failed to process upgrade '{target.display}': {exc}", "WARN")
            state["last_attempt_ts"] = now
            return []

        state["last_attempt_ts"] = now

        label_key = target.label.lower()

        if result is None or result.box is None:
            log_mission(
                f"[BLENDER] Upgrade '{target.display}' not found; skipping to next.",
                "WARN",
            )
            state["current_index"] = idx + 1
            maxed_flags[label_key] = False
            return []

        affordability = (result.box.affordability or "unknown").lower()
        reason = (result.purchase_reason or affordability).lower()

        if result.purchase_sent:
            log_mission(f"[BLENDER] Purchased '{target.display}'.", "ACTION")
            state["current_index"] = idx + 1
            maxed_flags[label_key] = False
            return []

        if affordability == "maxed" or "status=maxed" in reason:
            log_mission(f"[BLENDER] '{target.display}' already maxed; moving on.", "INFO")
            state["current_index"] = idx + 1
            maxed_flags[label_key] = True
            return []

        if affordability == "unaffordable" or "status=unaffordable" in reason:
            log_mission(
                f"[BLENDER] '{target.display}' unaffordable; will retry after cooldown.",
                "INFO",
            )
            maxed_flags[label_key] = False
            return []

        log_mission(
            f"[BLENDER] Purchase attempt for '{target.display}' did not send (reason={reason}); retrying later.",
            "WARN",
        )
        maxed_flags[label_key] = False
        return []

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, MutableSet

from automation.missions.base import MissionContext


Action = Dict[str, Any]


class BaseStrategy:
    """Common interface for runtime strategies layered on top of missions."""

    name = "base"

    def __init__(self) -> None:
        self.skip_upgrades: MutableSet[str] = set()

    def on_start(self, ctx: MissionContext) -> None:
        """Called once when the strategy is initialised."""

    def on_run_start(self, ctx: MissionContext) -> None:
        """Called when the automation loop transitions into RUNNING."""

        self.skip_upgrades.clear()

    def tick(self, ctx: MissionContext, screen, detection: Dict[str, Any]) -> List[Action]:
        """Return executor actions for the current frame. Default: no-op."""

        return []

    def requires_run_initialization(self) -> bool:
        """Whether this strategy owns an exclusive new-run initialization phase."""

        return False

    def is_run_initialization_complete(self, ctx: MissionContext) -> bool:
        """Return whether normal automation actions may resume for this run."""

        return True

    def requires_session_preflight(self) -> bool:
        """Whether this strategy owns a once-per-process preflight gate."""

        return False

    def is_session_preflight_complete(self, ctx: MissionContext) -> bool:
        """Return whether this process has verified its session requirements."""

        return True

    def session_preflight_requirements(self) -> Mapping[str, Any]:
        """Return persistent no-battle requirements, if this strategy has any."""

        return {}

    def session_preflight_gate_fallbacks(self) -> Mapping[str, Any]:
        """Return configured operator fallback choices keyed by requirement."""

        return {}

    def session_preflight_fingerprint(self) -> str:
        """Identify the exact session-check contract for restart reuse."""

        try:
            encoded = json.dumps(
                self._session_preflight_fingerprint_payload(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return ""
        return hashlib.sha256(encoded).hexdigest()

    def definition_fingerprint(self) -> str:
        """Identify the complete immutable behavior loaded for this instance."""

        try:
            encoded = json.dumps(
                self._definition_fingerprint_payload(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return ""
        return hashlib.sha256(encoded).hexdigest()

    def _definition_fingerprint_payload(self) -> Mapping[str, Any]:
        return {
            "strategy": self.name,
            "class": type(self).__qualname__,
            "session_preflight": self._session_preflight_fingerprint_payload(),
            "run_configuration": self.run_configuration(),
            "runtime_policy": self.runtime_policy(),
        }

    def _session_preflight_fingerprint_payload(self) -> Mapping[str, Any]:
        """Return the stable settings represented by a completion receipt."""

        return {
            "strategy": self.name,
            "requirements": self.session_preflight_requirements(),
            "run_configuration": self.run_configuration(),
            "runtime_policy": self.runtime_policy(),
        }

    def run_configuration(self) -> Mapping[str, Any]:
        """Return the resolved configuration recorded with battle results."""

        return {}

    def runtime_policy(self) -> Mapping[str, Any]:
        """Return optional handler restrictions for this strategy."""

        return {}

    def on_game_over(self, ctx: MissionContext) -> None:
        """Optional hook when GAME_OVER is handled."""

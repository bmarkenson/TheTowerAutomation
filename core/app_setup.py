from __future__ import annotations

"""Utilities for parsing CLI arguments and building the app configuration."""

import argparse
from dataclasses import dataclass
import os
from typing import Optional, Sequence


DEFAULT_AUTO_RETURN_CONF_THRESHOLD = 0.85
DEFAULT_COINS_TOGGLE_COOLDOWN = 15.0
DEFAULT_COINS_CONF_FLOOR = 60.0
DEFAULT_COINS_MAX_JUMP_FACTOR = 8.0
DEFAULT_COINS_JUMP_CONF_FLOOR = 90.0
DEFAULT_ADB_PORT = 5555
ADB_PORT_ENVIRONMENT_VARIABLE = "THETOWER_ADB_PORT"
DEFAULT_STRATEGY = "farm"
STRATEGY_ENVIRONMENT_VARIABLE = "THETOWER_STRATEGY"
DEFAULT_STARTUP_GATE_POLICY = "immediate"
STARTUP_GATE_POLICY_ENVIRONMENT_VARIABLE = "THETOWER_STARTUP_GATES"
STARTUP_GATE_POLICIES = ("immediate", "next_run")
CONFIGURABLE_STRATEGIES = (
    "farm_t18",
    "farm_t19_experiment",
    "tournament",
    "none",
)


@dataclass
class AppConfig:
    """Configuration values consumed by the runtime `App`."""
    auto_start_enabled: bool
    status_interval: int
    save_wave_samples: Optional[str]
    save_coin_samples: Optional[str]
    control_file: str
    auto_return_enabled: bool
    auto_return_secs: int
    auto_return_conf_threshold: float
    coins_toggle_cooldown: float
    coins_conf_floor: float
    coins_max_jump_factor: float
    coins_jump_conf_floor: float
    mission_name: str
    strategy_name: str
    mission_config_path: Optional[str]
    strategy_config_path: Optional[str]
    wait_on_start: bool
    match_trace: bool
    fast_game_over: bool
    full_game_over: bool
    mission_log_path: Optional[str]
    adb_port: int
    startup_gate_policy: str


def _adb_port(value: str) -> int:
    """Parse and validate a TCP port for ``--adb-port``."""
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ADB port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("ADB port must be between 1 and 65535")
    return port


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the argument parser that drives the CLI interface."""

    parser = argparse.ArgumentParser(description="Automation runtime controller")
    parser.add_argument(
        "--adb-port",
        type=_adb_port,
        default=os.getenv(
            ADB_PORT_ENVIRONMENT_VARIABLE,
            str(DEFAULT_ADB_PORT),
        ),
        metavar="PORT",
        help=(
            "BlueStacks ADB TCP port "
            f"(default: ${ADB_PORT_ENVIRONMENT_VARIABLE} or {DEFAULT_ADB_PORT})"
        ),
    )
    parser.add_argument("--no-restart", action="store_true", help="Disable auto restart on home screen")
    parser.add_argument("--match-trace", action="store_true", help="Emit per-frame match logs from detector")
    parser.add_argument("--status-interval", type=int, default=60, help="Seconds between status summaries (0=disable)")
    parser.add_argument("--save-wave-samples", default=None,
                        help="Directory to save per-status wave samples: raw frame (and bin winner). Filename encodes wave.")
    parser.add_argument("--save-coin-samples", default=None,
                        help="Directory to save per-status coin samples: raw frame (and bin winner). Filename encodes coins.")
    parser.add_argument("--no-auto-return", action="store_true",
                        help="Disable auto 'Return to Game' press when stuck (default: enabled)")
    parser.add_argument("--auto-return-minutes", type=int, default=15,
                        help="Minutes of continuous visibility before auto 'Return to Game' tap (default: 15)")
    parser.add_argument("--control-file", default="logs/automation_ctl.json",
                        help="Path to JSON control file for pause/resume/mode (default: logs/automation_ctl.json)")
    parser.add_argument("--fast-game-over", action="store_true",
                        help="Explicitly skip structured More Stats capture on GAME_OVER")
    parser.add_argument("--full-game-over", action="store_true",
                        help="Legacy override that forces capture when --fast-game-over is also supplied")
    parser.add_argument(
        "--mission",
        default="none",
        help="Legacy placeholder. Use --mission-config to load YAML missions (default: none)",
    )
    parser.add_argument(
        "--strategy",
        default=os.getenv(STRATEGY_ENVIRONMENT_VARIABLE, DEFAULT_STRATEGY),
        help=(
            "Runtime strategy: farm (Tier 18 default), farm_t18, "
            "farm_t19_experiment, tournament observer, or none. "
            "Legacy gc names remain aliases. "
            "Use none for the regular "
            "handler loop with no strategy actions or startup gates; "
            "--strategy-config overrides this option"
        ),
    )
    parser.add_argument(
        "--startup-gates",
        choices=STARTUP_GATE_POLICIES,
        default=os.getenv(
            STARTUP_GATE_POLICY_ENVIRONMENT_VARIABLE,
            DEFAULT_STARTUP_GATE_POLICY,
        ),
        help=(
            "Startup-gate policy: immediate treats the first observed battle "
            "as a new run; next_run attaches to an existing battle and arms "
            "gates only after its next authoritative run boundary"
        ),
    )
    parser.add_argument("--mission-log", default=None,
                        help="Optional path to write mission/strategy logs (always logs to actions.log as well)")
    parser.add_argument("--mission-config", default=None, help="Path to YAML mission config (overrides --mission)")
    parser.add_argument("--strategy-config", default=None, help="Path to YAML strategy config (reserved)")
    parser.add_argument("--wait-on-start", action="store_true", help="Start with ExecMode=WAIT (pause auto progression)")
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments into an argparse namespace."""

    parser = build_arg_parser()
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> AppConfig:
    """Convert parsed CLI arguments to an `AppConfig` dataclass."""

    auto_return_secs = max(0, int(args.auto_return_minutes)) * 60
    return AppConfig(
        auto_start_enabled=not args.no_restart,
        status_interval=max(0, int(args.status_interval)),
        save_wave_samples=args.save_wave_samples,
        save_coin_samples=args.save_coin_samples,
        control_file=args.control_file,
        auto_return_enabled=not args.no_auto_return,
        auto_return_secs=auto_return_secs,
        auto_return_conf_threshold=DEFAULT_AUTO_RETURN_CONF_THRESHOLD,
        coins_toggle_cooldown=DEFAULT_COINS_TOGGLE_COOLDOWN,
        coins_conf_floor=DEFAULT_COINS_CONF_FLOOR,
        coins_max_jump_factor=DEFAULT_COINS_MAX_JUMP_FACTOR,
        coins_jump_conf_floor=DEFAULT_COINS_JUMP_CONF_FLOOR,
        mission_name=args.mission,
        strategy_name=args.strategy,
        mission_config_path=args.mission_config,
        strategy_config_path=args.strategy_config,
        wait_on_start=bool(args.wait_on_start),
        match_trace=bool(args.match_trace),
        fast_game_over=bool(args.fast_game_over),
        full_game_over=bool(args.full_game_over),
        mission_log_path=args.mission_log,
        adb_port=args.adb_port,
        startup_gate_policy=args.startup_gates,
    )


__all__ = [
    "ADB_PORT_ENVIRONMENT_VARIABLE",
    "AppConfig",
    "CONFIGURABLE_STRATEGIES",
    "DEFAULT_ADB_PORT",
    "DEFAULT_STRATEGY",
    "DEFAULT_STARTUP_GATE_POLICY",
    "STARTUP_GATE_POLICIES",
    "STARTUP_GATE_POLICY_ENVIRONMENT_VARIABLE",
    "STRATEGY_ENVIRONMENT_VARIABLE",
    "build_arg_parser",
    "config_from_args",
    "parse_args",
]

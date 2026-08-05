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
ADB_CONNECTION_OWNER_ENVIRONMENT_VARIABLE = "THETOWER_ADB_CONNECTION_OWNER"
ADB_CONNECTION_OWNERS = ("runtime", "control-surface")
DEFAULT_ADB_CONNECTION_OWNER = "runtime"
DEFAULT_STRATEGY = "farm"
STRATEGY_ENVIRONMENT_VARIABLE = "THETOWER_STRATEGY"
DEFAULT_STARTUP_GATE_POLICY = "auto_validate"
STARTUP_GATE_POLICY_ENVIRONMENT_VARIABLE = "THETOWER_STARTUP_GATES"
PLAYER_SAVE_AUDIT_ENVIRONMENT_VARIABLE = "THETOWER_PLAYER_SAVE_AUDIT"
PLAYER_SAVE_AUDIT_INTERVAL_ENVIRONMENT_VARIABLE = (
    "THETOWER_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS"
)
DEFAULT_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS = 300
MIN_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS = 30
MAX_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS = 3600
STARTUP_GATE_POLICIES = (
    "auto",
    "auto_validate",
    "immediate",
    "next_run",
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
    adb_connection_owner: str
    startup_gate_policy: str
    player_save_audit_enabled: bool
    player_save_audit_interval_seconds: int


def _adb_port(value: str) -> int:
    """Parse and validate a TCP port for ``--adb-port``."""
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ADB port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("ADB port must be between 1 and 65535")
    return port


def _adb_connection_owner(value: str) -> str:
    """Parse the explicit reconnect owner used at the process boundary."""

    owner = str(value).strip().lower()
    if owner not in ADB_CONNECTION_OWNERS:
        raise argparse.ArgumentTypeError(
            "ADB connection owner must be one of: "
            + ", ".join(ADB_CONNECTION_OWNERS)
        )
    return owner


def _player_save_audit_interval(value: str) -> int:
    """Parse the bounded passive player-save audit cadence."""

    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "player-save audit interval must be an integer"
        ) from exc
    if not (
        MIN_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS
        <= seconds
        <= MAX_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS
    ):
        raise argparse.ArgumentTypeError(
            "player-save audit interval must be between "
            f"{MIN_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS} and "
            f"{MAX_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS} seconds"
        )
    return seconds


def _player_save_audit_environment_enabled(value: Optional[str]) -> bool:
    """Parse the explicit managed-environment opt-in without truthy guessing."""

    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(
        f"{PLAYER_SAVE_AUDIT_ENVIRONMENT_VARIABLE} must be one of "
        "1/0, true/false, yes/no, or on/off"
    )


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
    parser.add_argument(
        "--adb-connection-owner",
        type=_adb_connection_owner,
        choices=ADB_CONNECTION_OWNERS,
        default=os.getenv(
            ADB_CONNECTION_OWNER_ENVIRONMENT_VARIABLE,
            DEFAULT_ADB_CONNECTION_OWNER,
        ),
        help=(
            "ADB reconnect owner: runtime for direct launches or control-surface "
            "for the managed service "
            f"(default: ${ADB_CONNECTION_OWNER_ENVIRONMENT_VARIABLE} or "
            f"{DEFAULT_ADB_CONNECTION_OWNER})"
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
            "farm_t19, tournament observer, none, or a published custom profile. "
            "Legacy experiment and gc names remain aliases. "
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
            "Startup-gate policy: auto attaches when fresh evidence first "
            "shows an active/resumable battle and otherwise runs normal gates; "
            "auto_validate also runs safe strategy validation after attachment; "
            "immediate forces first-battle gates; next_run explicitly requests "
            "attachment semantics"
        ),
    )
    audit_group = parser.add_mutually_exclusive_group()
    audit_group.add_argument(
        "--player-save-audit",
        dest="player_save_audit",
        action="store_true",
        default=None,
        help=(
            "Enable the observation-only natural-boundary player-save audit "
            f"(default: disabled; environment: ${PLAYER_SAVE_AUDIT_ENVIRONMENT_VARIABLE})"
        ),
    )
    audit_group.add_argument(
        "--no-player-save-audit",
        dest="player_save_audit",
        action="store_false",
        help="Explicitly disable the player-save audit, including an environment opt-in",
    )
    parser.add_argument(
        "--player-save-audit-interval-seconds",
        type=_player_save_audit_interval,
        default=os.getenv(
            PLAYER_SAVE_AUDIT_INTERVAL_ENVIRONMENT_VARIABLE,
            str(DEFAULT_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS),
        ),
        metavar="SECONDS",
        help=(
            "Stable-read audit cadence in seconds "
            f"({MIN_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS}-"
            f"{MAX_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS}; default: "
            f"${PLAYER_SAVE_AUDIT_INTERVAL_ENVIRONMENT_VARIABLE} or "
            f"{DEFAULT_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS})"
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
    args = parser.parse_args(argv)
    if args.player_save_audit is None:
        try:
            args.player_save_audit = _player_save_audit_environment_enabled(
                os.getenv(PLAYER_SAVE_AUDIT_ENVIRONMENT_VARIABLE)
            )
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
    return args


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
        adb_connection_owner=args.adb_connection_owner,
        startup_gate_policy=args.startup_gates,
        player_save_audit_enabled=bool(args.player_save_audit),
        player_save_audit_interval_seconds=int(
            args.player_save_audit_interval_seconds
        ),
    )


__all__ = [
    "ADB_CONNECTION_OWNER_ENVIRONMENT_VARIABLE",
    "ADB_CONNECTION_OWNERS",
    "ADB_PORT_ENVIRONMENT_VARIABLE",
    "AppConfig",
    "DEFAULT_ADB_CONNECTION_OWNER",
    "DEFAULT_ADB_PORT",
    "DEFAULT_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS",
    "DEFAULT_STRATEGY",
    "DEFAULT_STARTUP_GATE_POLICY",
    "MAX_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS",
    "MIN_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS",
    "PLAYER_SAVE_AUDIT_ENVIRONMENT_VARIABLE",
    "PLAYER_SAVE_AUDIT_INTERVAL_ENVIRONMENT_VARIABLE",
    "STARTUP_GATE_POLICIES",
    "STARTUP_GATE_POLICY_ENVIRONMENT_VARIABLE",
    "STRATEGY_ENVIRONMENT_VARIABLE",
    "build_arg_parser",
    "config_from_args",
    "parse_args",
]

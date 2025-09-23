from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional, Sequence


DEFAULT_COINS_LOG_PATH = "logs/coins_per_min.csv"
DEFAULT_AUTO_RETURN_CONF_THRESHOLD = 0.85
DEFAULT_COINS_TOGGLE_COOLDOWN = 15.0
DEFAULT_COINS_CONF_FLOOR = 60.0
DEFAULT_COINS_MAX_JUMP_FACTOR = 8.0
DEFAULT_COINS_JUMP_CONF_FLOOR = 90.0


@dataclass
class AppConfig:
    auto_start_enabled: bool
    status_interval: int
    reset_wave_hint: bool
    save_wave_samples: Optional[str]
    save_coin_samples: Optional[str]
    coins_log_base: str
    coins_log_enabled: bool
    control_file: str
    auto_resume_enabled: bool
    auto_resume_secs: int
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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-restart", action="store_true", help="Disable auto restart on home screen")
    parser.add_argument("--match-trace", action="store_true", help="Emit per-frame match logs from detector")
    parser.add_argument("--status-interval", type=int, default=60, help="Seconds between status summaries (0=disable)")
    parser.add_argument("--reset-wave-hint", action="store_true",
                        help="Reset the wave OCR monotonic/time-weighted hint at startup")
    parser.add_argument("--save-wave-samples", default=None,
                        help="Directory to save per-status wave samples: raw frame (and bin winner). Filename encodes wave.")
    parser.add_argument("--save-coin-samples", default=None,
                        help="Directory to save per-status coin samples: raw frame (and bin winner). Filename encodes coins.")
    parser.add_argument("--coins-log", default=DEFAULT_COINS_LOG_PATH,
                        help=f"CSV path to append coins/min samples (default: {DEFAULT_COINS_LOG_PATH})")
    parser.add_argument("--no-coins-log", action="store_true",
                        help="Disable coins/min CSV logging")
    parser.add_argument("--no-auto-return", action="store_true",
                        help="Disable auto 'Return to Game' press when stuck (default: enabled)")
    parser.add_argument("--auto-return-minutes", type=int, default=15,
                        help="Minutes of continuous visibility before auto 'Return to Game' tap (default: 15)")
    parser.add_argument("--control-file", default="logs/automation_ctl.json",
                        help="Path to JSON control file for pause/resume/mode (default: logs/automation_ctl.json)")
    parser.add_argument("--no-auto-resume", action="store_true",
                        help="Disable automatic resume from PAUSED after timeout")
    parser.add_argument("--auto-resume-minutes", type=int, default=15,
                        help="Minutes to auto-resume from PAUSED (default: 15)")
    parser.add_argument("--fast-game-over", action="store_true",
                        help="Skip More Stats capture on GAME_OVER (default: enabled when --mission != none)")
    parser.add_argument("--full-game-over", action="store_true",
                        help="Force capture of More Stats on GAME_OVER even when a mission is active")
    parser.add_argument("--mission", default="none", help="Mission to run (none|demon_nuke|nuke|demon_mode)")
    parser.add_argument("--strategy", default="none", help="Run-time strategy (none|blender)")
    parser.add_argument("--mission-log", default=None,
                        help="Optional path to write mission/strategy logs (always logs to actions.log as well)")
    parser.add_argument("--mission-config", default=None, help="Path to YAML mission config (overrides --mission)")
    parser.add_argument("--strategy-config", default=None, help="Path to YAML strategy config (reserved)")
    parser.add_argument("--wait-on-start", action="store_true", help="Start with ExecMode=WAIT (pause auto progression)")
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = build_arg_parser()
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> AppConfig:
    auto_resume_secs = max(0, int(args.auto_resume_minutes)) * 60
    auto_return_secs = max(0, int(args.auto_return_minutes)) * 60
    coins_log_base = args.coins_log or DEFAULT_COINS_LOG_PATH
    return AppConfig(
        auto_start_enabled=not args.no_restart,
        status_interval=max(0, int(args.status_interval)),
        reset_wave_hint=bool(args.reset_wave_hint),
        save_wave_samples=args.save_wave_samples,
        save_coin_samples=args.save_coin_samples,
        coins_log_base=coins_log_base,
        coins_log_enabled=not args.no_coins_log,
        control_file=args.control_file,
        auto_resume_enabled=not args.no_auto_resume,
        auto_resume_secs=auto_resume_secs,
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
    )


__all__ = [
    "AppConfig",
    "DEFAULT_COINS_LOG_PATH",
    "build_arg_parser",
    "config_from_args",
    "parse_args",
]

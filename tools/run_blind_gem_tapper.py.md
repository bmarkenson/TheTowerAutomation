$PROJECT_ROOT/tools/run_blind_gem_tapper.py — Entrypoint
tools.run_blind_gem_tapper.start_blind_gem_tapper(duration=seconds, interval=seconds, blocking=False) — Returns: starts the blind floating gem tapper (foreground when blocking=True, otherwise daemon thread); Side effects: [tap][log][loop]; Errors: None material (inputs ≤0 are logged and aborted; non-reentrant—silently no-op if already active)
tools.run_blind_gem_tapper.main() — Entrypoint: CLI flags --duration, --interval, --blocking

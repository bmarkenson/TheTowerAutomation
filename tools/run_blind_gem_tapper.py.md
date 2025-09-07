tools/run_blind_gem_tapper.py
tools.run_blind_gem_tapper.start_blind_gem_tapper(duration=seconds, interval=seconds, blocking=False) — R: starts the blind floating gem tapper (foreground when blocking=True, otherwise daemon thread); S: [tap][log][loop]; E: None material (inputs ≤0 are logged and aborted; non-reentrant—silently no-op if already active)
tools.run_blind_gem_tapper.main() — Entrypoint: CLI flags --duration, --interval, --blocking

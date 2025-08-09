$PROJECT_ROOT/automation/run_demon_nuke.py — Entrypoint
automation.run_demon_nuke.main() — Returns: None; runs a persistent mission loop that calls handlers.mission_demon_nuke.run_demon_nuke_strategy() each iteration with a 2s delay; Side effects: [log][loop]; Errors: KeyboardInterrupt stops loop; other exceptions logged and retried after 2s

automation/run_demon_nuke.py
automation.run_demon_nuke.main() — R: None; runs a persistent mission loop that calls handlers.mission_demon_nuke.run_demon_nuke_strategy() each iteration with a 2s delay; S: [log][loop]; E: KeyboardInterrupt stops loop; other exceptions logged and retried after 2s

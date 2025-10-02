# TheTower Automation

This repository drives automated gameplay loops for *The Tower: Idle Tower Defense*. Runtime entry point is `main.py`, which wires detection, missions, and strategies.

## Running with YAML strategies

Strategies can now be authored in YAML and loaded at runtime. The Blender upgrade loop has been ported to `config/strategies/blender.strategy.yaml`.

### Editing the Blender plan

The runtime YAML is generated from a compact source file so you don’t have to hand-edit the large rule set.

1. Update `config/strategies/blender.source.yaml` (ordered list of targets, settings).
2. Regenerate the expanded strategy:

   ```
   python tools/strategy_builders/blender.py
   ```

   (Use `--source` / `--output` flags to override paths if needed.)
3. Commit both the source and regenerated `config/strategies/blender.strategy.yaml`.

The builder lives in `tools/strategy_builders/lib.py` if you want to script other plans.

Strategies can optionally declare `settings.ultimate_targets` (array of `{label, toggles}`) to specify which Ultimate Weapons should be enforced when the run starts; each toggle entry may be a string (implying `on`) or an object `{name, state: on|off}`. Individual phases may also include `ultimate_targets` to override the list once their conditions are met.

Example command:

```
python main.py \
  --strategy-config config/strategies/blender.strategy.yaml \
  --mission-log logs/blender_mission.log
```

Use `--mission-config` alongside `--strategy-config` to pair a YAML mission, or omit it to keep existing Python missions. Logs for rule firings and executor actions are written to `logs/actions.log` and mirrored to the optional mission log path.

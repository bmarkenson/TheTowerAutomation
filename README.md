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

Use `--mission-config` alongside `--strategy-config` to pair YAML plans. Legacy `--mission`/`--strategy` names have been removed; the CLI arguments remain only as placeholders to keep older scripts working but must stay `none`. Logs for rule firings and executor actions are written to `logs/actions.log` and mirrored to the optional mission log path.

## Runtime pause control

Pause and resume the running process through its persistent control file:

```bash
.venv/bin/python tools/automation_ctl.py pause
.venv/bin/python tools/automation_ctl.py pause --minutes 15
.venv/bin/python tools/automation_ctl.py status
.venv/bin/python tools/automation_ctl.py resume
```

A plain `pause` is indefinite and survives automation restarts. Pass
`--minutes N` to request a timed pause instead. Its deadline is stored in the
same authoritative control file, so the supervisor persists `RUNNING` before
resuming and cannot race against a stale `PAUSED` directive.

## Development backlog

Current planned work is tracked in [`PENDING_DEVELOPMENT.md`](PENDING_DEVELOPMENT.md).

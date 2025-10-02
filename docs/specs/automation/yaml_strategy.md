# YAML Strategy Schema

YAML strategies share the same basic structure as YAML missions and reuse the
runtime in `automation/strategies/yaml_strategy.py`. A strategy file defines the
state it keeps (`vars`), the rules it evaluates every `tick`, and the
actions it emits for `core/action_executor.py`.

```
meta:
  name: blender
  version: 1
vars:
  stage: 0
  completed: false
per_run_reset:
  - some_flag            # optional; resets bools to false or numbers to 0
rules:
  - name: init_quantities
    when:
      state: RUNNING
      assert: ["!quantities_initialized", "!completed"]
    cooldown_sec: 0      # optional; guards how often the rule can fire
    do:
      - type: upgrade_set_buy_quantities
        attack: max
        defense: max
        utility: max
      - { type: set, var: quantities_initialized, value: true }
```

## Conditions (`when`)

Each rule's `when` block filters when the rule is eligible:

- `state`: Current automation state (e.g. `RUNNING`, `GAME_OVER`).
- `menu`: Exact menu string reported by detection, if needed.
- `overlays_contains` / `overlays_not_contains`: Presence checks on overlays.
- `elapsed_secs`: Numeric or comparison string (\">= 30\", "< 5").
- `floating_visible`: Checks for a floating button by name.
- `upgrade_maxed`: `{ menu, label }` lookup using visible upgrade boxes.
- `assert`: Boolean expressions over strategy variables. Supports:
  - Bare variable name → truthy check (`assert: quantities_initialized`)
  - Negation (`assert: "!completed"`)
  - Equality (`assert: "stage == 3"`)
  - Lists → all expressions must be true (`assert: ["stage == 0", "!completed"]`)

## Actions (`do`)

Rules can emit any mix of `set` mutations and executor actions. `set` mutates
`ctx.data["mission_vars"]`, keeping strategy state alongside missions.
Non-`set` entries are passed to `execute_actions`:

- `upgrade_set_buy_quantities`: `{ attack|defense|utility: max|x100|x10|x5|x1 }`
- `upgrade_purchase`: `{ menu, label, quantity? }` (logs result + reason)
- `ultimate_ensure_state`: Optional `targets` array describing desired UW toggles.
  Each entry supports `label` and `toggles`, where each toggle may be a string (defaults to `on`)
  or an object `{ name, state: on|off }`.
- `sleep`: `{ ms }`, `fire_floating`, `tap_label`, etc. (all executor actions are available)

When a rule queues at least one action it logs via `utils.logger.log_mission`
and respects `cooldown_sec` (seconds between firings, tracked per rule name).
Rules that only mutate variables continue evaluation so later rules can still run
in the same tick.

`upgrade_purchase` also records the last attempt outcome into `mission_vars`:

```
last_upgrade_label          # string label passed to the action
last_upgrade_menu           # menu argument
last_upgrade_sent           # bool indicating a tap was sent
last_upgrade_reason         # text reason from navigation (e.g. status=unaffordable)
last_upgrade_maxed_after    # bool if the follow-up scan saw the upgrade as maxed
last_upgrade_ts             # epoch timestamp for the attempt
maxed_<slug>                # per-upgrade booleans (slugified label) tracking known maxed items
```

Strategies can assert on these variables to branch after a purchase (e.g. move
past an upgrade when it reports `status=unaffordable`).

`<slug>` is derived from the upgrade label by lowercasing and replacing any
non-alphanumeric characters with underscores (e.g. `"Rapid Fire Chance" →
`maxed_rapid_fire_chance`).

See `config/strategies/blender.strategy.yaml` for a concrete example that ports
the Python Blender strategy to YAML.

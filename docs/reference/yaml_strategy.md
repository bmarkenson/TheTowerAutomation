# YAML Strategy Reference

`automation/strategies/yaml_strategy.py` evaluates expanded runtime plans. This
document owns the stable plan shape and authoring route; current Python source,
builders, action executor, and tests remain authoritative for exact APIs.

## Compact source versus runtime plan

Builder-backed strategies have a compact `config/strategies/*.source.yaml`
owner and a tracked generated `*.strategy.yaml` runtime plan. Edit the source,
regenerate through the supported CLI, review the expanded diff, and commit both:

```bash
.venv/bin/python tools/strategy/build_strategy.py \
  config/strategies/farm_t19.source.yaml
```

The source is a required positional argument; `--output` is optional. Do not
hand-edit a generated plan or encode a strategy name in the generic evaluator.
Plans without a compact builder owner, such as the Tournament plan, are edited
directly with proportionate profile and runtime tests.

## Expanded top-level sections

| Section | Purpose |
| --- | --- |
| `meta` | Strategy identity such as `name`, `family`, `tier`, and version. |
| `vars` | Initial strategy variables copied into runtime `mission_vars`. |
| `per_run_reset` | Variables restored from `vars` at each run boundary; an undeclared existing bool becomes `false`, another value becomes `0`. |
| `run_initialization.complete_when` | Assertions that define completion of Home/new-run initialization. |
| `session_preflight.complete_when` | Assertions that define active-session configuration completion. |
| `session_preflight.requirements` | Profile values passed to the generic preflight workflow. |
| `session_preflight.fallbacks` | Requirement-scoped operator choices after a real gate failure. |
| `rules` | Ordered conditions, variable mutations, and executor actions. |
| `runtime_policy` | Handler, attachment, save-preflight, terminal, and exclusive-validation policy consumed by runtime owners. |
| `run_configuration` | Declared profile, Tier, settings, loadout, and gate metadata retained with battle evidence. |

`run_configuration` and `runtime_policy` are passed to their owning runtime
components; their nested contracts are not invented by `YamlStrategy`. Use a
current built plan, `config/run_profiles/`, and the relevant runtime source when
changing those fields.

## Rules and conditions

A rule may contain `name`, `when`, top-level `assert`, `cooldown_sec`,
`gate_phase`, `run_when_attached`, `attached_validation_only`, and `do`.
Evaluation is ordered. Variable-only rules may fall through to later rules in
the same tick; the first rule that queues executor actions stops evaluation.

Supported `when` filters are:

- exact `state` and `menu`;
- `secondary_contains` / `secondary_not_contains`;
- `overlays_contains` / `overlays_not_contains`;
- numeric/comparison `elapsed_secs` and `wave`;
- named `floating_visible` and `{menu, label}` `upgrade_maxed`;
- one assertion or a list of assertions over strategy variables.

Assertions support truthiness, `!name`, and equality such as `stage == 3`.
`cooldown_sec` is tracked by rule name (or rule index when unnamed).

Within `do`, `{type: set, var, value}` mutates `mission_vars`; every other
action is handed to `core/action_executor.py`. Use an existing action contract
rather than documenting or recreating executor functions in a strategy file.

## Startup gates and attachment

Rules participating in startup gates declare `gate_phase` as
`run_initialization` or `session_preflight`. When startup gates are deferred for
an attached battle, ordinary gate rules do not run. Only explicitly declared
`run_when_attached` rules are eligible, and an attached-validation request is
handled through its read-only validation action before other attached work.
`attached_validation_only` prevents that synthetic validation rule from
becoming ordinary run behavior.

Session-preflight requirements, fallbacks, gate rules, and the relevant
attachment policy contribute to the reuse fingerprint. Changing them therefore
invalidates an older completion receipt instead of silently reusing it.

Validate builder changes with at least the focused builder/profile tests and
the repository checkpoint required by `docs/new_thread.md`; live validation is
conditional on remaining device or transition uncertainty.

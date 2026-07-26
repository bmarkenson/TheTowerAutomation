# Codebase Maintenance Audit — 2026-07-26

This is a repository-local maintainability and removal-proposal audit. It does
not make claims about the current automation process, device, battle, or live
screen, and it did not perform ADB or runtime interaction.

The working tree was clean at the audit checkpoint on commit `5f297a1`. The
control-surface and artifact-retention work that was initially in progress
landed as commits `efec703` and `5f297a1` while this audit was running, so the
final validation includes those commits.

## Executive assessment

The codebase is healthier than its recent rate of change might suggest:

- all tracked Python parsed and compiled;
- no static Python import cycles were found;
- every runtime module has a static runtime caller, except intentional
  standalone CLI/tooling entry points;
- the state-definition and recursive clickmap validators pass;
- all 775 collected tests were demonstrated passing, with one localhost-socket
  test requiring host execution because the normal sandbox forbids socket
  creation;
- exact and normalized clone searches did not find broad cross-file
  duplication.

Complexity is concentrated rather than pervasive. The main risks are:

1. no checked-in Python dependency, test, lint, or CI configuration;
2. a few very large orchestration functions and classes;
3. validators that do not yet understand every dynamic configuration source;
4. compatibility code and old repository artifacts whose current ownership is
   unclear;
5. duplicated or stale generated documentation that can be mistaken for
   current guidance.

The safest next work is to strengthen the maintenance checks and remove only
the candidates classified below. Large runtime refactors should follow as
bounded extractions with existing behavior preserved.

## Scope and evidence

### Repository shape

- 200 tracked Python files, 69,431 lines total.
- 35,040 lines under `core/` and 23,131 lines under `test/`.
- 350 tracked PNG files, about 90 MB total.
- 4,866 lines of WPF C#/XAML; `MainWindow.xaml.cs` is 1,657 lines and
  `BattleHistoryWindow.xaml.cs` is 888 lines.
- 225 commits landed from 2026-07-01 through the initial audit snapshot.
  The highest-churn files were `core/app.py`, `config/clickmap.json`,
  `core/gc_no_battle_setup.py`, `test/test_run_initialization.py`,
  `core/battle_stats.py`, and `core/control_surface.py`.

### Automated validation

- `.venv/bin/python -m compileall -q automation core handlers tools utils main.py`
  passed.
- `.venv/bin/python test/validate_state_defs.py` passed.
- `.venv/bin/python test/clickmap_integrity.py --show-orphans` passed with
  279 entries, 214 clickmap-referenced templates, no integrity errors, and 44
  reported orphans.
- `.venv/bin/python -m pytest -q` produced 774 passes and one environment-only
  failure because the sandbox rejected an ephemeral localhost socket. The
  single failed test,
  `test_http_api_requires_token_but_static_gui_does_not`, passed when rerun
  with host socket permission. All 775 collected tests are therefore
  accounted for.

No WPF publish was run during this audit. The Windows client had just changed,
and its repository instructions require the dedicated Linux publish route for
meaningful validation.

## Structural findings

### Complexity is concentrated in orchestration

The following spans and branch-point estimates are static signals, not proof
that behavior is wrong:

| Location | Span | Approximate branch points | Assessment |
| --- | ---: | ---: | --- |
| `core/app.py:App` | 2,818 lines | — | Owns too many distinct workflows |
| `core/app.py:App.run` | 416 lines | 102 | Capture/control/detection scheduling is interleaved |
| `core/app.py:App._handle_primary_states` | 354 lines | 73 | Terminal, Home, reward, and overlay dispatch share one method |
| `core/gc_no_battle_setup.py:run_gc_no_battle_setup` | 726 lines | 41 | A guarded linear Home workflow with many injected collaborators |
| `core/gc_preflight_navigation.py:run_read_only_gc_preflight` | 547 lines | 55 | Navigation, evidence capture, validation, and cleanup share one function |
| `core/action_executor.py:execute_actions` | 443 lines | 135 | Eleven action families use one `if`/`elif` dispatcher |
| `tools/strategy_builders/lib.py:_build_gc_farm_strategy` | 444 lines | 53 | Multiple plan-generation responsibilities are co-located |
| `core/upgrade_navigation.py:ensure_ultimate_state` | 399 lines | 92 | Detection, navigation, enforcement, and recovery are combined |

`App` has three cohesive extraction seams already visible in its method
layout:

1. exclusive-validation ownership and launch coordination
   (`_reconcile_exclusive_validation` through
   `_advance_exclusive_validation_launch`);
2. no-strategy in-battle and post-run inventory;
3. top-level primary-state and overlay dispatch.

The first seam is the best initial extraction. It is large, internally
cohesive, and heavily characterized by the Tournament validation tests.
Preserve thin `App` delegates during extraction so tests and safety boundaries
remain stable.

`ControlDirectiveStore` and `AutomationSupervisor` are also large, but their
methods remain cohesive around durable transitions and runtime synchronization.
They are not first-priority split candidates.

### Duplication is limited

The only exact nontrivial cross-file function clones found were
`_cmp_elapsed` and `_bool_assert` in:

- `automation/missions/yaml_mission.py`
- `automation/strategies/yaml_strategy.py`

Both evaluators claim the same rule schema. Move their shared predicate
semantics to one small evaluator module before extending either schema.

A normalized cross-file function comparison did not find other high-similarity
clones above the audit threshold. Repeated guarded navigation should therefore
be consolidated only when an extraction preserves the distinct authority and
cleanup contracts; similar-looking tap flows are not automatically duplicates.

### Package boundaries are serviceable but blurred

There are no static import cycles, but `core` is both a low-level layer and the
application/orchestration layer:

- `core/app.py` imports every handler and the mission/strategy manager;
- `core/action_executor.py` imports mission context and the ad-gem handler;
- `core/upgrade_navigation.py` and `core/poison_swamp_stun.py` import an upgrade
  detail handler.

Do not perform a wholesale package rename. As workflows are extracted, place
new orchestration above reusable detection/input primitives and stop adding new
`core` → `handlers` dependencies.

### The WPF client needs a later boundary pass

The native client has nearly all interaction logic in two code-behind files.
There are no checked-in C# unit-test projects. After the current
battle-history work settles:

- move API polling and compatibility evaluation behind testable services;
- move activity/battle presentation transforms out of the windows;
- leave WPF event handlers as thin UI adapters;
- add unit coverage for transforms and compatibility decisions before further
  feature growth.

This is a later task, not a reason to interrupt the just-landed work.

## Tooling and reproducibility findings

There is no `pyproject.toml`, requirements file, pytest configuration, linter
configuration, pre-commit configuration, or checked-in CI workflow. The
current `.venv` contains at least OpenCV, NumPy, PyYAML, Pillow, pytesseract,
pytest, pynput, and keyboard, but the repository cannot recreate that
environment from tracked files.

Consequences already visible in the tree:

- `README.md` still shows `python main.py` once and references the nonexistent
  `tools/strategy_builders/blender.py`;
- `tools/gesture_logger.py` and `tools/crop_region.py` launch `python3`
  directly rather than preserving the repository interpreter;
- unused imports and dead compatibility exports have no automated check;
- validators are manual scripts rather than one normal checkpoint.

Recommended bounded task:

1. add a minimal checked-in Python project/dependency declaration and dev
   dependencies;
2. add one `.venv`-anchored checkpoint script for compile, state schema,
   recursive asset/config validation, and pytest;
3. add linting conservatively, fixing existing findings in a separate commit;
4. correct the README commands;
5. decide whether CI is useful for this private/device-oriented repository,
   while keeping live ADB checks out of ordinary CI.

## Asset and configuration audit

### Validator false positives

The clickmap validator reports all 24
`assets/match_templates/modules/ancestral/*.png` files as orphans. They are
active: `config/module_icon_index.json` references every one and
`core/module_icon_index.py` loads that catalog dynamically.

Extend the recursive validator to consume every authoritative asset catalog,
not just `clickmap.json`. This will reduce the current orphan report from 44 to
20 before any removal decision.

### Twenty reviewable asset-removal candidates

No active clickmap or secondary asset catalog references the following:

- coordinate-region snapshots:
  `_shared_match_regions/floating_buttons.png`,
  `_shared_match_regions/floating_gem_region.png`,
  `_shared_match_regions/upgrades_left.png`,
  `_shared_match_regions/upgrades_right.png`,
  `_shared_match_regions/wave_number.png`;
- superseded button templates:
  `buttons/claim_ad_gem:home.png`, `buttons/goto_store.png`,
  `buttons/uw_toggle_to_off.png`, `buttons/uw_toggle_to_on.png`;
- legacy Cards template: `indicators/cards:gc_slot.png`;
- superseded navigation/detail templates:
  `navigation/open_perks.png`, `overlays/uw_detail_popup.png`;
- old floating-gem overlays:
  `overlays/floating_gem.png` and the `-east`, `-north`, `-south`, and `-west`
  variants;
- obsolete or misspelled upgrade templates:
  `upgrades/attack/left.png`,
  `upgrades/attack/right/multi_shot_targets.png`,
  `upgrades/attack/right/super_crit_mult.png`.

Before deletion, add or identify a canonical fixture for each retained
replacement and run the strict validator with the dynamic catalog included.
The audit found no byte-identical PNG duplicates, so removal must be based on
reference and fixture evidence rather than hashes.

### Cards-specific conclusion

The backlog's named Cards entries split into three groups:

- Active:
  `navigation.Cards`, `indicators.cards:farm_slot`, and
  `indicators.cards:tournament_slot` are used by current guarded preflight
  navigation and validation.
- Compatibility-only:
  `Cards:GCFarmEarly`, `Cards:GCFarmLate`, `cards:locked`, and
  `cards:locked:ok` are emitted only by the optional
  `builder: glass_cannon` strategy builder. No bundled source uses that
  builder, and the public `glass_cannon` strategy name now resolves to
  `farm_t18`.
- Unreferenced or dangling:
  `indicators.menu:cards`, `indicators.cards:deck1`, and
  `indicators.cards:deck3` have no exact caller.
  `buttons.cards:locked:cancel` exists only in an executor suspension set and
  has no clickmap entry.

Make one explicit compatibility decision: either document and test custom
`builder: glass_cannon` as a supported external surface, or remove that builder
and its legacy Cards states/templates. Keeping the current half-supported
state costs more than either clear outcome.

### Strategy and mission configuration

Current Farm and Tournament compact sources plus generated plans are
intentional. The explicit generated YAML is part of the repository's runtime
contract and should remain.

The following need an operator-usage decision before removal:

- `gc_farm_t18.*` and `gc_farm_t19_experiment.*` are no longer selected by
  named runtime aliases, but tests still use their legacy source shape;
- `orbdevo*` plans are loadable only through explicit `--strategy-config`;
- `config/missions/{nuke,demon_mode,demon_nuke}.yaml` are loadable only through
  explicit `--mission-config`, have no focused evaluator tests, and use the
  compatibility `restart_run`/Surrender action.

The last group is safety-sensitive. Do not retain it as an undocumented example
and do not delete it without confirming whether the operator still invokes it.

## Unused and compatibility-code proposal

### High-confidence cleanup candidates

These have no repository caller beyond exports, old generated documentation,
or their own definition:

- `NoOpMission` and `NoOpStrategy`;
- `core.label_tapper._normalize_region`;
- the unused import of `ensure_ultimate_toggles_on` in
  `core/action_executor.py`;
- `handlers.dismiss_uw_detail.handle_uw_detail_popup`;
- a small set of ordinary unused imports found by the static audit.

The deprecated `utils/template_matcher.py` shim has only two callers:
`core/floating_button_detector.py` and `core/state_detector.py`. Migrate them to
`core.matcher`, then remove the shim. At that point,
`core.matcher.detect_floating_gem_square` also has no caller and is a removal
candidate.

Public-looking helpers such as `get_clickmap_path`, `reload_clickmap`,
`has_click`, `reload_state_definitions`, `module_icon_similarity`,
`get_coins_from_image`, and the scrcpy pixel sampler are not runtime-used but
may be interactive tooling APIs. Remove them only after checking operator
scripts outside the repository or after an explicit deprecation.

### Repository artifacts

The following tracked top-level files have no runtime caller:

- `.log_meminfo.py.swp`;
- `log` and `log.running` (October 2025 runtime output);
- `T14farm` (an external strategy note);
- `player_id`.

The swap file and logs are high-confidence removals. Move useful strategy
evidence from `T14farm` into a clearly sourced historical note or remove it
after confirming `docs/game_strategy.md` already captures the needed facts.
`player_id` should not be tracked if it is an account identifier; remove it
from the current tree and ignore its replacement. If it is sensitive, remember
that a normal deletion does not purge Git history.

`pack.sh` currently packages broad working-tree content and does not exclude
all top-level artifacts, `tmp/`, or account-local files. Either harden it around
`git ls-files` plus explicit generated assets, or retire it.

The two `.old` source snapshots are superseded by their active files and by Git
history:

- `tools/crop_region.py.old`;
- `tools/scrcpy_adb_input_bridge.py.old`.

They are high-confidence removals after the removal commit records their
replacement paths.

Together, the top-level artifact group and `.old` files account for roughly
954 KB and 10,400 lines/items of non-source clutter.

## Documentation classification

Retain as active or intentional history:

- canonical files named in `docs/documentation_maintenance.md`;
- `docs/modules/completed_tasks_log.md`;
- dated issue and backlog history;
- `docs/template_audit_2026-07-13.md` as an explicitly dated snapshot.

Review for archive/removal:

- 30 `docs/modules/*.py.md` summaries and 33 files under `docs/specs/` describe
  overlapping generated APIs, including modules that no longer exist;
- `docs/prompts/gemini_full_project.txt` is an 8,572-line generated source
  bundle and is already stale;
- the remaining `docs/prompts/` upload/resume machinery predates the current
  `AGENTS.md`/`docs/new_thread.md` workflow;
- `docs/modules/README*.md`, `PROJECT_PRIMER.md`, and `PROJECT_SCOPE.md`
  describe old directories, old handler names, and superseded assistant
  behavior;
- retired plans should be labeled as history or moved to a history location,
  not left beside active technical references.

Do not delete the useful current schema and input-policy references merely
because they live under `docs/modules/`. First separate active references from
generated summaries and historical plans.

The stale prompt bundle, duplicated generated specs, and legacy module
summaries total about 360 KB and 9,100 lines.

## Prioritized execution plan

### 1. Establish a trustworthy maintenance checkpoint

- Add tracked dependency/test/lint configuration.
- Make every checked-in runner preserve `.venv/bin/python`.
- Fold clickmap, state-definition, module-catalog, strategy-reference, and
  dangling runtime-key checks into one checkpoint.
- Correct README commands.

This is the recommended next task because it lowers the risk of every later
removal.

### 2. Remove proven repository debris

- Remove the swap file, old logs, and `.old` files.
- Decide and handle `player_id`, `T14farm`, and `pack.sh`.
- Remove the 20 asset candidates only after fixture-backed strict validation.
- Archive/remove stale generated prompts and API summaries according to
  `docs/documentation_maintenance.md`.

Keep this as one reviewable proposal, but commit independent coherent groups
separately.

### 3. Resolve compatibility surfaces

- Decide support for `builder: glass_cannon` and its legacy Cards controls.
- Decide support for explicit legacy GC profiles, OrbDevo plans, and the three
  Surrendering mission configs.
- Remove the matcher shim and high-confidence unused definitions.
- Add regression tests for every compatibility surface deliberately retained.

### 4. Extract orchestration without changing policy

1. Extract exclusive-validation coordination from `App`.
2. Extract the no-strategy observation/post-run workflow.
3. Replace the primary-state mega-dispatch with explicit state handlers.
4. Replace `execute_actions` branching with a generic action-handler registry.
5. Break Home and live preflight navigation into named ordered steps with a
   shared guarded-navigation context.

Each extraction should preserve current public methods as delegates initially,
retain operator-facing intent logs, and run the full repository-local suite.
Use retained fixtures; live validation is needed only if an extraction leaves
current-state, transition, timing, or device-integration uncertainty.

### 5. Revisit the WPF client after the current feature settles

Add testable presentation/services before further growth in the two large
code-behind files, then validate via the repository's Linux publish script.

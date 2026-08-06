# Startup-Gate Operations

This procedure handles declared per-Strategy run requirements. Runtime
architecture and exact save mappings live in
[`architecture/runtime.md`](../architecture/runtime.md#farm-profiles-and-loadouts)
and [`modules/player_save_import.md`](../modules/player_save_import.md); source
YAML owns mutable profile values.

## Stage or resolve a decision

The native **Configure run...** dialog and CLI stage one-run choices without
starting a run:

```bash
.venv/bin/python tools/automation_ctl.py configure-run
.venv/bin/python tools/automation_ctl.py configure-run skip bots_preset
.venv/bin/python tools/automation_ctl.py configure-run default bots_preset
```

Pause an active runtime before opening or saving configuration. A staged skip
is bound to the selected Strategy, claimed only at an applicable boundary, and
removed when claimed. Changing Strategy clears it.

After three complete Home setup attempts fail with the same requirement, the
runtime publishes its expected/observed evidence and allowed choices. Ordinary
failures return through guarded Home cleanup and retry from a fresh frame;
Pause, interruption, and unsupported requirements do not loop. Resolve the
pending request with the native/browser dialog or:

```bash
.venv/bin/python tools/automation_ctl.py gate
.venv/bin/python tools/automation_ctl.py gate retry
```

`gate` prompts; `gate <choice-id>` is noninteractive. **Decide later** leaves
the gate pending and sends no input. `force-continue` is only a compatibility
alias for the scoped `gate bypass_once`; it cannot create a waiver before a
real failure or skip unrelated checks.

## Authority and save-first behavior

Every choice is requirement-scoped. Retry and waiver recapture current
evidence; accepting one fallback does not skip unrelated Cards, Workshop,
Bots, Modules, Guardian, Perk, Target Priority, Ultimate Weapon, or in-battle
requirements. The selected decision and observed failure remain attached to
the run, and normal validation rearms after the boundary.

The default `save_first` policy may omit redundant Home UI only after one
guarded exact-target serialization/restore workflow yields a complete supported
mapping. Missing, mismatched, unknown, stale, unsupported, or audit-forced
claims use the existing guarded UI route after verified Home restoration;
owner, target, control, foreground, or boundary ambiguity blocks all later
input. A trusted mismatch queues only that requirement's existing verified UI
repair and supplies no mutation authority itself. `force_ui` retains complete
UI behavior; `comparison_audit` compares while UI stays authoritative.

An exhausted active-session Home-only mismatch may offer a profile-owned repair
only under
[`live_action_authority.md`](../live_action_authority.md#configuration-gate-repair).

# Startup-Gate Operations

This procedure handles declared per-Strategy run requirements. Runtime
architecture and exact save mappings live in
[`architecture/runtime.md`](../architecture/runtime.md#farm-profiles-and-loadouts)
and [`architecture/player_save.md`](../architecture/player_save.md); source
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

After bounded Home setup attempts fail, the runtime publishes its
expected/observed evidence as a nonblocking advisory with only scoped choices.
Ordinary failures return through guarded Home cleanup and retry from a fresh
frame; Pause, interruption, and unsupported requirements do not loop. The
failed requirement is then retained as degraded evidence and launch authority
is released. Review the advisory with the native/browser dialog or:

```bash
.venv/bin/python tools/automation_ctl.py gate
.venv/bin/python tools/automation_ctl.py gate retry
```

`gate` prompts; `gate <choice-id>` is noninteractive. **Decide later** leaves
the advisory pending and sends no input, but does not block automation.
`force-continue` is only a compatibility
alias for the scoped `gate bypass_once`; it cannot create a waiver before a
real failure or skip unrelated checks.

An in-battle mismatch or unavailable validator completes the one-shot session
preflight in degraded mode and never opens a blocking direction request. The
runtime retains the human-readable requirement plus expected/observed evidence,
and later successful validation clears that degraded state. If an old blocking
dialog remains visible after status refresh, close it without choosing an
option; current runtimes consume that legacy request automatically.

For Attach, every configuration rule is observational even when its ordinary
in-battle contract can repair a value. A mismatch, missing validator result,
unsupported action, or validator exception completes degraded and releases the
attachment hold. The attached battle is never changed to make its selected
Strategy match.

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
loss of control authority or exact-target ownership is catastrophic and blocks
later input. Ambiguous foreground or boundary evidence authorizes no input for
that check; it is flagged and released unless lifecycle input has already made
source restoration or the input result uncertain. A trusted mismatch queues
only that requirement's existing verified UI repair at a safe Home boundary
and supplies no mutation authority itself. If that repair is unavailable or
exhausts, the runtime records degraded evidence and continues. `force_ui`
retains complete UI behavior; `comparison_audit` compares while UI stays
authoritative.

The persistent save-mapping review banner is not a startup gate. It reports a
durable unmapped-value receipt or local exact-version Module identity
confirmation that still needs canonical review. Leave the banner visible until
the identity is integrated; do not use a startup-gate bypass to dismiss it.
Candidate or local-identity status does not change the current check's slot
allowlist, UI fallback, or repair authority.

An active-session Home-only mismatch cannot create Surrender or restart
authority. It remains degraded until an ordinary safe Home boundary can run the
profile-owned repair.

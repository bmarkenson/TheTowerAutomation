# Tournament Validation Operation

Use this only for a read-only inventory of an already active Tournament.
Complete [`../live_preflight.md`](../live_preflight.md), retain the prior control
state, and read [`../live_action_authority.md`](../live_action_authority.md)
before the validator's guarded navigation.

If the current runtime is safely `RUNNING`, request indefinite Pause and wait
for that same owner to acknowledge it. Then run:

```bash
.venv/bin/python tools/validate_tournament_preflight.py
```

The validator requires persistent `PAUSED` control plus a fresh complete frame
proving `RUNNING/TOURNAMENT`. It reads Cards, Ultimate Weapons, Modules, Bots,
and Guardians from the active battle. Only Workshop may use the guarded Exit
Battle → Go Home route, and the validator must prove Home **Resume Battle** and
return to that same Tournament. It never selects a preset, equips a module,
starts a battle, or Surrenders. A confident observational Module variation may
pass; an enforced-setting mismatch or incomplete identity exits nonzero.

Use `--capture-only --output-dir PATH` to retain the same guarded screens
without evaluation. The output is generated evidence and follows
[`evidence_retention.md`](evidence_retention.md).

When finished, reinspect the owner, target, screen, control, and action log.
Restore the previous state only under the agent-owned Pause cleanup rules; do
not blindly Resume a pre-existing Pause or changed battle.

To continue passive observation after a validated check, the managed
Tournament profile may attach without restarting the battle:

```bash
.venv/bin/python main.py --adb-port PORT --strategy tournament --no-restart
```

It attempts the same read-only session validation once, then limits action
authority to enabled reward collectors and the natural terminal handler. It
does not buy upgrades, repair configuration, Surrender, auto-return Home,
enter a Tournament, or start an ordinary battle. The architecture contract is
[`Tournament exclusive validation and observer profile`](../architecture/runtime.md#tournament-exclusive-validation-and-observer-profile).

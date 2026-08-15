# Linux user services

These units give the control surface one fixed, inspectable process owner and
keep managed ADB registration outside the automation lifecycle. The API can
start or stop only `thetower-automation.service`; it never accepts a PID, unit
name, or shell command from a client.

The checked-in units assume the repository is at
`%h/dev/python/TheTower`. If it is elsewhere, copy the files and adjust
`WorkingDirectory` and `ExecStart` in the copies before installation.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/thetower-automation.service ~/.config/systemd/user/
cp deploy/systemd/thetower-control-surface.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now thetower-control-surface.service
```

When upgrading an existing installation, copy both units, run
`systemctl --user daemon-reload`, and restart the control-surface unit before
the next managed automation start. Replace any active pre-upgrade automation
through the documented production procedure so it cannot retain the former
self-managed reconnect role beside the new service owner.

Do not enable the automation unit unless automatic launch at Linux login is
actually wanted. The Windows client can start it paused or running and can
completely stop it through the already-running control-surface service.

The automation unit reads its managed ADB port, next-start strategy, and
startup-gate policy from `~/.config/thetower/automation-adb.env`. The
control-surface API creates this dedicated file with mode `0600` when the
Windows client changes one of those values. Startup policy and strategy changes
are accepted only while automation is stopped and take effect on the next
managed start. The control-surface service owns bounded registration and
reconnect for that exact localhost target even while automation is stopped.
The automation unit's fixed
`THETOWER_ADB_CONNECTION_OWNER=control-surface` setting makes its runtime
observe-only; the API rejects start if an outdated installed unit does not
advertise that boundary. Direct manual launches retain self-managed reconnects.
When the file is absent, the runtime uses port `5555`, strategy `farm`, and
automatic attached-battle validation. Manual `main.py --adb-port PORT
--strategy NAME --startup-gates auto|auto_validate|immediate|next_run`
arguments continue to override these defaults.

Complete Stop may retain a one-shot exact-battle handoff when fresh runtime
evidence proves automation owns the active battle. The following Start uses
`next_run` only for that launch, restores the normal persisted startup policy,
and completes a fresh save-backed Attach only if the battle identity is
unchanged. This applies to battles automation started and battles it attached
to later; wave progression does not end the handoff.

The unit also reads the separate optional
`~/.config/thetower/player-save-audit.env`. It is intentionally not rewritten
by the control surface. The file can explicitly enable the observation-only
player-save projector. It consumes only bundles already acquired by a forced
lifecycle boundary, a natural terminal boundary, or an explicit Perk
selection/exhaustion checkpoint; it has no cadence or independent acquisition
setting. See
[`docs/operations/player_save_audit.md`](../../docs/operations/player_save_audit.md)
for the exact interface, receipt path, and authority limits. Absence leaves the
projector disabled.

An API bearer token is optional on the loopback-only SSH transport. To require
one, create `~/.config/thetower/control-surface.env` with permissions `0600`:

```text
THETOWER_CONTROL_TOKEN=a-random-secret-of-at-least-24-characters
```

Then restart `thetower-control-surface.service` and enter the same token in the
Windows client. Inspect the units with:

```bash
systemctl --user status thetower-control-surface.service
systemctl --user status thetower-automation.service
journalctl --user -u thetower-control-surface.service -n 100
```

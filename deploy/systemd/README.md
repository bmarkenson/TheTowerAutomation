# Linux user services

These units give the control surface one fixed, inspectable process owner. The
API can start or stop only `thetower-automation.service`; it never accepts a PID,
unit name, or shell command from a client.

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

Do not enable the automation unit unless automatic launch at Linux login is
actually wanted. The Windows client can start it paused or running and can
completely stop it through the already-running control-surface service.

The automation unit reads its managed ADB port, next-start strategy, and
startup-gate policy from `~/.config/thetower/automation-adb.env`. The
control-surface API creates this dedicated file with mode `0600` when the
Windows client changes one of those values. Startup policy and strategy changes
are accepted only while automation is stopped and take effect on the next
managed start. When the file is absent, the runtime uses port `5555`, strategy
`farm`, and immediate gates. Manual `main.py --adb-port PORT --strategy NAME
--startup-gates immediate|next_run` arguments continue to override these
defaults.

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

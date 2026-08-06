# Player-Save Audit Collector Operation

The `V1073-RUNTIME-013` collector is default-disabled and observation-only. It
never sends input, backgrounds the app, navigates, changes lifecycle, attaches
or creates a battle record, publishes Strategy facts, decides UI suppression,
or supplies terminal authority. Its exact mapping and receipt semantics are in
[`modules/player_save_import.md`](../modules/player_save_import.md#versioned-audit-matrix-data-9-game-1073--revision-3).

For a direct launch:

```bash
.venv/bin/python main.py --player-save-audit
.venv/bin/python main.py --player-save-audit \
  --player-save-audit-interval-seconds 600
.venv/bin/python main.py --no-player-save-audit
```

The interval is 30–3600 seconds and defaults to 300. For the managed unit,
enable it only at an authorized restart boundary:

```bash
install -d -m 700 ~/.config/thetower
printf '%s\n' \
  'THETOWER_PLAYER_SAVE_AUDIT=1' \
  'THETOWER_PLAYER_SAVE_AUDIT_INTERVAL_SECONDS=300' \
  > ~/.config/thetower/player-save-audit.env
chmod 600 ~/.config/thetower/player-save-audit.env
systemctl --user restart thetower-automation.service
```

Set `THETOWER_PLAYER_SAVE_AUDIT=0` in that mode-0600 file, or remove the file,
to restore the disabled default on the next authorized restart. CLI switches
override environment values; invalid values fail parsing.

Receipts append to `logs/player_save_audit/receipts-v1.jsonl`. Inspect without
modifying them:

```bash
tail -n 20 logs/player_save_audit/receipts-v1.jsonl | jq .
```

Each process has new runtime/collector session IDs and never restores round
continuity from old JSONL lines. Receipts contain only allowlisted normalized
identity, revision, Perk/tail, timing, and target-fingerprint evidence—never
raw saves, decoded roots, account/profile data, arbitrary history, pixels, OCR,
or exceptions. Unknown mapping, conflict, target-generation change, receipt
failure, or acquisition/decode error leaves normal UI and automation behavior
unchanged. The worker uses the already owned exact target and never manages an
ADB connection.

Runtime logs and receipts are ignored evidence, not durable issue records.
Promote a needed regression fixture or follow the issue-evidence lifecycle in
[`documentation_maintenance.md`](../documentation_maintenance.md).

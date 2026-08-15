# Player-Save Temporal-Audit Campaign

`V1073-RUNTIME-013` is a default-disabled, observation-only diagnostic. Use it
for a short campaign with one named question, the natural boundaries needed to
answer that question, and a finish condition. For example: “Across the next
ordinary Home → run → Game Over sequence, does the normalized Perk prefix only
extend, then clear, while the structural history tail changes once?” Enable it,
let those boundaries occur normally, inspect the receipts, and disable it when
the question is answered.

The receipts have no automated consumer. Runtime does not use them. The
auditor is also not an unknown-field discovery tool; targeted save-mapping and
calibration work gathers its own purpose-specific evidence. This campaign only
compares already-understood normalized claims over time. It consumes bundles
already acquired for a forced attachment/Home check, a natural terminal
boundary, or an explicit Perk selection/exhaustion checkpoint. It has no timer,
cadence, or independent save-read request.

It never sends input, backgrounds the app, navigates, changes lifecycle,
attaches or creates a battle record, publishes Strategy facts, decides Perks
navigation or UI suppression, or supplies terminal authority. It does not
change Game Stats, Perks, More Stats, continuity, or terminal-binding paths. A
terminal-only process remains unbound and cannot inherit Strategy or
process-local evidence.

## Direct launch

The CLI is explicit and defaults off:

```bash
.venv/bin/python main.py --player-save-audit
.venv/bin/python main.py --no-player-save-audit
```

The enable/disable switches override the environment value. There is no audit
interval setting because enabling audit does not schedule acquisitions.

## Managed unit

First complete the live preflight and use a natural, authorized process
replacement boundary. Do not create, end, or alter a battle for a campaign.
The Home `NEW_BATTLE` boundary gives the clearest pre-round baseline; a
mid-round campaign remains valid but must report that its pre-round baseline is
unavailable.

Stop through the control surface before changing the managed environment:

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data '{"action":"stop"}' \
  http://127.0.0.1:8787/api/v1/process | jq .
```

Atomically enable the campaign:

```bash
install -d -m 700 ~/.config/thetower
audit_env_next=$(mktemp ~/.config/thetower/player-save-audit.env.XXXXXX)
printf '%s\n' 'THETOWER_PLAYER_SAVE_AUDIT=1' > "$audit_env_next"
chmod 600 "$audit_env_next"
mv "$audit_env_next" ~/.config/thetower/player-save-audit.env
```

Start through the same control surface; it intentionally starts Paused:

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data '{"action":"start"}' \
  http://127.0.0.1:8787/api/v1/process | jq .
```

Verify a distinct current runtime, exact-target ownership, current Pause and
mode acknowledgements, and fresh screen evidence. Restore the prior authorized
control posture through the control surface only. If the prior posture was
Enabled and fresh evidence still makes that safe, the request is:

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data '{"action":"enable"}' \
  http://127.0.0.1:8787/api/v1/control | jq .
```

To end a campaign, repeat the supported Stop operation, atomically replace the
environment file with only the disabled flag, then use Start and restore the
authorized control posture as above:

```bash
audit_env_next=$(mktemp ~/.config/thetower/player-save-audit.env.XXXXXX)
printf '%s\n' 'THETOWER_PLAYER_SAVE_AUDIT=0' > "$audit_env_next"
chmod 600 "$audit_env_next"
mv "$audit_env_next" ~/.config/thetower/player-save-audit.env
```

Do not use a raw `systemctl restart`; managed process replacement belongs to
the control surface. Removing the environment file also restores the disabled
default on the next supported Start, but the explicit `0` makes intent visible.
Never delete old receipts merely to disable collection.

## Receipts and interpretation

Receipts append to `logs/player_save_audit/receipts-v1.jsonl`. Inspect them
without modifying them:

```bash
tail -n 20 logs/player_save_audit/receipts-v1.jsonl | jq .
```

Each process creates new runtime and collector session IDs and never restores
round continuity from old JSONL lines. The decoder must emit a supported,
shape-valid normalized runtime projection with the expected audit-matrix
capability. The manifest's version names that capability's evidence origin; it
is not a literal game-version lock. Receipts preserve the actual observed
mapping and game version. A merely parseable root is rejected, and a mapping,
version, capability, identity, or progression change inside one collector
session fails closed rather than merging rounds.

The bounded receipt schema keeps only normalized identity, revision, Perk/tail,
timing, target-fingerprint, and allowlisted visual-event metadata. It omits raw
saves, decoded roots, account/profile fields, arbitrary history, pixels, OCR,
and exception text so the log stays compact, reviewable, and decoupled from
decoder internals. This is evidence hygiene for a trusted-single-user project,
not an authentication or adversarial-security boundary.

Acquisition/decode/receipt failures and disabled optional components leave
normal UI and automation behavior unchanged. The App uses shared typed bundles;
the audit worker only projects those bundles and never pulls or decodes a save.
Perk checkpoints may still be requested while globally Paused because they are
read-only. Timing fields are observation bounds, not exact game write or
activation times. Survival checkpoints stay unavailable until
`V1073-RUNTIME-015`/`016` are independently promoted.

Runtime logs and receipts are ignored evidence, not durable issue records.
Promote a needed regression fixture or follow the issue-evidence lifecycle in
[`documentation_maintenance.md`](../documentation_maintenance.md).

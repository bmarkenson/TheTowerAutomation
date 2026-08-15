# Assist Module Assignment Field Mismatch — 2026-08-15

This narrow dated extract supports `ISSUE-2026-048`. It records historical
production behavior and a value-redacted structural inspection; it is not a
claim about current process, device, configuration, or control state.

## Bounded production sequence

The source was production-generated
`/home/brianm/dev/python/TheTower/logs/actions.log`. It was read without writes
on 2026-08-15. A bounded search retained only the save disposition and the
resulting Modules fallback rows.

| Local timestamp (PDT) | Retained record |
| --- | --- |
| 2026-08-14 20:16:51 | The first post-deployment version-1101 Modules check reported `complete=False`, `supported=False`, `ui_required`, and `Assist module assignment field is missing`. |
| 2026-08-15 01:26:24 | The next version-1101 Tournament check reported the same missing-field disposition while the other supported configuration checks completed. |
| 2026-08-15 01:26:27–01:26:35 | The fallback opened Modules, recorded current UI evidence, and returned Home. |
| 2026-08-15 01:26:38 | The observation-only result reported four of eight assignments matched, completed without changes, and recorded `repairs=[]`. |

Tournament declares `modules.mode: observe`; therefore the zero-repair result
was the policy's intended behavior after the erroneous save fallback, not
evidence that the UI repair path malfunctioned.

## Value-redacted shape inspection

After live preflight, an isolated development environment performed one
bounded stable exact-target read through `pull_player_save_bytes`, decoded the
NRBF object in memory, and emitted only sorted member names and value-type
classes for `assistModuleSlots`. The temporary probe and decoded object were
discarded. All four entries had the identical shape:

| Member | Redacted type |
| --- | --- |
| `__class__` | `str` |
| `equippedModule` | `ModuleItem` object |
| `mainEffectEfficiencyLevel` | `int` |
| `substatEfficiencyLevel` | `int` |
| `type` | `int` |
| `uniqueEffectEfficiencyLevel` | `int` |
| `unlocked` | `bool` |

No slot contained a member named `module`. No raw save, source bytes, decoded
object, field value, Module identity, GUID, inventory, account datum, or
screenshot was retained.

At candidate commit `73dbb06`, a second privacy-safe stable read through
`tools/import_player_save.py` published only the normal normalized report. Its
version-1101 Modules evidence was `status=observed`, `complete=true`, and
contained all eight exact assignments. That is development evidence for the
mapping correction; post-deployment ordinary-boundary confirmation remains
required.

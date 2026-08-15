# Tournament Orb Distance save alias — 2026-08-15

This narrow dated extract supports `ISSUE-2026-051`. It records historical
production evidence for an exact versioned player-save mapping; it is not a
statement about current runtime state.

> **Factual correction (2026-08-15):** `rangeLevelSelected=0` is the selected
> Range lab level, not visible Attack Range `98.38m`. Cards/Workshop preset
> names also do not prove effective Range. These pairings remain valid evidence
> for the two Orb raw fields and the contemporaneous UI values, but the original
> Range alias interpretation was insufficient for UI-suppression authority.
> `ISSUE-2026-052` records the corrected calculation and lifecycle boundary.

## Source and extraction

- Sources: production `logs/actions.log` and
  `logs/player_save_mapping_candidates/receipts-v2.jsonl`.
- Windows: the 09:53 PDT owned Tournament validation and the 11:41 PDT
  attachment to an already-running Tournament.
- Read-only extraction: bounded action-log reads plus exact searches for the
  relevant `workshopOrbDistance` values in the candidate receipts. Raw
  saves, decoded objects, account fields, request IDs, process IDs, target
  generations, and private fingerprints were intentionally omitted.

## Retained evidence

| PDT | Retained evidence |
| --- | --- |
| 09:53:15–09:53:52 | A guarded version-1101 save and the subsequent pre-mutation UI fallback paired Tournament Cards, Tourney Workshop, Range 98.38m, Extra 87.16m, and Workshop 80.37m with raw `rangeLevelSelected=0`, `innerOrbDistance=8.71588134765625`, and `workshopOrbDistance=8.036909103393555`. The UI matched and changed nothing. |
| 11:41:40–11:41:49 | Attachment forced a fresh version-1101 save, bound its exact active-round identity, and carried its supported configuration facts into the running battle. |
| 11:42:11 | Damage Slider consumed the bound save fact without opening its UI. |
| 11:42:53–11:43:18 | Orb Distance lacked an exact mapping for the carried raw value, so it opened the UI. The UI again observed 98.38m / 87.16m / 80.37m and changed nothing. |
| 11:43:14 | The second pre-mutation calibration receipt paired the same raw tuple and context with the same visible values. Its snapshot and UI-evidence fingerprints were distinct from the 09:53 observation. |

## Mapping boundary

The existing mapping recognized
`workshopOrbDistance=8.036911010742188` for the same visible 80.37m value. The
two independent version-1101 observations establish
`8.036909103393555` as a second observation within the reviewed one-decimal
center. They do not establish effective Attack Range: that requires the
separate versioned Workshop/current level, selected/researched lab, live Card,
Module, compression, and display calculation. They also do not establish an
Orb conversion formula or a neighboring semantic tuple; unmapped or ambiguous
tuples continue to use UI.

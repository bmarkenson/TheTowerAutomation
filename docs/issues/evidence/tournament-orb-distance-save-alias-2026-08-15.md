# Tournament Orb Distance save alias — 2026-08-15

This narrow dated extract supports `ISSUE-2026-051`. It records historical
production evidence for an exact versioned player-save mapping; it is not a
statement about current runtime state.

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

The existing Tournament authority recognized
`workshopOrbDistance=8.036911010742188` for the same visible 80.37m value. The
two independent version-1101 observations establish
`8.036909103393555` as a second exact alias in the same complete Tournament
tuple. They do not establish a conversion formula, rounding rule, neighboring
value, or numeric tolerance; every unenumerated tuple continues to use UI.

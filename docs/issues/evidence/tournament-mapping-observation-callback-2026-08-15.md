# Tournament Mapping-Observation Callback Mismatch — 2026-08-15

This narrow dated extract supports `ISSUE-2026-049`. It records historical
production behavior and repository analysis; it is not a claim about current
process, device, battle, or control state.

## Bounded production sequence

The source was production-generated
`/home/brianm/dev/python/TheTower/logs/actions.log`. It was read without writes
on 2026-08-15. Only lines 112403–112418 were retained for this issue.

| Local timestamp (PDT) | Retained record |
| --- | --- |
| 01:42:04 | Strategy-aware attachment completed with Tournament and armed observation-only validation. |
| 01:42:04 | The session-preflight rule began checking the active configuration. |
| 01:42:12 | The route dispatched its verified Modules navigation input. |
| 01:42:20 | The route dispatched its verified Return to Game input. |
| 01:42:25 | Validation failed with `validate_tournament_session_preflight_screens() got an unexpected keyword argument 'mapping_observation_fn'`. |
| 01:42:25–01:42:34 | The runtime recorded degraded continuation and its nonblocking advisory. |

The failure occurred after the read-only screens were collected; it does not
establish that any configuration was changed.

## Repository cause

`core/gc_preflight_navigation.py` conditionally adds
`mapping_observation_fn` whenever the injected save coordinator exposes the
existing read-only recorder. `core/gc_preflight.py` accepts that callback and
uses it for complete Modules and Guardian evidence. The Tournament wrapper in
`core/tournament_preflight.py` accepted neither the keyword nor a forwarding
parameter, producing the exact runtime exception before generic evaluation.

The regression calls the Tournament wrapper with the production callback and
retained Tournament screens, then requires a valid result and both `modules`
and `guardian_chips` observations. No live screen or input was used to develop
the repair.

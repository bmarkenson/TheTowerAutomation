# Production Promotion and Rollback

`develop` is the staging branch; production services remain fixed to the
`main` checkout. Promotion is operator- or explicitly assigned integration-
owner work. Complete the repository-change checklist before this procedure and
[`live_preflight.md`](../live_preflight.md) before any service/device action.

## Promote one exact candidate

1. Require clean feature, integration, and production worktrees. Any unrelated
   or unclear production change, staged file, unmerged entry, or unresolved
   nonignored untracked file blocks promotion.
2. Record production commit `M` and validated `develop` commit `D`. Recheck both
   refs and prove that `M` is an ancestor of `D`.
3. Run the complete checkpoint at `D`; review all `M..D` commits and the
   aggregate diff. Resolve remaining uncertainty with retained or live evidence
   as appropriate.
4. Create a unique annotated local tag at `M`, for example
   `production-before-20260804T210500Z-fe3c83b`. Never move or reuse it; pushing
   a tag is a separate operator decision.
5. Select the boundary below, fast-forward the production checkout to exact
   object `D`, and verify `HEAD == main == D`. Abort on a non-fast-forward,
   changed ref, or newly dirty checkout.
6. Apply only separately reviewed non-Git changes, restart affected services,
   and perform a bounded production smoke test. Record the deployed commit and
   result.

| Candidate contents | Production boundary |
| --- | --- |
| Documentation only | Fast-forward without stopping automation. |
| Runtime Python, YAML, templates, or runtime-read assets | Stop automation before update; restart and smoke-test afterward. |
| Control surface or shared modules | Stop/restart the control-surface service; also stop automation when shared runtime code changes. |
| Interpreter or locked dependencies | Stop every affected service and retain the prior environment or a proven rebuild path through smoke validation. |
| Installed unit or persistent-state format | Treat installation/migration as a separately reviewed operation with recovery recorded first. A checked-in unit change does not install itself. |

## Failed smoke test

1. Stop the affected service before changing files or environments again.
2. Reinspect production and the recorded `M`, `D`, tag, and any new operator-
   owned work.
3. Create and review a normal revert commit on `main` for the promoted range,
   or use a smaller fix-forward when it is clearer and equally quick. Do not
   silently move `main` backward.
4. Restore a prior environment, installed unit, or persistent data only when
   that item changed during deployment.
5. Restart and repeat the smoke test, then integrate the rollback or validated
   fix into `develop` before another promotion.

The branch and threat-model rationale is in
[`architecture/development_isolation.md`](../architecture/development_isolation.md#staging-promotion-and-rollback).

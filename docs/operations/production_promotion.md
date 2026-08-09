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
   as appropriate. Classify every publishable Windows-package input in that
   diff; a source checkout update does not publish the native client.
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
| Native Windows package input | Complete the [required Windows package publication](#required-windows-package-publication) after the production checkout reaches `D`. |
| Interpreter or locked dependencies | Stop every affected service and retain the prior environment or a proven rebuild path through smoke validation. |
| Installed unit or persistent-state format | Treat installation/migration as a separately reviewed operation with recovery recorded first. A checked-in unit change does not install itself. |

### Required Windows package publication

Any promotion whose aggregate `M..D` diff changes an input to either published
Windows executable must also publish the complete native Windows package.
Inputs include application source, XAML, project files, resources, and publish
tooling under `windows/TheTower.ControlSurface`, `windows/TheTower.TunnelHost`,
`windows/TheTower.TunnelHost.Core`, and `windows/TheTower.TunnelProtocol`;
documentation-only and test-only changes do not activate this boundary.

After verifying that the production checkout is exactly `D`, run the supported
[`publish-linux.sh`](../../windows/TheTower.ControlSurface/publish-linux.sh) or
Windows `publish.ps1` workflow. It must atomically replace
`windows/TheTower.ControlSurface/publish/win-x64` with a complete
self-contained package containing adjacent, nonempty
`TheTower.ControlSurface.exe` and `TheTower.TunnelHost.exe` files. Do not copy
only one executable, publish from a different commit, or treat portable tests
or an earlier package as satisfying this boundary.

Before reporting the promotion complete, record `D`, the publication time,
size, and SHA-256 digest of both executables. A failed or unverified publication
blocks the production-success claim even when the Linux deployment and smoke
test pass. Cross-publication does not establish WPF runtime behavior; follow
the native client's
[Windows-only lifecycle validation](../../windows/TheTower.ControlSurface/README.md#windows-only-lifecycle-validation)
before describing a package as deployed and validated on Windows.

## Retire feature work

The [repository topology](../architecture/development_isolation.md#repository-and-git-topology)
keeps the `main` and `develop` branches and worktrees permanent; each feature
branch and worktree is temporary. Retirement has separate integrated and
superseded dispositions; never describe patch-equivalent or selectively ported
work as integrated.

Before either disposition:

1. Re-list every local branch and linked worktree. Recheck the candidate's
   branch and `HEAD`, staged and unstaged changes, nonignored untracked files,
   ignored files that could contain operator work or required evidence, and
   active ownership.
2. Record the exact worktree path, local branch, tip commit, disposition, and
   replacement or integration target. Obtain operator approval for those exact
   local objects. Exclude `main`, `develop`, rollback tags, remote branches, and
   every ambiguous item; remote deletion is always a separate decision.

### Integrated feature

Use this disposition only after promotion succeeds and the outcome's required
validation and evidence are durable:

1. Prove the branch tip is an ancestor of `main`. A merged label or patch-
   equivalent cherry-pick does not override uncertainty or the
   `git branch -d` ancestry guard.
2. Run `git worktree remove <exact-path>` and then
   `git branch -d <exact-branch>`. Never recursively delete a worktree or use a
   force option; retain any refused pair for review.

### Explicitly superseded or abandoned feature

Use this disposition only when the operator explicitly declares the exact
local tip obsolete, rejected, or replaced and its disposition is already clear
from durable repository history. It removes branch/worktree clutter without
pretending the discarded commit was integrated:

1. Create a uniquely named annotated `archive/...` tag at the exact branch tip
   and verify that the tag object dereferences to that commit. Never move or
   reuse the tag; pushing it is a separate operator decision. Deleting the
   archive tag or making the commit unreachable is outside this procedure.
2. Recheck that the branch, worktree, tip, ownership, and inspected content are
   unchanged and that the verified archive tag still names the tip.
3. Run `git worktree remove <exact-path>` without `--force`. If Git refuses,
   retain the worktree and stop for review; never delete it recursively.
4. Run `git branch -D <exact-branch>` only for the approved, now-unlinked local
   branch and only while its verified archive tag remains. No other force-
   deletion path is authorized.

After either disposition, re-list branches and worktrees, verify `main` and
`develop` and their permanent checkouts remain unchanged and clean, preserve
rollback and archive tags, and run proportionate repository validation.

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

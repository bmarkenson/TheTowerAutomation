#!/usr/bin/python3.12
"""Inventory coordinated sources and guard promotion-owner release.

The small JSON journal lives in the repository's common Git directory, so it
survives retirement of any linked worktree without becoming production input.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Sequence


PROMOTION_OWNER_REF = "refs/thetower/promotion-owner"
INVENTORY_SCHEMA = 1
INVENTORY_DIRECTORY = Path("thetower/outcome-retirement")
DISPOSITIONS = ("integrated", "superseded", "retained")
_OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")


class RetirementGuardError(RuntimeError):
    """The inventory or requested transition is invalid."""


class ClosureBlocked(RetirementGuardError):
    """Promotion ownership cannot be released yet."""

    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__("promotion closure is blocked by the retirement inventory")


def _git(repository: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", os.fspath(repository), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip()
        raise RetirementGuardError(
            f"git {' '.join(arguments)} failed with exit "
            f"{completed.returncode}: {detail}"
        )
    return completed.stdout.strip()


def _root(repository: Path | str) -> Path:
    selected = Path(repository).absolute()
    try:
        return Path(_git(selected, "rev-parse", "--show-toplevel")).absolute()
    except RetirementGuardError as exc:
        raise RetirementGuardError(f"Not a Git worktree: {selected}") from exc


def _oid(repository: Path, revision: str) -> str:
    value = _git(repository, "rev-parse", "--verify", f"{revision}^{{commit}}")
    if not _OBJECT_ID.fullmatch(value):
        raise RetirementGuardError(f"{revision!r} is not one exact commit")
    return value


def _optional_oid(repository: Path, reference: str) -> str | None:
    return _git(
        repository,
        "rev-parse",
        "--verify",
        "--quiet",
        f"{reference}^{{commit}}",
        check=False,
    ) or None


def _require_owner(repository: Path, candidate: str) -> None:
    owner = _optional_oid(repository, PROMOTION_OWNER_REF)
    if owner != candidate:
        raise RetirementGuardError(
            f"{PROMOTION_OWNER_REF} must name {candidate}; observed {owner or 'absent'}"
        )


def _is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        [
            "git",
            "-C",
            os.fspath(repository),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode not in (0, 1):
        raise RetirementGuardError(completed.stderr.strip())
    return completed.returncode == 0


def _canonical_ref(repository: Path, reference: str) -> str:
    value = _git(
        repository,
        "rev-parse",
        "--symbolic-full-name",
        "--verify",
        reference,
    )
    if not value.startswith("refs/"):
        raise RetirementGuardError(f"{reference!r} is not an existing ref")
    return value


def _branch_name(branch: str) -> str:
    return branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"


def _archive_name(tag: str) -> str:
    value = tag if tag.startswith("refs/tags/") else f"refs/tags/{tag}"
    if not value.startswith("refs/tags/archive/"):
        raise RetirementGuardError("Superseded work requires an archive/... tag")
    return value


def _worktrees(repository: Path) -> list[dict[str, str | None]]:
    records: list[dict[str, str | None]] = []
    raw = _git(repository, "worktree", "list", "--porcelain", "-z")
    for record in raw.split("\0\0"):
        if not record:
            continue
        fields: dict[str, str] = {}
        for item in record.split("\0"):
            key, separator, value = item.partition(" ")
            if separator:
                fields[key] = value
        if "worktree" not in fields or "HEAD" not in fields:
            raise RetirementGuardError("Git returned malformed worktree data")
        records.append(
            {
                "path": os.path.abspath(fields["worktree"]),
                "head": fields["HEAD"],
                "branch": fields.get("branch"),
            }
        )
    return records


def _branches(repository: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    output = _git(
        repository,
        "for-each-ref",
        "--format=%(refname) %(objectname)",
        "refs/heads",
    )
    for line in output.splitlines():
        branch, object_id = line.rsplit(" ", 1)
        result[branch] = object_id
    return result


def _cherry(repository: Path, upstream: str, tip: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in _git(repository, "cherry", upstream, tip).splitlines():
        marker, commit = line.split(" ", 1)
        result[commit] = marker
    return result


def _patches_new_in_candidate(
    repository: Path,
    base: str,
    candidate: str,
    tip: str,
) -> list[str]:
    at_base = _cherry(repository, base, tip)
    if "+" not in at_base.values():
        return []
    at_candidate = _cherry(repository, candidate, tip)
    return sorted(
        commit
        for commit, marker in at_base.items()
        if marker == "+" and at_candidate.get(commit) == "-"
    )


def _common_git_directory(repository: Path) -> Path:
    selected = Path(_git(repository, "rev-parse", "--git-common-dir"))
    return (selected if selected.is_absolute() else repository / selected).absolute()


def _inventory_file(repository: Path, candidate: str) -> Path:
    return (
        _common_git_directory(repository)
        / INVENTORY_DIRECTORY
        / f"{candidate}.json"
    )


def inventory_path(repository: Path | str, candidate: str) -> Path:
    root = _root(repository)
    return _inventory_file(root, _oid(root, candidate))


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".inventory.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read(repository: Path, candidate: str) -> tuple[Path, dict[str, Any]]:
    path = _inventory_file(repository, candidate)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RetirementGuardError(f"Unable to read inventory {path}: {exc}") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != INVENTORY_SCHEMA
        or document.get("candidate") != candidate
        or not isinstance(document.get("sources"), list)
    ):
        raise RetirementGuardError(f"Invalid retirement inventory {path}")
    for source in document["sources"]:
        if (
            not isinstance(source, dict)
            or source.get("disposition") not in ("pending", *DISPOSITIONS)
            or not isinstance(source.get("branch"), str)
            or not isinstance(source.get("tip"), str)
        ):
            raise RetirementGuardError(f"Invalid source entry in inventory {path}")
    return path, document


def _discover(
    repository: Path,
    *,
    base: str,
    candidate: str,
    candidate_ref: str,
    declared: Iterable[str],
) -> list[dict[str, Any]]:
    declared_set = set(declared)
    branches = _branches(repository)
    trees = _worktrees(repository)
    tree_by_branch = {tree["branch"]: tree for tree in trees if tree["branch"]}
    missing = declared_set - branches.keys()
    unlinked = declared_set - tree_by_branch.keys()
    if missing:
        raise RetirementGuardError(
            "Missing declared source branches: " + ", ".join(sorted(missing))
        )
    if unlinked:
        raise RetirementGuardError(
            "Declared sources without linked worktrees: "
            + ", ".join(sorted(unlinked))
        )

    sources: list[dict[str, Any]] = []
    for branch, tip in sorted(branches.items()):
        if branch in {"refs/heads/main", candidate_ref}:
            continue
        exact = _is_ancestor(repository, tip, candidate) and not _is_ancestor(
            repository, tip, base
        )
        patches = _patches_new_in_candidate(repository, base, candidate, tip)
        if branch not in declared_set and not exact and not patches:
            continue
        detected_by = []
        if branch in declared_set:
            detected_by.append("declared")
        if exact:
            detected_by.append("exact_ancestry")
        if patches:
            detected_by.append("patch_new_in_candidate")
        tree = tree_by_branch.get(branch)
        sources.append(
            {
                "branch": branch,
                "worktree": tree["path"] if tree else None,
                "tip": tip,
                "detected_by": detected_by,
                "patches_newly_represented": patches,
                "disposition": "pending",
                "resolution": None,
            }
        )
    return sources


def _merge_sources(
    document: dict[str, Any],
    discovered: Iterable[dict[str, Any]],
    *,
    strict_tip: bool,
) -> list[str]:
    by_branch = {source["branch"]: source for source in document["sources"]}
    added: list[str] = []
    for source in discovered:
        prior = by_branch.get(source["branch"])
        if prior is None:
            document["sources"].append(source)
            by_branch[source["branch"]] = source
            added.append(source["branch"])
        elif prior["tip"] != source["tip"]:
            if strict_tip:
                raise RetirementGuardError(
                    f"{source['branch']} moved from {prior['tip']} to {source['tip']}"
                )
        else:
            prior["detected_by"] = sorted(
                set(prior["detected_by"]) | set(source["detected_by"])
            )
            prior["patches_newly_represented"] = sorted(
                set(prior["patches_newly_represented"])
                | set(source["patches_newly_represented"])
            )
    document["sources"].sort(key=lambda source: source["branch"])
    return added


def begin_inventory(
    repository: Path | str,
    *,
    base: str,
    candidate: str,
    candidate_ref: str,
    declared_sources: Iterable[str] = (),
) -> dict[str, Any]:
    root = _root(repository)
    base_oid = _oid(root, base)
    candidate_oid = _oid(root, candidate)
    _require_owner(root, candidate_oid)
    if not _is_ancestor(root, base_oid, candidate_oid):
        raise RetirementGuardError(f"{base_oid} is not an ancestor of {candidate_oid}")
    candidate_reference = _canonical_ref(root, candidate_ref)
    if _oid(root, candidate_reference) != candidate_oid:
        raise RetirementGuardError(
            f"{candidate_reference} does not name {candidate_oid}"
        )
    declared = tuple(
        _canonical_ref(root, _branch_name(branch)) for branch in declared_sources
    )
    if len(set(declared)) != len(declared):
        raise RetirementGuardError("Declared sources must be unique")
    if {candidate_reference, "refs/heads/main"} & set(declared):
        raise RetirementGuardError("Candidate and main cannot be delegated sources")

    trees = _worktrees(root)
    tree_by_branch = {tree["branch"]: tree for tree in trees if tree["branch"]}
    discovered = _discover(
        root,
        base=base_oid,
        candidate=candidate_oid,
        candidate_ref=candidate_reference,
        declared=declared,
    )
    path = _inventory_file(root, candidate_oid)
    if path.exists():
        _, document = _read(root, candidate_oid)
        if (
            document["base"] != base_oid
            or document["candidate_ref"] != candidate_reference
        ):
            raise RetirementGuardError(f"Inventory {path} has a different boundary")
        _merge_sources(document, discovered, strict_tip=True)
    else:
        candidate_tree = tree_by_branch.get(candidate_reference)
        document = {
            "schema_version": INVENTORY_SCHEMA,
            "base": base_oid,
            "candidate": candidate_oid,
            "candidate_ref": candidate_reference,
            "candidate_worktree": candidate_tree["path"] if candidate_tree else None,
            "sources": discovered,
        }
    _write(path, document)
    return document


def _source(document: dict[str, Any], branch: str) -> dict[str, Any]:
    selected = _branch_name(branch)
    for source in document["sources"]:
        if source["branch"] == selected:
            return source
    raise RetirementGuardError(f"{selected} is not in the inventory")


def _verify_archive(repository: Path, tag: str, tip: str) -> None:
    if _git(repository, "cat-file", "-t", tag) != "tag":
        raise RetirementGuardError(f"{tag} is not an annotated tag")
    if _oid(repository, tag) != tip:
        raise RetirementGuardError(f"{tag} does not dereference to {tip}")


def set_disposition(
    repository: Path | str,
    *,
    candidate: str,
    branch: str,
    disposition: str,
    archive_tag: str | None = None,
    reason: str | None = None,
    owner: str | None = None,
    remaining_work: str | None = None,
) -> dict[str, Any]:
    root = _root(repository)
    candidate_oid = _oid(root, candidate)
    _require_owner(root, candidate_oid)
    path, document = _read(root, candidate_oid)
    selected = _source(document, branch)
    if disposition == "integrated":
        if not _is_ancestor(root, selected["tip"], candidate_oid):
            raise RetirementGuardError(
                "patch-equivalent work must be superseded, not integrated"
            )
        resolution = {"target": candidate_oid}
    elif disposition == "superseded":
        if not archive_tag or not reason or not reason.strip():
            raise RetirementGuardError(
                "Superseded work needs an archive tag and reason"
            )
        archive = _archive_name(archive_tag)
        _verify_archive(root, archive, selected["tip"])
        resolution = {
            "archive_tag": archive,
            "reason": reason.strip(),
            "target": (
                candidate_oid if selected["patches_newly_represented"] else None
            ),
        }
    elif disposition == "retained":
        if (
            not owner
            or not owner.strip()
            or not remaining_work
            or not remaining_work.strip()
        ):
            raise RetirementGuardError(
                "Retained work needs an owner and remaining work"
            )
        branches = _branches(root)
        tree = next(
            (tree for tree in _worktrees(root) if tree["branch"] == selected["branch"]),
            None,
        )
        current_tip = branches.get(selected["branch"])
        if (
            current_tip is None
            or tree is None
            or tree["path"] != selected["worktree"]
            or not _is_ancestor(root, selected["tip"], current_tip)
        ):
            raise RetirementGuardError(
                "Retained source no longer matches its inventory"
            )
        resolution = {
            "owner": owner.strip(),
            "remaining_work": remaining_work.strip(),
            "tip_at_disposition": current_tip,
        }
    else:
        raise RetirementGuardError(f"Unknown disposition {disposition!r}")
    selected["disposition"] = disposition
    selected["resolution"] = resolution
    _write(path, document)
    return document


def _blocker(branch: str, code: str, message: str) -> dict[str, str]:
    return {"branch": branch, "code": code, "message": message}


def check_inventory(
    repository: Path | str,
    *,
    candidate: str,
) -> dict[str, Any]:
    root = _root(repository)
    candidate_oid = _oid(root, candidate)
    _require_owner(root, candidate_oid)
    path, document = _read(root, candidate_oid)
    discovered = _discover(
        root,
        base=document["base"],
        candidate=candidate_oid,
        candidate_ref=document["candidate_ref"],
        declared=(),
    )
    added = _merge_sources(document, discovered, strict_tip=False)
    if added:
        _write(path, document)

    branches = _branches(root)
    trees = _worktrees(root)
    tree_by_path = {tree["path"]: tree for tree in trees}
    blockers: list[dict[str, str]] = []
    for source in document["sources"]:
        branch = source["branch"]
        disposition = source["disposition"]
        current_tip = branches.get(branch)
        worktree_path = source["worktree"]
        linked = tree_by_path.get(worktree_path)
        if disposition == "pending":
            blockers.append(
                _blocker(
                    branch,
                    "pending_disposition",
                    "choose integrated, superseded, or retained",
                )
            )
        elif disposition in {"integrated", "superseded"}:
            if disposition == "integrated" and not _is_ancestor(
                root, source["tip"], candidate_oid
            ):
                blockers.append(
                    _blocker(branch, "invalid_integrated_disposition", source["tip"])
                )
            if disposition == "superseded":
                try:
                    _verify_archive(
                        root,
                        source["resolution"]["archive_tag"],
                        source["tip"],
                    )
                except (KeyError, RetirementGuardError) as exc:
                    blockers.append(
                        _blocker(branch, "invalid_superseded_archive", str(exc))
                    )
            if current_tip is not None:
                blockers.append(
                    _blocker(
                        branch,
                        f"{disposition}_branch_not_retired",
                        f"local branch still exists at {current_tip}",
                    )
                )
            if linked is not None or (worktree_path and os.path.lexists(worktree_path)):
                blockers.append(
                    _blocker(
                        branch,
                        f"{disposition}_worktree_not_retired",
                        f"recorded worktree still exists at {worktree_path}",
                    )
                )
        else:
            resolution = source["resolution"] or {}
            if not resolution.get("owner") or not resolution.get("remaining_work"):
                blockers.append(
                    _blocker(branch, "invalid_retained_disposition", "missing details")
                )
            if current_tip is None or not _is_ancestor(
                root, source["tip"], current_tip
            ):
                blockers.append(
                    _blocker(branch, "retained_tip_missing", source["tip"])
                )
            if (
                linked is None
                or linked["branch"] != branch
                or not worktree_path
                or not os.path.lexists(worktree_path)
            ):
                blockers.append(
                    _blocker(branch, "retained_worktree_missing", str(worktree_path))
                )
    return {
        "schema_version": INVENTORY_SCHEMA,
        "candidate": candidate_oid,
        "inventory_path": os.fspath(path),
        "ready": not blockers,
        "new_sources_added": added,
        "blockers": blockers,
        "sources": document["sources"],
    }


def close_promotion(
    repository: Path | str,
    *,
    candidate: str,
) -> dict[str, Any]:
    root = _root(repository)
    candidate_oid = _oid(root, candidate)
    result = check_inventory(root, candidate=candidate_oid)
    if not result["ready"]:
        raise ClosureBlocked(result)

    path, document = _read(root, candidate_oid)
    branches = _branches(root)
    for source in document["sources"]:
        if source["disposition"] == "retained":
            closing_tip = branches.get(source["branch"])
            if closing_tip is None or not _is_ancestor(
                root, source["tip"], closing_tip
            ):
                raise RetirementGuardError(
                    f"Retained source {source['branch']} changed during close"
                )
            source["resolution"]["tip_at_closure"] = closing_tip
    _write(path, document)
    _require_owner(root, candidate_oid)
    _git(root, "update-ref", "-d", PROMOTION_OWNER_REF, candidate_oid)
    if _optional_oid(root, PROMOTION_OWNER_REF) is not None:
        raise RetirementGuardError(f"{PROMOTION_OWNER_REF} was not released")
    return {**result, "closed": True, "released_ref": PROMOTION_OWNER_REF}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory coordinated sources and guard promotion closure."
    )
    parser.add_argument("--repository", default=".")
    commands = parser.add_subparsers(dest="command", required=True)

    begin = commands.add_parser("begin")
    begin.add_argument("--base", required=True)
    begin.add_argument("--candidate", required=True)
    begin.add_argument("--candidate-ref", required=True)
    begin.add_argument("--source", action="append", default=[])

    disposition = commands.add_parser("disposition")
    disposition.add_argument("--candidate", required=True)
    disposition.add_argument("--branch", required=True)
    disposition.add_argument("--disposition", choices=DISPOSITIONS, required=True)
    disposition.add_argument("--archive-tag")
    disposition.add_argument("--reason")
    disposition.add_argument("--owner")
    disposition.add_argument("--remaining-work")

    for command in ("check", "close"):
        selected = commands.add_parser(command)
        selected.add_argument("--candidate", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "begin":
            result = begin_inventory(
                arguments.repository,
                base=arguments.base,
                candidate=arguments.candidate,
                candidate_ref=arguments.candidate_ref,
                declared_sources=arguments.source,
            )
        elif arguments.command == "disposition":
            result = set_disposition(
                arguments.repository,
                candidate=arguments.candidate,
                branch=arguments.branch,
                disposition=arguments.disposition,
                archive_tag=arguments.archive_tag,
                reason=arguments.reason,
                owner=arguments.owner,
                remaining_work=arguments.remaining_work,
            )
        elif arguments.command == "check":
            result = check_inventory(
                arguments.repository,
                candidate=arguments.candidate,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["ready"] else 1
        else:
            result = close_promotion(
                arguments.repository,
                candidate=arguments.candidate,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ClosureBlocked as exc:
        print(json.dumps(exc.result, indent=2, sort_keys=True))
        return 1
    except RetirementGuardError as exc:
        print(f"outcome-retirement: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPLETED_TASK_MARKER = re.compile(
    r"^\s*(?:[-*+]|\d+[.)])\s+\[[xX]\](?:\s|$)"
)


def _active_work_owners() -> tuple[Path, ...]:
    return (
        ROOT / "PENDING_DEVELOPMENT.md",
        *sorted((ROOT / "docs/backlog").glob("*.md")),
        ROOT / "docs/observed_issues.md",
    )


def test_completed_task_marker_recognizes_task_list_forms() -> None:
    assert COMPLETED_TASK_MARKER.match("- [x] completed")
    assert COMPLETED_TASK_MARKER.match("  3. [X] completed")
    assert not COMPLETED_TASK_MARKER.match("- [ ] active")
    assert not COMPLETED_TASK_MARKER.match("prose mentioning [x] evidence")


def test_active_work_owners_contain_no_completed_task_markers() -> None:
    offenders: list[str] = []
    for path in _active_work_owners():
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if COMPLETED_TASK_MARKER.match(line):
                offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line}")

    assert not offenders, (
        "Move completed work to its lifecycle owner instead of retaining "
        "checked tasks in an active queue:\n" + "\n".join(offenders)
    )


def test_candidate_gate_guidance_commits_before_checkpoint_and_isolates_output() -> None:
    new_thread = " ".join(
        (ROOT / "docs/new_thread.md").read_text(encoding="utf-8").split()
    )
    promotion = " ".join(
        (ROOT / "docs/operations/production_promotion.md")
        .read_text(encoding="utf-8")
        .split()
    )

    assert "commit the exact candidate before running its final promotion gate" in (
        new_thread
    )
    assert "mutable or uncommitted working tree is development evidence" in (
        new_thread
    )
    assert "commit exact code/test candidate `V`" in promotion
    assert "pytest temporary directories" in new_thread
    assert "they must not populate the repository's ignored" in promotion

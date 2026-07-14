import json

import cv2
import numpy as np
import pytest

from tools.template_tool import (
    WorkflowError,
    build_plan,
    commit_plan,
    derive_template_ref,
    main,
    validate_plan,
)


def _write_fixture(tmp_path, *, name="source.png", seed=42):
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 256, size=(60, 80, 3), dtype=np.uint8)
    path = tmp_path / name
    assert cv2.imwrite(str(path), image)
    return path, image


def _write_clickmap(tmp_path, payload):
    path = tmp_path / "clickmap.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _new_plan(tmp_path):
    source_path, image = _write_fixture(tmp_path)
    clickmap_path = _write_clickmap(
        tmp_path,
        {"overlays": {}, "metadata": {"keep": {"tap": {"x": 1, "y": 2}}}},
    )
    template_dir = tmp_path / "templates"
    plan = build_plan(
        source_path=source_path,
        dot_path="overlays.sample",
        crop_region={"x": 20, "y": 15, "w": 8, "h": 6},
        match_region={"x": 15, "y": 10, "w": 20, "h": 18},
        clickmap_path=clickmap_path,
        template_dir=template_dir,
        roles=["overlay"],
    )
    return plan, image, clickmap_path, template_dir


def test_derives_nested_template_path_from_dot_path():
    assert derive_template_ref("upgrades.utility.left.EHLS") == (
        "upgrades/utility/left/EHLS.png"
    )


def test_build_and_validate_plan_separates_crop_from_search_region(tmp_path):
    plan, _, _, _ = _new_plan(tmp_path)
    negative_path, _ = _write_fixture(tmp_path, name="negative.png", seed=99)

    records, errors = validate_plan(
        plan,
        profile="both",
        negative_paths=[negative_path],
    )

    assert not errors
    assert plan.entry["match_region"] == {"x": 15, "y": 10, "w": 20, "h": 18}
    assert plan.crop.shape[:2] == (6, 8)
    assert {record["profile"] for record in records} == {"detector", "label"}
    assert all(record["passed"] for record in records)
    source_records = [record for record in records if record["kind"] == "source"]
    assert all(record["bbox"] == [20, 15, 8, 6] for record in source_records)


def test_refresh_plan_preserves_non_template_entry_fields(tmp_path):
    source_path, _ = _write_fixture(tmp_path)
    clickmap_path = _write_clickmap(
        tmp_path,
        {
            "buttons": {
                "sample": {
                    "match_template": "buttons/custom_name.png",
                    "match_region": {"x": 10, "y": 10, "w": 30, "h": 25},
                    "match_threshold": 0.85,
                    "roles": ["button"],
                    "tap": {"x": 70, "y": 50},
                }
            }
        },
    )

    plan = build_plan(
        source_path=source_path,
        dot_path="buttons.sample",
        crop_region={"x": 20, "y": 15, "w": 8, "h": 6},
        clickmap_path=clickmap_path,
        template_dir=tmp_path / "templates",
    )

    assert plan.existing_entry
    assert plan.template_ref == "buttons/custom_name.png"
    assert plan.entry["match_region"] == {"x": 10, "y": 10, "w": 30, "h": 25}
    assert plan.entry["match_threshold"] == 0.85
    assert plan.entry["tap"] == {"x": 70, "y": 50}


def test_commit_writes_template_and_targeted_entry(tmp_path):
    plan, image, clickmap_path, template_dir = _new_plan(tmp_path)
    records, errors = validate_plan(plan)
    assert records and not errors

    commit_plan(plan)

    saved = cv2.imread(str(template_dir / "overlays" / "sample.png"))
    assert np.array_equal(saved, image[15:21, 20:28])
    clickmap = json.loads(clickmap_path.read_text(encoding="utf-8"))
    assert clickmap["overlays"]["sample"] == plan.entry
    assert clickmap["metadata"] == {"keep": {"tap": {"x": 1, "y": 2}}}


def test_existing_entry_requires_explicit_replace(tmp_path):
    plan, _, clickmap_path, template_dir = _new_plan(tmp_path)
    clickmap = json.loads(clickmap_path.read_text(encoding="utf-8"))
    clickmap["overlays"]["sample"] = plan.entry
    clickmap_path.write_text(json.dumps(clickmap, indent=2) + "\n", encoding="utf-8")
    refreshed = build_plan(
        source_path=plan.source_path,
        dot_path="overlays.sample",
        crop_region=plan.crop_region,
        clickmap_path=clickmap_path,
        template_dir=template_dir,
    )

    with pytest.raises(WorkflowError, match="requires --replace"):
        commit_plan(refreshed)


def test_existing_asset_size_change_requires_explicit_consent(tmp_path):
    plan, _, clickmap_path, template_dir = _new_plan(tmp_path)
    clickmap = json.loads(clickmap_path.read_text(encoding="utf-8"))
    clickmap["overlays"]["sample"] = plan.entry
    clickmap_path.write_text(json.dumps(clickmap, indent=2) + "\n", encoding="utf-8")
    existing_path = template_dir / "overlays" / "sample.png"
    existing_path.parent.mkdir(parents=True)
    assert cv2.imwrite(str(existing_path), np.ones((3, 4, 3), dtype=np.uint8))
    refreshed = build_plan(
        source_path=plan.source_path,
        dot_path="overlays.sample",
        crop_region=plan.crop_region,
        clickmap_path=clickmap_path,
        template_dir=template_dir,
    )

    assert refreshed.asset_comparison == {
        "existing_dimensions": [4, 3],
        "candidate_dimensions": [8, 6],
        "same_dimensions": False,
        "differing_pixels": None,
        "rmse": None,
    }
    with pytest.raises(WorkflowError, match="dimensions differ"):
        commit_plan(refreshed, replace=True)


def test_cli_defaults_to_non_mutating_dry_run(tmp_path, capsys):
    plan, _, clickmap_path, template_dir = _new_plan(tmp_path)
    clickmap_before = clickmap_path.read_bytes()
    preview_dir = tmp_path / "preview"

    rc = main(
        [
            "--image",
            str(plan.source_path),
            "--dot-path",
            "overlays.cli_sample",
            "--crop",
            "20,15,8,6",
            "--match-region",
            "15,10,20,18",
            "--roles",
            "overlay",
            "--clickmap",
            str(clickmap_path),
            "--template-dir",
            str(template_dir),
            "--preview-dir",
            str(preview_dir),
        ]
    )

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "dry-run"
    assert clickmap_path.read_bytes() == clickmap_before
    assert not (template_dir / "overlays" / "cli_sample.png").exists()
    assert (preview_dir / "candidate.png").is_file()
    assert (preview_dir / "annotated_source.png").is_file()
    assert (preview_dir / "plan.json").is_file()

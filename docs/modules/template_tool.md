# Headless template workflow

`tools/template_tool.py` is the preferred workflow for Codex-driven template
creation and refreshes. It does not require an X11/Wayland session and defaults
to a non-mutating dry run.

The workflow keeps three concepts separate:

- **Crop region**: the exact pixels stored in the template PNG.
- **Match region**: the screen area searched at runtime; it may be larger than
  the crop or supplied through an existing `region_ref`.
- **Runtime profile**: detector matching uses color plus configured/default
  padding, while label matching preserves the existing grayscale/zero-padding
  behavior. The default `both` profile validates both paths.

## Dry-run and review

Use the repository virtual environment explicitly:

```bash
.venv/bin/python tools/template_tool.py \
  --image screenshots/template_source.png \
  --dot-path overlays.example_badge \
  --crop 995,1718,64,66 \
  --match-region 970,1690,100,110 \
  --roles overlay
```

For a fresh live screenshot, replace `--image` with a capture destination:

```bash
.venv/bin/python tools/template_tool.py \
  --capture screenshots/template_source.png \
  --dot-path overlays.example_badge \
  --crop 995,1718,64,66 \
  --match-region 970,1690,100,110 \
  --roles overlay
```

The dry run writes three review artifacts to a unique directory under `/tmp`
unless `--preview-dir` is supplied:

- `candidate.png`: the exact candidate asset.
- `annotated_source.png`: crop (green), configured search region (yellow),
  detector match (blue), and label match (magenta).
- `plan.json`: proposed clickmap entry, warnings, errors, and match results.

The source self-match verifies geometry and integration but is not independent
evidence that a template is stable. Add repeatable fixtures when available:

```bash
  --positive test/fixtures/screen_with_badge.png \
  --negative test/fixtures/screen_without_badge.png
```

Every positive must meet the configured threshold. Every negative must remain
below it. A source match at a different location is rejected as ambiguous.

## Commit

After visually reviewing the candidate and annotated source, rerun the same
command with `--commit`. Refreshing an existing entry or asset additionally
requires `--replace`:

```bash
.venv/bin/python tools/template_tool.py \
  --image test/fixtures/home_screen_new_day_store_badge_20260713.png \
  --dot-path overlays.daily_free_gems_badge_home \
  --crop 1001,1725,52,52 \
  --commit --replace
```

The template and clickmap are staged before replacement and rolled back if the
second write fails. Existing fields such as `tap`, `swipe`, roles, threshold,
and region are preserved unless explicitly replaced. An asset referenced by
other clickmap entries is blocked unless `--allow-shared-template` is supplied
after reviewing every affected entry. Changing an existing asset's dimensions
is independently blocked unless `--allow-size-change` is supplied after
reviewing the configured search geometry.

Always review `git diff`, run `test/clickmap_integrity.py`, and validate against
an independent fixture or live screen before treating a new template as proven.

## ImageMagick's role

ImageMagick remains optional inspection tooling. Commands such as `identify`,
`compare`, and `montage` are useful for dimensions, visual diffs, and contact
sheets, but all acceptance decisions use the same OpenCV engine as runtime.

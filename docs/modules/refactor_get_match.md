# Refactor: Centralized Clickmap Matching via `core.matcher.get_match`

## Summary
The old matching logic was spread across:
- `utils/template_matcher.py` (clickmap-backed `match_region`)
- one-off matchers in `matchers/` (e.g., `matchers/game_over.py`)
- ad-hoc `resolve_dot_path` + `match_region` calls in `core/state_detector.py` and elsewhere

This refactor centralizes **all** clickmap-backed matching in `core/matcher.py` to:
- Remove duplication
- Avoid manual clickmap entry resolution in callers
- Keep "perception" logic (image matching) separate from "action" logic (tapping, swiping)

## New API
```python
from core.matcher import get_match

# dot_path: e.g., "indicators.game_over"
pt, conf = get_match(dot_path, screenshot=screen)
if pt:
    ...
```

## Function signatures

**Public**
```python
def get_match(
    dot_path: str,
    *,
    screenshot,
    template_dir: str = "assets/match_templates",
) -> tuple[tuple[int, int] | None, float]:
    """Resolves clickmap entry, matches template in defined region, returns (pt, confidence)."""
```

**Private**
```python
def _match_entry(
    screenshot,
    entry: dict,
    template_dir: str = "assets/match_templates",
) -> tuple[tuple[int, int] | None, float]:
    """Lower-level matcher for a pre-resolved clickmap entry dict."""
```

**Other functions moved**
- `detect_floating_gem_square()` also relocated to `core/matcher.py`.

## Migration plan

1. **Shim for backwards compatibility**  
   `utils/template_matcher.py` now re-exports:
   ```python
   from core.matcher import _match_entry as match_region, detect_floating_gem_square
   ```
   This keeps existing imports (`match_region`) working until all callers are updated.

2. **Migrate live code to `get_match()`**  
   - Replace:
     ```python
     from utils.template_matcher import match_region
     from core.clickmap_access import resolve_dot_path
     entry = resolve_dot_path(key)
     pt, conf = match_region(screen, entry)
     ```
     With:
     ```python
     from core.matcher import get_match
     pt, conf = get_match(key, screenshot=screen)
     ```

3. **Delete deprecated modules**  
   - `matchers/` directory removed (all functions replaced by clickmap-driven detection).
   - Remove related `.py.md` specs.

4. **Tests**  
   - Tests may continue using `match_region` via the shim during migration.
   - Long-term: update tests to use `get_match()` or `_match_entry()` for synthetic entries.

## Benefits
- Single source of truth for clickmap matching.
- No import-time I/O — templates loaded only when needed.
- Easier to add features like:
  - Template caching
  - Grayscale matching toggle
  - Threshold override for testing
- Clearer separation of perception vs. action.

## Next steps
- Migrate `core/state_detector.py` to use `get_match()` directly (remove `resolve_dot_path` calls).
- Search for `utils.template_matcher` imports and replace with `core.matcher`.
- Remove the shim once no code imports from `utils/template_matcher`.

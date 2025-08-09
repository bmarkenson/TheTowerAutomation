# utils/template_matcher.py
# DEPRECATED SHIM — use core.matcher instead.
# Kept to avoid breaking existing imports; safe to remove once callers are updated.

from core.matcher import _match_entry as match_region, detect_floating_gem_square  # re-export

__all__ = ["match_region", "detect_floating_gem_square"]

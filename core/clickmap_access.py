"""Clickmap loading and lookup helpers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from utils.logger import log
CLICKMAP_FILE = Path(__file__).resolve().parent.parent / "config" / "clickmap.json"


def _load_clickmap(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        log(f"[CLICKMAP] Failed to load {path}: {exc}", "ERROR")
        return {}


_CLICKMAP: Dict[str, Any] = _load_clickmap(CLICKMAP_FILE)


def get_clickmap() -> Dict[str, Any]:
    """Return the mutable clickmap dictionary."""

    return _CLICKMAP


def get_clickmap_path() -> Path:
    """Return the absolute path to the clickmap JSON file."""

    return CLICKMAP_FILE


def reload_clickmap(path: Optional[Path] = None) -> Dict[str, Any]:
    """Reload the clickmap JSON from disk and clear cached lookups."""

    target = path or CLICKMAP_FILE
    _CLICKMAP.clear()
    _CLICKMAP.update(_load_clickmap(target))
    _resolve_cached.cache_clear()
    return _CLICKMAP


def save_clickmap(data: Optional[Dict[str, Any]] = None) -> None:
    """Persist the clickmap (or provided mapping) to disk atomically."""

    target = CLICKMAP_FILE
    payload = data if data is not None else _CLICKMAP
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    tmp_path.replace(target)
    log(f"[CLICKMAP] Saved to {target}", "INFO")


def _resolve_from_mapping(dot_path: str, mapping: Mapping[str, Any]) -> Any:
    parts = dot_path.split(".")
    cur: Any = mapping
    for part in parts:
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


@lru_cache(maxsize=1024)
def _resolve_cached(dot_path: str) -> Any:
    return _resolve_from_mapping(dot_path, _CLICKMAP)


def resolve_dot_path(dot_path: str, data: Optional[Mapping[str, Any]] = None) -> Any:
    """Resolve ``a.b.c`` paths against the provided mapping or global clickmap."""

    if data is None or data is _CLICKMAP:
        return _resolve_cached(dot_path)
    if isinstance(data, Mapping):
        return _resolve_from_mapping(dot_path, data)
    return None


def dot_path_exists(dot_path: str, data: Optional[Mapping[str, Any]] = None) -> bool:
    """Return True if the dot-path resolves to a non-None value."""

    return resolve_dot_path(dot_path, data) is not None


def set_dot_path(dot_path: str, value: Any, *, allow_overwrite: bool = False) -> None:
    """Set a value inside the global clickmap at ``dot_path``."""

    parts = dot_path.split(".")
    cur = _CLICKMAP
    for segment in parts[:-1]:
        nxt = cur.get(segment)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[segment] = nxt
        cur = nxt
    final = parts[-1]
    if final in cur and not allow_overwrite:
        raise KeyError(f"Key '{dot_path}' already exists. Use allow_overwrite=True to overwrite.")
    cur[final] = value
    _resolve_cached.cache_clear()


def flatten_clickmap(data: Optional[Dict[str, Any]] = None, prefix: str = "") -> Dict[str, Any]:
    """Return a flat dict mapping dot paths to leaf values."""

    source = data if data is not None else _CLICKMAP
    flat: Dict[str, Any] = {}
    for key, value in source.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten_clickmap(value, full_key))
        else:
            flat[full_key] = value
    return flat


def get_entries_by_role(role: str) -> Dict[str, Dict[str, Any]]:
    """Return clickmap entries whose ``roles`` include the given role."""

    results: Dict[str, Dict[str, Any]] = {}

    def _search(node: Mapping[str, Any], path: str = "") -> None:
        for key, value in node.items():
            new_path = f"{path}.{key}" if path else key
            if isinstance(value, Mapping):
                roles = value.get("roles")
                if isinstance(roles, Iterable) and role in roles:
                    results[new_path] = dict(value)
                _search(value, new_path)

    _search(_CLICKMAP)
    return results


def get_explicit_tap(name: str) -> Optional[Tuple[int, int]]:
    """Return only explicitly configured static tap coordinates for ``name``."""

    entry = resolve_dot_path(name)
    if not isinstance(entry, Mapping):
        return None

    tap = entry.get("tap")
    if isinstance(tap, Mapping) and {"x", "y"} <= tap.keys():
        try:
            return int(tap["x"]), int(tap["y"])
        except Exception:
            return None

    return None


def get_click(name: str) -> Optional[Tuple[int, int]]:
    """Resolve click coordinates using explicit tap or legacy region center.

    This compatibility lookup preserves the historical center of a direct
    ``match_region`` for tooling and callers that deliberately request it. The
    runtime blind-tap path uses :func:`get_explicit_tap` instead. Entries using
    a broad ``region_ref`` never resolve to that search window's center.
    """

    entry = resolve_dot_path(name)
    if not isinstance(entry, Mapping):
        log(f"[CLICKMAP] No entry for '{name}'", "WARN")
        return None

    explicit = get_explicit_tap(name)
    if explicit is not None:
        return explicit

    region = entry.get("match_region")
    if isinstance(region, Mapping) and {"x", "y", "w", "h"} <= region.keys():
        try:
            x = int(region["x"])
            y = int(region["y"])
            w = int(region["w"])
            h = int(region["h"])
            return x + w // 2, y + h // 2
        except Exception:
            return None

    log(f"[CLICKMAP] Entry '{name}' lacks tap/match_region data", "WARN")
    return None


def has_click(name: str) -> bool:
    """Return True when ``get_click`` resolves to coordinates."""

    return get_click(name) is not None


def get_swipe(name: str) -> Optional[Dict[str, int]]:
    """Return stored swipe parameters for ``name``."""

    entry = resolve_dot_path(name)
    swipe = entry.get("swipe") if isinstance(entry, Mapping) else None
    if isinstance(swipe, Mapping):
        try:
            return {
                "x1": int(swipe["x1"]),
                "y1": int(swipe["y1"]),
                "x2": int(swipe["x2"]),
                "y2": int(swipe["y2"]),
                "duration_ms": int(swipe.get("duration_ms", 0)),
            }
        except Exception:
            return None
    return None


__all__ = [
    "get_clickmap",
    "get_clickmap_path",
    "reload_clickmap",
    "save_clickmap",
    "resolve_dot_path",
    "dot_path_exists",
    "set_dot_path",
    "flatten_clickmap",
    "get_entries_by_role",
    "get_explicit_tap",
    "get_click",
    "has_click",
    "get_swipe",
]

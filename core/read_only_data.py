"""Small JSON-compatible immutable containers for shared runtime evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class FrozenDict(dict):
    """A ``dict``-compatible value that rejects every mutation operation."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("shared player-save evidence is read-only")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __copy__(self) -> "FrozenDict":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> "FrozenDict":
        return self


class FrozenList(list):
    """A ``list``-compatible value that rejects every mutation operation."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("shared player-save evidence is read-only")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable

    def __copy__(self) -> "FrozenList":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> "FrozenList":
        return self


def deep_freeze(value: Any) -> Any:
    """Return a recursively read-only, JSON-compatible evidence value."""

    if isinstance(value, (FrozenDict, FrozenList)):
        return value
    if isinstance(value, Mapping):
        return FrozenDict(
            (key, deep_freeze(child)) for key, child in value.items()
        )
    if isinstance(value, list):
        return FrozenList(deep_freeze(child) for child in value)
    if isinstance(value, tuple):
        return tuple(deep_freeze(child) for child in value)
    return value


def deep_thaw(value: Any) -> Any:
    """Return ordinary mutable JSON containers for serialization/output."""

    if isinstance(value, Mapping):
        return {
            key: deep_thaw(child) for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [deep_thaw(child) for child in value]
    return value


__all__ = ["FrozenDict", "FrozenList", "deep_freeze", "deep_thaw"]

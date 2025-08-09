$PROJECT_ROOT/core/clickmap_access.py — Library
core.clickmap_access.get_clickmap() — Returns: in-memory clickmap dict (mutable reference).
core.clickmap_access.get_clickmap_path() — Returns: absolute path to clickmap.json (str).
core.clickmap_access.resolve_dot_path(dot_path: str, data: Optional[Mapping[str, Any]] = None) — Returns: value at dot path in provided mapping or global clickmap; None if missing.
core.clickmap_access.dot_path_exists(dot_path: str, data: Optional[Mapping[str, Any]] = None) — Returns: True if resolve_dot_path() yields non-None; False otherwise.
core.clickmap_access.set_dot_path(dot_path: str, value: Any, allow_overwrite: bool = False) — Returns: None (mutates in-memory clickmap); Errors: KeyError if final key exists and allow_overwrite=False; ValueError if path traverses non-dict.
core.clickmap_access.interactive_get_dot_path(clickmap: Dict[str, Any]) — Returns: 'group.suffix' or 'upgrades.<attack|defense|utility>.<left|right>'; None if user cancels; Side effects: [fs?] none; interactive I/O.
core.clickmap_access.prompt_roles(group: str, key: str) — Returns: list[str] role suggestions (interactive override allowed).
core.clickmap_access.get_click(name: str) — Returns: (x:int, y:int) from explicit 'tap' or center of 'match_region'; None if unresolved.
core.clickmap_access.get_swipe(name: str) — Returns: swipe dict {x1,y1,x2,y2,duration_ms} or None.
core.clickmap_access.has_click(name: str) — Returns: bool indicating click coords resolvable.
core.clickmap_access.tap_now(name: str) — Returns: None (issues ADB tap); Side effects: [adb], [log]; Errors: CalledProcessError if adb_shell fails (when check=True upstream).
core.clickmap_access.swipe_now(name: str) — Returns: None (issues ADB swipe); Side effects: [adb], [log]; Errors: CalledProcessError if adb_shell fails (when check=True upstream).
core.clickmap_access.save_clickmap(data: Optional[Dict[str, Any]] = None) — Returns: None (atomic JSON write to clickmap.json UTF-8); Side effects: [fs].
core.clickmap_access.flatten_clickmap(data: Optional[Dict[str, Any]] = None, prefix: str = "") — Returns: flat dict mapping dot paths → leaf values.
core.clickmap_access.get_entries_by_role(role: str) — Returns: dict of entries whose 'roles' include role (dot-path → entry dict).

core/clickmap_access.py
core.clickmap_access.get_clickmap() — R: in-memory clickmap dict (mutable reference).
core.clickmap_access.get_clickmap_path() — R: absolute path to clickmap.json (str).
core.clickmap_access.resolve_dot_path(dot_path: str, data: Optional[Mapping[str, Any]] = None) — R: value at dot path in provided mapping or global clickmap; None if missing.
core.clickmap_access.dot_path_exists(dot_path: str, data: Optional[Mapping[str, Any]] = None) — R: True if resolve_dot_path() yields non-None; False otherwise.
core.clickmap_access.set_dot_path(dot_path: str, value: Any, allow_overwrite: bool = False) — R: None (mutates in-memory clickmap); E: KeyError if final key exists and allow_overwrite=False; ValueError if path traverses non-dict.
core.clickmap_access.interactive_get_dot_path(clickmap: Dict[str, Any]) — R: 'group.suffix' or 'upgrades.<attack|defense|utility>.<left|right>'; None if user cancels; S: [fs?] none; interactive I/O.
core.clickmap_access.prompt_roles(group: str, key: str) — R: list[str] role suggestions (interactive override allowed).
core.clickmap_access.get_click(name: str) — R: (x:int, y:int) from explicit 'tap' or center of 'match_region'; None if unresolved.
core.clickmap_access.get_swipe(name: str) — R: swipe dict {x1,y1,x2,y2,duration_ms} or None.
core.clickmap_access.has_click(name: str) — R: bool indicating click coords resolvable.
core.clickmap_access.tap_now(name: str) — R: None (issues ADB tap); S: [adb], [log]; E: CalledProcessError if adb_shell fails (when check=True upstream).
core.clickmap_access.swipe_now(name: str) — R: None (issues ADB swipe); S: [adb], [log]; E: CalledProcessError if adb_shell fails (when check=True upstream).
core.clickmap_access.save_clickmap(data: Optional[Dict[str, Any]] = None) — R: None (atomic JSON write to clickmap.json UTF-8); S: [fs].
core.clickmap_access.flatten_clickmap(data: Optional[Dict[str, Any]] = None, prefix: str = "") — R: flat dict mapping dot paths → leaf values.
core.clickmap_access.get_entries_by_role(role: str) — R: dict of entries whose 'roles' include role (dot-path → entry dict).

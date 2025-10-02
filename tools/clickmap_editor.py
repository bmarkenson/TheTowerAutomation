"""Interactive helpers for editing clickmap entries."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_last_region_group: Optional[str] = None


def prompt_roles(group: str, key: str) -> List[str]:
    """Suggest roles for a clickmap entry and allow interactive override."""

    group = group.lower()
    if group == "gesture_targets":
        default = "gesture"
    elif group == "upgrades":
        default = "upgrade_label"
    elif group == "util":
        entry = input("Enter roles manually (comma separated for util group): ").strip()
        roles = [r.strip() for r in entry.split(",") if r.strip()]
        return roles if roles else ["unknown"]
    else:
        default = group.rstrip("s") or "unknown"

    override = input(
        f"Suggested roles for `{group}:{key}` → [{default}] (edit or press Enter to accept): "
    ).strip()
    if override:
        return [r.strip() for r in override.split(",") if r.strip()]
    return [default]


def _valid_group_name(name: str) -> bool:
    return bool(name) and (name[0].isalpha() or name[0] == "_") and all(
        c.isalnum() or c == "_" for c in name[1:]
    )


def interactive_get_dot_path(clickmap: Dict[str, Any]) -> Optional[str]:
    """Interactive helper for selecting/creating a dot-path."""

    global _last_region_group
    groups = list(clickmap.keys())

    while True:
        print("\nAvailable groups:")
        for index, group in enumerate(groups, start=1):
            marker = " (last used)" if group == _last_region_group else ""
            print(f"  {index}. {group}{marker}")
        print("  n. <create new group>")

        choice = input("[Enter]=reuse last, [n]=new group, [q]=cancel, or choose number/name: ").strip()

        if choice.lower() == "q":
            print("[INFO] Skipped saving.")
            return None

        if choice == "":
            if _last_region_group:
                group = _last_region_group
                print(f"[INFO] Reusing last group: {_last_region_group}")
            else:
                print("❌ No group selected yet.")
                continue
        elif choice.lower() == "n":
            new_group = input(
                "Enter new group name (letters/digits/underscore; must start with letter/_): "
            ).strip()
            if not _valid_group_name(new_group):
                print("❌ Invalid group name.")
                continue
            if new_group not in groups:
                confirm = input(f"Create new group '{new_group}'? (Y/n): ").strip().lower()
                if confirm not in ("", "y", "yes"):
                    print("[INFO] Creation cancelled.")
                    continue
                clickmap[new_group] = {}
                groups.append(new_group)
                print(f"[INFO] Group '{new_group}' created.")
            group = new_group
        elif choice.isdigit() and 1 <= int(choice) <= len(groups):
            group = groups[int(choice) - 1]
        elif choice in groups:
            group = choice
        else:
            print(f"❌ Invalid selection. Choose one of: {', '.join(groups)} or 'n' for new.")
            continue

        _last_region_group = group

        if group == "upgrades":
            subgroup = input("Enter upgrade category [attack, defense, utility]: ").strip().lower()
            if subgroup not in {"attack", "defense", "utility"}:
                print("[ERROR] Invalid upgrade subgroup.")
                continue
            side = input("Enter side [left, right]: ").strip().lower()
            if side not in {"left", "right"}:
                print("[ERROR] Invalid upgrade side.")
                continue
            label = input("Enter upgrade label key (e.g., damage, attack_speed): ").strip()
            if not _valid_group_name(label):
                print("❌ Invalid label key. Use letters/digits/underscore; start with letter/_")
                continue
            return f"{group}.{subgroup}.{side}.{label}"

        suffix = input(
            f"Enter entry key for `{group}` (e.g., retry, attack_menu, claim_ad_gem): "
        ).strip()
        if not suffix:
            print("[INFO] Skipped saving.")
            return None
        return f"{group}.{suffix}"


__all__ = ["prompt_roles", "interactive_get_dot_path"]

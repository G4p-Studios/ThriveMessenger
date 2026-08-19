"""Metadata for self-contained and future installable Thrive server modules."""

from __future__ import annotations

MODULES = {
    "groups": {
        "name": "Group Rooms",
        "description": "Persistent rooms, roles, policies, messages, and room file transfers.",
        "features": ["group_chat", "group_policy"],
        "bundled": True,
        "dependencies": [],
    },
    "voice": {
        "name": "Live Voice",
        "description": "Server-relayed live group audio and call-session signaling.",
        "features": ["group_call", "voice_call"],
        "bundled": True,
        "dependencies": ["groups"],
    },
    "bots": {
        "name": "Bots and Bot Mesh",
        "description": "Bot contacts, rules, moderation, delegation, and temporary exchange.",
        "features": ["bots", "bot_mesh", "bot_moderation", "bot_rules"],
        "bundled": False,
        "experimental": True,
        "dependencies": [],
    },
    "server_manager": {
        "name": "Server Manager",
        "description": "Multi-server management, directory, and connection tools.",
        "features": ["server_manager"],
        "bundled": True,
        "dependencies": [],
    },
    "advanced_admin": {
        "name": "Advanced Administration",
        "description": "Remote administration console and feature-policy controls.",
        "features": ["admin_console"],
        "bundled": True,
        "dependencies": [],
    },
}


def module_catalog(feature_lookup, installed_ids=None):
    installed_ids = set(installed_ids or ())
    result = []
    for module_id, metadata in MODULES.items():
        states = [feature_lookup(feature) or {} for feature in metadata["features"]]
        result.append({
            "module_id": module_id,
            **metadata,
            "installed": bool(metadata.get("bundled") or module_id in installed_ids),
            "enabled": bool((metadata.get("bundled") or module_id in installed_ids) and states and all(state.get("enabled", False) for state in states)),
        })
    return result


def modules_for_state_change(module_id: str, enabled: bool) -> list[str]:
    if module_id not in MODULES:
        return []
    selected = {module_id}
    changed = True
    while changed:
        changed = False
        for candidate, metadata in MODULES.items():
            should_add = enabled and candidate in selected
            dependencies = set(metadata.get("dependencies", []))
            additions = dependencies if should_add else set()
            if not enabled and dependencies & selected:
                additions.add(candidate)
            before = len(selected); selected.update(additions); changed = changed or len(selected) != before
    return [key for key in MODULES if key in selected]

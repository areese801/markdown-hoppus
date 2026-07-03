"""
Default keymap (spec §9.16) and config-driven remapping helpers.

Every binding has a stable ``id`` (the action name). Users remap keys via
the ``keymap`` config section, mapping a binding id to a new key, e.g.::

    keymap:
      quit: ctrl+q
      toggle_right_sidebar: "|"

Single-character punctuation keys are normalized to Textual's long key
names (``?`` -> ``question_mark``) so config values can use either form.
"""

from typing import Any

from textual.binding import Binding

#: Punctuation characters mapped to Textual's long key names.
CHARACTER_KEY_ALIASES: dict[str, str] = {
    "?": "question_mark",
    "\\": "backslash",
    "+": "plus",
    "-": "minus",
    "|": "vertical_line",
    "/": "slash",
    ".": "full_stop",
    ",": "comma",
}

#: Spec §9.16 defaults: binding id -> (key, description, show in footer).
DEFAULT_KEYMAP: dict[str, tuple[str, str, bool]] = {
    "help": ("question_mark", "Help", True),
    "command_palette": ("ctrl+p", "Palette", True),
    "quick_switcher": ("ctrl+o", "Open note", True),
    "search": ("ctrl+f", "Search", True),
    "open_editor": ("e", "Editor", False),
    "open_system": ("O", "Open in app", False),
    "open_browser": ("b", "Browser", False),
    "link_note": ("l", "Link to…", False),
    "toggle_graph": ("g", "Graph", False),
    "graph_radius_up": ("plus", "Hops +", False),
    "graph_radius_down": ("minus", "Hops -", False),
    "cycle_pane": ("tab", "Cycle panes", True),
    "toggle_right_sidebar": ("backslash", "Backlinks", True),
    "tag_pane": ("t", "Tags", False),
    "daily_note": ("d", "Daily note", False),
    "new_note": ("n", "New note", False),
    "new_note_from_template": ("N", "New from template", False),
    "quick_capture": ("c", "Quick capture", False),
    "toggle_star": ("s", "Star", False),
    "yank_menu": ("y", "Yank", False),
    "reindex": ("r", "Reindex", False),
    "vault_switcher": ("v", "Vault", False),
    "quit": ("q", "Quit", True),
}


def normalize_key(key: str) -> str:
    """
    Normalize a user-supplied key string to Textual's key naming.

    Args:
        key: A key such as ``"?"``, ``"ctrl+p"``, or ``"backslash"``.

    Returns:
        The key with any single-character punctuation alias replaced by
        its long Textual name; other values pass through unchanged.
    """
    return CHARACTER_KEY_ALIASES.get(key, key)


def default_bindings() -> list[Binding]:
    """
    Build the default Textual bindings from :data:`DEFAULT_KEYMAP`.

    Returns:
        A list of ``Binding`` objects, each with ``id`` set to its
        action name so it can be remapped via ``App.set_keymap``.
    """
    return [
        Binding(key, action, description, show=show, id=action)
        for action, (key, description, show) in DEFAULT_KEYMAP.items()
    ]


def resolve_keymap_overrides(config: dict[str, Any]) -> dict[str, str]:
    """
    Extract and normalize keymap overrides from a merged config.

    Unknown binding ids are ignored so stale config entries cannot
    break app startup.

    Args:
        config: A merged configuration dict (see ``hoppus.config``).

    Returns:
        A mapping of binding id to normalized key, suitable for
        ``App.set_keymap``.
    """
    raw = config.get("keymap") or {}
    if not isinstance(raw, dict):
        return {}
    return {
        binding_id: normalize_key(str(key))
        for binding_id, key in raw.items()
        if binding_id in DEFAULT_KEYMAP and key
    }

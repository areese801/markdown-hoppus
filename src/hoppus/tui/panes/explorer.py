"""
File Explorer pane: a read-only vault tree (spec §9.2, HOPPUS-26).

Subclasses Textual's ``DirectoryTree`` rooted at the active vault path.
Dot-directories (``.hoppus/``, ``.obsidian/``, and any other foreign or
state directories) are hidden via ``filter_paths``; attachments
(non-``.md`` files) stay visible but are rendered dimmed so notes read
normally. Selection is surfaced through the standard
``DirectoryTree.FileSelected`` message, which the app handles to open
``.md`` notes in the preview. CRUD actions arrive in Epic 4.
"""

from pathlib import Path
from typing import Iterable

from rich.style import Style
from rich.text import Text
from textual.widgets import DirectoryTree
from textual.widgets._tree import TreeNode
from textual.widgets.directory_tree import DirEntry


class ExplorerPane(DirectoryTree):
    """
    The File Explorer tree for a single vault (read-only in this story).
    """

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        """
        Hide dot-entries so ``.hoppus/``/``.obsidian/`` never appear.

        Args:
            paths: Candidate directory entries from the loader.

        Returns:
            The entries whose names do not start with a dot.
        """
        return [path for path in paths if not path.name.startswith(".")]

    def render_label(
        self, node: TreeNode[DirEntry], base_style: Style, style: Style
    ) -> Text:
        """
        Render a node label, dimming attachments (non-``.md`` files).

        Args:
            node: The tree node being rendered.
            base_style: The base style of the widget.
            style: The style of the label.

        Returns:
            The styled label text.
        """
        label = super().render_label(node, base_style, style)
        entry = node.data
        if (
            entry is not None
            and not entry.path.is_dir()
            and entry.path.suffix.lower() != ".md"
        ):
            label.stylize("dim")
        return label

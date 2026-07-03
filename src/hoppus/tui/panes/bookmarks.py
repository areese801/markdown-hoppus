"""
Bookmarks pane: the starred-notes list in the left sidebar
(spec §9.12, HOPPUS-52).

An ``OptionList`` in the left sidebar's Bookmarks tab listing the
vault's starred notes, loaded from ``hoppus.bookmarks``. Each row shows
the note's title when the note is in the index, falling back to its
vault-relative path. Bookmarks whose file no longer exists are skipped
entirely (a stale entry stays in the YAML but is invisible until the
file returns or the bookmark is toggled off). Selecting an entry
navigates through the app's ``open_note`` funnel.
"""

from pathlib import Path

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from hoppus import bookmarks
from hoppus.index.indexer import Index

_EMPTY_LABEL = "No bookmarks"


class BookmarksPane(OptionList):
    """
    Left-sidebar list of the vault's bookmarked (starred) notes.
    """

    def __init__(self, *, id: str | None = None) -> None:
        """
        Args:
            id: Optional widget id.
        """
        super().__init__(id=id)
        self._vault_root: Path | None = None
        # Option id → note path; titles can collide across folders, so
        # selection maps back through ids, never the visible title.
        self._paths: dict[str, Path] = {}

    def on_mount(self) -> None:
        """
        Start in the empty (no bookmarks) state.
        """
        if self.option_count == 0:
            self._show_empty()

    def show_bookmarks(self, vault_root: Path, index: Index) -> None:
        """
        Populate the pane from the vault's persisted bookmarks.

        Loads ``<vault>/.hoppus/bookmarks.yaml`` in stored order. Rows
        show the note title when the note is in the index, else the
        vault-relative path; bookmarks whose file no longer exists are
        skipped. Shows a disabled empty-state row when nothing remains.

        Args:
            vault_root: The active vault root.
            index: The active vault's index, for title lookups.
        """
        self._vault_root = Path(vault_root)
        self._paths = {}
        self.clear_options()
        entries: list[tuple[str, Path]] = []
        for path in bookmarks.load_bookmarks(self._vault_root):
            if not path.is_file():
                continue
            note = index.notes_by_path.get(path)
            if note is not None:
                label = note.title
            else:
                try:
                    label = path.relative_to(self._vault_root).as_posix()
                except ValueError:
                    label = path.name
            entries.append((label, path))
        if not entries:
            self._show_empty()
            return
        for position, (label, path) in enumerate(entries):
            option_id = f"bookmark-{position}"
            self._paths[option_id] = path
            self.add_option(Option(label, id=option_id))

    def clear(self) -> None:
        """
        Reset to the empty state (vault switch / no vault).
        """
        self._vault_root = None
        self._paths = {}
        self.clear_options()
        self._show_empty()

    def _show_empty(self) -> None:
        """
        Show the disabled 'No bookmarks' placeholder row.
        """
        self.add_option(Option(f"[dim]{_EMPTY_LABEL}[/dim]", disabled=True))

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """
        Navigate to the selected bookmarked note via the app's funnel.
        """
        event.stop()
        path = self._paths.get(event.option.id or "")
        if path is None:
            return
        open_note = getattr(self.app, "open_note", None)
        if open_note is None:
            return
        await open_note(path, vault_root=self._vault_root)

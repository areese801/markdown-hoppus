"""
Backlinks pane: linked mentions for the active note (spec §9.1, HOPPUS-32).

An ``OptionList`` in the right sidebar listing the notes that link TO the
active note, populated from the index's ``backlinks`` map and sorted
case-insensitively by source title. Selecting an entry navigates to that
source note through the app's ``open_note`` funnel. Unlinked mentions are
a later story (HOPPUS-33) and will share this sidebar.
"""

from pathlib import Path

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from hoppus.index.indexer import Index

_EMPTY_LABEL = "No backlinks"


class BacklinksPane(OptionList):
    """
    Right-sidebar list of linked mentions for the active note.
    """

    def __init__(self, *, id: str | None = None) -> None:
        """
        Args:
            id: Optional widget id.
        """
        super().__init__(id=id)
        self._vault_root: Path | None = None
        # Option id → source note path; titles can collide across folders,
        # so selection maps back through ids, never the visible title.
        self._paths: dict[str, Path] = {}

    def on_mount(self) -> None:
        """
        Start in the empty (no active note) state.
        """
        if self.option_count == 0:
            self._show_empty()

    def show_backlinks(self, note_path: Path, index: Index, vault_root: Path) -> None:
        """
        Populate the pane with the notes linking to ``note_path``.

        Sources come from ``index.backlinks``; each row shows the source
        note's title (falling back to its file stem), sorted
        case-insensitively. Shows an empty state when there are none.

        Args:
            note_path: Path of the active note.
            index: The active vault's index.
            vault_root: Vault root, stored for later navigation.
        """
        self._vault_root = Path(vault_root)
        self._paths = {}
        self.clear_options()
        entries: list[tuple[str, Path]] = []
        for source in index.backlinks.get(Path(note_path), set()):
            note = index.notes_by_path.get(source)
            title = note.title if note is not None else source.stem
            entries.append((title, source))
        if not entries:
            self._show_empty()
            return
        entries.sort(key=lambda entry: entry[0].casefold())
        for position, (title, source) in enumerate(entries):
            option_id = f"backlink-{position}"
            self._paths[option_id] = source
            self.add_option(Option(title, id=option_id))

    def clear(self) -> None:
        """
        Reset to the empty state (vault switch / no active note).
        """
        self._vault_root = None
        self._paths = {}
        self.clear_options()
        self._show_empty()

    def _show_empty(self) -> None:
        """
        Show the disabled 'No backlinks' placeholder row.
        """
        self.add_option(Option(f"[dim]{_EMPTY_LABEL}[/dim]", disabled=True))

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """
        Navigate to the selected backlink source via the app's funnel.
        """
        event.stop()
        path = self._paths.get(event.option.id or "")
        if path is None:
            return
        open_note = getattr(self.app, "open_note", None)
        if open_note is None:
            return
        await open_note(path, vault_root=self._vault_root)

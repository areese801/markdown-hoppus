"""
Tags pane: browse the vault's tags and jump into a tag-filtered search
(spec §9.4, HOPPUS-98).

An ``OptionList`` in the left sidebar's Tags tab listing every tag in
the vault with the number of notes carrying it. Tags come from the
index's ``tags`` map — the per-note union of frontmatter and inline
tags (``Index.note_tags``) inverted to tag → notes — so nested tags
(``area/sub``) appear under their full name. Rows sort by note count
descending, then tag name. Selecting a tag opens the search screen
pre-filled with the ``tag:<name>`` operator, so the existing search
flow surfaces the matching notes (including nested-tag prefix matches).
The pane is repopulated whenever the index changes (launch build,
reindex, watcher events, vault switch).
"""

from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from hoppus.index.indexer import Index

_EMPTY_LABEL = "No tags"


class TagsPane(OptionList):
    """
    Left-sidebar list of the vault's tags with per-tag note counts.
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "first", "First", show=False),
        Binding("G", "last", "Last", show=False),
    ]

    def __init__(self, *, id: str | None = None) -> None:
        """
        Args:
            id: Optional widget id.
        """
        super().__init__(id=id)
        # Option id → tag name; selection maps back through ids so the
        # rendered count suffix never leaks into the search query.
        self._tags: dict[str, str] = {}

    def on_mount(self) -> None:
        """
        Start in the empty (no tags) state.
        """
        if self.option_count == 0:
            self._show_empty()

    def show_tags(self, index: Index) -> None:
        """
        Populate the pane from the vault index's tag map.

        Each row shows ``#tag  ·  N`` where N is the number of notes
        carrying the tag (frontmatter ∪ inline, spec §6.4). Nested tags
        render their full ``parent/child`` name. Rows sort by count
        descending, then case-insensitively by name. Shows a disabled
        empty-state row when the vault has no tags.

        Args:
            index: The active vault's index.
        """
        self._tags = {}
        self.clear_options()
        entries = sorted(
            ((tag, len(paths)) for tag, paths in index.tags.items()),
            key=lambda entry: (-entry[1], entry[0].casefold()),
        )
        if not entries:
            self._show_empty()
            return
        for position, (tag, count) in enumerate(entries):
            option_id = f"tag-{position}"
            self._tags[option_id] = tag
            self.add_option(Option(f"#{tag}  ·  {count}", id=option_id))

    def clear(self) -> None:
        """
        Reset to the empty state (vault switch / no vault).
        """
        self._tags = {}
        self.clear_options()
        self._show_empty()

    def _show_empty(self) -> None:
        """
        Show the disabled 'No tags' placeholder row.
        """
        self.add_option(Option(f"[dim]{_EMPTY_LABEL}[/dim]", disabled=True))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """
        Open the search screen pre-filled with ``tag:<name>`` for the
        selected tag (HOPPUS-98).

        Routes through the app's ``action_search`` funnel so tag
        browsing reuses the exact ``tag:`` operator semantics of
        spec §9.8 (case-insensitive, nested-tag prefix matches).
        """
        event.stop()
        tag = self._tags.get(event.option.id or "")
        if tag is None:
            return
        search = getattr(self.app, "action_search", None)
        if search is None:
            return
        search(initial_query=f"tag:{tag}")

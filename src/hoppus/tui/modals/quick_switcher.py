"""
Quick switcher modal: fuzzy jump to any note by title or alias
(spec §9.3, HOPPUS-28).

An ``Input`` on top live-filters an ``OptionList`` of every note in the
vault index, ranked by :func:`hoppus.search.fuzzy.rank` against both the
note title and each alias. Enter (or a click) dismisses with the chosen
note path for the preview; ``ctrl+e`` (or ``ctrl+enter``) tags the result
for the ``$EDITOR`` path instead; ``Escape`` cancels.
"""

from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from hoppus.index.indexer import Index
from hoppus.model import Note
from hoppus.search.fuzzy import rank


@dataclass(frozen=True)
class SwitcherResult:
    """
    The user's choice from the quick switcher.

    :param path: Path of the chosen note.
    :param edit: True when the note should open in ``$EDITOR`` rather
        than the preview.
    """

    path: Path
    edit: bool = False


class QuickSwitcherModal(ModalScreen[SwitcherResult | None]):
    """
    Fuzzy note switcher (spec §9.3): type to filter, Enter to open.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        # priority=True: Input binds ctrl+e (cursor to end), which would
        # otherwise swallow the editor modifier while typing.
        Binding("ctrl+e", "choose_edit", "Open in $EDITOR", priority=True),
        Binding(
            "ctrl+enter", "choose_edit", "Open in $EDITOR", show=False, priority=True
        ),
        Binding("down", "cursor(1)", "Next", show=False),
        Binding("up", "cursor(-1)", "Previous", show=False),
    ]

    CSS = """
    QuickSwitcherModal {
        align: center middle;
    }
    #switcher {
        width: 70%;
        max-width: 90;
        height: auto;
        max-height: 80%;
        border: round $primary;
        background: $surface;
    }
    #switcher-results {
        height: auto;
        max-height: 20;
    }
    """

    def __init__(self, index: Index, vault_root: Path) -> None:
        """
        Args:
            index: A built vault index supplying the candidate notes.
            vault_root: The vault root, for rendering relative paths.
        """
        super().__init__()
        self._vault_root = vault_root
        self._notes: list[Note] = sorted(
            index.notes_by_path.values(), key=lambda note: note.title.lower()
        )
        self._results: list[Note] = []

    def compose(self) -> ComposeResult:
        """
        Build the query input and the ranked results list.
        """
        with Vertical(id="switcher"):
            yield Input(placeholder="Jump to note…", id="switcher-input")
            yield OptionList(id="switcher-results")

    def on_mount(self) -> None:
        """
        Focus the input and show all notes for the empty query.
        """
        self._refilter("")
        self.query_one("#switcher-input", Input).focus()

    # -- Filtering -----------------------------------------------------------

    def _prompt(self, note: Note) -> str:
        """
        Render one result row: title plus a dim alias/path hint.

        :param note: The candidate note.
        :returns: A Rich-markup prompt string.
        """
        try:
            hint = str(note.path.relative_to(self._vault_root))
        except ValueError:
            hint = str(note.path)
        if note.aliases:
            hint = f"{', '.join(note.aliases)} · {hint}"
        return f"{note.title}  [dim]{hint}[/dim]"

    def _refilter(self, query: str) -> None:
        """
        Re-rank the candidates for a query and repopulate the list.

        :param query: The current input text.
        """
        ranked = rank(
            query,
            self._notes,
            key=lambda note: [note.title, *note.aliases],
        )
        self._results = [note for note, _score in ranked]
        options = self.query_one("#switcher-results", OptionList)
        options.clear_options()
        options.add_options(
            Option(self._prompt(note), id=str(note.path)) for note in self._results
        )
        if self._results:
            options.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        """
        Live-filter the results as the query changes.
        """
        event.stop()
        self._refilter(event.value)

    # -- Selection -----------------------------------------------------------

    def _highlighted_note(self) -> Note | None:
        """
        Return the currently highlighted candidate, if any.
        """
        if not self._results:
            return None
        options = self.query_one("#switcher-results", OptionList)
        highlighted = options.highlighted
        if highlighted is None or not 0 <= highlighted < len(self._results):
            highlighted = 0
        return self._results[highlighted]

    def _choose(self, edit: bool) -> None:
        """
        Dismiss with the highlighted note, tagged for preview or editor.

        :param edit: True for the ``$EDITOR`` path.
        """
        note = self._highlighted_note()
        if note is None:
            return
        self.dismiss(SwitcherResult(path=note.path, edit=edit))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """
        Enter in the input opens the highlighted note in the preview.
        """
        event.stop()
        self._choose(edit=False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """
        Clicking (or Enter on) a row opens that note in the preview.
        """
        event.stop()
        if event.option_index < len(self._results):
            note = self._results[event.option_index]
            self.dismiss(SwitcherResult(path=note.path, edit=False))

    def action_choose_edit(self) -> None:
        """
        Open the highlighted note via the ``$EDITOR`` path (spec §9.3).
        """
        self._choose(edit=True)

    def action_cursor(self, delta: int) -> None:
        """
        Move the result highlight up or down while typing.

        :param delta: +1 for down, -1 for up.
        """
        options = self.query_one("#switcher-results", OptionList)
        if options.option_count == 0:
            return
        current = options.highlighted if options.highlighted is not None else 0
        options.highlighted = (current + delta) % options.option_count

    def action_cancel(self) -> None:
        """
        Dismiss with no selection.
        """
        self.dismiss(None)

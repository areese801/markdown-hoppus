"""
Related-link picker modal (spec §7.2, HOPPUS-41).

Mirrors the quick switcher: an ``Input`` live-filters an ``OptionList``
of every note in the vault index, ranked by
:func:`hoppus.search.fuzzy.rank` against titles and aliases. Enter (or
a click) dismisses with the chosen note. When the typed query matches
no existing title or alias exactly, a trailing "create and link" row
offers the raw name so the app can bootstrap ``<name>.md`` at the vault
root (the section never gains a dangling link). ``Escape`` cancels.

Business logic (append/dedupe/bootstrap) lives in
:mod:`hoppus.related` and the app action — the modal only picks.
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
class RelatedChoice:
    """
    The user's choice from the related-link picker.

    Exactly one of the two fields is set.

    :param note: An existing note picked from the list, or None.
    :param new_name: A typed name with no existing note, to be
        bootstrapped at the vault root, or None.
    """

    note: Note | None = None
    new_name: str | None = None


class RelatedPickerModal(ModalScreen[RelatedChoice | None]):
    """
    Fuzzy picker for the ``## Related`` link target (spec §7.2).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("down", "cursor(1)", "Next", show=False),
        Binding("up", "cursor(-1)", "Previous", show=False),
    ]

    CSS = """
    RelatedPickerModal {
        align: center middle;
    }
    #related-picker {
        width: 70%;
        max-width: 90;
        height: auto;
        max-height: 80%;
        border: round $primary;
        background: $surface;
    }
    #related-results {
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
        self._results: list[RelatedChoice] = []

    def compose(self) -> ComposeResult:
        """
        Build the query input and the ranked results list.
        """
        with Vertical(id="related-picker"):
            yield Input(placeholder="Link to note…", id="related-input")
            yield OptionList(id="related-results")

    def on_mount(self) -> None:
        """
        Focus the input and show all notes for the empty query.
        """
        self._refilter("")
        self.query_one("#related-input", Input).focus()

    # -- Filtering -----------------------------------------------------------

    def _prompt(self, choice: RelatedChoice) -> str:
        """
        Render one result row: title plus a dim alias/path hint, or the
        create-and-link offer for a new name.

        :param choice: The candidate choice.
        :returns: A Rich-markup prompt string.
        """
        if choice.note is None:
            return f"＋ Create '{choice.new_name}' and link"
        note = choice.note
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

        Appends the create-and-link row when the trimmed query is
        non-empty and matches no title or alias exactly
        (case-insensitively).

        :param query: The current input text.
        """
        ranked = rank(
            query,
            self._notes,
            key=lambda note: [note.title, *note.aliases],
        )
        self._results = [RelatedChoice(note=note) for note, _score in ranked]
        name = query.strip()
        if name and not self._matches_existing(name):
            self._results.append(RelatedChoice(new_name=name))
        options = self.query_one("#related-results", OptionList)
        options.clear_options()
        options.add_options(Option(self._prompt(choice)) for choice in self._results)
        if self._results:
            options.highlighted = 0

    def _matches_existing(self, name: str) -> bool:
        """
        Whether a name equals an existing title or alias, ignoring case.

        :param name: The trimmed typed query.
        """
        wanted = name.casefold()
        return any(
            wanted
            in (candidate.casefold() for candidate in [note.title, *note.aliases])
            for note in self._notes
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        """
        Live-filter the results as the query changes.
        """
        event.stop()
        self._refilter(event.value)

    # -- Selection -----------------------------------------------------------

    def _highlighted_choice(self) -> RelatedChoice | None:
        """
        Return the currently highlighted candidate, if any.
        """
        if not self._results:
            return None
        options = self.query_one("#related-results", OptionList)
        highlighted = options.highlighted
        if highlighted is None or not 0 <= highlighted < len(self._results):
            highlighted = 0
        return self._results[highlighted]

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """
        Enter in the input dismisses with the highlighted choice.
        """
        event.stop()
        choice = self._highlighted_choice()
        if choice is not None:
            self.dismiss(choice)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """
        Clicking (or Enter on) a row dismisses with that choice.
        """
        event.stop()
        if event.option_index < len(self._results):
            self.dismiss(self._results[event.option_index])

    def action_cursor(self, delta: int) -> None:
        """
        Move the result highlight up or down while typing.

        :param delta: +1 for down, -1 for up.
        """
        options = self.query_one("#related-results", OptionList)
        if options.option_count == 0:
            return
        current = options.highlighted if options.highlighted is not None else 0
        options.highlighted = (current + delta) % options.option_count

    def action_cancel(self) -> None:
        """
        Dismiss with no selection.
        """
        self.dismiss(None)

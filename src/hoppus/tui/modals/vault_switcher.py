"""
Vault switcher modal: switch the active vault in-TUI (spec §9.15,
HOPPUS-30).

Mirrors the quick switcher's shape: an ``Input`` on top live-filters an
``OptionList`` of vault names discovered under the Vaults Root, ranked by
:func:`hoppus.search.fuzzy.rank`. Enter (or a click) dismisses with the
chosen vault name; ``Escape`` cancels. The current vault, when present,
is pre-selected for the empty query.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from hoppus.search.fuzzy import rank


class VaultSwitcherModal(ModalScreen[str | None]):
    """
    Fuzzy vault switcher (spec §9.15): type to filter, Enter to switch.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("down", "cursor(1)", "Next", show=False),
        Binding("up", "cursor(-1)", "Previous", show=False),
    ]

    CSS = """
    VaultSwitcherModal {
        align: center middle;
    }
    #vault-switcher {
        width: 60%;
        max-width: 70;
        height: auto;
        max-height: 70%;
        border: round $primary;
        background: $surface;
    }
    #vault-switcher-results {
        height: auto;
        max-height: 16;
    }
    """

    def __init__(self, vault_names: list[str], current: str | None = None) -> None:
        """
        Args:
            vault_names: Names of the vaults discovered under the
                Vaults Root, in display order.
            current: The active vault name, pre-selected when present.
        """
        super().__init__()
        self._vault_names = list(vault_names)
        self._current = current
        self._results: list[str] = []

    def compose(self) -> ComposeResult:
        """
        Build the query input and the ranked vault list.
        """
        with Vertical(id="vault-switcher"):
            yield Input(placeholder="Switch vault…", id="vault-switcher-input")
            yield OptionList(id="vault-switcher-results")

    def on_mount(self) -> None:
        """
        Focus the input and show all vaults for the empty query.
        """
        self._refilter("")
        self.query_one("#vault-switcher-input", Input).focus()

    # -- Filtering -----------------------------------------------------------

    def _refilter(self, query: str) -> None:
        """
        Re-rank the vault names for a query and repopulate the list.

        :param query: The current input text.
        """
        ranked = rank(query, self._vault_names, key=lambda name: name)
        self._results = [name for name, _score in ranked]
        options = self.query_one("#vault-switcher-results", OptionList)
        options.clear_options()
        options.add_options(Option(name, id=name) for name in self._results)
        if self._results:
            highlighted = 0
            if self._current in self._results:
                highlighted = self._results.index(self._current)
            options.highlighted = highlighted

    def on_input_changed(self, event: Input.Changed) -> None:
        """
        Live-filter the vault list as the query changes.
        """
        event.stop()
        self._refilter(event.value)

    # -- Selection -----------------------------------------------------------

    def _highlighted_name(self) -> str | None:
        """
        Return the currently highlighted vault name, if any.
        """
        if not self._results:
            return None
        options = self.query_one("#vault-switcher-results", OptionList)
        highlighted = options.highlighted
        if highlighted is None or not 0 <= highlighted < len(self._results):
            highlighted = 0
        return self._results[highlighted]

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """
        Enter in the input switches to the highlighted vault.
        """
        event.stop()
        name = self._highlighted_name()
        if name is None:
            return
        self.dismiss(name)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """
        Clicking (or Enter on) a row switches to that vault.
        """
        event.stop()
        if event.option_index < len(self._results):
            self.dismiss(self._results[event.option_index])

    def action_cursor(self, delta: int) -> None:
        """
        Move the vault highlight up or down while typing.

        :param delta: +1 for down, -1 for up.
        """
        options = self.query_one("#vault-switcher-results", OptionList)
        if options.option_count == 0:
            return
        current = options.highlighted if options.highlighted is not None else 0
        options.highlighted = (current + delta) % options.option_count

    def action_cancel(self) -> None:
        """
        Dismiss with no selection.
        """
        self.dismiss(None)
